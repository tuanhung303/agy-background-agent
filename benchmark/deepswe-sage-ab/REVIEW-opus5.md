Read the plan. Peer file `/tmp/agy/deepswe-review/codex.md` contains only a failed Codex run (unsupported model), so no cross-check available. Host measured: 11 cores, 36 GB, load avg 4.75/6.09/6.54 with 28 active sessions — relevant to Q3.

---

**1. Minimum defensible sample size**

Verdict: n=2 can never reach significance — perfect 2-vs-2 separation gives one-sided Fisher p=0.167. The floor for *any* claim is n=3 (3/3 vs 0/3 → p=0.05); a realistic 100%-vs-60% contrast needs ~n=20/arm unpaired. Don't buy repeats. Power-per-cost ranking: **(1) calibrate task difficulty to ~50% baseline** — Medium/OFF is pinned at 2/2, so you have zero measurement range and repeats add literally nothing; free to fix, biggest gain. **(2) Paired tasks** (identical task/base/prompt, sage toggled), analyzed with McNemar on discordant pairs — removes task-difficulty variance, which dominates. **(3) Partial credit** (Q5) converts binary→continuous, cutting required n ~3–5×. **(4) More repeats** — last. Minimum defensible: **8–10 paired tasks × 1 run/arm** at calibrated difficulty, reported with McNemar exact + Agresti-Caffo CI. Below that, label it "pilot". Never claim "neither" — equivalence at 10 pairs has a ±30pp CI.

**2. The task confound / completing the 2×2**

Verdict: drop arm1/arm2 now; run High×kysely only after difficulty is fixed. The koota runs contribute zero comparable information — different task, structurally different grading config (199 vs 276 graded), both saturated at 1.0. A footnote does not neutralize a task confound; the honest table today is four Medium kysely runs. Cost of dropping: zero. Cost of High×kysely both sage states: ~2 runs, 30–40 min wall — trivial. But expected value is low: High solved the *easier* task at 1.0, and Medium already solves kysely at 1.0 in 3/4 runs, so all four cells will pin at 1.0 and the tier×sage interaction stays unmeasurable. Spend that 40 minutes calibrating a harder task instead. If you publish a 2×2 for symmetry, run it — but make turns/tokens the primary outcome, since reward carries no signal at ceiling.

**3. Parallelization ceiling and harness changes**

Verdict: the agent phase scales to 20+ (API-bound, near-zero local CPU); the grading phase caps you at **4 safe / 6 degraded / 8 hard wall**. Reserve 2 cores + 8 GB for OS/Orca/existing load ~5; each worker needs ~1 sustained core and 2–3 GB peak during tsc+mocha. Critical: concurrent grading inflates wall 1.4–1.8× and thermally throttles, **corrupting your headline wall metric** — serialize grading behind a `flock` semaphore (concurrency 2) while agents run wide. Required changes: per-worker `store-dir`, or pre-warm the pnpm store and install `--frozen-lockfile --prefer-offline`; APFS `cp -c` clonefile a hydrated `node_modules` template instead of N installs; `git worktree add --reference` instead of full clones; per-worker ports + DB names if any dialect test hits a real server; audit the verifier for fixed absolute paths (a shared `/tmp` CTRF file silently overwrites rewards); per-spawn sage *output* paths, not just the env gate; `ulimit -n 4096`.

**4. Methodology gaps to fix before publishing**

Verdict: six blockers. (a) **No variance reporting** — publish n per cell and a Wilson CI, or state "no CI computable". (b) **Mixed tasks in one table** — split or drop. (c) **Reproducibility identifiers** — "Medium tier" is not a model id; pin and publish agy version, model snapshot, temperature, system-prompt hash, tool set, base SHA, and the initial prompt bytes. (d) **Grader tamper-resistance** — the agent edits the repo, and canonicalized mocha node-ids move when `describe` titles change; restore the test tree from base before applying test.patch, fail loudly if it doesn't apply cleanly, assert graded count == 276 exactly, and double-grade one worktree for bit-identical CTRF. (e) **Contamination** — kysely is public and GROUPING SETS may exist upstream pre-cutoff; check and disclose. (f) **Prompt-in-brief bias** — naming SimplifyFramePlugin/eb.fn hands over the design; publish the brief verbatim. Add a token/$ cost axis.

**5. Binary vs partial credit**

Verdict: keep binary as the headline (it is the convention and it means "did it work"), but it is inadequate alone — arm4 proves it. arm4 scored 0.0 for a stray space that broke 4 of 254 assertions: binary reports total failure, reality is 99.98% correct with a cosmetic defect. That collapse is a main source of your lost power. The single most informative addition: **end-of-run f2p pass fraction (250/254 = 0.984)**, reported beside binary. It's free — already in the CTRF file — continuous, and cuts required sample size ~3–5× at the same effect size. Never promote it to headline; it's gameable. If you take a second metric, use **distinct failing-assertion clusters**: arm4's 4 failures are *one* root cause replicated across dialect suites, so raw failing-test counts overweight whichever assertion happens to be shared. Report "4 failures / 1 distinct cause".

**6. Suspicious items worth re-verifying**

Verdict: five, ranked. (1) **arm1/arm2 P2P = 128 vs the task's 22** — a P2P set 6× the F2P set is unusual; confirm columns aren't swapped or mislabeled. (2) **arm4 = 34 turns vs arm3 = 141**, and r2b = 44 vs r2a ~120+ — sage-ON shows a consistent 3–4× turn reduction. Either sage truncates runs prematurely (making the whitespace bug a symptom of not iterating on test output — your most interesting finding) or the counter under-counts when sage is active. Resolve before any sage claim. (3) **arm3 ~25m vs r2a ~13m, same cell, both 1.0** — 2× wall variance; if arms overlapped in time on this loaded host, every wall number is invalid. Check overlap. (4) **Tildes and approximations** in a results table — use logged values or drop the columns. (5) **arm4 reward 0.0 with P2P 22/22** — confirm reward is exactly `all_f2p AND all_p2p`. (6) 5/6 runs at 1.0: no measurement range.

---

**Priority actions**

1. Drop arm1/arm2 from the table and re-label the remainder a 4-run Medium pilot — zero cost, removes the fatal confound.
2. Add end-of-run f2p pass fraction (already in CTRF) plus token cost to every row — one parser change, restores measurement range.
3. Explain the sage turn-count gap (34/44 vs 141/120+): instrument the counter and read arm4's tail transcript — this is the actual result.
4. Harden the grader: restore tests from base, assert 276 graded, per-worker CTRF paths, double-grade determinism check.
5. Calibrate a ~50%-baseline task set and run 8–10 paired tasks with grading serialized behind a semaphore; only then publish numbers.

計画通り: review complete
