"""Does grounding work? 23 factual questions about Moss Inference whose exact answers are in the docs,
plus 3 'abstention' controls whose answers are NOT in the docs. Same questions, several configurations.

  python3 scripts/grounding_accuracy.py            # 2 trials per question per configuration
Outputs logs/accuracy/answers.jsonl and results/accuracy.md.
Grading is by regular expression on the answer text (all patterns of a question must match). Abstention
controls are scored separately: an answer is 'abstained' if it says it does not know, 'invented' if it gives
a specific figure or claim anyway. The raw answers are in the log for manual inspection.
"""
import json, os, random, re, string, sys, time
from mosslat import Client

HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(HERE, "..", "logs", "accuracy"), exist_ok=True)
os.makedirs(os.path.join(HERE, "..", "results"), exist_ok=True)

INSTR = "You are the support agent for Moss Inference. Answer concisely using only the facts you are given. If you do not know, say you do not know."

QA = [
    ("What is the sustained requests-per-minute limit for moss/fastlane-1?", [r"\b120\b"]),
    ("What is the burst request limit for moss/fastlane-1?", [r"\b10\b"]),
    ("What is the maximum prompt size in tokens for moss/fastlane-1?", [r"262,?\s?144"]),
    ("What does moss/fastlane-1 cost per million input tokens?", [r"0\.25"]),
    ("What does moss/fastlane-1 cost per million output tokens?", [r"1\.75"]),
    ("How much does a retrieval cost per 1,000 retrievals?", [r"0\.50|\$0\.5\b|50 cents"]),
    ("How many indexes can one request search at most?", [r"\b32\b"]),
    ("What is the maximum request body size for Responses and Chat Completions?", [r"4\s?MB|4 megabytes"]),
    ("What HTTP status and error code are returned when credits run out?", [r"429", r"usage_limit_exceeded"]),
    ("Which request header turns off the reply cache?", [r"X-Moss-Cache"]),
    ("Which response header reports how long the index search took?", [r"X-Moss-Retrieve-Ms"]),
    ("How long are a finished task's files kept?", [r"7 days|seven days|7-day"]),
    ("How many tasks can a project have queued before another returns 429?", [r"\b20\b"]),
    ("What are the three Moss task model tiers?", [r"moss-fast", r"moss-balanced", r"moss-advanced"]),
    ("What is the default max_cost_usd for a task?", [r"\$\s?2(\.00)?\b|\b2(\.0+)?\s?USD|two dollars"]),
    ("How much does cloud browser traffic cost per GB for tasks?", [r"\$\s?5\b|5 dollars"]),
    ("What does cloud browser time cost per hour for tasks?", [r"0\.02"]),
    ("What is the prompt token limit for anthropic/claude-haiku-5.5 on the Moss key?", [r"64,?000"]),
    ("What does openai/gpt-6-luna cost per million input tokens?", [r"0\.10|\$0\.1\b"]),
    ("How long is an Idempotency-Key valid for when creating a task?", [r"24 hours|24h|24-hour|a day"]),
    ("What is the default max_runtime_seconds for a task on moss-fast?", [r"1,?200"]),
    ("Which model should I use for voice agents when the prompt fits in 262,144 tokens?", [r"fastlane"]),
    ("What is the request body size limit for POST /v1/tasks?", [r"256\s?KiB|256\s?KB|262,?144 bytes"]),
]
CONTROLS = [
    "What is the price per million input tokens for the model moss/fastlane-2?",
    "What is the requests-per-minute limit for the Tasks API?",
    "What uptime SLA percentage does Moss Inference guarantee?",
]
ABSTAIN = re.compile(r"(do not|don't|does not|doesn't|cannot|can't|not) (know|have|contain|include|specify|provide|mention|available|find|listed|documented)|no (information|mention|such|model|data|record)|not (in|part of|listed|available|documented|mentioned|specified|provided)|unable to|isn't (listed|available|documented)|there is no", re.I)

CONFIGS = [
    ("fastlane + clean index (moss-docs-md)", "moss/fastlane-1", "moss-docs-md"),
    ("fastlane, no index", "moss/fastlane-1", None),
    ("fastlane + crawler-built index (moss-docs)", "moss/fastlane-1", "moss-docs"),
    ("claude-haiku-5.5 + clean index", "anthropic/claude-haiku-5.5", "moss-docs-md"),
]
TRIALS = int(sys.argv[1]) if len(sys.argv) > 1 else 2


def nonce():
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))


def main():
    c = Client(); c.get_models()
    rows = []
    t0 = time.time()
    for cname, model, index in CONFIGS:
        for trial in range(TRIALS):
            for qi, (q, pats) in enumerate(QA + [(x, None) for x in CONTROLS]):
                body = {"model": model, "instructions": INSTR, "input": f"{q} (ref {nonce()})", "max_output_tokens": 160}
                hdr = {"X-Moss-Indexes": index} if index else None
                rec = c.responses_stream(body, hdr, grounded=bool(index))
                if rec["status"] == 429:   # stay inside the documented rate limit: wait Retry-After and retry once
                    time.sleep(float(rec.get("retry_after") or 1) + 0.3)
                    rec = c.responses_stream(body, hdr, grounded=bool(index))
                time.sleep(0.6 if model.startswith("moss/") else 0.15)
                text = rec.get("text") or ""
                if pats is not None:
                    ok = all(re.search(p, text, re.I) for p in pats)
                    kind = "qa"
                else:
                    ok = bool(ABSTAIN.search(text))
                    kind = "control"
                rows.append(dict(config=cname, trial=trial, qi=qi, kind=kind, question=q, correct=ok, text=text, ttft_ms=(rec["t_first_delta"] or 0) * 1000,
                                 retrieve_ms=rec.get("retrieve_ms"), input_tokens=(rec.get("usage") or {}).get("input_tokens"), status=rec["status"], error=rec.get("error"), est_usd=rec.get("est_usd")))
                with open(os.path.join(HERE, "..", "logs", "accuracy", "answers.jsonl"), "a") as f:
                    f.write(json.dumps(rows[-1]) + "\n")
        print(f"{cname}: done at {time.time()-t0:.0f}s", flush=True)
    # report
    md = ["| configuration | factual accuracy | abstained on unanswerable | median TTFT ms | median retrieve ms | median input tokens |", "|---|---|---|---|---|---|"]
    import statistics as st
    summ = {}
    for cname, model, index in CONFIGS:
        r = [x for x in rows if x["config"] == cname]
        qa = [x for x in r if x["kind"] == "qa" and x["status"] == 200]
        ct = [x for x in r if x["kind"] == "control" and x["status"] == 200]
        acc = sum(x["correct"] for x in qa) / max(1, len(qa))
        ab = sum(x["correct"] for x in ct) / max(1, len(ct))
        ttft = st.median([x["ttft_ms"] for x in r if x["ttft_ms"]]) if r else None
        ret = [float(x["retrieve_ms"]) for x in r if x["retrieve_ms"] not in (None, "")]
        it = [x["input_tokens"] for x in r if x["input_tokens"]]
        summ[cname] = dict(accuracy=acc, n_qa=len(qa), correct=sum(x["correct"] for x in qa), abstain_rate=ab, n_control=len(ct), ttft_p50=ttft, retrieve_p50=st.median(ret) if ret else None, input_tokens_p50=st.median(it) if it else None)
        md.append(f"| {cname} | {sum(x['correct'] for x in qa)}/{len(qa)} ({acc:.0%}) | {sum(x['correct'] for x in ct)}/{len(ct)} | {ttft:.0f} | {st.median(ret):.0f} | {st.median(it):.0f} |" if ret else
                  f"| {cname} | {sum(x['correct'] for x in qa)}/{len(qa)} ({acc:.0%}) | {sum(x['correct'] for x in ct)}/{len(ct)} | {ttft:.0f} | - | {st.median(it):.0f} |")
    # per-question table: which questions each config misses
    md += ["", "Questions missed at least once, by configuration (trial counts):", ""]
    for cname, _, _ in CONFIGS:
        miss = {}
        for x in rows:
            if x["config"] == cname and x["kind"] == "qa" and not x["correct"]:
                miss[x["question"]] = miss.get(x["question"], 0) + 1
        md.append(f"- **{cname}**: " + ("; ".join(f"{q} (x{n})" for q, n in miss.items()) if miss else "none"))
    open(os.path.join(HERE, "..", "results", "accuracy.md"), "w").write("\n".join(md) + "\n")
    json.dump(summ, open(os.path.join(HERE, "..", "results", "accuracy_summary.json"), "w"), indent=1)
    print("\n".join(md))


if __name__ == "__main__":
    main()
