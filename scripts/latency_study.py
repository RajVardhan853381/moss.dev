"""Latency study for Moss Inference (Responses API, streaming).

Experiments (run one by name, or `all`):
  baseline     network floor: GET /v1/models, keep-alive vs fresh connection
  main         TTFT for 4 models x {ungrounded, grounded}, unique prompts, interleaved random order
  concurrency  moss/fastlane-1 grounded, 1 worker vs 3 overlapping workers (kept under the documented rate limit)
  promptsize   moss/fastlane-1 TTFT vs prompt length, plus the prefix-cache effect
Output: logs/latency/<experiment>.jsonl (one record per request; no secrets).
Usage:  python3 scripts/latency_study.py main --n 100 --index moss-docs-md
"""
import argparse, json, os, random, string, sys, threading, time
from mosslat import Client, COST, pct

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "latency")
os.makedirs(OUT, exist_ok=True)

MODELS = ["moss/fastlane-1", "anthropic/claude-haiku-5.5", "openai/gpt-6-luna", "google/gemini-3.5-flash-lite"]
INSTR = "You are the support agent for Moss Inference. Answer in one or two sentences."
MAX_OUT = 80

QUESTIONS = [
    "What is the sustained requests-per-minute limit for moss/fastlane-1?",
    "How much does moss/fastlane-1 cost per million input tokens?",
    "How do I turn off the reply cache for one call?",
    "What does the X-Moss-Retrieve-Ms header tell me?",
    "How many indexes can one request search?",
    "What happens when my credits run out?",
    "Which header names the indexes Moss should search?",
    "How long are task files kept after a task finishes?",
    "What are the Moss task model tiers?",
    "How is a browser task billed?",
    "What is the prompt limit for anthropic/claude-haiku-5.5 on the Moss key?",
    "Does Moss store responses between calls?",
    "How do I stream a reply?",
    "What is the default model if I send none?",
    "How much does one retrieval cost?",
    "What is the maximum request body size?",
    "How do I bring my own OpenAI key?",
    "What does the 403 permission_error mean?",
    "Which models should I use for voice agents?",
    "What is the cached input price for moss/fastlane-1?",
    "What is the default max_runtime_seconds for a task?",
    "How many tasks can be queued at once?",
    "What does moss_search do?",
    "How do I call a function tool?",
    "Which fields does the Responses endpoint refuse?",
    "What is the context window of moss/fastlane-1?",
    "How can I see how much of my rate limit is left?",
    "What price does cloud browser traffic have?",
    "What is the idempotency behaviour for creating tasks?",
    "Can I use an MCP server with Moss Inference?",
    "How do I cancel a running task?",
    "What does a 503 capacity error mean?",
    "Does moss/fastlane-1 accept images?",
    "How do I set a task's cost limit?",
    "What is the output price for openai/gpt-6-luna?",
    "How do Chat Completions streams end?",
    "What does the reply cache do?",
    "Which SDK languages does Moss Inference support?",
    "How do I list the models I can call?",
    "What is retrieved when I name an index in a call?",
]


def nonce(rng):
    return "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(6))


def dump(name, rec):
    with open(os.path.join(OUT, name + ".jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")


def body_for(model, i, rng, question=None):
    q = (question or QUESTIONS[i % len(QUESTIONS)]) + f" (ref {nonce(rng)})"
    body = {"model": model, "instructions": INSTR, "input": q, "max_output_tokens": MAX_OUT}
    # GPT-6 Luna reasons by default and spends the whole 80-token cap on hidden reasoning (HTTP 200, status "incomplete", no text),
    # so reasoning is switched off for a fair first-token comparison. Gemini 3.5 Flash-Lite rejects the field (opaque 400), so it is left out.
    if model != "google/gemini-3.5-flash-lite":
        body["reasoning"] = {"effort": "none"}
    return body


def exp_baseline(args):
    c = Client()
    c.get_models()  # warm
    for i in range(60):
        st, dt = c.get_models()
        dump("baseline", dict(exp="baseline", kind="keepalive", status=st, ms=dt * 1000, ts=time.time()))
        time.sleep(0.4)
    for i in range(20):
        st, dt = Client().get_models()
        dump("baseline", dict(exp="baseline", kind="fresh_connection", status=st, ms=dt * 1000, ts=time.time()))
        time.sleep(0.4)
    print("baseline done")


def exp_main(args):
    rng = random.Random(20261127)
    c = Client()
    conds = [(m, g) for m in MODELS for g in (False, True)]
    extra_g = {"X-Moss-Indexes": args.index}
    # warm-up (excluded from stats): 2 per condition
    for (m, g) in conds:
        for k in range(2):
            rec = c.responses_stream(body_for(m, k, rng), extra_g if g else None, grounded=g)
            rec.update(exp="main", cond=f"{m}|{'grounded' if g else 'plain'}", warmup=True, ts=time.time())
            dump("main" + args.tag, rec)
    sched = [(cnd, i) for cnd in conds for i in range(args.n)]
    random.Random(7).shuffle(sched)
    t_start = time.time()
    for k, ((m, g), i) in enumerate(sched):
        rec = c.responses_stream(body_for(m, i, rng), extra_g if g else None, grounded=g)
        rec.update(exp="main", cond=f"{m}|{'grounded' if g else 'plain'}", q=i, seq=k, warmup=False, ts=time.time())
        dump("main" + args.tag, rec)
        if rec["status"] == 429:
            time.sleep(float(rec.get("retry_after") or 2))
        if k % 50 == 0:
            print(f"{k}/{len(sched)} elapsed {time.time()-t_start:.0f}s est_spend ${COST['usd']:.3f}", flush=True)
    print(f"main done: {len(sched)} calls, est_spend ${COST['usd']:.3f}")


def exp_concurrency(args):
    m = "moss/fastlane-1"
    extra_g = {"X-Moss-Indexes": args.index}
    for workers, total, rate in [(1, 60, 1.5), (3, 60, 1.5)]:
        lock = threading.Lock()
        state = {"next": time.time(), "i": 0, "inflight": 0}
        gap = 1.0 / rate

        def worker(wid):
            c = Client()
            c.get_models()
            rng = random.Random(1000 + wid)
            while True:
                with lock:
                    if state["i"] >= total:
                        return
                    idx = state["i"]; state["i"] += 1
                    wait = max(0.0, state["next"] - time.time())
                    state["next"] = max(state["next"], time.time()) + gap
                time.sleep(wait)
                with lock:
                    state["inflight"] += 1; inflight = state["inflight"]
                rec = c.responses_stream(body_for(m, idx, rng), extra_g, grounded=True)
                with lock:
                    state["inflight"] -= 1
                rec.update(exp="concurrency", workers=workers, inflight_at_start=inflight, ts=time.time())
                dump("concurrency", rec)

        ths = [threading.Thread(target=worker, args=(w,)) for w in range(workers)]
        [t.start() for t in ths]; [t.join() for t in ths]
        print(f"concurrency workers={workers} done, est_spend ${COST['usd']:.3f}", flush=True)
        time.sleep(15)


VOCAB = ("river stone cloud engine paper window garden signal market copper violet anchor ladder pencil harbor "
         "meadow tunnel button candle forest mirror thunder saddle ribbon lantern marble compass feather drift "
         "orchard planet quartz timber velvet whistle yonder zephyr basket cobalt dune ember fable glacier hollow "
         "island jungle kettle lagoon mosaic nectar oasis prairie quiver rumble summit trellis umbra valley").split()


def filler(n_tokens, rng):
    return " ".join(rng.choice(VOCAB) for _ in range(int(n_tokens / 1.3)))


def exp_promptsize(args):
    m = "moss/fastlane-1"
    c = Client(); c.get_models()
    rng = random.Random(99)
    sizes = [(200, 12), (2000, 12), (8000, 12), (32000, 10), (120000, 5)]
    for target, n in sizes:
        for i in range(n):
            text = filler(target, rng) + "\n\nIn one short sentence, what is this text about?"
            body = {"model": m, "input": text, "max_output_tokens": 40}
            rec = c.responses_stream(body)
            rec.update(exp="promptsize", target_tokens=target, kind="unique", ts=time.time())
            dump("promptsize", rec)
        print(f"size {target} done, est_spend ${COST['usd']:.3f}", flush=True)
    # prefix cache: same 32k prefix, different final question each call
    prefix = filler(32000, random.Random(5))
    for i in range(10):
        body = {"model": m, "input": prefix + f"\n\nQuestion {i}: name one word from the text that starts with the letter {string.ascii_lowercase[i]}. (ref {nonce(rng)})", "max_output_tokens": 20}
        rec = c.responses_stream(body)
        rec.update(exp="promptsize", target_tokens=32000, kind="shared_prefix", idx=i, ts=time.time())
        dump("promptsize", rec)
    print(f"promptsize done, est_spend ${COST['usd']:.3f}")


def exp_cachehit(args):
    """Reply cache: each unique prompt is sent once (miss) and then twice more (hits)."""
    rng = random.Random(4242)
    c = Client(); c.get_models()
    for m in ["moss/fastlane-1", "anthropic/claude-haiku-5.5"]:
        for i in range(30):
            body = body_for(m, i, rng)
            for rep in range(3):
                rec = c.responses_stream(body)
                rec.update(exp="cachehit", cond=m, kind="miss" if rep == 0 else "hit", rep=rep, ts=time.time())
                dump("cachehit", rec)
    print(f"cachehit done, est_spend ${COST['usd']:.3f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("exp", choices=["baseline", "main", "concurrency", "promptsize", "cachehit", "all"])
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--index", default="moss-docs-md")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    for name, fn in [("baseline", exp_baseline), ("main", exp_main), ("concurrency", exp_concurrency), ("promptsize", exp_promptsize), ("cachehit", exp_cachehit)]:
        if a.exp in (name, "all"):
            fn(a)
    print(json.dumps({"est_spend_usd": round(COST["usd"], 4), "calls": COST["calls"]}))
