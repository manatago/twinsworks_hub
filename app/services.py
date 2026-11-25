"""サービス層。MCP ツール（将来はダッシュボードも）はここを呼ぶ。

権限チェック（require_scope）は各関数の入口で行い、API 側で強制する。
設計: docs/mcp-tools.md
"""

from . import auth, db

TASK_STATUSES = ("open", "in_progress", "done", "archived")
TASK_PRIORITIES = ("low", "normal", "high")

_TASK_COLUMNS = (
    "id, title, description, status, owner_identity_id, due_at, source,"
    " project, priority, promoted_from, promoted_at, completed_at,"
    " created_at, updated_at"
)
_DOCUMENT_COLUMNS = (
    "id, title, kind, locator, source, external_id, ingested_at, created_at"
)
_IDENTITY_COLUMNS = "id, kind, name, email, notes, status, created_at, updated_at"


def _row(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def _rows(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------- tasks


def tasks_list(status: str | None = None, project: str | None = None,
               limit: int = 100) -> list[dict]:
    auth.require_scope("tasks:read")
    clauses, params = [], []
    if status:
        if status not in TASK_STATUSES:
            raise ValueError(f"invalid status: {status}")
        clauses.append("status = %s")
        params.append(status)
    if project:
        clauses.append("project = %s")
        params.append(project)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    limit = max(1, min(int(limit), 500))
    with db.connect() as conn:
        rows = _rows(
            conn,
            f"SELECT {_TASK_COLUMNS} FROM tasks{where}"
            " ORDER BY created_at ASC LIMIT %s",
            (*params, limit),
        )
    return db.to_jsonable(rows)


def tasks_get(task_id: str) -> dict:
    auth.require_scope("tasks:read")
    with db.connect() as conn:
        task = _row(conn, f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = %s",
                    (task_id,))
        if task is None:
            raise ValueError(f"task not found: {task_id}")
        docs = _rows(
            conn,
            f"SELECT {_DOCUMENT_COLUMNS} FROM documents d"
            " JOIN task_documents td ON td.document_id = d.id"
            " WHERE td.task_id = %s ORDER BY d.created_at ASC",
            (task_id,),
        )
    task = db.to_jsonable(task)
    task["documents"] = db.to_jsonable(docs)
    return task


def _validate_task_fields(priority: str | None) -> None:
    if priority is not None and priority not in TASK_PRIORITIES:
        raise ValueError(f"invalid priority: {priority}")


def _insert_task(conn, *, title, description, owner_identity_id, due_at,
                 source, project, priority, promoted_from=None,
                 mark_promoted=False):
    return _row(
        conn,
        f"INSERT INTO tasks (title, description, owner_identity_id, due_at,"
        f" source, project, priority, promoted_from, promoted_at)"
        f" VALUES (%s, %s, %s, %s, %s, %s, %s, %s,"
        f" CASE WHEN %s THEN now() ELSE NULL END)"
        f" RETURNING {_TASK_COLUMNS}",
        (title, description, owner_identity_id, due_at, source, project,
         priority, promoted_from, mark_promoted),
    )


def tasks_create(title: str, description: str | None = None,
                 owner_identity_id: str | None = None,
                 due_at: str | None = None, source: str | None = None,
                 project: str | None = None,
                 priority: str = "normal") -> dict:
    auth.require_scope("tasks:write")
    _validate_task_fields(priority)
    with db.connect() as conn:
        task = _insert_task(
            conn, title=title, description=description,
            owner_identity_id=owner_identity_id, due_at=due_at,
            source=source, project=project, priority=priority,
        )
    return db.to_jsonable(task)


def tasks_promote(promoted_from: str, title: str,
                  description: str | None = None,
                  owner_identity_id: str | None = None,
                  due_at: str | None = None, source: str | None = None,
                  project: str | None = None,
                  priority: str = "normal") -> dict:
    """個人側タスクの共有化（昇格）。共有側に正本を作る。

    個人側にリンクだけ残す処理は呼び出し側（個人側エージェント）が行う。
    """
    auth.require_scope("tasks:write")
    _validate_task_fields(priority)
    with db.connect() as conn:
        task = _row(
            conn,
            "SELECT id FROM tasks WHERE promoted_from = %s",
            (promoted_from,),
        )
        if task is not None:
            raise ValueError(f"already promoted: {promoted_from}")
        task = _insert_task(
            conn, title=title, description=description,
            owner_identity_id=owner_identity_id, due_at=due_at,
            source=source, project=project, priority=priority,
            promoted_from=promoted_from, mark_promoted=True,
        )
    return db.to_jsonable(task)


def _ledger_number(conn, task_id: str) -> int | None:
    """台帳番号（created_at 順、id でタイブレーク）を返す。"""
    row = _row(
        conn,
        "SELECT n FROM (SELECT id, ROW_NUMBER() OVER"
        " (ORDER BY created_at ASC, id ASC) AS n FROM tasks) ranked"
        " WHERE id = %s",
        (task_id,),
    )
    return int(row["n"]) if row else None


def tasks_update(task_id: str, title: str | None = None,
                 description: str | None = None,
                 owner_identity_id: str | None = None,
                 due_at: str | None = None,
                 project: str | None = None,
                 priority: str | None = None) -> dict:
    """タスクの項目を部分的に修正する。

    None の項目は変更しない。due_at / owner_identity_id / project は
    空文字列を渡すと未設定（NULL）に戻す。状態の遷移は
    tasks_update_status を使うこと（ここでは扱わない）。
    """
    auth.require_scope("tasks:write")
    _validate_task_fields(priority)
    if title is not None and not title.strip():
        raise ValueError("title must not be empty")
    sets, params = [], []
    if title is not None:
        sets.append("title = %s")
        params.append(title)
    if description is not None:
        sets.append("description = %s")
        params.append(description or None)
    if owner_identity_id is not None:
        sets.append("owner_identity_id = %s")
        params.append(owner_identity_id or None)
    if due_at is not None:
        sets.append("due_at = %s")
        params.append(due_at or None)
    if project is not None:
        sets.append("project = %s")
        params.append(project or None)
    if priority is not None:
        sets.append("priority = %s")
        params.append(priority)
    if not sets:
        raise ValueError("nothing to update")
    sets.append("updated_at = now()")
    with db.connect() as conn:
        task = _row(
            conn,
            f"UPDATE tasks SET {', '.join(sets)}"
            f" WHERE id = %s RETURNING {_TASK_COLUMNS}",
            (*params, task_id),
        )
        if task is None:
            raise ValueError(f"task not found: {task_id}")
    return db.to_jsonable(task)


def tasks_merge(source_task_id: str, target_task_id: str) -> dict:
    """source タスクを target タスクへ統合する（2026-11-21 の手作業の定型化）。

    - target の説明末尾に、source の台帳番号・タイトル・説明を
      「統合された内訳」として追記する。
    - source に紐付く documents は target へ付け替える。
    - source は archived にし、説明末尾へ統合先の注記を残す
      （done にはしない。統合は完了ではないため）。
    戻り値は target（documents 付き）と source の要約。
    """
    auth.require_scope("tasks:write")
    if source_task_id == target_task_id:
        raise ValueError("cannot merge a task into itself")
    with db.connect() as conn:
        source = _row(
            conn, f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = %s",
            (source_task_id,),
        )
        if source is None:
            raise ValueError(f"task not found: {source_task_id}")
        target = _row(
            conn, f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = %s",
            (target_task_id,),
        )
        if target is None:
            raise ValueError(f"task not found: {target_task_id}")
        if source["status"] == "archived":
            raise ValueError("source task is already archived")
        source_no = _ledger_number(conn, source_task_id)
        target_no = _ledger_number(conn, target_task_id)

        breakdown = (
            f"\n\n【統合された内訳】#{source_no} {source['title']}"
        )
        if source.get("description"):
            breakdown += f"\n{source['description']}"
        new_target_desc = (target.get("description") or "") + breakdown
        conn.execute(
            "UPDATE tasks SET description = %s, updated_at = now()"
            " WHERE id = %s",
            (new_target_desc, target_task_id),
        )
        conn.execute(
            "INSERT INTO task_documents (task_id, document_id)"
            " SELECT %s, document_id FROM task_documents WHERE task_id = %s"
            " ON CONFLICT DO NOTHING",
            (target_task_id, source_task_id),
        )
        conn.execute(
            "DELETE FROM task_documents WHERE task_id = %s",
            (source_task_id,),
        )
        note = (
            f"\n\n【統合済み】#{target_no}「{target['title']}」へ統合されたため"
            "アーカイブ（完了ではない）。"
        )
        new_source_desc = (source.get("description") or "") + note
        merged_source = _row(
            conn,
            "UPDATE tasks SET status = 'archived', description = %s,"
            " updated_at = now()"
            f" WHERE id = %s RETURNING {_TASK_COLUMNS}",
            (new_source_desc, source_task_id),
        )
        docs = _rows(
            conn,
            f"SELECT {_DOCUMENT_COLUMNS} FROM documents d"
            " JOIN task_documents td ON td.document_id = d.id"
            " WHERE td.task_id = %s ORDER BY d.created_at ASC",
            (target_task_id,),
        )
        target = _row(
            conn, f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = %s",
            (target_task_id,),
        )
    target = db.to_jsonable(target)
    target["documents"] = db.to_jsonable(docs)
    return {"target": target, "source": db.to_jsonable(merged_source)}


def tasks_update_status(task_id: str, status: str) -> dict:
    auth.require_scope("tasks:write")
    if status not in TASK_STATUSES:
        raise ValueError(f"invalid status: {status}")
    with db.connect() as conn:
        task = _row(
            conn,
            "UPDATE tasks SET status = %s,"
            " completed_at = CASE WHEN %s = 'done' THEN now()"
            "                       WHEN status = 'done' THEN NULL"
            "                       ELSE completed_at END,"
            " updated_at = now()"
            f" WHERE id = %s RETURNING {_TASK_COLUMNS}",
            (status, status, task_id),
        )
        if task is None:
            raise ValueError(f"task not found: {task_id}")
    return db.to_jsonable(task)


# ------------------------------------------------------------ identities


def _identity_with_handles(conn, identity: dict) -> dict:
    handles = _rows(
        conn,
        "SELECT channel, handle, is_primary FROM identity_handles"
        " WHERE identity_id = %s ORDER BY channel, handle",
        (identity["id"],),
    )
    identity = dict(identity)
    identity["handles"] = handles
    return identity


def identities_list() -> list[dict]:
    auth.require_scope("identities:read")
    with db.connect() as conn:
        rows = _rows(
            conn,
            f"SELECT {_IDENTITY_COLUMNS} FROM identities ORDER BY created_at ASC",
        )
        result = [_identity_with_handles(conn, r) for r in rows]
    return db.to_jsonable(result)


def identities_resolve(handle: str, channel: str | None = None) -> dict:
    auth.require_scope("identities:read")
    clauses = ["h.handle = %s"]
    params: list = [handle]
    if channel:
        clauses.append("h.channel = %s")
        params.append(channel)
    with db.connect() as conn:
        row = _row(
            conn,
            f"SELECT i.id, i.kind, i.name, i.email, i.notes, i.status,"
            " i.created_at, i.updated_at FROM identities i"
            " JOIN identity_handles h ON h.identity_id = i.id"
            f" WHERE {' AND '.join(clauses)} LIMIT 1",
            tuple(params),
        )
        if row is None:
            raise ValueError(f"identity not found for handle: {handle}")
        result = _identity_with_handles(conn, row)
    return db.to_jsonable(result)


def identities_create(kind: str, name: str, email: str | None = None,
                      notes: str | None = None,
                      handles: list[dict] | None = None) -> dict:
    auth.require_scope("identities:write")
    if kind not in ("person", "agent", "service"):
        raise ValueError(f"invalid kind: {kind}")
    with db.connect() as conn:
        identity = _row(
            conn,
            "INSERT INTO identities (kind, name, email, notes)"
            f" VALUES (%s, %s, %s, %s) RETURNING {_IDENTITY_COLUMNS}",
            (kind, name, email, notes),
        )
        for h in handles or []:
            conn.execute(
                "INSERT INTO identity_handles"
                " (identity_id, channel, handle, is_primary)"
                " VALUES (%s, %s, %s, %s)",
                (identity["id"], h["channel"], h["handle"],
                 bool(h.get("is_primary", False))),
            )
        result = _identity_with_handles(conn, identity)
    return db.to_jsonable(result)


# ------------------------------------------------------------- documents


def documents_list(source: str | None = None, kind: str | None = None,
                   limit: int = 100, since: str | None = None,
                   order: str = "asc") -> list[dict]:
    auth.require_scope("documents:read")
    if order not in ("asc", "desc"):
        raise ValueError(f"invalid order: {order}")
    clauses, params = [], []
    if source:
        clauses.append("source = %s")
        params.append(source)
    if kind:
        clauses.append("kind = %s")
        params.append(kind)
    if since:
        clauses.append("created_at >= %s")
        params.append(since)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    limit = max(1, min(int(limit), 500))
    with db.connect() as conn:
        rows = _rows(
            conn,
            f"SELECT {_DOCUMENT_COLUMNS} FROM documents{where}"
            f" ORDER BY created_at {order.upper()} LIMIT %s",
            (*params, limit),
        )
    return db.to_jsonable(rows)


def documents_get(document_id: str) -> dict:
    auth.require_scope("documents:read")
    with db.connect() as conn:
        doc = _row(
            conn,
            f"SELECT {_DOCUMENT_COLUMNS}, body FROM documents WHERE id = %s",
            (document_id,),
        )
        if doc is None:
            raise ValueError(f"document not found: {document_id}")
    return db.to_jsonable(doc)


def documents_ingest(title: str, locator: str, kind: str | None = None,
                     source: str | None = None,
                     external_id: str | None = None,
                     task_id: str | None = None,
                     body: str | None = None) -> dict:
    """文書の登録。

    取り込む文書は原文ファイルを呼び出し側が VPS 上へ配置済みの前提で、
    locator だけを登録する。基盤の工程が生成する文書（朝の速報など）は
    body に本文を渡して DB に保存する（locator は参照名でよい）。

    source + external_id が既にあれば既存を返し、重複登録しない。
    """
    auth.require_scope("ingestion:write")
    with db.connect() as conn:
        created = True
        doc = None
        if source and external_id:
            doc = _row(
                conn,
                f"SELECT {_DOCUMENT_COLUMNS} FROM documents"
                " WHERE source = %s AND external_id = %s",
                (source, external_id),
            )
            created = doc is None
        if doc is None:
            doc = _row(
                conn,
                "INSERT INTO documents (title, kind, locator, source,"
                " external_id, body) VALUES (%s, %s, %s, %s, %s, %s)"
                f" RETURNING {_DOCUMENT_COLUMNS}",
                (title, kind, locator, source, external_id, body),
            )
        if task_id:
            conn.execute(
                "INSERT INTO task_documents (task_id, document_id)"
                " VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (task_id, doc["id"]),
            )
    result = db.to_jsonable(doc)
    result["created"] = created
    return result


# ------------------------------------------------------------- ingestion


def ingestion_record_checkpoint(source: str,
                                last_external_id: str | None = None,
                                last_seen_at: str | None = None) -> dict:
    auth.require_scope("ingestion:write")
    with db.connect() as conn:
        row = _row(
            conn,
            "INSERT INTO ingestion_checkpoints"
            " (source, last_external_id, last_seen_at)"
            " VALUES (%s, %s, %s)"
            " ON CONFLICT (source) DO UPDATE SET"
            " last_external_id = COALESCE(EXCLUDED.last_external_id,"
            " ingestion_checkpoints.last_external_id),"
            " last_seen_at = COALESCE(EXCLUDED.last_seen_at,"
            " ingestion_checkpoints.last_seen_at),"
            " updated_at = now()"
            " RETURNING source, last_external_id, last_seen_at, updated_at",
            (source, last_external_id, last_seen_at),
        )
    return db.to_jsonable(row)
