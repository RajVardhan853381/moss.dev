"""Collect every graded research run into results/research_task.md (table) and results/research_summary.json."""
import glob, json, os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = sorted(glob.glob(os.path.join(HERE, "..", "logs", "research", "*_*")))
ORDER = {"moss-fast": 0, "moss-balanced": 1, "moss-advanced": 2}


def secs(f):
    a = datetime.fromisoformat(f["started_at"].replace("Z", "+00:00")); b = datetime.fromisoformat(f["finished_at"].replace("Z", "+00:00"))
    return (b - a).total_seconds()


rows = []
for d in RUNS:
    if not os.path.exists(os.path.join(d, "final.json")) or not os.path.exists(os.path.join(d, "request.json")):
        continue
    f = json.load(open(os.path.join(d, "final.json")))
    req = json.load(open(os.path.join(d, "request.json")))
    g = json.load(open(os.path.join(d, "grade.json"))) if os.path.exists(os.path.join(d, "grade.json")) else {}
    u = f.get("usage") or {}
    tier = req["model"]
    variant = (req.get("metadata") or {}).get("variant", "easy")
    fake = g.get("fake_vendor") or {}
    rows.append(dict(run=os.path.basename(d), tier=tier, variant=variant, status=f["status"], schema_valid=(f.get("verification") or {}).get("passed"),
                     schema_problems=len((f.get("verification") or {}).get("problems") or []), content=g.get("strict_score"), abstained=g.get("abstained_fields", 0), fake_ok=fake.get("passed"),
                     overconfident=len(g.get("overconfident_rows") or []), steps=u.get("steps"), calls=u.get("model_calls"), seconds=secs(f),
                     cost=(u.get("cost") or {}).get("total_usd"), model_usd=(u.get("cost") or {}).get("model_usd"), browser_usd=(u.get("cost") or {}).get("browser_usd"),
                     traffic_usd=(u.get("cost") or {}).get("traffic_usd"), traffic_mb=u.get("browser_traffic_mb"),
                     in_tok=u.get("input_tokens"), cached=u.get("cached_input_tokens"), out_tok=u.get("output_tokens")))
rows.sort(key=lambda r: (r["variant"], ORDER.get(r["tier"], 9), r["run"]))
md = ["| variant | tier | run | content score | schema valid | fake vendor handled | confident-but-wrong rows | steps | model calls | wall time | cost (USD) | input tokens (cached) | output tokens |",
      "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
for r in rows:
    md.append(f"| {r['variant']} | {r['tier']} | {r['run']} | {r['content']}{(' (+%d left null)' % r['abstained']) if r['abstained'] else ''} | {'yes' if r['schema_valid'] else 'NO (%d problems)' % r['schema_problems']} | {'yes' if r['fake_ok'] else 'NO'} | {r['overconfident']} | {r['steps']} | {r['calls']} | {r['seconds']/60:.1f} min | ${r['cost']:.3f} | {r['in_tok']:,} ({r['cached']:,}) | {r['out_tok']:,} |")
tot = sum(r["cost"] or 0 for r in rows)
traffic = [r for r in rows if r["traffic_usd"] is not None]
md += ["", f"Total reported cost of these {len(rows)} runs: ${tot:.2f} (model + browser time; browser traffic is billed separately at $5/GB and is reported only hours later: "
       + (f"reported for {len(traffic)} of {len(rows)} runs so far)." if traffic else "not yet reported for any run).")]
os.makedirs(os.path.join(HERE, "..", "results"), exist_ok=True)
open(os.path.join(HERE, "..", "results", "research_task.md"), "w").write("\n".join(md) + "\n")
json.dump(rows, open(os.path.join(HERE, "..", "results", "research_summary.json"), "w"), indent=1)
print("\n".join(md))
