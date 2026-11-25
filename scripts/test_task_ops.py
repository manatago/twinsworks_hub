"""tasks.update / tasks.merge とダッシュボード書き込みの確認テスト。

検証用スタックに対して実行する。実データのある環境では実行しないこと。

環境変数:
  MCP_TEST_URL  (default http://127.0.0.1:8001/mcp)
  MCP_TEST_KEY_FULL  (キー文字列そのもの、または @ファイルパス)
  DASH_BASE  (default http://127.0.0.1:8001)
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("MCP_TEST_URL", "http://127.0.0.1:8001/mcp")
DASH = os.environ.get("DASH_BASE", "http://127.0.0.1:8001")


def _key(env):
    v = os.environ[env]
    return open(v[1:]).read().strip() if v.startswith("@") else v


FULL = _key("MCP_TEST_KEY_FULL")


def call(method, params=None, key=FULL):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(
        BASE, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def unwrap(res):
    result = res.get("result", {})
    if result.get("isError"):
        return {"__error__": str(result.get("content"))[:400]}
    sc = result.get("structuredContent")
    if sc is not None:
        if isinstance(sc, dict) and list(sc.keys()) == ["result"]:
            return sc["result"]
        return sc
    content = result.get("content") or []
    if content and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except Exception:
            return content
    return content


def tool(name, args):
    return unwrap(call("tools/call", {"name": name, "arguments": args}))


passed = failed = 0


def check(label, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS {label}")
    else:
        failed += 1
        print(f"FAIL {label} {extra}")


# --- tools/list に新ツールがあるか
res = call("tools/list")
names = [t["name"] for t in res["result"]["tools"]]
check("tools.list includes tasks.update", "tasks.update" in names, str(names))
check("tools.list includes tasks.merge", "tasks.merge" in names)

# --- 準備: タスク2件と、source に紐付く文書1件
a = tool("tasks.create", {"title": "テストA（統合先）", "description": "Aの説明",
                          "project": "テスト"})
b = tool("tasks.create", {"title": "テストB（統合元）", "description": "Bの説明",
                          "project": "テスト"})
check("create A/B", "id" in a and "id" in b, f"{a} {b}")
doc = tool("documents.ingest", {
    "title": "テスト文書", "locator": "test/doc1", "kind": "note",
    "source": "test_ops", "external_id": "doc-1", "task_id": b["id"]})
check("ingest doc on B", doc.get("created") is True, str(doc))

# --- tasks.update
upd = tool("tasks.update", {"task_id": a["id"], "title": "テストA改",
                            "priority": "high",
                            "due_at": "2026-12-31T00:00:00+09:00"})
check("update title/priority/due",
      upd.get("title") == "テストA改" and upd.get("priority") == "high"
      and (upd.get("due_at") or "").startswith("2026-12-3"), str(upd))
upd2 = tool("tasks.update", {"task_id": a["id"], "due_at": ""})
check("update clears due_at", upd2.get("due_at") is None, str(upd2))
err = tool("tasks.update", {"task_id": a["id"]})
check("update with nothing errors", "__error__" in err, str(err))
err = tool("tasks.update", {"task_id": a["id"], "title": "  "})
check("update empty title errors", "__error__" in err, str(err))

# --- tasks.merge
err = tool("tasks.merge", {"source_task_id": a["id"],
                           "target_task_id": a["id"]})
check("merge into self errors", "__error__" in err, str(err))
merged = tool("tasks.merge", {"source_task_id": b["id"],
                              "target_task_id": a["id"]})
tgt = merged.get("target", {})
src = merged.get("source", {})
check("merge source archived", src.get("status") == "archived", str(src))
check("merge note on source",
      "統合済み" in (src.get("description") or ""), str(src.get("description")))
check("merge breakdown on target",
      "統合された内訳" in (tgt.get("description") or "")
      and "Bの説明" in (tgt.get("description") or ""),
      str(tgt.get("description")))
doc_ids = [d["id"] for d in tgt.get("documents", [])]
check("merge moves documents", doc.get("id") in doc_ids, str(doc_ids))
b_after = tool("tasks.get", {"task_id": b["id"]})
check("source documents detached", b_after.get("documents") == [],
      str(b_after.get("documents")))
err = tool("tasks.merge", {"source_task_id": b["id"],
                           "target_task_id": a["id"]})
check("merge archived source errors", "__error__" in err, str(err))

# --- ダッシュボード書き込み
def dash_post(path, data):
    req = urllib.request.Request(
        DASH + path,
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


opener = urllib.request.build_opener(NoRedirect)


def dash_post_status(path, data):
    req = urllib.request.Request(
        DASH + path,
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with opener.open(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


with urllib.request.urlopen(DASH + "/", timeout=20) as r:
    page = r.read().decode()
check("dashboard shows ops forms", "/status" in page and "編集" in page)
st = dash_post_status(f"/tasks/{a['id']}/status",
                      {"status": "in_progress", "tab": "active"})
check("dashboard status POST 303", st == 303, str(st))
a_now = tool("tasks.get", {"task_id": a["id"]})
check("dashboard status applied", a_now.get("status") == "in_progress",
      str(a_now.get("status")))
st = dash_post_status(f"/tasks/{a['id']}/edit",
                      {"title": "テストA改2", "description": "画面から編集",
                       "owner_identity_id": "", "due_at": "",
                       "project": "テスト2", "priority": "low",
                       "tab": "active"})
check("dashboard edit POST 303", st == 303, str(st))
a_now = tool("tasks.get", {"task_id": a["id"]})
check("dashboard edit applied",
      a_now.get("title") == "テストA改2"
      and a_now.get("project") == "テスト2"
      and a_now.get("priority") == "low", str(a_now))
c = tool("tasks.create", {"title": "テストC（画面統合元）",
                          "project": "テスト2"})
st = dash_post_status(f"/tasks/{c['id']}/merge",
                      {"target_id": a["id"], "tab": "active"})
check("dashboard merge POST 303", st == 303, str(st))
c_now = tool("tasks.get", {"task_id": c["id"]})
check("dashboard merge applied", c_now.get("status") == "archived",
      str(c_now.get("status")))

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
