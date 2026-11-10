"""人間用ダッシュボード（読み取り専用）。

v1 ではダッシュボードからの書き込みは行わない（docs/mcp-tools.md）。
表示はサービス層経由で行い、ダッシュボード内部では読み取り専用スコープの
合成エージェントとしてサービス層を呼ぶ。書き込みスコープは持たない。
公開は Tailscale Serve（テールネット内）のみ。
"""

import html
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from . import auth, services

JST = timezone(timedelta(hours=9))
READ_SCOPES = ["tasks:read", "identities:read", "documents:read"]

router = APIRouter()

STATUS_LABELS = {
    "open": "未着手",
    "in_progress": "進行中",
    "done": "完了",
    "archived": "アーカイブ",
}
ACTIVE_STATUSES = ("open", "in_progress")


@contextmanager
def _readonly():
    token = auth.set_current_agent({"name": "dashboard", "scopes": READ_SCOPES})
    try:
        yield
    finally:
        auth.reset_current_agent(token)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _sort_key(task: dict, now: datetime):
    status = task["status"]
    due = _parse_dt(task.get("due_at"))
    if status in ACTIVE_STATUSES:
        if due is not None and due < now:
            group = 0  # 期限切れ
        elif due is not None and due < now + timedelta(hours=48):
            group = 1  # 48時間以内に期限
        elif status == "in_progress":
            group = 2
        else:
            group = 3
    elif status == "done":
        group = 4
    else:
        group = 5
    due_key = due.timestamp() if due is not None else float("inf")
    return (group, due_key, task.get("created_at") or "")


def _fmt_due(task: dict, now: datetime) -> str:
    due = _parse_dt(task.get("due_at"))
    if due is None:
        return "期限なし"
    text = due.astimezone(JST).strftime("%Y-%m-%d %H:%M")
    if task["status"] in ACTIVE_STATUSES and due < now:
        return f'<span class="overdue">{text}（期限切れ）</span>'
    return text


def _task_card(task: dict, owners: dict, now: datetime) -> str:
    title = html.escape(task["title"])
    if task.get("priority") == "high":
        title = f'<span class="badge-high">高</span> {title}'
    status = STATUS_LABELS.get(task["status"], task["status"])
    owner = owners.get(task.get("owner_identity_id") or "", "—")
    project = html.escape(task.get("project") or "—")
    desc = html.escape(task.get("description") or "")
    desc_html = f'<div class="desc">{desc}</div>' if desc else ""
    created = _parse_dt(task.get("created_at"))
    created_text = created.astimezone(JST).strftime("%Y-%m-%d") if created else ""
    muted = ' class="card muted"' if task["status"] in ("done", "archived") else ' class="card"'
    return (
        f"<div{muted}>"
        f'<div class="title">{title}</div>'
        f'<div class="meta"><span class="chip">{html.escape(status)}</span>'
        f" 担当: {html.escape(owner)} / 事業: {project}"
        f" / 期限: {_fmt_due(task, now)} / 登録: {created_text}</div>"
        f"{desc_html}</div>"
    )


_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>twins works 共有タスク</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 880px;
          padding: 0 1rem; color: #1a1a1a; background: #fafafa; }}
  h1 {{ font-size: 1.3rem; }}
  .sub {{ color: #666; font-size: .85rem; margin-bottom: 1rem; }}
  .filters {{ margin: 1rem 0; font-size: .9rem; }}
  .filters a {{ margin-right: .6rem; }}
  .card {{ background: #fff; border: 1px solid #ddd; border-radius: 8px;
           padding: .8rem 1rem; margin: .6rem 0; }}
  .card.muted {{ opacity: .55; }}
  .title {{ font-weight: 600; }}
  .meta {{ color: #555; font-size: .85rem; margin-top: .3rem; }}
  .desc {{ white-space: pre-wrap; margin-top: .5rem; font-size: .92rem; }}
  .chip {{ background: #eef; border-radius: 4px; padding: .05rem .45rem; }}
  .badge-high {{ background: #c0392b; color: #fff; border-radius: 4px;
                 padding: .05rem .45rem; font-size: .8rem; }}
  .overdue {{ color: #c0392b; font-weight: 600; }}
  .empty {{ color: #666; margin: 2rem 0; }}
</style>
</head>
<body>
<h1>twins works 共有タスク</h1>
<div class="sub">読み取り専用です。タスクの登録・変更はエージェント（MCP）経由で行います。全 {total} 件</div>
<div class="filters">絞り込み: {filter_links}</div>
{cards}
</body>
</html>"""


@router.get("/", response_class=HTMLResponse)
def dashboard(status: str | None = None, project: str | None = None):
    now = datetime.now(timezone.utc)
    with _readonly():
        tasks = services.tasks_list(limit=500)
        identities = services.identities_list()
    owners = {i["id"]: i["name"] for i in identities}
    projects = sorted({t["project"] for t in tasks if t.get("project")})

    if status:
        tasks = [t for t in tasks if t["status"] == status]
    if project:
        tasks = [t for t in tasks if t.get("project") == project]
    tasks.sort(key=lambda t: _sort_key(t, now))

    links = ['<a href="/">すべて</a>']
    for s, label in STATUS_LABELS.items():
        links.append(f'<a href="/?status={s}">{label}</a>')
    for p in projects:
        links.append(f'<a href="/?project={html.escape(p, quote=True)}">{html.escape(p)}</a>')

    if tasks:
        cards = "\n".join(_task_card(t, owners, now) for t in tasks)
    else:
        cards = '<div class="empty">表示するタスクはありません。</div>'

    return _PAGE.format(total=len(tasks), filter_links=" ".join(links), cards=cards)
