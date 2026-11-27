"""Ground truth for the HARD research variant, computed from PyPI's JSON API.

  python3 scripts/pypi_truth.py research/ground_truth_hard_before.json

For each package:
  latest_stable            highest non-pre-release, non-yanked version
  releases_in_window       number of PyPI releases (stable + pre-release + yanked) whose first file was uploaded
                           between WINDOW_START and WINDOW_END inclusive (UTC date)
  versions_in_window       the versions counted
  requires_python_change   minimum Python of latest stable vs the previous stable: raised | lowered | unchanged
"""
import json, re, sys, time, urllib.request, urllib.error
from packaging.version import Version, InvalidVersion
from packaging.specifiers import SpecifierSet

WINDOW_START, WINDOW_END = "2026-08-29", "2026-11-27"
PACKAGES = {"Pinecone": "pinecone", "Qdrant": "qdrant-client", "Weaviate": "weaviate-client", "Chroma": "chromadb",
            "Moss": "moss", "Milvus (Zilliz)": "pymilvus", "LanceDB": "lancedb", "Turbopuffer": "turbopuffer"}


def get(url):
    return json.loads(urllib.request.urlopen(url, timeout=30).read())


def min_python(spec):
    """Smallest minor version a requires_python string allows, e.g. '>=3.10' -> (3,10)."""
    if not spec:
        return None
    m = re.findall(r">=\s*(\d+)\.(\d+)", spec)
    return tuple(map(int, m[0])) if m else None


def truth(pkg):
    d = get(f"https://pypi.org/pypi/{pkg}/json")
    rel = d["releases"]
    items = []
    for v, files in rel.items():
        if not files:
            continue
        try:
            pv = Version(v)
        except InvalidVersion:
            continue
        up = min(f["upload_time_iso_8601"] for f in files)[:10]
        yanked = all(f.get("yanked") for f in files)
        items.append(dict(version=v, parsed=pv, date=up, pre=pv.is_prerelease or pv.is_devrelease, yanked=yanked))
    stable = sorted([i for i in items if not i["pre"] and not i["yanked"]], key=lambda i: i["parsed"])
    latest, prev = stable[-1], stable[-2] if len(stable) > 1 else None
    win = [i["version"] for i in items if WINDOW_START <= i["date"] <= WINDOW_END]
    rp_latest = get(f"https://pypi.org/pypi/{pkg}/{latest['version']}/json")["info"].get("requires_python")
    rp_prev = get(f"https://pypi.org/pypi/{pkg}/{prev['version']}/json")["info"].get("requires_python") if prev else None
    a, b = min_python(rp_latest), min_python(rp_prev)
    change = "unchanged" if a == b else ("raised" if (a and b and a > b) else ("lowered" if (a and b and a < b) else "unknown"))
    # Alternative reading of "the stable release immediately before": by UPLOAD DATE instead of version number
    # (differs when a project maintains two release lines in parallel, e.g. pymilvus 2.6.x and 3.x).
    older = sorted([i for i in stable if (i["date"], i["version"]) < (latest["date"], latest["version"]) and i["version"] != latest["version"]],
                   key=lambda i: min(f["upload_time_iso_8601"] for f in rel[i["version"]]))
    prev_chrono = older[-1] if older else None
    rp_prev_chrono = get(f"https://pypi.org/pypi/{pkg}/{prev_chrono['version']}/json")["info"].get("requires_python") if prev_chrono else None
    b2 = min_python(rp_prev_chrono)
    change_chrono = "unchanged" if a == b2 else ("raised" if (a and b2 and a > b2) else ("lowered" if (a and b2 and a < b2) else "unknown"))
    return dict(previous_stable_by_upload=prev_chrono["version"] if prev_chrono else None, requires_python_previous_by_upload=rp_prev_chrono,
                requires_python_change_by_upload=change_chrono, latest_stable=latest["version"], latest_date=latest["date"], previous_stable=prev["version"] if prev else None,
                requires_python_latest=rp_latest, requires_python_previous=rp_prev, requires_python_change=change,
                releases_in_window=len(win), versions_in_window=sorted(win, key=Version), total_releases=len(items))


if __name__ == "__main__":
    out = {"fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "window": [WINDOW_START, WINDOW_END], "data": {}}
    for vendor, pkg in PACKAGES.items():
        try:
            out["data"][pkg] = truth(pkg)
            t = out["data"][pkg]
            print(f"{pkg:16s} latest={t['latest_stable']:10s} prev={t['previous_stable']!s:10s} rp: {t['requires_python_previous']} -> {t['requires_python_latest']} ({t['requires_python_change']}) releases_in_window={t['releases_in_window']} total={t['total_releases']}")
        except Exception as e:
            out["data"][pkg] = {"error": f"{type(e).__name__}: {e}"}
            print(pkg, "ERROR", e)
    if len(sys.argv) > 1:
        json.dump(out, open(sys.argv[1], "w"), indent=1)
