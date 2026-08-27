<h1 align="center">agy-background-agent</h1>

<p align="center">
  A background supervisor for Antigravity coding agents.<br>
  Tracks execution steps, prevents premature completion, and flags drift.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/harness-Antigravity%20(AGY)-0F766E?style=flat-square" alt="Antigravity">
  <img src="https://img.shields.io/badge/python-%E2%89%A53.10-1D4ED8?style=flat-square" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/tests-748%2B-15803D?style=flat-square" alt="748+ tests">
  <img src="https://img.shields.io/badge/deps-zero-6D28D9?style=flat-square" alt="Zero deps">
</p>

---

Fast agents fail when they enter repetitive error loops, drift from task requirements, or stop early on mock tests. This repository provides Antigravity hooks that run beside the worker agent to catch these failures.

<p align="center">
  <img src="assets/architecture.svg" alt="agy-background-agent hook architecture" width="100%">
</p>

## Benchmark: sage ON/OFF on DeepSWE-style long-horizon tasks

This benchmark measures whether the background supervisor improves agy workers on long-horizon coding tasks across multiple repositories in the DeepSWE-bench suite. Workers run Gemini 3.7 Flash in isolated worktrees. Setting `AGY_SAGE_DISABLED=1` via `--sage-off` unplugs the background supervisor per spawn without mutating shared configuration files.

Tasks evaluated:
1. `koota-deferred-mutation-buffer`: 71 fail-to-pass and 128 pass-to-pass tests in pmndrs/koota (TypeScript / Vitest). Requires implementing a deferred command buffer across entity, trait, relation, and query subsystems.
2. `kysely-window-grouping-helpers`: 254 fail-to-pass and 22 pass-to-pass tests in kysely-org/kysely (TypeScript / Mocha). Requires byte-exact SQL compilation across PostgreSQL, MySQL, MSSQL, and SQLite dialects.

<p align="center">
  <img src="assets/benchmark-deepswe.svg" alt="Benchmark: Sage ON/OFF on DeepSWE-style long-horizon tasks" width="100%">
</p>

### Findings

- On `koota-deferred-mutation-buffer`, Sage ON completed in 131 planner turns compared to 148 turns for Sage OFF (17 fewer turns). Sage actively diagnosed a re-entrancy recursion bug during flush operations, helping the worker recover to 69/71 F2P (97.2%) and 122/128 P2P.
- On `kysely-window-grouping-helpers`, all runs that included byte-exact verification reached a 1.0 reward across both supervisor states (arm3, r2a, r2b, r2c). The arm4 run missed binary reward on a single whitespace syntax bug in emitted SQL across dialect suites despite passing 250 of 254 assertions (98.4% pass fraction).
- The generalized harness grades both suites against held-out test patches using official JUnit and CTRF matching frames.

Methodology review and harness architecture details are documented in [REVIEW-opus5.md](benchmark/deepswe-sage-ab/REVIEW-opus5.md).

## Dogfooding: sage debugging its own timeout bug

A live-failure retest loop: when sage itself hit a runtime defect in a real session, the coordinator spawned two orca agy workers against the same incident to test both the diagnosis quality and the fix quality — with and without sage supervising.

### Incident

In a long-running design session (13+ prior requests), sage's model cascade timed out:
`sage/executor.py` gave candidate 1 (`Gemini 3.7 Flash High`) 30s of its 38s total budget, leaving fallback candidates 5s each. Both timed out; final gate degraded to error and allowed termination without a verdict.

### Setup

Two arms ran in parallel split panes on identical context (real `/tmp/agy_sage.log` excerpt, affected conversation ID):

- **Arm RC (sage ON)**: investigate root cause only, cite exact `sage/*.py` lines, no code changes.
- **Arm FIX (sage OFF)**: same context plus instructions to patch the root cause directly with unit tests.

### Results

| | Arm RC (sage ON) | Arm FIX (sage OFF) |
|---|---|---|
| Root cause found | Cascade budget math (`executor.py:176`, `config.py:107`) **plus** the deeper driver: unbounded `SESSION HISTORY` prompt growth (`transcript.py:89`) exceeding the `user_prompt[:2000]` slice that drops the active goal | Same cascade math; independently identified the same unbounded history growth |
| Deliverable | Diagnosis + 3 ranked proposals | Working patch: sliding-window session history (`MAX_PRIOR_REQUESTS=5`, keeps request 1 + latest), goal-preservation rework in `build_sage_prompt`, 6 new unit tests |
| Verification | Line-cited claims matching the codebase | 761/761 pytest green before handoff |

The two arms converged on the same root cause through different routes — cascade budget arithmetic from log evidence, and prompt-bloat economics from reading the transcript builder. Sage ON produced the more complete *explanation*; sage OFF produced the shippable *fix*. Coordinator reviewed the diff and merged it as the basis for the repair.

### Takeaways

1. **Ambiguity handling held**: given an open-ended "find the root cause" brief, neither worker scope-laundered into a cheap proxy (mock test edits, doc-only writeups). The interpretation-receipt gate contributed visible pin reasoning.
2. **Sage steering stayed useful without grabbing the wheel**: injections were pinned-goal framing and a recap check — no false off_track interrupts during a healthy investigation arc.
3. **Same-result-different-artifact**: when both arms agree on cause but only one produces runnable change, run the two-arm pattern again for any class of bug that is diagnosable separately from fixable.

## Benchmark: sage ON/OFF on Terminal-Bench 2.1 (LLM Inference Batching Scheduler)

A benchmark on the hardest algorithmic systems optimization task in Terminal-Bench 2.1: `llm-inference-batching-scheduler`. The task requires packing 1,600 inference requests across two buckets into batches while adhering to hardware granularity alignment (multiple of 64 tokens), a hard limit of at most 8 unique tensor shapes across both buckets, and beating 4 strict analytical cost and latency thresholds.

### Setup

Two workers ran in parallel Orca split panes via `orca-agy.sh` on Gemini 3.7 Flash:
- **Arm 1 (Sage ON - `tb-sched-on`)**: Supervised with active goal pinning and step verification.
- **Arm 2 (Sage OFF - `tb-sched-off`)**: Unsupervised baseline (`--sage-off`).

### Results

| Metric | Threshold | Baseline Packer | Arm 2: Sage OFF | Arm 1: Sage ON | Delta (Sage ON vs OFF) |
|---|---|---|---|---|---|
| **Bucket 1: Cost** | $\le 3.0 \times 10^{11}$ | $2.4830 \times 10^{12}$ | $2.9445 \times 10^{11}$ | **$2.8765 \times 10^{11}$** | **-2.3% cheaper (-6.80B)** |
| **Bucket 1: Pad Ratio** | $\le 0.055$ | $1.4363$ | **$0.05093$** | $0.05134$ | +0.8% |
| **Bucket 1: P95 Latency (ms)** | $\le 2.1 \times 10^6$ | $1.3157 \times 10^7$ | $2.0434 \times 10^6$ | $2.0434 \times 10^6$ | Parity |
| **Bucket 1: Sequential Timecost (ms)** | $\le 2.7 \times 10^8$ | $4.8973 \times 10^7$ | $2.6788 \times 10^8$ | **$2.3102 \times 10^8$** | **-13.8% faster (-36.86M ms)** |
| **Bucket 2: Cost** | $\le 4.8 \times 10^{10}$ | $1.6673 \times 10^{12}$ | **$4.5688 \times 10^{10}$** | $4.6797 \times 10^{10}$ | +2.4% |
| **Bucket 2: Pad Ratio** | $\le 0.150$ | $4.0430$ | $0.13696$ | **$0.13643$** | **-0.4% less padding** |
| **Bucket 2: P95 Latency (ms)** | $\le 2.1 \times 10^5$ | $3.4104 \times 10^6$ | **$1.9237 \times 10^5$** | $1.9534 \times 10^5$ | +1.5% |
| **Bucket 2: Sequential Timecost (ms)** | $\le 3.2 \times 10^7$ | $1.1463 \times 10^7$ | $3.1567 \times 10^7$ | **$2.9077 \times 10^7$** | **-7.9% faster (-2.49M ms)** |
| **Global Unique Shapes Used** | $\le 8$ | 8 | 7 shapes | **8 shapes (optimal)** | Full budget utilization |
| **Unit & Integration Tests** | 6/6 pass | 0/6 | 6/6 PASS | **6/6 PASS** | Full verification |

### Key Findings

1. **Better algorithmic exploration**: Sage ON utilized all 8 allowed shape slots `[64, 128, 192, 256, 384, 512, 640, 2048]`, incorporating shape `256` to avoid over-padding medium-length prompts. Sage OFF stopped at 7 shapes, resulting in **13.8% slower sequential execution**.
2. **Modular code vs transient scripts**: Sage ON produced a standalone, reproducible optimizer script (`optimize_scheduler.py`) using 1D Dynamic Programming over prompt clusters, whereas Sage OFF ran one-off inline bash commands without persisting the optimizer.
3. **Rigorous verification loop**: Sage ON maintained test-green tracking through its supervisor prompt, running both unit and integration checks prior to signaling task completion.

Full methodology and logs are documented in [benchmark/terminal-bench-scheduler/README.md](benchmark/terminal-bench-scheduler/README.md).


