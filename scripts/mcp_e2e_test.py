"""MCP サーバーの通し確認テスト（検証用スタックに対して実行する）。

12ツールと認証・スコープ検査を一通り確認する。実データのある環境では実行しないこと。
"""
import json, urllib.request, urllib.error, sys

# 接続先とキーは環境変数で指定する:
#   MCP_TEST_URL   (default http://127.0.0.1:8001/mcp)
#   MCP_TEST_KEY_FULL / MCP_TEST_KEY_RO  (キー文字列そのもの、または @ファイルパス)
import os
BASE = os.environ.get("MCP_TEST_URL", "http://127.0.0.1:8001/mcp")
def _key(env):
    v = os.environ[env]
    return open(v[1:]).read().strip() if v.startswith("@") else v
FULL = _key("MCP_TEST_KEY_FULL")
RO = _key("MCP_TEST_KEY_RO")

def call(method, params=None, key=FULL):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        data = e.read().decode()
        try: return e.code, json.loads(data)
        except Exception: return e.code, {"raw": data[:300]}

def unwrap(res):
    result = res.get("result", {})
    if result.get("isError"):
        return {"__error__": str(result.get("content"))[:300]}
    sc = result.get("structuredContent")
    if sc is not None:
        if isinstance(sc, dict) and list(sc.keys()) == ["result"]:
            return sc["result"]
        return sc
    content = result.get("content") or []
    if content and content[0].get("type") == "text":
        try: return json.loads(content[0]["text"])
        except Exception: return content
    return content

def tool(name, args, key=FULL):
    st, res = call("tools/call", {"name": name, "arguments": args}, key=key)
    assert st == 200, (name, st, res)
    return unwrap(res)

passed = failed = 0
def check(label, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print(f"PASS {label}")
    else: failed += 1; print(f"FAIL {label} {extra}")

st, _ = call("tools/list", key=None)
check("no-key 401", st == 401, f"got {st}")
st, _ = call("tools/list", key="twk_deadbeef")
check("bad-key 401", st == 401, f"got {st}")
st, res = call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "e2e", "version": "0"}})
check("initialize", st == 200 and "result" in res, str(res)[:200])
st, res = call("tools/list")
names = sorted(t["name"] for t in res["result"]["tools"])
check("tools/list 12", len(names) == 12, str(names))

ident = tool("identities.create", {"kind": "person", "name": "テスト 太郎", "email": "taro@example.jp", "handles": [{"channel": "slack", "handle": "U_TEST_1", "is_primary": True}, {"channel": "email", "handle": "taro@example.jp"}]})
check("identities.create", isinstance(ident, dict) and ident.get("name") == "テスト 太郎" and len(ident.get("handles", [])) == 2, str(ident)[:200])
iid = ident["id"]
r = tool("identities.resolve", {"handle": "U_TEST_1", "channel": "slack"})
check("identities.resolve", isinstance(r, dict) and r.get("id") == iid, str(r)[:200])
ll = tool("identities.list", {})
check("identities.list", isinstance(ll, list) and any(x.get("id") == iid for x in ll), str(ll)[:120])

t = tool("tasks.create", {"title": "E2Eテストタスク", "description": "動作確認用", "owner_identity_id": iid, "project": "test-project", "source": "会議", "priority": "high"})
check("tasks.create", isinstance(t, dict) and t.get("title") == "E2Eテストタスク" and t.get("status") == "open", str(t)[:200])
tid = t["id"]
tll = tool("tasks.list", {"project": "test-project"})
check("tasks.list filter", isinstance(tll, list) and len(tll) == 1 and tll[0]["id"] == tid, str(tll)[:150])
tl2 = tool("tasks.list", {"project": "no-such"})
check("tasks.list empty filter", isinstance(tl2, list) and len(tl2) == 0, str(tl2)[:100])

dd = tool("documents.ingest", {"title": "議事録テスト", "locator": "/data/docs/test-001.md", "kind": "議事録", "source": "meet", "external_id": "mtg-001", "task_id": tid})
check("documents.ingest created", isinstance(dd, dict) and dd.get("created") is True, str(dd)[:200])
did = dd["id"]
dd2 = tool("documents.ingest", {"title": "議事録テスト（重複）", "locator": "/data/docs/test-001.md", "source": "meet", "external_id": "mtg-001"})
check("documents.ingest dedupe", dd2.get("created") is False and dd2.get("id") == did, str(dd2)[:200])
dll = tool("documents.list", {"source": "meet"})
check("documents.list", isinstance(dll, list) and len(dll) == 1, str(dll)[:150])
dg = tool("documents.get", {"document_id": did})
check("documents.get", isinstance(dg, dict) and dg.get("locator") == "/data/docs/test-001.md", str(dg)[:150])
tgg = tool("tasks.get", {"task_id": tid})
check("tasks.get documents link", isinstance(tgg, dict) and len(tgg.get("documents", [])) == 1 and tgg["documents"][0]["id"] == did, str(tgg)[:250])

c1 = tool("ingestion.record_checkpoint", {"source": "meet", "last_external_id": "mtg-001"})
check("checkpoint 1", isinstance(c1, dict) and c1.get("last_external_id") == "mtg-001", str(c1)[:150])
c2 = tool("ingestion.record_checkpoint", {"source": "meet", "last_external_id": "mtg-002", "last_seen_at": "2026-11-10T05:00:00Z"})
check("checkpoint 2 upsert", isinstance(c2, dict) and c2.get("last_external_id") == "mtg-002", str(c2)[:150])

uu = tool("tasks.update_status", {"task_id": tid, "status": "done"})
check("update_status done", isinstance(uu, dict) and uu.get("status") == "done" and uu.get("completed_at"), str(uu)[:200])
e = tool("tasks.update_status", {"task_id": tid, "status": "bogus"})
check("invalid status error", isinstance(e, dict) and "__error__" in e, str(e)[:150])

pp = tool("tasks.promote", {"promoted_from": "personal-core:task-42", "title": "昇格テスト", "project": "test-project"})
check("tasks.promote", isinstance(pp, dict) and pp.get("promoted_from") == "personal-core:task-42" and pp.get("promoted_at"), str(pp)[:200])
p2 = tool("tasks.promote", {"promoted_from": "personal-core:task-42", "title": "二重昇格"})
check("promote duplicate error", isinstance(p2, dict) and "__error__" in p2, str(p2)[:150])

rtl = tool("tasks.list", {}, key=RO)
check("ro tasks.list ok", isinstance(rtl, list), str(rtl)[:120])
rtc = tool("tasks.create", {"title": "書けるはずがない"}, key=RO)
check("ro tasks.create denied", isinstance(rtc, dict) and "__error__" in rtc and "tasks:write" in rtc["__error__"], str(rtc)[:200])
rdi = tool("documents.ingest", {"title": "x", "locator": "/x"}, key=RO)
check("ro ingest denied", isinstance(rdi, dict) and "__error__" in rdi, str(rdi)[:150])

print(f"\n=== {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
