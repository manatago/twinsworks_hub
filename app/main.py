"""共有基盤の最小アプリ。

サービス層 + MCP サーバー（エージェント向け）+ Web ダッシュボード（人間向け）を
このアプリに載せていく。当面はヘルスチェックのみ。
"""

import os

import psycopg
from fastapi import FastAPI

app = FastAPI(title="twinsworks_hub")


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


@app.get("/")
def root():
    return {"service": "twinsworks_hub", "status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok", "db": db_ok()}
