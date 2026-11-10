"""エージェント用 API キーの認証とスコープ検査。

キーは Authorization: Bearer <key> で受け取り、SHA-256 ハッシュで
agent_api_keys を引く。チェックはサービス層側（ここ）で強制する。
"""

import contextvars
import hashlib

from . import db

_current_agent: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "current_agent", default=None
)


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def authenticate(authorization: str | None) -> dict | None:
    """Authorization ヘッダを検証し、エージェント情報（name / scopes）を返す。"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    raw = authorization[len("Bearer ") :].strip()
    if not raw:
        return None
    with db.connect() as conn:
        row = conn.execute(
            "SELECT name, scopes FROM agent_api_keys"
            " WHERE key_hash = %s AND NOT revoked",
            (hash_key(raw),),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE agent_api_keys SET last_used_at = now() WHERE key_hash = %s",
            (hash_key(raw),),
        )
    return {"name": row["name"], "scopes": list(row["scopes"] or [])}


def set_current_agent(agent: dict | None):
    return _current_agent.set(agent)


def reset_current_agent(token) -> None:
    _current_agent.reset(token)


def require_scope(scope: str) -> dict:
    """現在のエージェントが scope を持つことを要求する。"""
    agent = _current_agent.get()
    if agent is None:
        raise PermissionError("authentication required")
    if scope not in agent["scopes"]:
        raise PermissionError(f"missing scope: {scope}")
    return agent
