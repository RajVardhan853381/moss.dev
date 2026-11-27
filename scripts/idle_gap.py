"""Does anything go cold after idle? v2: separates connection re-establishment from model/retrieval warmth.
For each gap (no traffic at all in between):
   a) GET /v1/models on the same keep-alive session        -> cost of the first request after idle (reconnect?)
   b) grounded fastlane query immediately after            -> TTFT with the connection already warm
   c) a second grounded query right after                  -> steady-state reference
Also run `c0` queries with NO preceding GET after the gap to measure what a real client sees.
Run this with no other Moss traffic from the same project."""
import json, os, random, sys, time
from mosslat import Client
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "latency", "idle_gap_v2.jsonl")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
index = sys.argv[1] if len(sys.argv) > 1 else "moss-docs-md"
gaps = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else "10,30,90,240,600".split(","))]

def q(c):
    return c.responses_stream({"model": "moss/fastlane-1", "instructions": "Answer in one sentence.",
        "input": f"What is the burst request limit for moss/fastlane-1? (ref {random.random():.7f})", "max_output_tokens": 40},
        {"X-Moss-Indexes": index}, grounded=True)

def log(rec, **kw):
    rec.update(exp="idle_gap_v2", index=index, ts=time.time(), **kw)
    open(OUT, "a").write(json.dumps(rec) + "\n")

c = Client(); c.get_models(); q(c); q(c)  # warm everything
for g in gaps:
    for variant in ("with_probe_get", "direct_query"):
        time.sleep(g)
        if variant == "with_probe_get":
            t0 = time.perf_counter(); st, dt = c.get_models()
            log({"get_ms": dt * 1000, "status": st}, gap=g, step="a_get_models", variant=variant)
        r1 = q(c); log(r1, gap=g, step="b_first_query", variant=variant)
        r2 = q(c); log(r2, gap=g, step="c_second_query", variant=variant)
        print(f"gap={g:>3}s {variant:>14}: " + (f"GET={dt*1000:.0f}ms " if variant == "with_probe_get" else "") +
              f"first_query ttft={r1['t_first_delta']*1000:.0f}ms retrieve={r1['retrieve_ms']}  second ttft={r2['t_first_delta']*1000:.0f}ms retrieve={r2['retrieve_ms']}", flush=True)
