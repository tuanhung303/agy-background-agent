# Sage A/B execution-time & progress ledger

| round | reward | turns | wall | note |
|---|---|---|---|---|
| r3a OFF | 1.0 | 72 | ~5 | — |
| r3d ON | 0.0 (0/254 string) | 173 | 38 | first fanout attempt lost to 10m print-timeout |
| r4a OFF | 1.0 | 182 | 8.8 |  |
| r4b ON | 0.0 (218->250 type) | 40+436 sub | 20.4 | first real fanout, 3 subagents |
| r5a OFF | 1.0 | 168 | 11 |  |
| r5b ON | 0.0 (0/254 type) | 91 solo | 22 | review leg ran, passed falsely |
| r6a OFF | 1.0 | 180 | 11 |  |
| r6b ON | 0.0 (218/254 runtime) | 197 solo | ~22 | conformance sweep fixed compile layer |

ON wall trend: 38 -> 20.4 -> 22 -> ~22 min; ON fail layer moved string -> type -> runtime each fix (converging).
Generated 2026-08-29; update each round.