"""MCP サーバー（エージェント向けの主窓口）。

ツールは docs/mcp-tools.md の12個。実処理はサービス層（app.services）に委譲し、
権限チェックはサービス層で強制する。
"""

from mcp.server.fastmcp import FastMCP

from . import services

mcp = FastMCP(
    "twinsworks_hub",
    instructions=(
        "共有基盤（twinsworks_hub）の MCP サーバー。"
        "共有タスク・身元・共有文書の読み書きと、取り込みの記録に使う。"
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp",
)


# ------------------------------------------------------------------ tasks


@mcp.tool(name="tasks.list")
def tasks_list(status: str | None = None, project: str | None = None,
               limit: int = 100) -> list[dict]:
    """共有タスク一覧。status・project で絞り込みできる。（要 tasks:read）"""
    return services.tasks_list(status=status, project=project, limit=limit)


@mcp.tool(name="tasks.get")
def tasks_get(task_id: str) -> dict:
    """タスク1件と、紐付く documents を返す。（要 tasks:read）"""
    return services.tasks_get(task_id)


@mcp.tool(name="tasks.create")
def tasks_create(title: str, description: str | None = None,
                 owner_identity_id: str | None = None,
                 due_at: str | None = None, source: str | None = None,
                 project: str | None = None,
                 priority: str = "normal") -> dict:
    """共有タスクを作成する。project は事業の区別。（要 tasks:write）"""
    return services.tasks_create(
        title=title, description=description,
        owner_identity_id=owner_identity_id, due_at=due_at, source=source,
        project=project, priority=priority,
    )


@mcp.tool(name="tasks.update_status")
def tasks_update_status(task_id: str, status: str) -> dict:
    """タスクの状態を遷移させる（open / in_progress / done / archived）。
    （要 tasks:write）"""
    return services.tasks_update_status(task_id, status)


@mcp.tool(name="tasks.promote")
def tasks_promote(promoted_from: str, title: str,
                  description: str | None = None,
                  owner_identity_id: str | None = None,
                  due_at: str | None = None, source: str | None = None,
                  project: str | None = None,
                  priority: str = "normal") -> dict:
    """個人側タスクを共有化（昇格）し、共有側に正本を作る。
    promoted_from は個人側の参照情報。個人側にリンクだけ残す処理は
    呼び出し側で行う。（要 tasks:write）"""
    return services.tasks_promote(
        promoted_from=promoted_from, title=title, description=description,
        owner_identity_id=owner_identity_id, due_at=due_at, source=source,
        project=project, priority=priority,
    )


# ------------------------------------------------------------- identities


@mcp.tool(name="identities.list")
def identities_list() -> list[dict]:
    """身元一覧（handles を含む）。（要 identities:read）"""
    return services.identities_list()


@mcp.tool(name="identities.resolve")
def identities_resolve(handle: str, channel: str | None = None) -> dict:
    """handle（Slack ID・メールアドレス等）から身元を引く。
    （要 identities:read）"""
    return services.identities_resolve(handle, channel=channel)


@mcp.tool(name="identities.create")
def identities_create(kind: str, name: str, email: str | None = None,
                      notes: str | None = None,
                      handles: list[dict] | None = None) -> dict:
    """身元と handles を登録する。kind は person / agent / service。
    handles は {channel, handle, is_primary} の列。（要 identities:write）"""
    return services.identities_create(
        kind=kind, name=name, email=email, notes=notes, handles=handles,
    )


# -------------------------------------------------------------- documents


@mcp.tool(name="documents.list")
def documents_list(source: str | None = None, kind: str | None = None,
                   limit: int = 100) -> list[dict]:
    """共有文書一覧。source・kind で絞り込みできる。（要 documents:read）"""
    return services.documents_list(source=source, kind=kind, limit=limit)


@mcp.tool(name="documents.get")
def documents_get(document_id: str) -> dict:
    """文書1件のメタ情報と locator を返す。原文そのものは返さない。
    （要 documents:read）"""
    return services.documents_get(document_id)


@mcp.tool(name="documents.ingest")
def documents_ingest(title: str, locator: str, kind: str | None = None,
                     source: str | None = None,
                     external_id: str | None = None,
                     task_id: str | None = None) -> dict:
    """取り込み文書を登録する。原文ファイルは呼び出し側が VPS 上へ
    配置しておくこと。source + external_id で重複排除する（既存があれば
    それを返し、created=false になる）。（要 ingestion:write）"""
    return services.documents_ingest(
        title=title, locator=locator, kind=kind, source=source,
        external_id=external_id, task_id=task_id,
    )


# -------------------------------------------------------------- ingestion


@mcp.tool(name="ingestion.record_checkpoint")
def ingestion_record_checkpoint(source: str,
                                last_external_id: str | None = None,
                                last_seen_at: str | None = None) -> dict:
    """定期取得の最終位置（チェックポイント）を記録する。
    （要 ingestion:write）"""
    return services.ingestion_record_checkpoint(
        source=source, last_external_id=last_external_id,
        last_seen_at=last_seen_at,
    )
