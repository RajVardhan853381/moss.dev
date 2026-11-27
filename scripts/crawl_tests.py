"""Was the 1-page crawl my configuration? Try other crawl settings on docs.moss.dev and count pages / junk chunks.
Each test builds a throwaway index, counts chunks per source URL, flags hydration-code junk, then deletes it. Needs MOSS_PROJECT_ID / MOSS_PROJECT_KEY."""
import json, os, random, string, sys, time
from behavior_checks import manage, wait_job
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "index_crawl_tests.json")
tests = {
    "A: root=overview HTML, no includePaths, depth 2, maxPages 10": dict(rootUrl="https://docs.moss.dev/docs/inference/overview", maxPages=10, maxDepth=2),
    "B: root=overview HTML, includePaths '/docs/inference/**', depth 2": dict(rootUrl="https://docs.moss.dev/docs/inference/overview", maxPages=10, maxDepth=2, includePaths=["/docs/inference/**"]),
    "C: root=docs home HTML, depth 1, maxPages 10": dict(rootUrl="https://docs.moss.dev/", maxPages=10, maxDepth=1),
}
res = {}
for name, cfg in tests.items():
    idx = "crawl-test-" + "".join(random.choice(string.ascii_lowercase) for _ in range(5))
    for attempt in range(20):
        st, d = manage(dict(action="createWebSource", indexName=idx, **cfg))
        if st == 202: break
        if "Concurrent" not in json.dumps(d):   # only the concurrent-job cap is worth retrying; an index-limit error is permanent
            break
        time.sleep(8)
    if st != 202:
        res[name] = dict(error=f"HTTP {st} {d}"); continue
    jd, took = wait_job(d["jobId"], timeout=300)
    st2, ws = manage({"action": "getWebSource", "sourceId": d["id"]})
    st3, docs = manage({"action": "getDocs", "indexName": idx})
    docs = docs if isinstance(docs, list) else []
    pages = {}
    for x in docs:
        pages[x["metadata"].get("sourceUrl")] = pages.get(x["metadata"].get("sourceUrl"), 0) + 1
    junk = sum(("_jsx" in x["text"] or "_components" in x["text"]) for x in docs)
    res[name] = dict(effectiveMaxPages=d.get("effectiveMaxPages"), job=jd.get("status"), seconds=round(took), lastPageCount=ws.get("lastPageCount"), lastDocCount=ws.get("lastDocCount"),
                     chunks=len(docs), junk_chunks=junk, pages=sorted(pages))
    print(name, "->", {k: v for k, v in res[name].items() if k != "pages"}, flush=True)
    manage({"action": "deleteIndex", "indexName": idx})
    json.dump(res, open(OUT, "w"), indent=1)
