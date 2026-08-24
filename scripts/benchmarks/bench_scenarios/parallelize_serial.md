# Bench scenario: parallelize trigger (wave 3)

Worker prompt (independent costly legs, NO ordering mandate — serial-ness must be
the worker's own choice so advising parallelize is legitimate. v1 lesson: explicitly
forbidding parallelization makes sage holds CORRECT, since advice would contradict
an explicit user constraint):

```
Benchmark worker. Work ONLY inside this directory. Create FOUR independent modules
create four independent modules - fib.py, roman.py, brackets.py, caesar.py - each
with its own unittest suite including a live performance probe >= 20 seconds (sum of
primes up to 3_000_000, timed). Run every suite for real. Write SUMMARY.md at the end
with per-module timings. Real commands only, no mocks.
```

Expected sage behavior:
- After leg 2-3, ~60s+ of serial evidence accumulates -> `watchout`,
  `category: parallelize`, action naming `invoke_subagent` per remaining leg.
- Dedup: identical advice suppressed on repeat; escalation only via ignored_advice.

Observe via: statusline (`adv:f[N]`), `/tmp/agy_sage.log` or `/tmp/agy_stop_audit.log`
(`watchout emitted`, `Sage prompt mode`), transcript for injected `※ sage:` line.

Note: firing is model judgment, not guaranteed. A silent hold on healthy serial work
is a correct outcome; the scenario exists to give the category its chance and to catch
false-positive parallelize advice on coupled work.

Wave-2 result (v1 prompt): 5/5 correct holds — sage refused to advise parallelize
against an explicit "do NOT parallelize" instruction. Constraint-respect validated;
keep this version for firing validation.
