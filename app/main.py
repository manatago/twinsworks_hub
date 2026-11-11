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
_RESEARCH_MOUNTS = {
    # マウント名: (clone のリポジトリ名, 配信するサブディレクトリ)
    "ResearchJanken": ("ResearchJanken", "reports/collect_dashboards"),
    "ResearchJanken-dlsite": ("ResearchJanken", "reports/dlsite"),
    "ResearchLiveChat": ("ResearchLiveChat", "reports/collect_dashboards"),
}
for _mount, (_repo_dir, _sub) in _RESEARCH_MOUNTS.items():
    _dash_dir = _RESEARCH_BASE / _repo_dir / _sub
    if _dash_dir.is_dir():
        app.mount(
            f"/research/{_mount}",
            _StaticFiles(directory=str(_dash_dir), html=True),
            name=f"research-{_mount}",
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

# DLsite 販売サマリー専用の操作性拡張（配信時にだけ注入する）。
# 線（polyline）の当たり判定を太くし、ホバーで商品名のツールチップ、
# クリックで同じ data-series の凡例へスクロール＋強調表示する。
# 線と凡例の対応は元ページの data-series 属性を使う。
_DL_ENHANCE = """
<style>
#muse-tip { position:fixed; z-index:9999; pointer-events:none; background:#111827; color:#fff;
  padding:6px 10px; border-radius:6px; font-size:12.5px; max-width:340px; display:none;
  box-shadow:0 2px 8px rgba(0,0,0,.3); }
.legend.muse-flash .legend-label { fill:#dc2626 !important; font-weight:700; }
</style>
<div id="muse-tip"></div>
<script>
(function () {
  var tip = document.getElementById("muse-tip");
  if (!tip) return;
  function seriesName(g, svg) {
    var t = g.querySelector("circle title");
    if (t && t.textContent) return t.textContent.split("\\n")[0];
    var id = g.getAttribute("data-series");
    if (id && svg) {
      var lg = svg.querySelector('.legend[data-series="' + id + '"] .legend-label');
      if (lg) return lg.textContent;
    }
    return id || "";
  }
  document.querySelectorAll("svg").forEach(function (svg) {
    svg.querySelectorAll(".series").forEach(function (g) {
      var line = g.querySelector("polyline") || g.querySelector("path");
      if (!line) return;
      var hit = line.cloneNode(false);
      hit.removeAttribute("class");
      hit.style.stroke = "rgba(0,0,0,0)";
      hit.style.strokeWidth = "14px";
      hit.style.fill = "none";
      hit.style.pointerEvents = "stroke";
      hit.style.cursor = "pointer";
      line.parentNode.insertBefore(hit, line.nextSibling);
      var name = seriesName(g, svg);
      hit.addEventListener("mouseenter", function () {
        tip.textContent = name;
        tip.style.display = "block";
        line.style.strokeWidth = "4px";
      });
      hit.addEventListener("mousemove", function (e) {
        tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 360) + "px";
        tip.style.top = Math.min(e.clientY + 14, window.innerHeight - 60) + "px";
      });
      hit.addEventListener("mouseleave", function () {
        tip.style.display = "none";
        line.style.strokeWidth = "";
      });
      hit.addEventListener("click", function () {
        var id = g.getAttribute("data-series");
        var lg = id && svg.querySelector('.legend[data-series="' + id + '"]');
        if (lg) {
          lg.scrollIntoView({ behavior: "smooth", block: "center" });
          lg.classList.add("muse-flash");
          setTimeout(function () { lg.classList.remove("muse-flash"); }, 1800);
        }
      });
    });
  });
})();
</script>
""".encode("utf-8")

_DL_PREFIX = "/research/ResearchJanken-dlsite/"


class ResearchNavMiddleware:
    """ /research/ 配下の HTML への配信時加工。

    サマリー HTML は Mac mini 側の生成物なので、ファイル自体は変更しない。
    - 通常の HTML には <body> 直後へコンソールへ戻るバーを注入する
      （ラッパーページの iframe 内表示＝embed=1 では付けない）。
    - DLsite 販売サマリーには、線ホバーで商品名ツールチップ／クリックで
      凡例へジャンプする拡張スクリプトも注入する（embed 時も有効）。
    画像など HTML 以外は素通しする。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/research/"):
            await self.app(scope, receive, send)
            return
        embed = b"embed=1" in (scope.get("query_string") or b"")
        dlsite = scope.get("path", "").startswith(_DL_PREFIX)
        if embed and not dlsite:
            # ラッパーページの iframe 内表示ではバーを付けない（二重のナビ防止）
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
            embed = b"embed=1" in (scope.get("query_string") or b"")
            dlsite = scope.get("path", "").startswith(_DL_PREFIX)
            if not embed:
                lower = body.lower()
                idx = lower.find(b"<body")
                if idx >= 0:
                    end = lower.find(b">", idx)
                    if end >= 0:
                        body = body[: end + 1] + _NAV_BAR + body[end + 1 :]
            if dlsite:
                close = body.lower().rfind(b"</body>")
                if close >= 0:
                    body = body[:close] + _DL_ENHANCE + body[close:]
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


# Research サマリーのラッパーページ。
# コンソールのサイドバー付きの枠でサマリーを iframe 表示し、サマリー側の
# ページ遷移でもサイドバーが残るようにする（静的配信の URL はそのまま）。
import html as _html  # noqa: E402

from fastapi import HTTPException as _HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse as _HTMLResponse  # noqa: E402

from .dashboard import _research_sidebar as _sidebar_html  # noqa: E402

_VIEW_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Research サマリー | twins works</title>
<style>
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:-apple-system,"Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif; background:#f6f5f2; }}
  .layout {{ display:flex; align-items:stretch; min-height:100vh; }}
  .side {{ width:232px; flex:0 0 232px; background:#111827; color:#e5e7eb; padding:18px 14px; position:sticky; top:0; height:100vh; overflow-y:auto; }}
  .side .brand {{ font-size:13px; font-weight:700; letter-spacing:.04em; color:#fff; margin:2px 6px 14px; }}
  .side a {{ display:block; color:#e5e7eb; text-decoration:none; padding:8px 10px; border-radius:8px; font-size:13px; margin-bottom:2px; }}
  .side a:hover {{ background:#1f2937; }}
  .side a.active {{ background:#1f2937; color:#fff; font-weight:700; }}
  .side .sec {{ font-size:11px; color:#9ca3af; margin:16px 6px 6px; letter-spacing:.05em; }}
  .side .upd {{ display:block; font-size:11px; color:#9ca3af; padding:0 10px; margin:-2px 0 6px; }}
  .side .foot {{ font-size:11px; color:#9ca3af; margin:18px 6px 0; line-height:1.6; }}
  .content {{ flex:1; min-width:0; }}
  iframe {{ display:block; width:100%; height:100vh; border:0; background:#fff; }}
  @media (max-width:760px) {{ .layout {{ flex-direction:column; }} .side {{ width:auto; flex:none; height:auto; position:static; }} iframe {{ height:calc(100vh - 200px); }} }}
</style>
</head>
<body>
<div class="layout">
<aside class="side">{sidebar}</aside>
<div class="content"><iframe src="{src}" title="Research サマリー"></iframe></div>
</div>
</body>
</html>"""


@app.get("/research/view/{repo}/{rest:path}", response_class=_HTMLResponse)
def research_view(repo: str, rest: str):
    entries = {
        "ResearchJanken": "latest.html",
        "ResearchJanken-dlsite": "latest.html",
        "ResearchLiveChat": "index.html",
    }
    if repo not in entries:
        raise _HTTPException(status_code=404)
    entry = rest or entries[repo]
    sidebar = _sidebar_html()
    sidebar = sidebar.replace('<a href="/" class="active">', '<a href="/">', 1)
    sidebar = sidebar.replace(
        f'<a href="/research/view/{repo}/',
        f'<a class="active" href="/research/view/{repo}/',
        1,
    )
    src = _html.escape(f"/research/{repo}/{entry}?embed=1", quote=True)
    return _VIEW_PAGE.format(sidebar=sidebar, src=src)


# MCP はルートにマウントし、サブアプリ側のパス（/mcp）で受ける。
# / と /health の後に置くことで、そちらが優先される。
app.mount("/", ApiKeyMiddleware(mcp_app))
