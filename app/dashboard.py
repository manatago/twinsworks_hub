"""人間用ダッシュボード。

表示・操作ともサービス層経由で行い、ダッシュボード内部では合成
エージェントとしてサービス層を呼ぶ。読み取りには読み取りスコープのみ、
書き込み（状態変更・編集・統合）のときだけ tasks:write を加えた
スコープで呼ぶ。公開は Tailscale Serve（テールネット内）のみで、
画面の操作はテールネット内の人間が行う前提。

デザインは試作モック（docs/shared-tasks.html）の見た目に合わせる。

一覧は「未完了」「完了」の2タブに分ける（2026-11-10 決定）。
各タスクはどちらか一方のタブにだけ出る（重複表示はしない）。
"""

import html
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from . import auth, services

JST = timezone(timedelta(hours=9))
READ_SCOPES = ["tasks:read", "identities:read", "documents:read"]
WRITE_SCOPES = READ_SCOPES + ["tasks:write"]

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


@contextmanager
def _writeable():
    token = auth.set_current_agent(
        {"name": "dashboard", "scopes": WRITE_SCOPES}
    )
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


def _ops_html(task: dict, number: int, identities: list, all_tasks: list,
              numbers: dict, tab: str) -> str:
    """タスクカード下部の操作フォーム（状態変更・編集・統合）。"""
    tid = task["id"]
    status_opts = "".join(
        f'<option value="{s}"{" selected" if s == task["status"] else ""}>'
        f"{STATUS_LABELS[s]}</option>"
        for s in STATUS_LABELS
    )
    owner_opts = '<option value="">未定</option>' + "".join(
        f'<option value="{html.escape(i["id"], quote=True)}"'
        f'{" selected" if i["id"] == task.get("owner_identity_id") else ""}>'
        f"{html.escape(i['name'])}</option>"
        for i in identities
    )
    due = _parse_dt(task.get("due_at"))
    due_val = due.astimezone(JST).date().isoformat() if due else ""
    prio_opts = "".join(
        f'<option value="{p}"'
        f'{" selected" if p == (task.get("priority") or "normal") else ""}>'
        f"{PRIORITY_LABELS[p]}</option>"
        for p in PRIORITY_LABELS
    )
    merge_targets = [
        t for t in all_tasks
        if t["id"] != tid and t["status"] != "archived"
    ]
    merge_html = ""
    if merge_targets and task["status"] != "archived":
        target_opts = "".join(
            f'<option value="{html.escape(t["id"], quote=True)}">'
            f'#{numbers[t["id"]]} {html.escape(t["title"])}</option>'
            for t in merge_targets
        )
        merge_html = (
            f'<form class="op" method="post" action="/tasks/{tid}/merge"'
            ' onsubmit="return confirm('
            "'このタスクを統合先へ統合し、アーカイブします。よろしいですか？'"
            ')">'
            f'<input type="hidden" name="tab" value="{tab}">'
            f'<label>統合先 <select name="target_id">{target_opts}</select>'
            "</label>"
            '<button type="submit">統合する</button></form>'
        )
    return (
        '<div class="ops">'
        f'<form class="op" method="post" action="/tasks/{tid}/status">'
        f'<input type="hidden" name="tab" value="{tab}">'
        f'<label>状態 <select name="status">{status_opts}</select></label>'
        '<button type="submit">変更</button></form>'
        "<details><summary>編集</summary>"
        f'<form class="edit" method="post" action="/tasks/{tid}/edit">'
        f'<input type="hidden" name="tab" value="{tab}">'
        f'<label>タイトル <input type="text" name="title" required'
        f' value="{html.escape(task["title"], quote=True)}"></label>'
        f'<label>説明 <textarea name="description" rows="5">'
        f'{html.escape(task.get("description") or "")}</textarea></label>'
        f'<label>担当 <select name="owner_identity_id">{owner_opts}</select>'
        "</label>"
        f'<label>期限 <input type="date" name="due_at" value="{due_val}">'
        "</label>"
        f'<label>プロジェクト <input type="text" name="project"'
        f' value="{html.escape(task.get("project") or "", quote=True)}">'
        "</label>"
        f'<label>優先度 <select name="priority">{prio_opts}</select></label>'
        '<button type="submit">保存</button></form></details>'
        f"{merge_html}</div>"
    )


def _task_html(task: dict, number: int, owners: dict, today,
               identities: list, all_tasks: list, numbers: dict,
               tab: str) -> str:
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
        f"{desc_html}"
        f"{_ops_html(task, number, identities, all_tasks, numbers, tab)}"
        "</div>"
    )


RESEARCH_ITEMS = [
    # (マウント名, 入口ファイル, サイドバーの表示名, Pull 状態を見るリポジトリ名)
    ("ResearchJanken", "latest.html", "ResearchJanken（Ci-en）", "ResearchJanken"),
    ("ResearchJanken-dlsite", "latest.html", "ResearchJanken（DLsite）", "ResearchJanken"),
    ("ResearchLiveChat", "index.html", "ResearchLiveChat", "ResearchLiveChat"),
]


def _research_sidebar() -> str:
    """左サイドバー。共有タスクへの導線と、Research サマリーへのリンク。

    最終更新は xvps 上の定期 Pull が書く pull_status.json から読む
    （RESEARCH_BASE_DIR、既定 /research。アプリへは読み取り専用マウント）。
    読めない場合は日時なしでリンクだけ出す。
    """
    base = os.environ.get("RESEARCH_BASE_DIR", "/research")
    status = {}
    try:
        with open(os.path.join(base, "pull_status.json"), encoding="utf-8") as f:
            status = json.load(f)
    except Exception:
        status = {}
    items = []
    for mount, entry, label, status_repo in RESEARCH_ITEMS:
        upd = ""
        head = ((status.get(status_repo) or {}).get("head") or "").strip()
        if head:
            parts = head.split()
            when = " ".join(parts[1:3]) if len(parts) >= 3 else head
            upd = f'<span class="upd">最終更新：{html.escape(when)}</span>'
        items.append(
            f'<a href="/research/view/{mount}/{entry}">{html.escape(label)}</a>{upd}'
        )
    return (
        '<div class="brand">twins works</div>'
        '<a href="/" class="active">共有タスク</a>'
        '<a href="/digest">朝の速報</a>'
        '<div class="sec">Research サマリー</div>'
        + "".join(items)
        + '<p class="foot">サマリーは xvps 上の clone から配信しています（毎時自動 Pull）。</p>'
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
  @media (max-width:760px) {{ .layout {{ flex-direction:column; }} .side {{ width:auto; flex:none; height:auto; position:static; }} }}
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
  .ops {{ margin-top:10px; border-top:1px dashed var(--line); padding-top:10px;
    display:flex; flex-direction:column; gap:8px; }}
  .ops .op {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; font-size:13px; }}
  .ops label {{ display:flex; gap:6px; align-items:center; }}
  .ops select, .ops input[type="text"], .ops input[type="date"] {{ padding:6px 8px;
    border:1px solid #d1d5db; border-radius:8px; font-size:13px; background:#fff; }}
  .ops button {{ padding:6px 12px; font-size:13px; background:#f3f4f6;
    border:1px solid var(--line); color:var(--ink); }}
  .ops details summary {{ cursor:pointer; font-size:13px; color:var(--muted); }}
  .ops form.edit {{ display:flex; flex-direction:column; gap:8px; margin-top:8px; }}
  .ops form.edit label {{ align-items:flex-start; flex-direction:column; gap:4px; font-size:13px; }}
  .ops form.edit input[type="text"], .ops form.edit textarea {{ width:100%; }}
  .ops textarea {{ padding:8px; border:1px solid #d1d5db; border-radius:8px;
    font-size:13px; font-family:inherit; }}
</style>
</head>
<body>
<div class="layout">
<aside class="side">{sidebar}</aside>
<div class="content">
<header>
  <h1>twins works 共有タスク管理 <span style="font-weight:400;font-size:13px;">— manatagoとの共同事業</span></h1>
  <p>共通基盤で管理する共有タスクだけを表示します。各タスクの状態変更・編集・統合はこの画面からも、エージェント（MCP）経由でも行えます。</p>
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
</div>
</div>
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
            _task_html(
                t, numbers[t["id"]], owners, today,
                identities, all_tasks, numbers, tab,
            )
            for t in tasks
        )
    else:
        tasks_html = '<p class="empty">条件に合うタスクがありません。</p>'

    return _PAGE.format(
        sidebar=_research_sidebar(),
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


# ------------------------------------------------------------- 朝の速報


def _digest_body_html(body: str) -> str:
    """速報本文（プレーンテキスト）を表示用 HTML にする。URL はリンク化。"""
    import re

    text = html.escape(body or "")
    text = re.sub(
        r"(https?://[^\s<]+)",
        r'<a href="\1" target="_blank" rel="noopener">\1</a>',
        text,
    )
    return text.replace("\n", "<br>\n")


_DIGEST_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>朝の速報 | twins works</title>
<style>
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:-apple-system,"Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif;
    background:#f6f5f2; color:#1f2937; }}
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
  @media (max-width:760px) {{ .layout {{ flex-direction:column; }} .side {{ width:auto; flex:none; height:auto; position:static; }} }}
  header {{ background:#111827; color:#fff; padding:18px 24px; }}
  header h1 {{ margin:0; font-size:20px; }}
  main {{ max-width:860px; margin:0 auto; padding:20px 16px 60px; }}
  .card {{ background:#fff; border:1px solid #e5e7eb; border-radius:12px; padding:18px; }}
  .dates {{ display:flex; gap:6px; flex-wrap:wrap; margin-bottom:14px; }}
  .dates a {{ padding:6px 12px; border:1px solid #e5e7eb; border-radius:999px;
    background:#f3f4f6; color:#1f2937; text-decoration:none; font-size:13px; }}
  .dates a.active {{ background:#111827; color:#fff; border-color:#111827; font-weight:700; }}
  .digest-body {{ font-size:14px; line-height:1.8; }}
  .digest-body a {{ color:#2563eb; word-break:break-all; }}
  .empty {{ color:#6b7280; font-size:13px; }}
</style>
</head>
<body>
<div class="layout">
<aside class="side">{sidebar}</aside>
<div class="content">
<header><h1>朝の速報</h1></header>
<main>
  <div class="dates">{dates}</div>
  <section class="card">{body}</section>
</main>
</div>
</div>
</body>
</html>"""


@router.get("/digest", response_class=HTMLResponse)
def digest_page(date: str | None = None):
    with _readonly():
        digests = services.documents_list(
            source="morning_digest", kind="digest", limit=100, order="desc",
        )
        selected = None
        if digests:
            chosen = next(
                (d for d in digests if d.get("external_id") == date),
                digests[0],
            )
            selected = services.documents_get(chosen["id"])
    sidebar = _research_sidebar()
    sidebar = sidebar.replace('<a href="/" class="active">', '<a href="/">', 1)
    sidebar = sidebar.replace(
        '<a href="/digest">', '<a class="active" href="/digest">', 1,
    )
    if not digests:
        return _DIGEST_PAGE.format(
            sidebar=sidebar, dates="",
            body='<p class="empty">保存された速報がまだありません。</p>',
        )
    dates_html = "".join(
        f'<a href="/digest?date={html.escape(d.get("external_id") or "", quote=True)}"'
        f'{" class=\"active\"" if selected and d["id"] == selected["id"] else ""}>'
        f'{html.escape(d.get("external_id") or "")}</a>'
        for d in digests
    )
    body_html = (
        f'<div class="digest-body">'
        f'{_digest_body_html(selected.get("body") or "")}</div>'
        if selected and selected.get("body")
        else '<p class="empty">本文が保存されていません。</p>'
    )
    return _DIGEST_PAGE.format(
        sidebar=sidebar, dates=dates_html, body=body_html,
    )


# ------------------------------------------------------------- 書き込み操作


def _back(tab: str | None) -> RedirectResponse:
    target = "/?tab=done" if tab == "done" else "/?tab=active"
    return RedirectResponse(target, status_code=303)


def _error(message: str) -> HTMLResponse:
    return HTMLResponse(
        "<!DOCTYPE html><html lang=\"ja\"><head><meta charset=\"utf-8\">"
        f"<title>エラー</title></head><body><p>{html.escape(message)}</p>"
        '<p><a href="/">タスク一覧へ戻る</a></p></body></html>',
        status_code=400,
    )


@router.post("/tasks/{task_id}/status")
def task_status(task_id: str, status: str = Form(...),
                tab: str = Form("active")):
    try:
        with _writeable():
            services.tasks_update_status(task_id, status)
    except ValueError as e:
        return _error(str(e))
    return _back(tab)


@router.post("/tasks/{task_id}/edit")
def task_edit(task_id: str, title: str = Form(...),
              description: str = Form(""),
              owner_identity_id: str = Form(""),
              due_at: str = Form(""),
              project: str = Form(""),
              priority: str = Form("normal"),
              tab: str = Form("active")):
    due = f"{due_at}T00:00:00+09:00" if due_at else ""
    try:
        with _writeable():
            services.tasks_update(
                task_id, title=title, description=description,
                owner_identity_id=owner_identity_id, due_at=due,
                project=project, priority=priority,
            )
    except ValueError as e:
        return _error(str(e))
    return _back(tab)


@router.post("/tasks/{task_id}/merge")
def task_merge(task_id: str, target_id: str = Form(...),
               tab: str = Form("active")):
    try:
        with _writeable():
            services.tasks_merge(task_id, target_id)
    except ValueError as e:
        return _error(str(e))
    return _back(tab)
