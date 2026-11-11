"""共有基盤のアプリ。

サービス層 + MCP サーバー（エージェント向け）をこのアプリに載せる。
Web ダッシュボード（人間向け）は今後ここへ追加する。
"""

import os
from contextlib import asynccontextmanager

import anyio
import psycopg
from fastapi import FastAPI
from starlette.responses import JSONResponse

from . import auth
from .mcp_server import mcp

mcp_app = mcp.streamable_http_app()


class ApiKeyMiddleware:
    """MCP エンドポイント用の API キー認証（ASGI ミドルウェア）。

    検証したエージェント情報を contextvar に載せ、サービス層の
    スコープチェック（app.auth.require_scope）が参照する。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        agent = await anyio.to_thread.run_sync(
            auth.authenticate, headers.get("authorization")
        )
        if agent is None:
            response = JSONResponse(
                {"error": "unauthorized"}, status_code=401
            )
            await response(scope, receive, send)
            return
        token = auth.set_current_agent(agent)
        try:
            await self.app(scope, receive, send)
        finally:
            auth.reset_current_agent(token)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="twinsworks_hub", lifespan=lifespan)


def db_ok() -> bool:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return False
    try:
        with psycopg.connect(url, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


@app.get("/health")
def health():
    return {"status": "ok", "db": db_ok()}


# ダッシュボード（人間用・読み取り専用）が / を担う
from .dashboard import router as dashboard_router  # noqa: E402

app.include_router(dashboard_router)


# Research サマリーの静的配信。
# xvps 上の clone（~/research）を読み取り専用マウントし、各リポジトリの
# reports/collect_dashboards/ だけを /research/<repo>/ で配信する。
# clone が無い環境（ローカル等）ではマウント自体をしない。
from pathlib import Path as _Path  # noqa: E402

from fastapi.staticfiles import StaticFiles as _StaticFiles  # noqa: E402

_RESEARCH_BASE = _Path(os.environ.get("RESEARCH_BASE_DIR", "/research"))
for _repo in ("ResearchJanken", "ResearchLiveChat"):
    _dash_dir = _RESEARCH_BASE / _repo / "reports" / "collect_dashboards"
    if _dash_dir.is_dir():
        app.mount(
            f"/research/{_repo}",
            _StaticFiles(directory=str(_dash_dir), html=True),
            name=f"research-{_repo}",
        )


_NAV_BAR = (
    '<div style="background:#111827;color:#e5e7eb;padding:8px 14px;'
    "font-family:-apple-system,'Hiragino Kaku Gothic ProN','Noto Sans JP',sans-serif;"
    'font-size:13px;">'
    '<a href="/" style="color:#fff;text-decoration:none;font-weight:700;">'
    "← twins works コンソール</a>"
    '<span style="color:#9ca3af;margin-left:10px;">Research サマリー</span>'
    "</div>"
).encode("utf-8")


class ResearchNavMiddleware:
    """ /research/ 配下の HTML に、コンソールへ戻るバーを配信時に差し込む。

    サマリー HTML は Mac mini 側の生成物なので、ファイル自体は変更しない。
    HTML レスポンスの <body> 直後にだけバーを注入する（画像等は素通し）。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/research/"):
            await self.app(scope, receive, send)
            return
        start_msg = None
        body_parts = []

        async def capture(message):
            nonlocal start_msg
            if message["type"] == "http.response.start":
                start_msg = message
            elif message["type"] == "http.response.body":
                body_parts.append(message.get("body", b""))
                if not message.get("more_body"):
                    await self._flush(scope, send, start_msg, b"".join(body_parts))
            else:
                await send(message)

        await self.app(scope, receive, capture)

    async def _flush(self, scope, send, start_msg, body):
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in (start_msg or {}).get("headers", [])
        }
        is_html = (
            (start_msg or {}).get("status") == 200
            and headers.get("content-type", "").startswith("text/html")
            and scope.get("method") == "GET"
        )
        if is_html:
            lower = body.lower()
            idx = lower.find(b"<body")
            if idx >= 0:
                end = lower.find(b">", idx)
                if end >= 0:
                    body = body[: end + 1] + _NAV_BAR + body[end + 1 :]
                    new_headers = [
                        (k, v)
                        for k, v in start_msg["headers"]
                        if k.decode("latin-1").lower() != "content-length"
                    ]
                    new_headers.append(
                        (b"content-length", str(len(body)).encode("latin-1"))
                    )
                    start_msg = {**start_msg, "headers": new_headers}
        await send(start_msg)
        await send({"type": "http.response.body", "body": body})


app.add_middleware(ResearchNavMiddleware)


# MCP はルートにマウントし、サブアプリ側のパス（/mcp）で受ける。
# / と /health の後に置くことで、そちらが優先される。
app.mount("/", ApiKeyMiddleware(mcp_app))
