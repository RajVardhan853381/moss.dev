"""Inference API checks: models, latency (TTFT) per model, params, error shapes, doc-vs-reality."""
import statistics as st, sys
from common import call, stream

def section(t): print(f"\n=== {t}")

section("models list")
s, b, h, _ = call("GET", "/models", log="inference")
print(s, [m["id"] for m in b["data"]] if s == 200 else b)

section("TTFT, moss/fastlane-1 vs vendor models (stream, 5 runs each)")
models = sys.argv[1:] or ["moss/fastlane-1", "anthropic/claude-haiku-5.5", "openai/gpt-6-luna"]
for m in models:
    t = []
    for i in range(5):
        ttft, tot, ev, s = stream("/responses", {"model": m, "stream": True, "input": "Reply with one short sentence about rain."}, log="inference_ttft")
        if s != 200: print(m, "status", s, ev[:1]); break
        t.append(ttft or float("nan"))
    if t: print(f"{m}: ttft median={st.median(t)*1000:.0f}ms min={min(t)*1000:.0f}ms max={max(t)*1000:.0f}ms")

section("error shapes / validation")
cases = {
 "unknown field": {"model": "moss/fastlane-1", "input": "hi", "bogus": 1},
 "previous_response_id": {"model": "moss/fastlane-1", "input": "hi", "previous_response_id": "resp_x"},
 "bad model": {"model": "nope/none", "input": "hi"},
 "missing input": {"model": "moss/fastlane-1"},
 "list instructions": {"model": "moss/fastlane-1", "input": "hi", "instructions": ["a"]},
 "background true": {"model": "moss/fastlane-1", "input": "hi", "background": True},
 "reasoning on fastlane (ignored?)": {"model": "moss/fastlane-1", "input": "hi", "reasoning": {"effort": "high"}},
 "unknown index": {"model": "moss/fastlane-1", "input": "hi"},
}
for name, body in cases.items():
    hd = {"X-Moss-Indexes": "does-not-exist"} if name == "unknown index" else None
    s, b, _, dt = call("POST", "/responses", body, headers=hd, log="inference")
    print(f"{name}: {s} {str(b)[:160]}")

section("chat completions parity")
s, b, _, dt = call("POST", "/chat/completions", {"model": "moss/fastlane-1", "messages": [{"role": "user", "content": "Say hi"}]}, log="inference")
print(s, str(b)[:200], f"{dt:.2f}s")

section("json_schema output")
s, b, _, dt = call("POST", "/responses", {"model": "anthropic/claude-haiku-5.5", "input": "Capital of France?", "text": {"format": {"type": "json_schema", "name": "a", "schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"], "additionalProperties": False}, "strict": True}}}, log="inference")
print(s, str(b)[:300])

section("big input / limits")
s, b, _, dt = call("POST", "/responses", {"model": "moss/fastlane-1", "input": "x" * 3_000_000}, log="inference")
print(s, str(b)[:200])
