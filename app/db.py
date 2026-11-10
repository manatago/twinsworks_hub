"""DB 接続の共通処理。サービス層はここ経由で Postgres に触る。"""

import os
from datetime import datetime, timezone
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


def connect() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(url, row_factory=dict_row)


def to_jsonable(value):
    """DB の戻り値（UUID / datetime 等）を JSON 化できる形に寄せる。"""
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value
