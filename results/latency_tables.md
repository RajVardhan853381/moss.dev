## Network floor (GET /v1/models from the sandbox, same path as the tests)

| connection | n | p50 ms | p95 ms | max ms |
|---|---|---|---|---|
| keep-alive | 60 | 137 | 165 | 240 |
| new connection each time | 20 | 137 | 177 | 438 |

## Time to first token: 4 models x {plain, grounded}, unique prompts, interleaved randomly

| condition | n ok | err | cache hits | TTFT p50 (95% CI) | p90 | p95 | p99 | max | p50 minus floor | retrieve ms p50 / p95 | input tokens (median) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| anthropic/claude-haiku-5.5 / grounded | 100 | 0 | 0 | 537 (523-557) | 834 | 884 | 1065 | 1092 | 401 | 11 / 15 | 1202 |
| google/gemini-3.5-flash-lite / grounded | 100 | 0 | 0 | 643 (628-659) | 745 | 774 | 839 | 1050 | 506 | 11 / 14 | 882 |
| moss/fastlane-1 / grounded | 100 | 0 | 0 | 292 (286-302) | 355 | 372 | 586 | 692 | 155 | 11 / 14 | 1083 |
| openai/gpt-6-luna / grounded | 100 | 0 | 0 | 698 (673-723) | 950 | 1062 | 1919 | 2005 | 561 | 12 / 15 | 834 |
| anthropic/claude-haiku-5.5 / plain | 100 | 0 | 0 | 492 (463-527) | 846 | 970 | 1177 | 1309 | 355 | - | 73 |
| google/gemini-3.5-flash-lite / plain | 100 | 0 | 0 | 598 (585-615) | 671 | 696 | 752 | 888 | 461 | - | 54 |
| moss/fastlane-1 / plain | 100 | 0 | 0 | 210 (207-215) | 243 | 259 | 295 | 459 | 73 | - | 145 |
| openai/gpt-6-luna / plain | 100 | 0 | 0 | 575 (566-590) | 717 | 780 | 920 | 1113 | 438 | - | 57 |

### Cost of grounding (median TTFT grounded minus plain, with 95% bootstrap CI)

| model | delta ms | 95% CI |
|---|---|---|
| anthropic/claude-haiku-5.5 | +46 | +9 to +81 |
| google/gemini-3.5-flash-lite | +45 | +25 to +66 |
| moss/fastlane-1 | +82 | +75 to +93 |
| openai/gpt-6-luna | +124 | +91 to +149 |

## Control run: same design, n=40, no research tasks running (the main run overlapped with them)

| condition | main run p50 / p95 (n=100) | control p50 / p95 (n=40) | p50 difference |
|---|---|---|---|
| anthropic/claude-haiku-5.5 / grounded | 537 / 884 | 515 / 862 | -22 |
| google/gemini-3.5-flash-lite / grounded | 643 / 774 | 619 / 735 | -24 |
| moss/fastlane-1 / grounded | 292 / 372 | 246 / 293 | -45 |
| openai/gpt-6-luna / grounded | 698 / 1062 | 717 / 951 | +19 |
| anthropic/claude-haiku-5.5 / plain | 492 / 970 | 478 / 802 | -13 |
| google/gemini-3.5-flash-lite / plain | 598 / 696 | 570 / 654 | -28 |
| moss/fastlane-1 / plain | 210 / 259 | 208 / 231 | -2 |
| openai/gpt-6-luna / plain | 575 / 780 | 589 / 1365 | +15 |

## Reply cache: classified by what the API reported (usage.input_tokens == 0 means hit)

Each prompt was sent 3 times back-to-back. For moss/fastlane-1 some requests were rate-limited (429) because the experiment exceeded the documented 120/min and burst-10 limits; those are excluded from TTFT and counted here.

| model | requests | 429s | misses: n, TTFT p50 / p95 ms | hits: n, TTFT p50 / p95 ms | repeats that were hits |
|---|---|---|---|---|---|
| anthropic/claude-haiku-5.5 | 90 | 0 | 80, 483 / 1015 | 10, 141 / 172 | 17% (10/60) |
| moss/fastlane-1 | 90 | 31 | 30, 194 / 207 | 29, 144 / 184 | 71% (29/41) |

## Overlapping requests (moss/fastlane-1, grounded, ~1.5 req/s, under the documented limit)

| workers | n ok | 429s | TTFT p50 | p95 | p99 | max | median in-flight at start |
|---|---|---|---|---|---|---|---|
| 1 | 60 | 0 | 252 | 354 | 482 | 622 | 1 |
| 3 | 60 | 0 | 242 | 331 | 385 | 387 | 1 |

## Prompt length (moss/fastlane-1, ungrounded, unique random text)

| target tokens | actual input tokens (median) | n | TTFT p50 | p95 | max |
|---|---|---|---|---|---|
| 200 | 292 | 12 | 193 | 209 | 219 |
| 2000 | 2068 | 12 | 317 | 384 | 399 |
| 8000 | 7945 | 12 | 612 | 631 | 632 |
| 32000 | 31480 | 10 | 2242 | 2283 | 2302 |
| 120000 | 117964 | 5 | 10994 | 11030 | 11034 |

Shared 32k-token prefix, 10 different questions: first call TTFT 2246 ms; calls 2-10 median 269 ms, median cached tokens 31200.0.

## First request after idle (retrieval index: moss-docs-md)

| idle gap | what the client did first | GET /models ms | 1st query TTFT ms | 2nd query TTFT ms | 1st retrieve ms |
|---|---|---|---|---|---|
| 10s | query directly | - | 386 | 259 | 12.7 |
| 10s | GET /models, then query | 157 | 252 | 261 | 13.6 |
| 60s | query directly | - | 608 | 300 | 13.9 |
| 60s | GET /models, then query | 420 | 272 | 264 | 17.9 |
| 300s | query directly | - | 586 | 249 | 14.7 |
| 300s | GET /models, then query | 441 | 271 | 259 | 13.2 |

Estimated spend of these experiments (token prices from the docs + $0.50/1k retrievals): $0.82
