# Moss Inference: work sample

Everything in this repository comes from live runs against Moss Inference across the Responses and Chat Completions endpoints, the Tasks API, and the index-management API. All raw request and response logs are saved in `logs/`, with project IDs and API keys stripped out.

| Start here | What it is |
|---|---|
| [`ONE_PAGER.md`](ONE_PAGER.md) / [`.pdf`](ONE_PAGER.pdf) | The one-page write-up (portal / Inference API / Tasks API) |
| [`results/RESULTS.md`](results/RESULTS.md) | Full write-up: method, tables, charts, findings, gaps, and suggested fixes |
| [`results/ttft_by_model.png`](results/ttft_by_model.png) | Time to first token, 4 models x plain/grounded |
| [`results/ttft_vs_prompt_length.png`](results/ttft_vs_prompt_length.png) | Time to first token vs prompt length |
| [`results/latency_tables.md`](results/latency_tables.md) | Latency tables (floor, TTFT, cache, idle, concurrency, prompt size) |
| [`results/accuracy.md`](results/accuracy.md) | Grounding accuracy evaluation |
| [`results/research_task.md`](results/research_task.md) | Tasks API research task: 10 runs graded against ground truth |

## Headline numbers (client-side, unique prompts)

* `moss/fastlane-1` hit first token in 210 ms median plain and 246-292 ms grounded across two runs (vendor models behind the same endpoint took 490-700 ms against a 137 ms network baseline).
* Index retrieval (`X-Moss-Retrieve-Ms`) took 11 ms median on 129 chunks, though the very first query on a brand-new index spent 815 ms.
* Grounding pushed factual accuracy from 0% to 85%, with every missed answer saying `"I do not know"`.
* Research tasks scored 30/30 (easy) and 24/24 (hard) across all tiers. `moss-fast` ran ~10x cheaper, though 1 of its 6 runs returned invalid output schema while still ending as `completed`.

## Layout

```
scripts/
  mosslat.py             keep-alive streaming client + timing + cost tally
  latency_study.py       baseline | main | concurrency | promptsize | cachehit   (--tag _control for the control run)
  idle_gap.py            first request after idle (separates reconnect from model/retrieval)
  behavior_checks.py     reply-cache key + staleness, freshness probe, cold index, stream formats, doc-vs-reality, burst/429
  grounding_accuracy.py  23 factual questions + 3 unanswerable controls, 4 configurations
  crawl_tests.py         was the 1-page crawl my configuration? (no: 3 settings tried)
  research_task.py       the 6-vendor (easy) and 9-vendor (hard) research tasks on the Tasks API
  pypi_truth.py / grade_research.py   ground truth from PyPI JSON + live GitHub pages, and the grader
  analyze_latency.py / make_charts.py / summarize_research.py / build_results.py / make_onepager_pdf.py
  common.py, inference_tests.py, tasks_tests.py   first-pass checks (validation, idempotency, cancel, queue)
research/                ground truth used to grade the research task
logs/                    raw JSONL / text logs of every run
results/                 tables, charts, write-ups (RESULTS.md is generated from RESULTS_narrative.md + the tables)
```

## Reproduce

```bash
export MOSS_AUTH="<project_id>:<project_key>"   # sent as a Bearer token; never written to logs
python3 scripts/latency_study.py baseline && python3 scripts/latency_study.py main --n 100 --index <your-index>
python3 scripts/analyze_latency.py && python3 scripts/make_charts.py
```

The index-management calls (`behavior_checks.py probe|fresh|coldindex`, `crawl_tests.py`) also need `MOSS_PROJECT_ID` and `MOSS_PROJECT_KEY`.

## Scope and limits

* Timings are client-side from a remote Linux host routing through an HTTPS proxy, so they include network transit. I timed the baseline round-trip over the same path and report both since Moss doesn't expose server-side TTFT.
* Everything ran on one account in one region over one afternoon. Tail percentiles past p95 are noisy at these sample sizes, and grounded fastlane drifted by roughly +-25 ms between runs.
* Each tier cell in the research task only has 1 to 3 runs, so treat tier comparisons as rough signals.
* Several early findings needed fixes as I tested further: an initial latency run that hit the reply cache, an apparent cache bug that turned out to be index-refresh lag, and four adjustments to my research grader. Each correction is explained in `results/RESULTS.md`, with the original logs kept intact.
* Browser-traffic charges ($5/GB) hadn't posted yet when I finished; the $2.77 total covers model tokens and browser runtime across the 10 runs.
