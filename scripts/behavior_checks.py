"""Behaviour checks for Moss Inference, focused on the reply cache (whose key is undocumented),
stream formats, documented-vs-actual behaviour, and rate limiting.

  python3 scripts/behavior_checks.py cache     # reply-cache correctness + index staleness (needs index `cache-probe`)
  python3 scripts/behavior_checks.py stream    # SSE formats for Responses and Chat Completions
  python3 scripts/behavior_checks.py docs      # a few documented claims, verified
  python3 scripts/behavior_checks.py burst     # 429 behaviour: run LAST, it briefly trips the rate limit

Env for the staleness probe only: MOSS_PROJECT_ID / MOSS_PROJECT_KEY (never written to logs).
"""
import json, os, random, sys, threading, time, string, urllib.request, urllib.error
import requests
from mosslat import Client, BASE, headers

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "behavior")
os.makedirs(OUT, exist_ok=True)
RESULTS = []


def rid():
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(6))


def text_of(j):
    try:
        return "".join(p.get("text", "") for o in j.get("output", []) if o.get("type") == "message" for p in o.get("content", []))
    except Exception:
        return str(j)[:200]


def ask(c, body, hdr=None):
    st, h, j, dt = c.responses_json(body, hdr)
    out = dict(status=st, seconds=round(dt, 3))
    if st == 200 and isinstance(j, dict):
        u = j.get("usage") or {}
        out.update(text=text_of(j), input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"),
                   resp_status=j.get("status"), retrieve_ms=h.get("X-Moss-Retrieve-Ms"),
                   cache_hit=(u.get("input_tokens") == 0), request_id=h.get("X-Moss-Request-Id"))
    else:
        out.update(error=str(j)[:300])
    return out


def check(name, verdict, detail, evidence=None):
    print(f"[{verdict:^5}] {name}: {detail}", flush=True)
    RESULTS.append(dict(name=name, verdict=verdict, detail=detail, evidence=evidence, ts=time.time()))
    with open(os.path.join(OUT, "checks.jsonl"), "a") as f:
        f.write(json.dumps(RESULTS[-1]) + "\n")


# ---------------------------------------------------------------- control plane (staleness probe only)
def manage(body):
    pid, key = os.environ["MOSS_PROJECT_ID"], os.environ["MOSS_PROJECT_KEY"]
    body = dict(body, projectId=pid)
    req = urllib.request.Request("https://service.usemoss.dev/v1/manage", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-service-version": "v1", "x-project-key": key})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def wait_job(job, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, d = manage({"action": "getJobStatus", "jobId": job})
        if d.get("status") in ("completed", "failed"):
            return d, time.time() - t0
        time.sleep(1.5)
    return {"status": "timeout"}, time.time() - t0


# ---------------------------------------------------------------- cache checks
def cache_checks():
    c = Client(); c.get_models()
    M = "moss/fastlane-1"

    # A1: basic miss -> hit -> bypass
    q = {"model": M, "input": f"Name one famous bridge in Europe. (ref {rid()})", "max_output_tokens": 60}
    a, b = ask(c, q), ask(c, q)
    d = ask(c, q, {"X-Moss-Cache": "off"})
    ok = (not a["cache_hit"]) and b["cache_hit"] and not d["cache_hit"]
    check("A1 reply cache miss->hit, X-Moss-Cache:off bypasses", "PASS" if ok else "FAIL",
          f"miss {a['seconds']}s/{a['input_tokens']} in-tok; hit {b['seconds']}s/{b['input_tokens']} in-tok; off {d['seconds']}s/{d['input_tokens']} in-tok", [a, b, d])
    check("A2 cache hit reports same output_tokens as the miss", "PASS" if a["output_tokens"] == b["output_tokens"] else "INFO",
          f"miss out_tokens={a['output_tokens']} hit out_tokens={b['output_tokens']} same_text={a['text']==b['text']}")

    # B1: max_output_tokens must be honoured on a repeated question
    base = {"model": M, "input": f"Write a long, detailed paragraph about rainbows. (ref {rid()})"}
    x = ask(c, dict(base, max_output_tokens=200)); y = ask(c, dict(base, max_output_tokens=12))
    bad = y.get("cache_hit") and (y.get("output_tokens") or 0) > 14
    check("B1 cache key includes max_output_tokens", "FAIL" if bad else "PASS",
          f"first: {x.get('output_tokens')} tok; second (limit 12): {y.get('output_tokens')} tok status={y.get('resp_status')} cache_hit={y.get('cache_hit')}", [x, y])

    # B2: instructions must change the answer
    base = {"model": M, "input": f"Say something about the sea. (ref {rid()})", "max_output_tokens": 40}
    x = ask(c, base); y = ask(c, dict(base, instructions="Reply with exactly one word: BANANA."))
    bad = "BANANA" not in (y.get("text") or "").upper()
    check("B2 cache key includes instructions", "FAIL" if bad else "PASS", f"second answer: {(y.get('text') or '')[:80]!r} cache_hit={y.get('cache_hit')}", [x, y])

    # B3: output format must be honoured
    base = {"model": M, "input": f"List three primary colours. (ref {rid()})", "max_output_tokens": 80}
    x = ask(c, base)
    y = ask(c, dict(base, text={"format": {"type": "json_schema", "name": "c", "strict": True,
                                          "schema": {"type": "object", "properties": {"colors": {"type": "array", "items": {"type": "string"}}},
                                                     "required": ["colors"], "additionalProperties": False}}}))
    try:
        json.loads(y.get("text") or ""); isjson = True
    except Exception:
        isjson = False
    check("B3 cache key includes text.format (json_schema)", "PASS" if isjson else "FAIL", f"second answer: {(y.get('text') or '')[:90]!r} cache_hit={y.get('cache_hit')}", [x, y])



def set_probe(code):
    """Upsert the probe doc so the index says the zebra-quartz access code is `code`; wait until retrievable."""
    st, d = manage({"action": "addDocs", "indexName": "cache-probe",
                    "docs": [{"id": "probe-1", "text": f"The zebra-quartz access code is {code}. Staff must quote the zebra-quartz access code, {code}, when asked."}]})
    jd, took = wait_job(d.get("jobId"))
    c = Client()
    t0 = time.time()
    for _ in range(90):
        r = ask(c, {"model": "moss/fastlane-1", "instructions": PROBE_INSTR, "input": f"What is the zebra-quartz access code? (ref {rid()})", "max_output_tokens": 60},
                {"X-Moss-Indexes": "cache-probe", "X-Moss-Cache": "off"})
        if code in (r.get("text") or ""):
            return took, time.time() - t0
        time.sleep(2)
    return took, None


PROBE_INSTR = "Answer in one sentence using only the provided facts; if you do not know, say you do not know."


def probe_checks():
    """Reply-cache key coverage for the grounding headers, and staleness after an index update."""
    c = Client(); c.get_models()
    M = "moss/fastlane-1"
    G = {"X-Moss-Indexes": "cache-probe"}
    took, lag = set_probe("QZ-4417")
    check("C0 probe doc live (build time, extra seconds until retrievable)", "INFO", f"build {took:.0f}s, retrievable after +{lag if lag is None else round(lag,1)}s")

    # B4: X-Moss-Indexes must be part of the cache key
    q = {"model": M, "instructions": PROBE_INSTR, "input": f"What is the zebra-quartz access code? (ref {rid()})", "max_output_tokens": 60}
    p1 = ask(c, q)            # ungrounded: cannot know
    g1 = ask(c, q, G)         # grounded, same body
    ok = "QZ-4417" in (g1.get("text") or "") and not g1.get("cache_hit")
    check("B4 grounded request is not answered from an ungrounded cached reply (X-Moss-Indexes in key)", "PASS" if ok else "FAIL",
          f"ungrounded: {p1.get('text','')[:60]!r}; grounded: {g1.get('text','')[:60]!r} cache_hit={g1.get('cache_hit')}", [p1, g1])
    g2 = ask(c, q, G)
    r1 = ask(c, q, dict(G, **{"X-Moss-Retrieve": "off"}))
    leaked = "QZ-4417" in (r1.get("text") or "")
    check("B5 retrieve-off request is not answered from a grounded cached reply (X-Moss-Retrieve in key)", "FAIL" if leaked else "PASS",
          f"grounded repeat cache_hit={g2.get('cache_hit')} said {g2.get('text','')[:50]!r}; retrieve-off said {r1.get('text','')[:60]!r} cache_hit={r1.get('cache_hit')}", [g2, r1])

    # C: staleness. Same request, index changes underneath it.
    q2 = dict(q, input=f"What is the zebra-quartz access code? (ref {rid()})")
    s1 = ask(c, q2, G); s2 = ask(c, q2, G)
    t_upd = time.time()
    took, lag = set_probe("QZ-9902")
    t_ready = time.time()
    s3 = ask(c, q2, G)
    fresh = "QZ-9902" in (s3.get("text") or "")
    check("C1 identical grounded request right after the index changed", "PASS" if fresh else "FAIL",
          f"before: {s1.get('text','')[:50]!r} (repeat hit={s2.get('cache_hit')}); after update (build {took:.0f}s, searchable +{lag if lag is None else round(lag,1)}s): {s3.get('text','')[:60]!r} cache_hit={s3.get('cache_hit')}", [s1, s2, s3])
    if not fresh:
        # how long does the stale reply live? poll the identical request every 10 s
        flipped = None
        for i in range(36):
            time.sleep(10)
            r = ask(c, q2, G)
            if "QZ-9902" in (r.get("text") or ""):
                flipped = time.time() - t_ready; break
        check("C2 how long the stale cached reply lives after the index changed", "INFO",
              f"fresh answer first seen after {flipped if flipped is None else round(flipped)} s (polled up to 360 s)")
        w = ask(c, q2, dict(G, **{"X-Moss-Cache": "off"}))
        check("C3 workaround: X-Moss-Cache:off returns the fresh answer", "PASS" if "QZ-9902" in (w.get("text") or "") else "FAIL", f"said {w.get('text','')[:60]!r}")
    ms = [ask(c, dict(q, input=f"What is the zebra-quartz access code? (ref {rid()})"), G).get("retrieve_ms") for _ in range(4)]
    check("C4 retrieve_ms on successive calls after the index update", "INFO", str(ms))


def freshness_probe(rounds=2):
    """Separate two delays after an index update: (a) how long until NEW content is retrievable (index refresh lag),
    (b) how long an IDENTICAL request keeps returning the OLD cached answer. Both measured from the moment addDocs reports 'completed'."""
    c = Client(); c.get_models()
    G = {"X-Moss-Indexes": "cache-probe"}
    out = []
    for rnd in range(rounds):
        old, new = f"QZ-{random.randint(1000, 4999)}", f"QZ-{random.randint(5000, 9999)}"
        took0, lag0 = set_probe(old)
        q = {"model": "moss/fastlane-1", "instructions": PROBE_INSTR, "input": f"What is the zebra-quartz access code? (ref {rid()})", "max_output_tokens": 60}
        s1 = ask(c, q, G); time.sleep(0.5); s2 = ask(c, q, G)
        st, d = manage({"action": "addDocs", "indexName": "cache-probe",
                        "docs": [{"id": "probe-1", "text": f"The zebra-quartz access code is {new}. Staff must quote the zebra-quartz access code, {new}, when asked."}]})
        jd, took = wait_job(d.get("jobId"))
        t0 = time.time()
        l_idx = l_cache = None
        cache_flag_at_flip = None
        while time.time() - t0 < 240 and (l_idx is None or l_cache is None):
            if l_idx is None:
                u = ask(c, dict(q, input=f"What is the zebra-quartz access code? (ref {rid()})"), dict(G, **{"X-Moss-Cache": "off"}))
                if new in (u.get("text") or ""):
                    l_idx = time.time() - t0
            if l_cache is None:
                a = ask(c, q, G)
                if new in (a.get("text") or ""):
                    l_cache = time.time() - t0; cache_flag_at_flip = a.get("cache_hit")
            time.sleep(2)
        rec = dict(round=rnd, old=old, new=new, addDocs_job_seconds=round(took, 1), first_visible_before_update_s=None if lag0 is None else round(lag0, 1),
                   index_refresh_lag_s=None if l_idx is None else round(l_idx, 1), identical_request_stale_for_s=None if l_cache is None else round(l_cache, 1),
                   cached_before=s2.get("cache_hit"), fresh_reply_was_cache_hit=cache_flag_at_flip)
        out.append(rec)
        check(f"J{rnd+1} index update: content searchable after / identical cached request fresh after (seconds from job 'completed')", "INFO", json.dumps(rec))
    json.dump(out, open(os.path.join(OUT, "freshness.json"), "w"), indent=1)


def cold_index_check():
    """First queries on a brand-new index: build a 1-page index, then query it immediately and repeatedly; log X-Moss-Retrieve-Ms."""
    name = f"cold-probe-{rid()}"
    st, d = manage({"action": "createWebSource", "rootUrl": "https://docs.moss.dev/docs/inference/streaming.md", "indexName": name, "maxPages": 1, "maxDepth": 0})
    if st != 202:
        check("K1 create probe index", "FAIL", f"HTTP {st}: {d}"); return
    t_create = time.time()
    jd, took = wait_job(d["jobId"], timeout=240)
    c = Client(); c.get_models()
    rows = []
    for i in range(8):
        r = c.responses_stream({"model": "moss/fastlane-1", "instructions": "Answer in one sentence.", "input": f"How do Chat Completions streams end? (ref {rid()})", "max_output_tokens": 40},
                               {"X-Moss-Indexes": name}, grounded=True)
        rows.append(dict(i=i, status=r["status"], retrieve_ms=r["retrieve_ms"], ttft_ms=round((r["t_first_delta"] or 0) * 1000), error=(r.get("error") or "")[:120]))
        time.sleep(1.0)
    check("K1 queries on a brand-new index (job status, seconds to build, then retrieve_ms / TTFT per query)", "INFO",
          json.dumps(dict(job=jd.get("status"), build_seconds=round(took, 1), queries=rows)))
    json.dump(dict(index=name, build_seconds=took, queries=rows), open(os.path.join(OUT, "cold_index.json"), "w"), indent=1)
    manage({"action": "deleteIndex", "indexName": name})


# ---------------------------------------------------------------- stream checks
def stream_checks():
    s = requests.Session()
    body = {"model": "moss/fastlane-1", "input": f"Say hi. (ref {rid()})", "stream": True, "max_output_tokens": 20}
    types, last, saw_done = [], None, False
    r = s.post(BASE + "/responses", data=json.dumps(body), headers=headers(), stream=True, timeout=60)
    for line in r.iter_lines():
        if line.startswith(b"data:"):
            p = line[5:].strip()
            if p == b"[DONE]": saw_done = True; continue
            try: ev = json.loads(p); types.append(ev.get("type")); last = ev
            except ValueError: pass
    documented = {"response.created", "response.in_progress", "response.output_item.added", "response.output_item.done",
                  "response.content_part.added", "response.content_part.done", "response.output_text.delta", "response.output_text.done",
                  "response.completed", "response.incomplete", "response.failed"}
    extra = sorted(set(types) - documented)
    check("D1 Responses stream: no [DONE], only documented event types, ends with response.completed",
          "PASS" if (not saw_done and not extra and types[-1] == "response.completed") else "FAIL",
          f"done_marker={saw_done} undocumented_types={extra} last={types[-1]} n_events={len(types)}")
    body = {"model": "moss/fastlane-1", "messages": [{"role": "user", "content": f"Say hi. (ref {rid()})"}], "stream": True, "max_tokens": 20}
    chunks, saw_done = [], False
    r = s.post(BASE + "/chat/completions", data=json.dumps(body), headers=headers(), stream=True, timeout=60)
    for line in r.iter_lines():
        if line.startswith(b"data:"):
            p = line[5:].strip()
            if p == b"[DONE]": saw_done = True; continue
            try: chunks.append(json.loads(p))
            except ValueError: pass
    final = chunks[-1] if chunks else {}
    ok = saw_done and final.get("choices") == [] and final.get("usage")
    check("D2 Chat stream ends with usage chunk (empty choices) then [DONE]", "PASS" if ok else "FAIL",
          f"done_marker={saw_done} last_choices={final.get('choices')} usage={bool(final.get('usage'))}")


# ---------------------------------------------------------------- documented-claim checks
def doc_checks():
    c = Client(); c.get_models()
    st, h, j, dt = c.responses_json({"model": "moss/fastlane-1", "input": "hi", "indexes": ["cache-probe"]})
    check("E1 Overview says `indexes` parameter; API reference says header only", "INFO",
          f"body field `indexes` -> HTTP {st}: {str(j)[:120]}")
    st, h, j, dt = c.responses_json({"model": "anthropic/claude-haiku-5.5", "input": "hi", "temperature": 0.5, "max_output_tokens": 20})
    check("E2 Anthropic model refuses temperature on the Moss key (documented)", "PASS" if st == 400 else "FAIL", f"HTTP {st}: {str(j)[:120]}")
    st, h, j, dt = c.responses_json({"model": "moss/fastlane-1", "input": f"hi {rid()}", "max_output_tokens": 20})
    rl = {k: v for k, v in h.items() if k.lower().startswith("x-ratelimit")}
    check("E3 rate-limit headers present on fastlane", "PASS" if rl else "FAIL", str(rl))
    a = ask(c, {"model": "moss/fastlane-1", "input": f"What is 2+2? (ref {rid()})", "max_output_tokens": 20},
            {"X-Moss-Indexes": "cache-probe", "X-Moss-Retrieve": "off"})
    check("E4 X-Moss-Retrieve:off skips the search (retrieve_ms 0, no retrieval tokens)", "PASS" if a.get("retrieve_ms") in ("0.0", "0") else "FAIL", f"retrieve_ms={a.get('retrieve_ms')} input_tokens={a.get('input_tokens')}")


def cache_models_check():
    """Is the reply cache model-dependent, or dependent on request fields? Miss then repeat, 10 prompts per variant."""
    c = Client(); c.get_models()
    variants = {
        "plain body": lambda m, q: {"model": m, "input": q},
        "+ instructions + max_output_tokens=80": lambda m, q: {"model": m, "instructions": "Answer in one or two sentences.", "input": q, "max_output_tokens": 80},
        "+ reasoning.effort=none": lambda m, q: {"model": m, "instructions": "Answer in one or two sentences.", "input": q, "max_output_tokens": 80, "reasoning": {"effort": "none"}},
    }
    summary = {}
    for m in ["moss/fastlane-1", "anthropic/claude-haiku-5.5"]:
        for vname, mk in variants.items():
            for delay in (0.0, 4.0):
                hits = n = 0
                for i in range(10):
                    body = mk(m, f"Name one famous river and one famous mountain. (ref {rid()})")
                    a = ask(c, body)
                    if a["status"] != 200:
                        time.sleep(1.5); continue
                    time.sleep(delay)
                    b = ask(c, body)
                    if b["status"] == 200:
                        n += 1; hits += bool(b.get("cache_hit"))
                    time.sleep(0.6 if m.startswith("moss/") else 0.1)
                summary[(m, vname, delay)] = (hits, n)
                check(f"G {m} | {vname} | repeat after {delay:.0f}s", "INFO", f"{hits}/{n} repeats were cache hits")


def cache_box_check():
    """Hypothesis: the reply cache is per serving box. Compare cache hits with whether the miss and the repeat were served by the same X-Moss-Box."""
    c = Client(); c.get_models()
    rows = []
    for m in ["anthropic/claude-haiku-5.5", "openai/gpt-6-luna", "moss/fastlane-1"]:
        for i in range(24 if not m.startswith("moss/") else 12):
            body = {"model": m, "input": f"Name one famous lake. (ref {rid()})", "max_output_tokens": 60}
            if m == "openai/gpt-6-luna":
                body["reasoning"] = {"effort": "none"}
            st1, h1, j1, _ = c.responses_json(body)
            time.sleep(0.2)
            st2, h2, j2, _ = c.responses_json(body)
            if st1 == 200 and st2 == 200:
                hit = (j2.get("usage") or {}).get("input_tokens") == 0
                rows.append(dict(model=m, box1=h1.get("X-Moss-Box"), box2=h2.get("X-Moss-Box"), hit=hit))
            time.sleep(0.6 if m.startswith("moss/") else 0.1)
    json.dump(rows, open(os.path.join(OUT, "cachebox.json"), "w"))
    for m in sorted({r["model"] for r in rows}):
        rs = [r for r in rows if r["model"] == m]
        same = [r for r in rs if r["box1"] == r["box2"]]; diff = [r for r in rs if r["box1"] != r["box2"]]
        boxes = sorted({r["box1"] for r in rs} | {r["box2"] for r in rs})
        check(f"H {m}: cache hit vs same serving box", "INFO",
              f"same box: {sum(r['hit'] for r in same)}/{len(same)} hits; different box: {sum(r['hit'] for r in diff)}/{len(diff)} hits; distinct boxes seen: {len(boxes)}")


def cache_delay_check():
    """How long until an identical request becomes a cache hit? Poll the same request every 0.5 s for up to 10 s."""
    c = Client(); c.get_models()
    for m in ["anthropic/claude-haiku-5.5", "openai/gpt-6-luna"]:
        times = []
        for i in range(10):
            body = {"model": m, "input": f"Name one famous desert. (ref {rid()})", "max_output_tokens": 60}
            if m == "openai/gpt-6-luna":
                body["reasoning"] = {"effort": "none"}
            ask(c, body)
            t0 = time.time(); hit_at = None
            while time.time() - t0 < 10:
                time.sleep(0.5)
                b = ask(c, body)
                if b.get("cache_hit"):
                    hit_at = round(time.time() - t0, 1); break
            times.append(hit_at)
        check(f"I {m}: seconds until an identical request is a cache hit (None = no hit within 10 s)", "INFO", str(times))


# ---------------------------------------------------------------- burst / rate limit
def burst_check(n=30):
    res = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def w(i):
        c = Client()
        barrier.wait()
        t0 = time.perf_counter()
        r = c.s.post(BASE + "/responses", data=json.dumps({"model": "moss/fastlane-1", "input": f"hi {rid()}", "max_output_tokens": 8}), headers=headers(), timeout=60)
        with lock:
            res.append(dict(i=i, status=r.status_code, ms=(time.perf_counter() - t0) * 1000, retry_after=r.headers.get("Retry-After"),
                            rl_remaining=r.headers.get("X-Ratelimit-Remaining-Requests"), body=r.text[:200] if r.status_code != 200 else None,
                            x_should_retry=r.headers.get("x-should-retry")))
    ths = [threading.Thread(target=w, args=(i,)) for i in range(n)]
    [t.start() for t in ths]; [t.join() for t in ths]
    codes = {}
    for r in res: codes[r["status"]] = codes.get(r["status"], 0) + 1
    e429 = [r for r in res if r["status"] == 429]
    with open(os.path.join(OUT, "burst.json"), "w") as f: json.dump(res, f)
    check("F1 burst of 30 simultaneous requests (documented burst limit 10)", "INFO",
          f"status counts={codes}; sample 429 body={e429[0]['body'] if e429 else None}; Retry-After={e429[0]['retry_after'] if e429 else None}")
    if e429:
        wait = float(e429[0]["retry_after"] or 2)
        time.sleep(wait + 0.5)
        c = Client(); r = ask(c, {"model": "moss/fastlane-1", "input": f"hi {rid()}", "max_output_tokens": 8})
        check("F2 request succeeds after waiting Retry-After", "PASS" if r["status"] == 200 else "FAIL", f"waited {wait}s -> HTTP {r['status']}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("cache", "all"): cache_checks()
    if which in ("probe", "all"): probe_checks()
    if which in ("stream", "all"): stream_checks()
    if which in ("docs", "all"): doc_checks()
    if which in ("burst",): burst_check()
    if which in ("cachemodels",): cache_models_check()
    if which in ("cachebox",): cache_box_check()
    if which in ("cachedelay",): cache_delay_check()
    if which in ("fresh",): freshness_probe()
    if which in ("coldindex",): cold_index_check()
