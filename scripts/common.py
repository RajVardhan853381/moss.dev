"""Tiny stdlib-only helper for the Moss Inference tests.

Auth: set MOSS_AUTH="<project_id>:<project_key>" for local runs. If unset, no
Authorization header is sent (useful when a proxy injects it).
Every request/response is appended to logs/<name>.jsonl so results are auditable.
"""
import json, os, time, urllib.request, urllib.error

BASE = os.environ.get("MOSS_BASE", "https://inference.moss.dev/v1")
LOGDIR = os.path.join(os.path.dirname(__file__), "..", "logs")

def call(method, path, body=None, headers=None, log="calls", timeout=120, raw_body=None):
    h = {"Content-Type": "application/json"}
    if os.environ.get("MOSS_AUTH"):
        h["Authorization"] = "Bearer " + os.environ["MOSS_AUTH"]
    h.update(headers or {})
    data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    t0 = time.perf_counter()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        status, rh, text = r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        status, rh, text = e.code, dict(e.headers), e.read().decode()
    dt = time.perf_counter() - t0
    try: parsed = json.loads(text)
    except Exception: parsed = text
    rec = {"ts": time.time(), "method": method, "path": path, "req": body, "status": status,
           "latency_s": round(dt, 3), "resp_headers": {k: v for k, v in rh.items() if k.lower().startswith(("x-moss", "retry", "idempotent"))},
           "resp": parsed}
    os.makedirs(LOGDIR, exist_ok=True)
    with open(os.path.join(LOGDIR, log + ".jsonl"), "a") as f: f.write(json.dumps(rec) + "\n")
    return status, parsed, rh, dt

def stream(path, body, headers=None, log="stream", timeout=120):
    """POST with SSE; returns (ttft_s, total_s, events, status)."""
    h = {"Content-Type": "application/json"}
    if os.environ.get("MOSS_AUTH"): h["Authorization"] = "Bearer " + os.environ["MOSS_AUTH"]
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST", headers=h)
    t0 = time.perf_counter(); ttft = None; events = []
    try:
        r = urllib.request.urlopen(req, timeout=timeout); status = r.status
        for line in r:
            line = line.decode().strip()
            if not line.startswith("data:"): continue
            payload = line[5:].strip()
            if payload == "[DONE]": break
            try: ev = json.loads(payload)
            except Exception: continue
            events.append(ev)
            if ttft is None and ev.get("type") in ("response.output_text.delta",) or (ttft is None and ev.get("choices") and ev["choices"][0].get("delta", {}).get("content")):
                ttft = time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        status = e.code; events = [e.read().decode()]
    total = time.perf_counter() - t0
    os.makedirs(LOGDIR, exist_ok=True)
    with open(os.path.join(LOGDIR, log + ".jsonl"), "a") as f:
        f.write(json.dumps({"path": path, "req": body, "status": status, "ttft_s": ttft, "total_s": round(total, 3), "n_events": len(events), "last_event": events[-1] if events else None}) + "\n")
    return ttft, total, events, status
