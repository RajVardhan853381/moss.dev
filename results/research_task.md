| variant | tier | run | content score | schema valid | fake vendor handled | confident-but-wrong rows | steps | model calls | wall time | cost (USD) | input tokens (cached) | output tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| easy | moss-fast | moss-fast_1 | 30/30 | yes | yes | 0 | 22 | 27 | 4.4 min | $0.037 | 570,458 (509,967) | 20,502 |
| easy | moss-balanced | moss-balanced_1 | 30/30 | yes | yes | 0 | 23 | 24 | 4.4 min | $0.340 | 736,690 (683,776) | 8,242 |
| easy | moss-advanced | moss-advanced_1 | 30/30 | yes | yes | 0 | 36 | 37 | 5.8 min | $0.540 | 1,575,289 (1,503,444) | 11,948 |
| hard | moss-fast | moss-fast-hard_1 | 24/24 | NO (20 problems) | yes | 0 | 41 | 64 | 10.0 min | $0.134 | 2,246,993 (1,843,540) | 60,558 |
| hard | moss-fast | moss-fast-hard_2 | 23/24 (+1 left null) | yes | yes | 0 | 19 | 26 | 4.1 min | $0.041 | 456,125 (397,935) | 29,888 |
| hard | moss-fast | moss-fast-hard_3 | 24/24 | yes | yes | 0 | 21 | 28 | 5.2 min | $0.043 | 624,419 (558,101) | 27,234 |
| hard | moss-balanced | moss-balanced-hard_1 | 24/24 | yes | yes | 0 | 30 | 31 | 5.0 min | $0.396 | 950,927 (901,228) | 11,513 |
| hard | moss-balanced | moss-balanced-hard_2 | 24/24 | yes | yes | 0 | 30 | 31 | 4.9 min | $0.410 | 939,014 (883,204) | 11,369 |
| hard | moss-advanced | moss-advanced-hard_1 | 24/24 | yes | yes | 0 | 30 | 31 | 4.9 min | $0.421 | 947,933 (894,220) | 12,693 |
| hard | moss-advanced | moss-advanced-hard_2 | 24/24 | yes | yes | 0 | 30 | 31 | 4.6 min | $0.409 | 1,033,271 (978,388) | 10,581 |

Total reported cost of these 10 runs: $2.77 (model + browser time; browser traffic is billed separately at $5/GB and is reported only hours later: not yet reported for any run).
