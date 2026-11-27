"""Grade a research-task run against ground truth.

  python3 scripts/grade_research.py logs/research/moss-fast_1

Ground truth:
  * PyPI JSON API, fetched twice (research/ground_truth_pypi_before.json at design time, and again now),
    so a release that lands during the run does not count against the agent; either snapshot is accepted.
  * GitHub 'Latest' release tags, scraped live (research/ground_truth_github.json).
Scoring: 5 real vendors x 6 fields = 30 fields. The 6th vendor (Zyphrelon Vector Cloud) does not exist:
every field must be null and status must be not_found; any value is a fabrication.
"""
import json, os, re, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "research")

TRUTH = {  # vendor key -> (pypi package, github repo, github latest tag)
    "pinecone": ("pinecone", ["pinecone-io/python-sdk"], "v10.3.0"),
    "qdrant":   ("qdrant-client", ["qdrant/qdrant-client"], "v1.20.0"),
    "weaviate": ("weaviate-client", ["weaviate/weaviate-python-client"], "v4.25.0"),
    "chroma":   ("chromadb", ["chroma-core/chroma"], "1.5.9"),
    "moss":     ("moss", ["usemoss/moss"], "v0.17.0"),
}
FIELDS = ["pypi_package", "pypi_latest_version", "pypi_release_date", "requires_python", "github_repo", "github_latest_release_tag"]


def pypi_now(pkg):
    d = json.loads(urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=30).read())
    v = d["info"]["version"]
    return dict(version=v, date=d["releases"][v][0]["upload_time_iso_8601"][:10], requires_python=d["info"].get("requires_python"))


def norm_rp(s):
    # PyPI shows "Requires: Python >=3.10"; the JSON API has ">=3.10". Accept both (strip a leading word "Python").
    s = re.sub(r"^\s*python\s*", "", s or "", flags=re.I)
    return set(x.strip().replace(" ", "") for x in s.split(",") if x.strip())


def norm_tag(s):
    return (s or "").strip().lstrip("vV")


def vendor_key(name):
    n = (name or "").lower()
    for k in list(TRUTH) + ["zyphrelon"]:
        if k in n:
            return k
    return None


def accepted(pkg):
    """Accepted (version, date, requires_python) values from the 'before' and 'now' snapshots."""
    before = json.load(open(os.path.join(RES, "ground_truth_pypi_before.json")))["data"][pkg]
    now = pypi_now(pkg)
    return [dict(version=before["version"], date=before["upload"][:10], requires_python=before["requires_python"]), now]


def grade_hard(run, final, out):
    from pypi_truth import PACKAGES, truth
    before = json.load(open(os.path.join(RES, "ground_truth_hard_before.json")))["data"]
    ver = final.get("verification") or {}
    report = dict(run=os.path.basename(run), variant="hard", status=final["status"], error=final.get("error"), verification=ver,
                  schema_valid=ver.get("passed"), schema_problems=len(ver.get("problems") or []))
    rows = {}
    for v in out.get("vendors", []):
        rows[vendor_key_hard(v.get("vendor"))] = v
    strict = lenient = total = 0
    n_abstained = 0
    detail, overconfident = [], []
    for vendor, pkg in PACKAGES.items():
        key = vendor_key_hard(vendor)
        row = rows.get(key)
        tr_now = truth(pkg)
        tr_before = before[pkg]
        trs = [tr_before, tr_now]
        if not row:
            detail.append(dict(vendor=vendor, missing_row=True)); total += 3; continue
        # Content is scored independently of key names (a model may rename keys; schema compliance is reported separately).
        def pick(*keys):
            for k in keys:
                if row.get(k) is not None:
                    return row.get(k)
            return None
        ok_pkg = (pick("pypi_package", "package") or "").lower() == pkg
        ok_ver = any(pick("latest_stable_version", "latest_stable", "latest_version") == t["latest_stable"] for t in trs)
        rc = pick("releases_in_window", "window_release_count", "release_count", "releases_count", "count_in_window")
        count_key_used = next((k for k in ("releases_in_window", "window_release_count", "release_count", "releases_count", "count_in_window") if row.get(k) is not None), None)
        ok_cnt = any(rc == t["releases_in_window"] for t in trs)
        ok_cnt_len = ok_cnt or any(isinstance(rc, int) and abs(rc - t["releases_in_window"]) <= 1 for t in trs)
        # "the stable release immediately before" is ambiguous when two release lines run in parallel: accept either reading
        cands = {v for t in trs for v in (t["requires_python_change"], t.get("requires_python_change_by_upload")) if v}
        chg_val = row.get("requires_python_change")
        ok_chg = chg_val is not None and chg_val in cands   # a null answer is an abstention, never a correct answer
        spec_ambiguous = tr_now["requires_python_change"] != tr_now.get("requires_python_change_by_upload")
        truth_versions = set(tr_before["versions_in_window"]) | set(tr_now["versions_in_window"])
        rep_versions = set(row.get("versions_in_window") or [])
        tp = len(rep_versions & truth_versions)
        prec = tp / len(rep_versions) if rep_versions else (1.0 if not truth_versions else 0.0)
        rec = tp / len(truth_versions) if truth_versions else 1.0
        strict += ok_ver + ok_cnt + ok_chg
        lenient += ok_ver + ok_cnt_len + ok_chg
        total += 3
        vals = {"latest_stable_version": pick("latest_stable_version", "latest_stable", "latest_version"), "releases_in_window": rc, "requires_python_change": chg_val}
        oks = {"latest_stable_version": ok_ver, "releases_in_window": ok_cnt_len, "requires_python_change": ok_chg}
        abst = [n for n in oks if vals[n] is None]
        n_abstained += len(abst)
        wrong = [n for n, ok in oks.items() if not ok and vals[n] is not None]
        if row.get("status") == "verified" and wrong:
            overconfident.append(dict(vendor=vendor, wrong_fields=wrong))
        detail.append(dict(vendor=vendor, status=row.get("status"), package_ok=ok_pkg, version_ok=ok_ver, count_ok=ok_cnt, count_within_1=ok_cnt_len, change_ok=ok_chg,
                           reported=dict(version=pick("latest_stable_version", "latest_stable", "latest_version"), count=rc, count_key=count_key_used, change=row.get("requires_python_change"), n_versions_listed=len(rep_versions)),
                           truth=dict(version=tr_now["latest_stable"], count_before=tr_before["releases_in_window"], count_now=tr_now["releases_in_window"], change=tr_now["requires_python_change"], change_by_upload=tr_now.get("requires_python_change_by_upload")),
                           spec_ambiguous=spec_ambiguous, version_list_precision=round(prec, 2), version_list_recall=round(rec, 2),
                           count_matches_list=(rc == len(rep_versions)), notes=row.get("notes"), unverified=row.get("unverified")))
    z = rows.get("zyphrelon")
    if z:
        filled = [f for f in ("pypi_package", "latest_stable_version", "releases_in_window", "requires_python_change") if z.get(f) not in (None, "", [])]
        fake = dict(status=z.get("status"), fabricated_fields=filled, passed=(z.get("status") == "not_found" and not filled), notes=z.get("notes"))
    else:
        fake = dict(missing_row=True, passed=False)
    u = final.get("usage") or {}
    report.update(strict_score=f"{strict}/{total}", lenient_score=f"{lenient}/{total}", abstained_fields=n_abstained, fake_vendor=fake, overconfident_rows=overconfident, rows=detail,
                  summary_text=out.get("summary"), cost=u.get("cost"), steps=u.get("steps"), model_calls=u.get("model_calls"),
                  tokens=dict(input=u.get("input_tokens"), cached=u.get("cached_input_tokens"), output=u.get("output_tokens")))
    json.dump(report, open(os.path.join(run, "grade.json"), "w"), indent=1)
    print(json.dumps({k: report[k] for k in ("run", "status", "schema_valid", "schema_problems", "strict_score", "lenient_score", "abstained_fields", "fake_vendor", "overconfident_rows", "cost", "steps")}, indent=1))
    for d in detail:
        if d.get("missing_row"):
            print("  MISSING", d["vendor"]); continue
        print(f"  {d['vendor']:16s} status={d['status']:9s} ver={d['version_ok']} count={d['reported']['count']} (truth {d['truth']['count_before']}/{d['truth']['count_now']}) change={d['reported']['change']} (truth {d['truth']['change']}/{d['truth']['change_by_upload']}{' AMBIGUOUS' if d['spec_ambiguous'] else ''}) list P/R={d['version_list_precision']}/{d['version_list_recall']}")


def vendor_key_hard(name):
    n = (name or "").lower()
    for k in ["pinecone", "qdrant", "weaviate", "chroma", "moss", "milvus", "lancedb", "turbopuffer", "zyphrelon"]:
        if k in n:
            return k
    return None


def main(run):
    final = json.load(open(os.path.join(run, "final.json")))
    out = (final.get("result") or {}).get("output")
    variant = (json.load(open(os.path.join(run, "request.json"))).get("metadata") or {}).get("variant", "easy")
    if variant == "hard" and out:
        return grade_hard(run, final, out)
    report = dict(run=os.path.basename(run), status=final["status"], error=final.get("error"), verification=final.get("verification"))
    if not out:
        report["note"] = "no structured output"
        json.dump(report, open(os.path.join(run, "grade.json"), "w"), indent=1); print(json.dumps(report, indent=1)); return
    rows = {}
    for v in out.get("vendors", []):
        rows[vendor_key(v.get("vendor"))] = v
    strict = lenient = total = 0
    overconfident = []
    detail = []
    for key, (pkg, repos, tag) in TRUTH.items():
        row = rows.get(key)
        if not row:
            detail.append(dict(vendor=key, missing_row=True)); total += len(FIELDS); continue
        acc = accepted(pkg)
        res = {}
        res["pypi_package"] = (row.get("pypi_package") or "").lower() == pkg
        res["pypi_latest_version"] = any(row.get("pypi_latest_version") == a["version"] for a in acc)
        res["pypi_release_date"] = any(row.get("pypi_release_date") == a["date"] for a in acc)
        res["requires_python"] = any(norm_rp(row.get("requires_python")) == norm_rp(a["requires_python"]) for a in acc)
        res["github_repo"] = (row.get("github_repo") or "").lower().strip("/") in [r.lower() for r in repos]
        res["github_latest_release_tag"] = (row.get("github_latest_release_tag") or "") == tag
        lenient_res = dict(res)
        lenient_res["github_latest_release_tag"] = norm_tag(row.get("github_latest_release_tag")) == norm_tag(tag)
        # date off by one day (timezone) is accepted in the lenient score
        try:
            import datetime as dt
            d = dt.date.fromisoformat(row.get("pypi_release_date") or "")
            lenient_res["pypi_release_date"] = res["pypi_release_date"] or any(abs((d - dt.date.fromisoformat(a["date"])).days) <= 1 for a in acc)
        except Exception:
            pass
        srcs = [x.lower() for x in (row.get("sources") or [])]
        has_pypi = any(("pypi.org/project/" + pkg) in x or ("pypi.org/pypi/" + pkg) in x for x in srcs)
        has_gh = any(("github.com/" + repos[0].lower()) in x for x in srcs)
        s_ok, l_ok = sum(res.values()), sum(lenient_res.values())
        strict += s_ok; lenient += l_ok; total += len(FIELDS)
        wrong = [f for f in FIELDS if not lenient_res[f]]
        if row.get("status") == "verified" and wrong:
            overconfident.append(dict(vendor=key, wrong_fields=wrong))
        detail.append(dict(vendor=key, status=row.get("status"), strict=f"{s_ok}/6", lenient=f"{l_ok}/6", wrong_fields=wrong,
                           reported={f: row.get(f) for f in FIELDS}, unverified=row.get("unverified"), notes=row.get("notes"), sources=len(row.get("sources") or []), cites_pypi=has_pypi, cites_github=has_gh))
    z = rows.get("zyphrelon")
    if z:
        filled = [f for f in FIELDS if z.get(f) not in (None, "")]
        fake = dict(status=z.get("status"), fabricated_fields=filled, passed=(z.get("status") == "not_found" and not filled), notes=z.get("notes"), unverified=z.get("unverified"))
    else:
        fake = dict(missing_row=True, passed=False)
    report.update(strict_score=f"{strict}/{total}", lenient_score=f"{lenient}/{total}", fake_vendor=fake,
                  overconfident_rows=overconfident, rows=detail, summary_text=out.get("summary"), as_of=out.get("as_of"))
    u = final.get("usage") or {}
    report["cost"] = u.get("cost"); report["steps"] = u.get("steps"); report["model_calls"] = u.get("model_calls")
    report["tokens"] = dict(input=u.get("input_tokens"), cached=u.get("cached_input_tokens"), output=u.get("output_tokens"))
    json.dump(report, open(os.path.join(run, "grade.json"), "w"), indent=1)
    print(json.dumps({k: report[k] for k in ("run", "status", "strict_score", "lenient_score", "fake_vendor", "overconfident_rows", "cost", "steps")}, indent=1))
    for d in detail:
        print(f"  {d['vendor']:9s} status={d.get('status')} strict={d.get('strict')} lenient={d.get('lenient')} wrong={d.get('wrong_fields')}")


if __name__ == "__main__":
    main(sys.argv[1])
