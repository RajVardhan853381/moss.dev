"""Turn logs/latency/*.jsonl into tables (results/latency_tables.md) and a machine-readable summary.

  python3 scripts/analyze_latency.py

Method notes (also written into the output):
  * TTFT = seconds from just before the request is written until the first response.output_text.delta arrives,
    on a keep-alive connection, from a cloud sandbox via an HTTPS proxy. It is a client-side number.
  * A request whose reply reports usage.input_tokens == 0 is a reply-cache hit and is excluded from miss statistics.
  * Percentiles are linear-interpolated; intervals are 95% bootstrap CIs (2000 resamples) of the median.
    With n=100 per condition the p95 is noisy and p99 is close to the sample maximum: read them as indicative.
  * "Floor" = median of a keep-alive GET /v1/models measured the same way. TTFT minus floor is an estimate of
    server-side time to first token, not a measurement of it.
"""
import json, os, glob, statistics as st
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "..", "logs", "latency")
OUT = os.path.join(HERE, "..", "results")
os.makedirs(OUT, exist_ok=True)
rng = np.random.default_rng(0)


def load(name):
    p = os.path.join(LOG, name + ".jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in open(p) if l.strip()]


def pctl(xs, q):
    return float(np.percentile(xs, q)) if len(xs) else float("nan")


def ci_median(xs, n=2000):
    xs = np.asarray(xs)
    if len(xs) < 5:
        return (float("nan"), float("nan"))
    meds = np.median(rng.choice(xs, size=(n, len(xs)), replace=True), axis=1)
    return float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def ci_diff_median(a, b, n=2000):
    a, b = np.asarray(a), np.asarray(b)
    da = np.median(rng.choice(a, size=(n, len(a)), replace=True), axis=1)
    db = np.median(rng.choice(b, size=(n, len(b)), replace=True), axis=1)
    d = da - db
    return float(np.median(a) - np.median(b)), float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def ms(x):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.0f}"


def main():
    md = []
    summary = {}
    # ------------------------------------------------------------------ baseline
    base = load("baseline")
    floor = None
    if base:
        ka = [r["ms"] for r in base if r["kind"] == "keepalive" and r["status"] == 200]
        fr = [r["ms"] for r in base if r["kind"] == "fresh_connection" and r["status"] == 200]
        floor = float(np.median(ka))
        summary["baseline"] = dict(keepalive_p50=floor, keepalive_p95=pctl(ka, 95), fresh_p50=float(np.median(fr)), fresh_p95=pctl(fr, 95), n_keepalive=len(ka), n_fresh=len(fr))
        md += ["## Network floor (GET /v1/models from the sandbox, same path as the tests)", "",
               "| connection | n | p50 ms | p95 ms | max ms |", "|---|---|---|---|---|",
               f"| keep-alive | {len(ka)} | {ms(np.median(ka))} | {ms(pctl(ka,95))} | {ms(max(ka))} |",
               f"| new connection each time | {len(fr)} | {ms(np.median(fr))} | {ms(pctl(fr,95))} | {ms(max(fr))} |", ""]
    # ------------------------------------------------------------------ main
    main_rows = [r for r in load("main") if not r.get("warmup")]
    conds = {}
    for r in main_rows:
        conds.setdefault(r["cond"], []).append(r)
    if conds:
        md += ["## Time to first token: 4 models x {plain, grounded}, unique prompts, interleaved randomly", "",
               "| condition | n ok | err | cache hits | TTFT p50 (95% CI) | p90 | p95 | p99 | max | p50 minus floor | retrieve ms p50 / p95 | input tokens (median) |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        summary["main"] = {}
        order = sorted(conds, key=lambda c: (c.split("|")[1], c.split("|")[0]))
        for c in order:
            rs = conds[c]
            ok = [r for r in rs if r["status"] == 200 and r.get("t_first_delta") is not None and not r.get("cache_hit")]
            err = [r for r in rs if r["status"] != 200 or r.get("t_first_delta") is None]
            hits = [r for r in rs if r.get("cache_hit")]
            t = [r["t_first_delta"] * 1000 for r in ok]
            lo, hi = ci_median(t)
            ret = [r["retrieve_ms"] for r in ok if r.get("retrieve_ms") is not None]
            itok = [r["usage"]["input_tokens"] for r in ok if r.get("usage")]
            p50 = pctl(t, 50)
            row = dict(n_ok=len(ok), n_err=len(err), cache_hits=len(hits), p50=p50, p50_ci=[lo, hi], p90=pctl(t, 90), p95=pctl(t, 95), p99=pctl(t, 99), max=max(t) if t else None,
                       retrieve_p50=pctl(ret, 50) if ret else None, retrieve_p95=pctl(ret, 95) if ret else None, input_tokens_median=float(np.median(itok)) if itok else None,
                       est_spend_usd=sum(r.get("est_usd", 0) for r in rs))
            if floor is not None:
                row["p50_minus_floor"] = p50 - floor
            summary["main"][c] = row
            md.append(f"| {c.replace('|', ' / ')} | {len(ok)} | {len(err)} | {len(hits)} | {ms(p50)} ({ms(lo)}-{ms(hi)}) | {ms(row['p90'])} | {ms(row['p95'])} | {ms(row['p99'])} | {ms(row['max'])} | "
                      f"{ms(row.get('p50_minus_floor'))} | {('%s / %s' % (ms(row['retrieve_p50']), ms(row['retrieve_p95']))) if ret and row['retrieve_p50'] else '-'} | {ms(row['input_tokens_median'])} |")
        md.append("")
        # paired grounded vs plain deltas
        md += ["### Cost of grounding (median TTFT grounded minus plain, with 95% bootstrap CI)", "", "| model | delta ms | 95% CI |", "|---|---|---|"]
        for m in sorted({c.split("|")[0] for c in conds}):
            def tt(tag):
                return [r["t_first_delta"] * 1000 for r in conds.get(f"{m}|{tag}", []) if r["status"] == 200 and r.get("t_first_delta") is not None and not r.get("cache_hit")]
            a, b = tt("grounded"), tt("plain")
            if len(a) > 5 and len(b) > 5:
                d, lo, hi = ci_diff_median(a, b)
                summary.setdefault("grounding_delta", {})[m] = dict(delta=d, ci=[lo, hi])
                md.append(f"| {m} | {d:+.0f} | {lo:+.0f} to {hi:+.0f} |")
        md.append("")
        # drift check: first half vs second half by seq for fastlane grounded
        fl = [r for r in conds.get("moss/fastlane-1|grounded", []) if r["status"] == 200 and r.get("t_first_delta") is not None and not r.get("cache_hit")]
        if len(fl) > 20:
            fl.sort(key=lambda r: r["seq"])
            h = len(fl) // 2
            summary["drift_fastlane_grounded"] = dict(first_half_p50=float(np.median([r["t_first_delta"] * 1000 for r in fl[:h]])), second_half_p50=float(np.median([r["t_first_delta"] * 1000 for r in fl[h:]])))
        # time-to-first-token vs time-to-headers: do headers arrive before the first token?
        gap = [(r["t_first_delta"] - r["t_headers"]) * 1000 for rs in conds.values() for r in rs if r.get("t_first_delta") and r.get("t_headers")]
        if gap:
            summary["headers_to_first_delta_ms_p50"] = float(np.median(gap))
    # ------------------------------------------------------------------ control run (no research tasks in flight)
    ctrl_rows = [r for r in load("main_control") if not r.get("warmup")]
    if ctrl_rows and conds:
        md += ["## Control run: same design, n=40, no research tasks running (the main run overlapped with them)", "",
               "| condition | main run p50 / p95 (n=100) | control p50 / p95 (n=40) | p50 difference |", "|---|---|---|---|"]
        summary["control"] = {}
        cc = {}
        for r in ctrl_rows:
            cc.setdefault(r["cond"], []).append(r)
        for c in sorted(cc, key=lambda c: (c.split("|")[1], c.split("|")[0])):
            ok = [r["t_first_delta"] * 1000 for r in cc[c] if r["status"] == 200 and r.get("t_first_delta") is not None and not r.get("cache_hit")]
            if not ok or c not in summary.get("main", {}):
                continue
            m_ = summary["main"][c]
            summary["control"][c] = dict(n=len(ok), p50=pctl(ok, 50), p95=pctl(ok, 95), main_p50=m_["p50"], main_p95=m_["p95"])
            md.append(f"| {c.replace('|', ' / ')} | {ms(m_['p50'])} / {ms(m_['p95'])} | {ms(pctl(ok,50))} / {ms(pctl(ok,95))} | {pctl(ok,50)-m_['p50']:+.0f} |")
        md.append("")
    # ------------------------------------------------------------------ reply cache
    ch = load("cachehit")
    if ch:
        md += ["## Reply cache: classified by what the API reported (usage.input_tokens == 0 means hit)", "",
               "Each prompt was sent 3 times back-to-back. For moss/fastlane-1 some requests were rate-limited (429) because the experiment exceeded the documented 120/min and burst-10 limits; those are excluded from TTFT and counted here.", "",
               "| model | requests | 429s | misses: n, TTFT p50 / p95 ms | hits: n, TTFT p50 / p95 ms | repeats that were hits |", "|---|---|---|---|---|---|"]
        summary["cachehit"] = {}
        for m in sorted({r["cond"] for r in ch}):
            rs = [r for r in ch if r["cond"] == m]
            ok = [r for r in rs if r["status"] == 200 and r.get("t_first_delta") is not None]
            miss = [r["t_first_delta"] * 1000 for r in ok if not r.get("cache_hit")]
            hit = [r["t_first_delta"] * 1000 for r in ok if r.get("cache_hit")]
            reps = [r for r in ok if r["rep"] > 0]
            rate = sum(bool(r.get("cache_hit")) for r in reps) / max(1, len(reps))
            n429 = sum(r["status"] == 429 for r in rs)
            summary["cachehit"][m] = dict(requests=len(rs), n429=n429, miss_n=len(miss), miss_p50=pctl(miss, 50), miss_p95=pctl(miss, 95), hit_n=len(hit), hit_p50=pctl(hit, 50) if hit else None,
                                          hit_p95=pctl(hit, 95) if hit else None, repeat_hit_rate=rate)
            md.append(f"| {m} | {len(rs)} | {n429} | {len(miss)}, {ms(pctl(miss,50))} / {ms(pctl(miss,95))} | {len(hit)}, {ms(pctl(hit,50)) if hit else '-'} / {ms(pctl(hit,95)) if hit else '-'} | {rate:.0%} ({sum(bool(r.get('cache_hit')) for r in reps)}/{len(reps)}) |")
        md.append("")
    # ------------------------------------------------------------------ concurrency
    conc = load("concurrency")
    if conc:
        md += ["## Overlapping requests (moss/fastlane-1, grounded, ~1.5 req/s, under the documented limit)", "",
               "| workers | n ok | 429s | TTFT p50 | p95 | p99 | max | median in-flight at start |", "|---|---|---|---|---|---|---|---|"]
        summary["concurrency"] = {}
        for w in sorted({r["workers"] for r in conc}):
            rs = [r for r in conc if r["workers"] == w]
            ok = [r for r in rs if r["status"] == 200 and r.get("t_first_delta") is not None and not r.get("cache_hit")]
            t = [r["t_first_delta"] * 1000 for r in ok]
            n429 = sum(r["status"] == 429 for r in rs)
            row = dict(n_ok=len(ok), n429=n429, p50=pctl(t, 50), p95=pctl(t, 95), p99=pctl(t, 99), max=max(t) if t else None, inflight=float(np.median([r["inflight_at_start"] for r in rs])))
            summary["concurrency"][str(w)] = row
            md.append(f"| {w} | {len(ok)} | {n429} | {ms(row['p50'])} | {ms(row['p95'])} | {ms(row['p99'])} | {ms(row['max'])} | {row['inflight']:.0f} |")
        md.append("")
    # ------------------------------------------------------------------ prompt size
    ps = load("promptsize")
    if ps:
        md += ["## Prompt length (moss/fastlane-1, ungrounded, unique random text)", "",
               "| target tokens | actual input tokens (median) | n | TTFT p50 | p95 | max |", "|---|---|---|---|---|---|"]
        summary["promptsize"] = {}
        for tgt in sorted({r["target_tokens"] for r in ps if r["kind"] == "unique"}):
            rs = [r for r in ps if r["kind"] == "unique" and r["target_tokens"] == tgt and r["status"] == 200 and r.get("t_first_delta") is not None]
            if not rs:
                continue
            t = [r["t_first_delta"] * 1000 for r in rs]
            it = float(np.median([r["usage"]["input_tokens"] for r in rs if r.get("usage")]))
            summary["promptsize"][str(tgt)] = dict(actual_tokens=it, n=len(rs), p50=pctl(t, 50), p95=pctl(t, 95), max=max(t))
            md.append(f"| {tgt} | {it:.0f} | {len(rs)} | {ms(pctl(t,50))} | {ms(pctl(t,95))} | {ms(max(t))} |")
        sp = [r for r in ps if r["kind"] == "shared_prefix" and r["status"] == 200 and r.get("t_first_delta") is not None]
        if sp:
            sp.sort(key=lambda r: r["idx"])
            first, rest = sp[0], sp[1:]
            cached = [(r["usage"].get("input_tokens_details") or {}).get("cached_tokens", 0) for r in rest if r.get("usage")]
            summary["prefix_cache"] = dict(first_ttft=first["t_first_delta"] * 1000, rest_p50=pctl([r["t_first_delta"] * 1000 for r in rest], 50),
                                           cached_tokens_median=float(np.median(cached)) if cached else None, input_tokens=first["usage"]["input_tokens"] if first.get("usage") else None)
            md += ["", f"Shared 32k-token prefix, 10 different questions: first call TTFT {ms(summary['prefix_cache']['first_ttft'])} ms; calls 2-10 median {ms(summary['prefix_cache']['rest_p50'])} ms, "
                   f"median cached tokens {summary['prefix_cache']['cached_tokens_median']}."]
        md.append("")
    # ------------------------------------------------------------------ idle
    idle = load("idle_gap_v2")
    if idle:
        md += ["## First request after idle (retrieval index: moss-docs-md)", "",
               "| idle gap | what the client did first | GET /models ms | 1st query TTFT ms | 2nd query TTFT ms | 1st retrieve ms |", "|---|---|---|---|---|---|"]
        summary["idle"] = []
        keys = sorted({(r["gap"], r["variant"]) for r in idle})
        for gap, var in keys:
            rs = [r for r in idle if r["gap"] == gap and r["variant"] == var]
            g = next((r["get_ms"] for r in rs if r["step"] == "a_get_models"), None)
            b = next((r for r in rs if r["step"] == "b_first_query"), None)
            c = next((r for r in rs if r["step"] == "c_second_query"), None)
            if not b or not c:
                continue
            summary["idle"].append(dict(gap=gap, variant=var, get_ms=g, first_ttft=b["t_first_delta"] * 1000, second_ttft=c["t_first_delta"] * 1000, first_retrieve=b["retrieve_ms"]))
            md.append(f"| {gap}s | {'GET /models, then query' if var == 'with_probe_get' else 'query directly'} | {ms(g) if g else '-'} | {ms(b['t_first_delta']*1000)} | {ms(c['t_first_delta']*1000)} | {b['retrieve_ms']} |")
        md.append("")
    # ------------------------------------------------------------------ spend
    total = sum(r.get("est_usd", 0) for name in ("main", "main_control", "concurrency", "promptsize", "idle_gap_v2", "cachehit") for r in load(name))
    summary["est_spend_usd_latency_experiments"] = total
    md += [f"Estimated spend of these experiments (token prices from the docs + $0.50/1k retrievals): ${total:.2f}", ""]
    open(os.path.join(OUT, "latency_tables.md"), "w").write("\n".join(md))
    json.dump(summary, open(os.path.join(OUT, "latency_summary.json"), "w"), indent=1)
    print("\n".join(md))


if __name__ == "__main__":
    main()
