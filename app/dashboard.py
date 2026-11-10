"""人間用ダッシュボード（読み取り専用）。

v1 ではダッシュボードからの書き込みは行わない（docs/mcp-tools.md）。
表示はサービス層経由で行い、ダッシュボード内部では読み取り専用スコープの
合成エージェントとしてサービス層を呼ぶ。書き込みスコープは持たない。
公開は Tailscale Serve（テールネット内）のみ。

デザインは試作モック（docs/shared-tasks.html）の見た目に合わせる。
モック側の追加フォームや操作ボタンは v1 の対象外なので持ち込まない。

一覧は「未完了」「完了」の2タブに分ける（2026-11-10 決定）。
各タスクはどちらか一方のタブにだけ出る（重複表示はしない）。
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
DONE_STATUSES = ("done", "archived")
PRIORITY_LABELS = {"high": "高", "normal": "普通", "low": "低"}


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


def _sort_key_active(task: dict, now: datetime):
    due = _parse_dt(task.get("due_at"))
    if due is not None and due < now:
        group = 0  # 期限切れ
    elif due is not None and due < now + timedelta(hours=48):
        group = 1  # 48時間以内に期限
    elif task["status"] == "in_progress":
        group = 2
    else:
        group = 3
    due_key = due.timestamp() if due is not None else float("inf")
    return (group, due_key, task.get("created_at") or "")


def _sort_key_done(task: dict):
    completed = _parse_dt(task.get("completed_at"))
    # 完了したものは新しい順。completed_at が無いものは後ろ。
    return (
        completed is None,
        -(completed.timestamp() if completed is not None else 0),
        task.get("created_at") or "",
    )


def _due_label(task: dict, today) -> str:
    due = _parse_dt(task.get("due_at"))
    if due is None:
        return "期限なし"
    due_date = due.astimezone(JST).date()
    text = due_date.isoformat()
    if task["status"] not in ACTIVE_STATUSES:
        return f"期限 {text}"
    diff = (due_date - today).days
    if diff < 0:
        return f'<span class="due-over">期限切れ {text}（{-diff}日前）</span>'
    if diff == 0:
        return f'<span class="due-over">期限：今日 {text}</span>'
    if diff == 1:
        return f'<span class="due-soon">期限：明日 {text}</span>'
    return f"期限 {text}"


def _task_html(task: dict, number: int, owners: dict, today) -> str:
    status = task["status"]
    badge = (
        f'<span class="badge st-{status}">'
        f"{STATUS_LABELS.get(status, status)}</span>"
    )
    owner = html.escape(owners.get(task.get("owner_identity_id") or "", "未定"))
    source = html.escape(task.get("source") or "—")
    project = html.escape(task.get("project") or "—")
    priority = PRIORITY_LABELS.get(task.get("priority") or "normal", "普通")
    desc = task.get("description")
    desc_html = f'<p class="desc">{html.escape(desc)}</p>' if desc else ""
    done_cls = " done" if status == "done" else ""
    return (
        f'<div class="task{done_cls}">'
        f'<div class="top"><h3><span class="tnum">{number}</span>'
        f"{html.escape(task['title'])}</h3>{badge}</div>"
        f'<div class="meta"><span>担当：{owner}</span>'
        f"<span>{_due_label(task, today)}</span>"
        f"<span>由来：{source}</span>"
        f"<span>プロジェクト：{project}</span>"
        f"<span>優先度：{priority}</span></div>"
        f"{desc_html}</div>"
    )


_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>twins works 共有タスク管理</title>
<style>
  :root {{ --bg:#f6f5f2; --card:#fff; --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb;
    --open:#2563eb; --prog:#b45309; --done:#15803d; --arch:#6b7280; --danger:#dc2626; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:-apple-system,"Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif;
    background:var(--bg); color:var(--ink); }}
  header {{ background:#111827; color:#fff; padding:18px 24px; }}
  header h1 {{ margin:0; font-size:20px; }}
  header p {{ margin:4px 0 0; font-size:12px; color:#d1d5db; }}
  main {{ max-width:960px; margin:0 auto; padding:20px 16px 60px; }}
  .counts {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:16px; }}
  .count {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; min-width:110px; }}
  .count b {{ display:block; font-size:22px; }}
  .count span {{ font-size:12px; color:var(--muted); }}
  .count.overdue b {{ color:var(--danger); }}
  section.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:16px; }}
  h2 {{ font-size:15px; margin:0 0 12px; }}
  .tabs {{ display:flex; gap:6px; margin-bottom:14px; }}
  .tab {{ padding:8px 18px; border:1px solid var(--line); border-radius:999px;
          background:#f3f4f6; color:var(--ink); text-decoration:none; font-size:14px; }}
  .tab.active {{ background:#111827; color:#fff; border-color:#111827; font-weight:700; }}
  select {{ padding:8px 10px; border:1px solid #d1d5db; border-radius:8px; font-size:14px; background:#fff; }}
  button {{ cursor:pointer; border:0; border-radius:8px; padding:9px 16px; font-size:14px; }}
  .btn-primary {{ background:#111827; color:#fff; }}
  .filters {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
  .filters select {{ width:auto; }}
  .task {{ border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin-bottom:10px; background:#fff; }}
  .task.done {{ opacity:.65; }}
  .task .top {{ display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }}
  .task h3 {{ margin:0; font-size:15px; }}
  .tnum {{ display:inline-block; min-width:26px; text-align:center; background:#eef2ff; color:#3730a3; border-radius:6px; padding:1px 6px; margin-right:8px; font-size:13px; font-weight:700; }}
  .task .meta {{ font-size:12px; color:var(--muted); margin-top:4px; display:flex; gap:12px; flex-wrap:wrap; }}
  .task .desc {{ font-size:13px; margin:8px 0 0; white-space:pre-wrap; }}
  .badge {{ display:inline-block; font-size:11px; font-weight:700; padding:2px 8px; border-radius:999px; color:#fff; white-space:nowrap; }}
  .st-open {{ background:var(--open); }} .st-in_progress {{ background:var(--prog); }}
  .st-done {{ background:var(--done); }} .st-archived {{ background:var(--arch); }}
  .due-over {{ color:var(--danger); font-weight:700; }}
  .due-soon {{ color:var(--prog); font-weight:700; }}
  .note {{ font-size:12px; color:var(--muted); line-height:1.7; }}
  .empty {{ color:var(--muted); font-size:13px; padding:12px 0; }}
</style>
</head>
<body>
<header>
  <h1>twins works 共有タスク管理 <span style="font-weight:400;font-size:13px;">— manatagoとの共同事業</span></h1>
  <p>共通基盤で管理する共有タスクだけを表示します。この画面は読み取り専用です。タスクの登録・変更はエージェント（MCP）経由で行います。</p>
</header>
<main>
  <div class="counts">{counts}</div>
  <section class="card">
    <h2>タスク一覧</h2>
    <div class="tabs">{tabs}</div>
    <form class="filters" method="get" action="/">
      <input type="hidden" name="tab" value="{tab}">
      <select name="status">
        <option value="">状態：すべて</option>{status_options}
      </select>
      <select name="owner">
        <option value="">担当：すべて</option>{owner_options}
      </select>
      <select name="project">
        <option value="">プロジェクト：すべて</option>{project_options}
      </select>
      <button class="btn-primary" type="submit" style="padding:7px 14px;font-size:13px;">絞り込む</button>
    </form>
    <div style="margin-top:14px;">{tasks_html}</div>
  </section>
  <p class="note">未完了タブの並び順：期限切れ → 48時間以内に期限 → 進行中 → その他（期限順）。完了タブは完了した順（新しい順）です。台帳番号は登録順で固定され、タブや絞り込みでは変わりません。</p>
</main>
</body>
</html>"""


def _options(values, current, labels=None):
    out = []
    for v in values:
        sel = " selected" if v == current else ""
        label = (labels or {}).get(v, v)
        out.append(
            f'<option value="{html.escape(v, quote=True)}"{sel}>'
            f"{html.escape(label)}</option>"
        )
    return "".join(out)


@router.get("/", response_class=HTMLResponse)
def dashboard(tab: str | None = None, status: str | None = None,
              owner: str | None = None, project: str | None = None):
    tab = "done" if tab == "done" else "active"
    now = datetime.now(timezone.utc)
    today = now.astimezone(JST).date()
    with _readonly():
        all_tasks = services.tasks_list(limit=500)
        identities = services.identities_list()
    owners = {i["id"]: i["name"] for i in identities}
    # 台帳番号は登録順（created_at 順）で固定する。タブ・絞り込みでは変わらない。
    numbers = {t["id"]: n for n, t in enumerate(all_tasks, start=1)}

    def _is_overdue(t):
        due = _parse_dt(t.get("due_at"))
        return (
            t["status"] in ACTIVE_STATUSES
            and due is not None
            and due.astimezone(JST).date() < today
        )

    def _is_due_today(t):
        due = _parse_dt(t.get("due_at"))
        return (
            t["status"] in ACTIVE_STATUSES
            and due is not None
            and due.astimezone(JST).date() == today
        )

    counts_html = "".join(
        f'<div class="count{" overdue" if label == "期限切れ" else ""}">'
        f"<b>{value}</b><span>{label}</span></div>"
        for label, value in [
            ("未着手", sum(1 for t in all_tasks if t["status"] == "open")),
            ("進行中", sum(1 for t in all_tasks if t["status"] == "in_progress")),
            ("完了", sum(1 for t in all_tasks if t["status"] == "done")),
            ("期限切れ", sum(1 for t in all_tasks if _is_overdue(t))),
            ("今日が期限", sum(1 for t in all_tasks if _is_due_today(t))),
        ]
    )

    active_count = sum(1 for t in all_tasks if t["status"] in ACTIVE_STATUSES)
    done_count = sum(1 for t in all_tasks if t["status"] in DONE_STATUSES)
    tabs_html = (
        f'<a class="tab{" active" if tab == "active" else ""}" href="/?tab=active">未完了（{active_count}）</a>'
        f'<a class="tab{" active" if tab == "done" else ""}" href="/?tab=done">完了（{done_count}）</a>'
    )

    tab_statuses = ACTIVE_STATUSES if tab == "active" else DONE_STATUSES
    tasks = [t for t in all_tasks if t["status"] in tab_statuses]
    if status in tab_statuses:
        tasks = [t for t in tasks if t["status"] == status]
    if owner:
        tasks = [t for t in tasks if owners.get(t.get("owner_identity_id") or "") == owner]
    if project:
        tasks = [t for t in tasks if t.get("project") == project]
    if tab == "active":
        tasks.sort(key=lambda t: _sort_key_active(t, now))
    else:
        tasks.sort(key=_sort_key_done)

    if tasks:
        tasks_html = "\n".join(
            _task_html(t, numbers[t["id"]], owners, today) for t in tasks
        )
    else:
        tasks_html = '<p class="empty">条件に合うタスクがありません。</p>'

    return _PAGE.format(
        counts=counts_html,
        tabs=tabs_html,
        tab=tab,
        status_options=_options(list(tab_statuses), status or "", labels=STATUS_LABELS),
        owner_options=_options(sorted(set(owners.values())), owner or ""),
        project_options=_options(
            sorted({t["project"] for t in all_tasks if t.get("project")}),
            project or "",
        ),
        tasks_html=tasks_html,
    )
