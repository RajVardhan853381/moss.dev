"""Assemble results/RESULTS.md from results/RESULTS_narrative.md + generated tables.
Placeholders in the narrative:
  {{latency:<start of a '## ' heading in latency_tables.md>}}  -> that section's body
  {{accuracy}}                                                  -> results/accuracy.md
  {{research}}                                                  -> results/research_task.md
Refuses to build if any '<<FILL' marker is left."""
import os, re, sys
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
narr = open(os.path.join(R, "RESULTS_narrative.md")).read()
lat = open(os.path.join(R, "latency_tables.md")).read()
sections = {}
for chunk in re.split(r"(?m)^## ", lat)[1:]:
    head, _, body = chunk.partition("\n")
    sections[head.strip()] = body.strip("\n")


def lat_sub(m):
    key = m.group(1)
    for h, body in sections.items():
        if h.startswith(key):
            return "\n".join(l for l in body.split("\n") if not l.startswith("Estimated spend")).strip()
    raise SystemExit(f"no latency section starting with {key!r}")


out = re.sub(r"\{\{latency:([^}]+)\}\}", lat_sub, narr)
out = out.replace("{{accuracy}}", open(os.path.join(R, "accuracy.md")).read().split("\n\nQuestions missed")[0].strip())
out = out.replace("{{research}}", open(os.path.join(R, "research_task.md")).read().strip())
if "<<FILL" in out and "--allow-fill" not in sys.argv:
    left = re.findall(r"<<FILL[^>]*>>", out)
    raise SystemExit(f"unfilled markers: {left}")
open(os.path.join(R, "RESULTS.md"), "w").write(out)
print("wrote results/RESULTS.md", len(out.split()), "words")
