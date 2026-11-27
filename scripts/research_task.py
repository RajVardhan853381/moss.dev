"""Run one research task on the Tasks API and record everything.

  python3 scripts/research_task.py moss-fast --max-cost 0.8
  python3 scripts/research_task.py moss-balanced --max-cost 1.5

Writes logs/research/<tier>_<n>/{request.json, polls.jsonl, final.json, files.json} (no secrets).
Auth: via the sandbox proxy (no Authorization header) or MOSS_AUTH="<project_id>:<project_key>".
"""
import argparse, json, os, sys, time
import requests

BASE = os.environ.get("MOSS_BASE", "https://inference.moss.dev/v1")

VENDORS = ["Pinecone", "Qdrant", "Weaviate", "Chroma",
           "Moss (the real-time semantic search runtime from moss.dev / usemoss)",
           "Zyphrelon Vector Cloud"]

GOAL = """You are doing competitive-intelligence research for an engineering team that is choosing a retrieval / vector-search vendor. Compare these six vendors by looking at each vendor's official Python SDK:

1. Pinecone
2. Qdrant
3. Weaviate
4. Chroma
5. Moss (the real-time semantic search runtime from moss.dev / usemoss)
6. Zyphrelon Vector Cloud

For each vendor, find the vendor's current, officially supported Python SDK on PyPI (pypi.org) and its source repository on GitHub, then report:
- pypi_package: the PyPI package name of the officially supported Python SDK
- pypi_latest_version: the latest stable version shown on that package's PyPI page (not a pre-release)
- pypi_release_date: that version's release date in UTC as YYYY-MM-DD, as shown on PyPI
- requires_python: the "Requires: Python ..." value from the PyPI page, exactly as shown
- github_repo: owner/name of the SDK's source repository, as linked from PyPI or the vendor
- github_latest_release_tag: the tag of the release marked "Latest" on that repository's GitHub Releases page
- sources: the URLs you actually opened that support these values
- status: "verified" if every field above is confirmed from a page you opened, "partial" if some could not be confirmed, "not_found" if you could not find an official Python SDK for the vendor
- unverified: each field you could not confirm, with the reason
- notes: anything else a reader should know

Rules: only report values you saw on a page you opened. Never guess and never fill a field from memory. If a value cannot be confirmed, set it to null and list it in "unverified". Put today's date (UTC) in "as_of". Finish with a 2-3 sentence "summary" naming which SDKs were released most recently and any vendor you could not verify."""

NULLABLE_STR = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "as_of": {"type": "string"},
        "vendors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "vendor": {"type": "string"},
                    "pypi_package": NULLABLE_STR,
                    "pypi_latest_version": NULLABLE_STR,
                    "pypi_release_date": NULLABLE_STR,
                    "requires_python": NULLABLE_STR,
                    "github_repo": NULLABLE_STR,
                    "github_latest_release_tag": NULLABLE_STR,
                    "sources": {"type": "array", "items": {"type": "string"}},
                    "status": {"type": "string", "enum": ["verified", "partial", "not_found"]},
                    "unverified": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                },
                "required": ["vendor", "pypi_package", "pypi_latest_version", "pypi_release_date", "requires_python",
                             "github_repo", "github_latest_release_tag", "sources", "status", "unverified", "notes"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["as_of", "vendors", "summary"],
}


GOAL_HARD = """You are doing competitive-intelligence research on the release activity of the Python SDKs of retrieval / vector-search vendors. Use PyPI (pypi.org) for every value. The vendors are:

1. Pinecone
2. Qdrant
3. Weaviate
4. Chroma
5. Moss (the real-time semantic search runtime from moss.dev / usemoss)
6. Milvus (Zilliz)
7. LanceDB
8. Turbopuffer
9. Zyphrelon Vector Cloud

For each vendor, find the vendor's current, officially supported Python SDK on PyPI and report:
- pypi_package: the PyPI package name
- latest_stable_version: the latest stable version (not a pre-release, not yanked)
- releases_in_window: how many releases PyPI's "Release history" for the project lists with an upload date from 2026-08-29 through 2026-11-27 inclusive (UTC). Count every listed release in that date range, including pre-releases and yanked ones. If there are none, the answer is 0.
- versions_in_window: the version numbers you counted, so the count can be audited
- requires_python_change: compare the "Requires: Python" value of the latest stable release with that of the stable release immediately before it (open both release pages): "raised" if the minimum Python version went up, "lowered" if it went down, "unchanged" if it is the same
- sources: the URLs you actually opened that support these values
- status: "verified" if every field above is confirmed from pages you opened, "partial" if some could not be confirmed, "not_found" if you could not find an official Python SDK for the vendor
- unverified: each field you could not confirm, with the reason
- notes: anything else a reader should know

Rules: only report values you saw on a page you opened. Never guess and never fill a field from memory. If a value cannot be confirmed, set it to null (use [] for versions_in_window) and list it in "unverified". Put today's date (UTC) in "as_of". Finish with a 2-3 sentence "summary" naming the vendors with the most release activity in the window and any vendor you could not verify."""

_ENUM_NULL = {"type": ["string", "null"], "enum": ["raised", "lowered", "unchanged", None]}
SCHEMA_HARD = {
    "type": "object",
    "properties": {
        "as_of": {"type": "string"},
        "vendors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "vendor": {"type": "string"},
                    "pypi_package": NULLABLE_STR,
                    "latest_stable_version": NULLABLE_STR,
                    "releases_in_window": {"type": ["integer", "null"]},
                    "versions_in_window": {"type": "array", "items": {"type": "string"}},
                    "requires_python_change": _ENUM_NULL,
                    "sources": {"type": "array", "items": {"type": "string"}},
                    "status": {"type": "string", "enum": ["verified", "partial", "not_found"]},
                    "unverified": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                },
                "required": ["vendor", "pypi_package", "latest_stable_version", "releases_in_window", "versions_in_window",
                             "requires_python_change", "sources", "status", "unverified", "notes"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["as_of", "vendors", "summary"],
}


def hdr():
    h = {"Content-Type": "application/json"}
    if os.environ.get("MOSS_AUTH"):
        h["Authorization"] = "Bearer " + os.environ["MOSS_AUTH"]
    return h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tier")
    ap.add_argument("--max-cost", type=float, default=1.0)
    ap.add_argument("--max-runtime", type=int, default=1500)
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--tag", default="")
    ap.add_argument("--variant", choices=["easy", "hard"], default="easy")
    a = ap.parse_args()

    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "research")
    tag = a.tag + ("-hard" if a.variant == "hard" else "")
    n = 1
    while os.path.exists(os.path.join(root, f"{a.tier}{tag}_{n}")):
        n += 1
    out = os.path.join(root, f"{a.tier}{tag}_{n}")
    os.makedirs(out)

    body = {"goal": GOAL_HARD if a.variant == "hard" else GOAL, "environment": {"type": "browser", "start_url": "https://pypi.org/"}, "model": a.tier,
            "effort": a.effort, "output_schema": SCHEMA_HARD if a.variant == "hard" else SCHEMA,
            "limits": {"max_runtime_seconds": a.max_runtime, "max_cost_usd": a.max_cost},
            "metadata": {"purpose": "work-sample research task", "tier": a.tier, "variant": a.variant}}
    json.dump(body, open(os.path.join(out, "request.json"), "w"), indent=1)

    s = requests.Session()
    r = s.post(BASE + "/tasks", data=json.dumps(body), headers=hdr(), timeout=60)
    print("create:", r.status_code, r.headers.get("X-Moss-Request-Id"))
    if r.status_code != 202:
        print(r.text[:800]); json.dump({"status": r.status_code, "body": r.text[:2000]}, open(os.path.join(out, "create_error.json"), "w")); sys.exit(1)
    task = r.json()
    tid = task["id"]
    print("task:", tid, flush=True)
    t0 = time.time()
    last = None
    with open(os.path.join(out, "polls.jsonl"), "a") as pf:
        while True:
            g = s.get(f"{BASE}/tasks/{tid}", headers=hdr(), timeout=60)
            if g.status_code != 200:
                print("poll error", g.status_code, g.text[:200]); time.sleep(5); continue
            t = g.json()
            u = t.get("usage") or {}
            pf.write(json.dumps({"t": round(time.time() - t0, 1), "status": t["status"], "steps": u.get("steps"), "model_calls": u.get("model_calls"),
                                 "cost_usd": u.get("cost_usd"), "total_usd": (u.get("cost") or {}).get("total_usd")}) + "\n"); pf.flush()
            line = (t["status"], u.get("steps"), round(u.get("cost_usd") or 0, 4))
            if line != last:
                print(f"[{time.time()-t0:6.0f}s] {line}", flush=True); last = line
            if t["status"] in ("completed", "failed", "cancelled"):
                break
            if time.time() - t0 > a.max_runtime + 120:
                print("local timeout; cancelling"); s.post(f"{BASE}/tasks/{tid}/cancel", headers=hdr());
            time.sleep(10)
    json.dump(t, open(os.path.join(out, "final.json"), "w"), indent=1)
    fl = s.get(f"{BASE}/tasks/{tid}/files", headers=hdr(), timeout=60)
    json.dump(fl.json() if fl.status_code == 200 else {"status": fl.status_code, "body": fl.text[:300]}, open(os.path.join(out, "files.json"), "w"), indent=1)
    u = t.get("usage") or {}
    print(json.dumps({"status": t["status"], "error": t.get("error"), "verification": t.get("verification"),
                      "steps": u.get("steps"), "model_calls": u.get("model_calls"), "cost": u.get("cost"),
                      "input_tokens": u.get("input_tokens"), "cached_input_tokens": u.get("cached_input_tokens"), "output_tokens": u.get("output_tokens"),
                      "browser_seconds": u.get("browser_seconds"), "created": t.get("created_at"), "started": t.get("started_at"), "finished": t.get("finished_at")}, indent=1))
    print("saved to", out)


if __name__ == "__main__":
    main()
