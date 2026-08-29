# R3 — headless round: agy sage-off vs sage-on vs Opus 5

Date: 2026-08-29. Task: `kysely-window-grouping-helpers` (same task as r2/arm3/arm4 series).
Same brief byte-exact for all three arms. Grading: held-out `test.patch` + official verifier
recipe replicated locally (mocha + mocha-ctrf-json-reporter, base/new modes, node-id
whitelists from tests/config.json, names whitespace-normalized).

## Mode change caveat (read before comparing to r2)

R2/arm3/arm4 ran in the orca TUI. R3 ran **headless** (`agy -p`, print mode) because the
orca `--split` spawn path cannot adopt unregistered /tmp worktrees (spawns land in the
active pane's worktree and the DONE detector matches the injection echo). Cross-round turn
counts are NOT comparable; within-round comparison is.

## Results

| arm | model | sage | f2p | p2p | reward | partial | turns | wall |
|---|---|---|---|---|---|---|---|---|
| r3a-medium-off | Gemini 3.7 Flash (Medium) | OFF | 254/254 | 22/22 | 1.0 | 1.0 | 72 | ~5 min |
| r3d-medium-on | Gemini 3.7 Flash (Medium) | ON | 250/254 | 22/22 | 0.0 | 0.992 | 173 | ~38 min |
| opus-r3 | Claude Opus 5 | n/a | 254/254 | 22/22 | 1.0 | 1.0 | 147 | ~35 min |

## Findings

1. **This round does NOT show sage improving the worker.** Sage-ON used 2.4x more turns
   (173 vs 72) and failed binary reward on a single-root-cause defect (GROUPING SETS SQL
   compilation mismatch, same assertion across 4 dialects; partial credit 0.992). Sage
   steering did fire mid-run (transcript row 34) and the run completed honestly, but the
   extra turns did not buy correctness here.
2. **Plain agy (72 turns) beat Opus 5 (147 turns) at reward 1.0** on the identical brief —
   the Flash worker at Medium effort is roughly 2x more turn-efficient than Opus 5 on this
   task, at a fraction of the tokens (Opus: 17.7M total incl. 17.3M cache-read).
3. **Harness bug found & fixed mid-round:** the dispatcher's hardcoded `--print-timeout 10m`
   killed the first sage-ON attempt at exactly 10 min (printmode.go:521) and the tracked-file
   edits were lost with the aborted session; untracked new files survived. Rerun with a 60m
   wrapper copy. Any future headless benchmark must override print-timeout.
4. **Prior sage-ON turn reduction (r2: off 201 vs on 165/154) did not replicate** in the
   headless mode. Consistent with the Opus review's demand: instrument the turn counter and
   pin mode/model/version before making sage claims.

## Provenance

- Repo state: 274a441 + uncommitted sage/workers.py + tests/test_worker_facts.py (839 tests pass).
- agy CLI 1.1.22, model "Gemini 3.7 Flash (Medium)", `--dangerously-skip-permissions`.
- Brain transcripts: r3a f061f669, r3d 98552391 (first attempt 00080ed2, timeout-killed).
- Claude session: f0559a89-7e16-43c8-aa09-6309a50b1cee.
- Evidence dirs: /tmp/deepswe_harness/runs/{r3a-medium-off,r3d-medium-on,opus-r3}.

---

# R4 addendum — same task, after the small optimizations (2026-08-29)

Post-r3 changes merged: directive-tone delegation doctrine, no force-continue stop on healthy
background waits, sage read-only MCP tools, --model shorthand normalization, 60m print-timeout harness.

| arm | sage | f2p | p2p | reward | partial | turns | wall |
|---|---|---|---|---|---|---|---|
| r4a | OFF | 254/254 | 22/22 | 1.0 | 1.0 | 182 | 8.8 min |
| r4b | ON | 250/254 | 22/22 | 0.0 | 0.992 | 40 main + 436 in 3 subagents | 20.4 min |

Findings:
1. **Behavior change is real**: r4b is the first observed delegation conversion — the agent fanned
   out to 3 parallel subagents (r3d ran 173 serial turns and ignored the nudge). Wall time halved
   (38 -> 20.4 min) despite ~476 total turns (parallelism hides them).
2. **Binary reward unchanged**: the same single-root-cause GROUPING SETS compile assertion missed
   across 4 dialects, identical to r3d. Across rounds: OFF 2/2 pass, ON 0/2 pass on binary reward.
   The ON runs churn more and end with the exact-string mismatch unrepaired.
3. **OFF baseline variance is dominant**: 72 (r3a) vs 182 (r4a) turns on the identical task+model
   label. n=1 turn counts are noise; no overhead-reduction claim is possible from main-turn drops
   (173 -> 40) without counting the 436 subagent turns.
4. Next leverage: the remaining failure is ONE precise string assertion — the MCP self-verify loop
   (sage reads the failing junit/CTRF itself, orders the exact fix) targets exactly this.

---

# R5 addendum — first round with the typed delegation taxonomy (2026-08-29)

| arm | sage | f2p | p2p | reward | turns | wall |
|---|---|---|---|---|---|---|
| r5a | OFF | 254/254 | 22/22 | 1.0 | 168 | 11 min |
| r5b | ON (typed taxonomy) | 0/254 | 22/22 | 0.0 | 91 (solo) | 22 min |

1. r5b did NOT fan out (solo run) — new failure mode is type-conformance: the agent's API accepted
   call shapes the reference rejects (unused @ts-expect-error -> tsc fails -> scored test never built).
2. Cross-round: OFF 3/3 binary pass vs ON 0/3 (r3d, r4b, r5b). One-sided Fisher p = 0.05.
   Small n, but the sage-ON-correlates-with-failure pattern is now at the significance boundary.
3. Grading harness note: r5b committed its own window-frame.test.ts; graders must restore the base
   tree / remove agent-committed test files before applying the held-out patch (grade.py's
   subprocess_apply already does this; the local verifier flow must too).
4. Next diagnostic (before more A/B rounds): read the ON transcripts to see whether sage steering
   (stop-gate blocks, review-gate, QA mandates) correlates with stopping before held-out
   conformance is checked — the OFF runs iterate until the held-out-shaped strings/types match.

---

# R6 addendum — first round after the spec conformance sweep (2026-08-29)

Diagnosis that drove the change: r5b's blind review passed a non-conformant implementation because
it verified the agent's PARAPHRASED DoD, never the original spec's reject-clauses ("accepts
number|bigint, not reference expressions"). Fix: Spec Conformance Sweep in the final gate + review
contract (verify the ORIGINAL request clause-by-clause, negative case per accept/reject clause).

| arm | sage | f2p | p2p | reward | partial | turns |
|---|---|---|---|---|---|---|
| r6a | OFF | 254/254 | 22/22 | 1.0 | 1.0 | 180 |
| r6b | ON (conformance sweep) | 218/254 | 22/22 | 0.0 | 0.929 | 197 (solo) |

1. The sweep measurably fixed the compile layer: build is clean, the type-rejection conformance
   that killed r5b (0/254) passes — ON same-task trajectory 0/254 -> 218/254, partial 0.5 -> 0.929.
2. Remaining failures cluster in RUNTIME semantics (lag/lead offsets/defaultValue must be emitted
   as query parameters; bigint bucket acceptance) plus the persistent GROUPING SETS 4-dialect
   cluster — inspected-but-not-executed negative cases still slip through.
3. Cross-round binary: OFF 4/4 vs ON 0/4 (one-sided Fisher ~0.014). However the ON failure LAYER
   changed every round (string -> type -> runtime), i.e. each general fix moved the frontier —
   convergence-in-progress, not noise. Next general lever: review contract must EXECUTE negative
   cases (run them), not verify their existence.
