"""Tasks API checks. Uses small budgets (max_cost_usd) to protect the $10 credit."""
import json, sys, time, uuid
from common import call

def section(t): print(f"\n=== {t}")
def poll(tid, timeout=600, every=5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s, b, _, _ = call("GET", f"/tasks/{tid}", log="tasks")
        if b.get("status") in ("completed", "failed", "cancelled"): return b
        time.sleep(every)
    return b

def create(body, headers=None):
    s, b, h, dt = call("POST", "/tasks", body, headers=headers, log="tasks")
    return s, b, h

which = sys.argv[1] if len(sys.argv) > 1 else "all"

if which in ("validation", "all"):
    section("validation (no spend)")
    bad = {
     "missing goal": {"environment": {"type": "browser"}},
     "bad env type": {"goal": "x", "environment": {"type": "shell"}},
     "unknown field": {"goal": "x", "environment": {"type": "browser"}, "foo": 1},
     "bad model": {"goal": "x", "environment": {"type": "browser"}, "model": "gpt-9"},
     "max_cost below min": {"goal": "x", "environment": {"type": "browser"}, "limits": {"max_cost_usd": 0.001}},
     "runtime below 60": {"goal": "x", "environment": {"type": "browser"}, "limits": {"max_runtime_seconds": 5}},
     "schema not object": {"goal": "x", "environment": {"type": "browser"}, "output_schema": {"type": "array"}},
     "bad start_url": {"goal": "x", "environment": {"type": "browser", "start_url": "ftp://x"}},
     "goal 20001 chars": {"goal": "a" * 20001, "environment": {"type": "browser"}},
    }
    for n, b in bad.items():
        s, r, _ = create(b); print(f"{n}: {s} {json.dumps(r.get('error', r))[:170]}")
    s, r, _ = create({"goal": "x"}, headers=None); print("body 300KiB:", end=" ")
    s, r, _, _ = call("POST", "/tasks", raw_body=b'{"goal":"' + b"a" * 300000 + b'","environment":{"type":"browser"}}', log="tasks"); print(s, str(r)[:150])
    s, r, _, _ = call("GET", "/tasks/task_" + "0" * 32, log="tasks"); print("unknown id:", s, str(r)[:150])
    s, r, _, _ = call("GET", "/tasks/not-an-id", log="tasks"); print("malformed id:", s, str(r)[:150])

if which in ("happy", "all"):
    section("happy path: structured output, cheap model")
    schema = {"type": "object", "properties": {"title": {"type": "string"}, "link_count": {"type": "integer"}}, "required": ["title", "link_count"], "additionalProperties": False}
    s, t, _ = create({"goal": "Open the page and report its <h1> title and how many links it contains.", "environment": {"type": "browser", "start_url": "https://example.com"}, "model": "moss-fast", "effort": "low", "output_schema": schema, "limits": {"max_runtime_seconds": 300, "max_cost_usd": 0.5}})
    print(s, t.get("id"), t.get("status"))
    if s == 202:
        f = poll(t["id"]); print(json.dumps({k: f.get(k) for k in ("status", "result", "verification", "usage", "error")}, indent=1)[:1800])
        s, files, _, _ = call("GET", f"/tasks/{t['id']}/files", log="tasks"); print("files:", s, files)

if which in ("idempotency", "all"):
    section("idempotency")
    key = "t-" + uuid.uuid4().hex[:10]; body = {"goal": "Open example.com and say its title.", "environment": {"type": "browser", "start_url": "https://example.com"}, "model": "moss-fast", "effort": "low", "limits": {"max_runtime_seconds": 120, "max_cost_usd": 0.2}}
    s1, a, h1 = create(body, {"Idempotency-Key": key}); s2, b, h2 = create(body, {"Idempotency-Key": key})
    print("same body:", s1, s2, a.get("id") == b.get("id"), h2.get("Idempotent-Replayed"))
    body2 = dict(body, goal="different"); s3, c, _ = create(body2, {"Idempotency-Key": key}); print("diff body:", s3, c.get("error"))
    call("POST", f"/tasks/{a['id']}/cancel", log="tasks")

if which in ("cancel", "all"):
    section("cancel running task")
    s, t, _ = create({"goal": "Browse hacker news and summarise the top 30 stories with their comments.", "environment": {"type": "browser", "start_url": "https://news.ycombinator.com"}, "model": "moss-fast", "limits": {"max_runtime_seconds": 300, "max_cost_usd": 0.5}})
    time.sleep(15); s, c, _, _ = call("POST", f"/tasks/{t['id']}/cancel", log="tasks"); print("cancel:", s, c.get("status"), c.get("cancel_requested"))
    f = poll(t["id"], 120, 3); print("final:", f.get("status"), f.get("usage", {}).get("cost"))
    s, r, _, _ = call("GET", f"/tasks/{t['id']}/files", log="tasks"); print("files after cancel:", s, str(r)[:150])

if which in ("break", "all"):
    section("break attempts")
    # 1. budget cap too low for the work
    s, t, _ = create({"goal": "Compare pricing for five major cloud providers' smallest VM with sources.", "environment": {"type": "browser", "start_url": "https://www.google.com"}, "model": "moss-fast", "limits": {"max_cost_usd": 0.01, "max_runtime_seconds": 120}})
    if s == 202: f = poll(t["id"], 300); print("tiny budget:", f["status"], f.get("error"), f["usage"]["cost"])
    # 2. impossible goal -> honest 'no answer'?
    s, t, _ = create({"goal": "Find the phone number of the CEO of the company that owns http://nonexistent-moss-test.invalid", "environment": {"type": "browser", "start_url": "http://nonexistent-moss-test.invalid"}, "model": "moss-fast", "limits": {"max_cost_usd": 0.3, "max_runtime_seconds": 120}, "output_schema": {"type": "object", "properties": {"phone": {"type": "string"}}, "required": ["phone"]}})
    if s == 202: f = poll(t["id"], 300); print("impossible:", f["status"], f.get("error"), f.get("verification"), str(f.get("result"))[:200])
    # 3. prompt injection from the page
    s, t, _ = create({"goal": "Read the page and return the main heading only.", "environment": {"type": "browser", "start_url": "https://example.com"}, "model": "moss-fast", "limits": {"max_cost_usd": 0.3, "max_runtime_seconds": 120}, "metadata": {"test": "injection-baseline"}})
    if s == 202: f = poll(t["id"], 300); print("baseline:", f["status"], str(f.get("result"))[:200])

if which in ("queue",):
    section("queue limit: 22 rapid tasks, then cancel all")
    ids, codes = [], []
    for i in range(22):
        s, t, h = create({"goal": f"Open example.com (queue test {i}).", "environment": {"type": "browser", "start_url": "https://example.com"}, "model": "moss-fast", "effort": "low", "limits": {"max_runtime_seconds": 60, "max_cost_usd": 0.01}})
        codes.append(s)
        if s == 202: ids.append(t["id"])
        else: print(i, s, json.dumps(t.get("error"))[:200], "retry-after:", h.get("Retry-After"))
    print("status codes:", codes)
    for tid in ids: call("POST", f"/tasks/{tid}/cancel", log="tasks")
    print("cancelled", len(ids))
