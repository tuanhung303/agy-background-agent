<h1 align="center">agy-background-agent</h1>

<p align="center">
  A background supervisor for Antigravity coding agents.<br>
  Tracks execution steps, prevents premature completion, and flags drift.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/harness-Antigravity%20(AGY)-0F766E?style=flat-square" alt="Antigravity">
  <img src="https://img.shields.io/badge/python-%E2%89%A53.10-1D4ED8?style=flat-square" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/tests-830%2B-15803D?style=flat-square" alt="830+ tests">
  <img src="https://img.shields.io/badge/deps-zero-6D28D9?style=flat-square" alt="Zero deps">
</p>

---

Fast agents fail when they enter repetitive error loops, drift from task requirements, or stop early on mock tests. This repository provides Antigravity hooks that run beside the worker agent to catch these failures.

<p align="center">
  <img src="assets/architecture.svg" alt="agy-background-agent hook architecture" width="100%">
</p>

## Hook System

- [hooks/session-sage.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/hooks/session-sage.py#L1-L48): Lifecycle supervisor entry point. Dispatches `PostInvocation` mid-turn assessments and `Stop` final gates. Evaluates goal pinning, steers worker progress, and verifies deliverables before allowing session exit.
- [hooks/sage-enforce.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/hooks/sage-enforce.py#L1-L124): Zero-delay `PreToolUse` gate. When delegation is active (`delegate_cmd_turn`), blocks inline mutation tools (`run_command`, `write_to_file`, `replace_file_content`) to force subagent dispatch via `invoke_subagent`.
- [hooks/command-timer.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/hooks/command-timer.py#L1-L328): Command execution duration tracker. Categorizes runs into 5 tiers (`0-10s OK`, `10-30s IMPROVE_NEXT_TIME`, `30-90s ADJUST_FILTER`, `90-900s HEAVY_RECOMMEND_BACKGROUND`, `>900s FORBIDDEN_EXCEEDED_LIMIT`) and injects ephemeral context feedback.

## Sage Architecture

- **Supervisor Engine** ([sage/sage.py:182](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/sage.py#L182-L204), [sage/executor.py:140](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/executor.py#L140-L208)): Runs slow-thinking model cascades in an isolated home environment (`~/.gemini/antigravity-cli/sage_isolated_home`) with dedicated conversation locking and session persistence.
- **Transcript Parser** ([sage/transcript.py:67](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/transcript.py#L67-L104)): Extracts turn steps, sanitizes tool outputs, normalizes prompts, and builds bounded session history windows (`MAX_PRIOR_REQUESTS=5`).
- **Triage & Deduplication** ([sage/triage.py:53](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/triage.py#L53-L184)): Classifies model verdicts with confidence thresholds (`SAGE_STEER_MIN_CONFIDENCE=0.7`, `SAGE_ESCALATE_MIN_CONFIDENCE=0.85`), applies category-independent keyed deduplication (`compute_advice_key`), and formats structured `[STEER·category]` or `[WATCH·category]` tags.
- **Verification Ladder** ([sage/ladder.py:28](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/ladder.py#L28-L49)): Demands rising verification depth across 4 tiers (`static` -> `unit` -> `integration` -> `smoke`), preventing deep tasks from stopping at partial proof.
- **Policies & Gating** ([sage/policies.py:104](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/policies.py#L104-L223), [sage/policies.py:225](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/policies.py#L225-L242)): Evaluates dynamic tool weight thresholds (`compute_dynamic_tool_threshold`), overrides cadence on tool repeat loops, and enforces the terminal stop gate.
- **Watchers** ([sage/watchers.py:47](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/watchers.py#L47-L70), [sage/watchers.py:72](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/watchers.py#L72-L139), [sage/watchers.py:141](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/watchers.py#L141-L189)): Tracks active subagents, external terminal worker panes, and background tasks (300s grace period) to prevent premature termination while asynchronous work runs.
- **Delegated Worker Evidence** ([sage/workers.py:111](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/workers.py#L111-L248)): Scans transcript lines for worker spawn/idle states, extracts screen tails with line citations, and validates empirical delivery before approval.
- **Event Journal** ([sage/journal.py:30](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/journal.py#L30-L51)): Centralized structured JSONL log (`/tmp/agy_sage_events.jsonl`) recording audit events, violations, and decisions with automatic 2MB rotation.

## Task Complexity Classification & Routing

Sage categorizes incoming objectives into three complexity classes:

1. `simple_qa`: Single-fact queries, conceptual questions, or trivial edits. Relaxes tool weight intervals, skips mandatory delegation commands, and allows clean fast termination.
2. `complex_code`: Single-module bug fixes, algorithmic updates, and localized refactoring. Enforces verification ladder progression (`unit` -> `integration`), monitors tool repeat loops, and requires live artifact verification.
3. `multi_file`: Multi-module architectures, cross-directory features, and comprehensive benchmark tasks. Issues an immediate delegation command (`[CMD·delegate]`), locks inline execution in `sage-enforce.py`, mandates subagent dispatch via `invoke_subagent`, and checks full-tier verification (`unit` -> `integration` -> `smoke`).

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

**Winner Selection:**
- [x] **Arm 1: Sage ON (Better Overall)** — 2.3% lower cost ($2.8765 \times 10^{11}$ vs $2.9445 \times 10^{11}$), 13.8% faster sequential timecost ($2.3102 \times 10^8$ ms vs $2.6788 \times 10^8$ ms), full utilization of the 8-shape budget, and clean modular artifact generation.
- [ ] **Arm 2: Sage OFF** — Suboptimal 7-shape partition causing over-padding and slower sequential execution; transient unversioned shell scripts.

### Key Findings

1. **Better algorithmic exploration**: Sage ON utilized all 8 allowed shape slots `[64, 128, 192, 256, 384, 512, 640, 2048]`, incorporating shape `256` to avoid over-padding medium-length prompts. Sage OFF stopped at 7 shapes, resulting in **13.8% slower sequential execution**.
2. **Modular code vs transient scripts**: Sage ON produced a standalone, reproducible optimizer script (`optimize_scheduler.py`) using 1D Dynamic Programming over prompt clusters, whereas Sage OFF ran one-off inline bash commands without persisting the optimizer.
3. **Rigorous verification loop**: Sage ON maintained test-green tracking through its supervisor prompt, running both unit and integration checks prior to signaling task completion.

Full methodology and logs are documented in [benchmark/terminal-bench-scheduler/README.md](benchmark/terminal-bench-scheduler/README.md).

## Benchmark: sage ON/OFF on Terminal-Bench 1.0 (WAL Recovery Ordering & Storage Engine)

A benchmark on the concurrency and crash recovery task in Terminal-Bench 1.0: `wal-recovery-ordering`. The task requires repairing a multi-stage Write-Ahead Log storage engine and crash recovery system to satisfy strict contiguous LSN prefix replay starting at 1, authoritative segment IDs, durability-before-acknowledgment watermarks, deep memory detachment, and strict AST static sandbox gates (zero forbidden imports, no eval/exec, no bare except-pass).

### Setup

Two workers ran in parallel Orca split panes via `orca-agy.sh` on Gemini 3.7 Flash:
- **Arm 1 (Sage ON - `tb-wal-on`)**: Supervised with active goal pinning and step verification.
- **Arm 2 (Sage OFF - `tb-wal-off`)**: Unsupervised baseline (`--sage-off`).

### Results

| Metric | Threshold / Target | Baseline Engine | Arm 2: Sage OFF | Arm 1: Sage ON | Delta / Observation |
|---|---|---|---|---|---|
| **Structural AST Gate** | 7/7 checks pass | FAIL (missing symbols) | **PASS** (7/7) | **PASS** (7/7) | Full AST sandbox compliance |
| **Performance Gate** | 5/5 runs $\le 0.1\text{s}$ | FAIL | **PASS** ($\le 0.022\text{s}$) | **PASS** ($\le 0.027\text{s}$) | Sub-linear scaling |
| **Functional & Hypothesis Tests** | 25/25 pass | 13/25 PASS (12 FAIL) | **25/25 PASS (100%)** | **25/25 PASS (100%)** | Full invariant recovery |
| **Defect Prevention Interception** | 0 violations | N/A | None (missed lint) | **Caught `except: pass` in `test_wal.py`** | Sage prevented AST gate failure |
| **Active Steer Interventions** | N/A | 0 | 0 (unsupervised) | **3 steers** (Goal, AST Watch, Recap) | Invariant pinning & guardrails |

**Winner Selection:**
- [x] **Arm 1: Sage ON (Better Overall)** — Active defect interception (caught and removed forbidden `except: pass` in intermediate test file), pinned 5 core recovery invariants, and achieved 100% test pass rate across structural, performance, and Hypothesis test suites.
- [ ] **Arm 2: Sage OFF** — Succeeded on patch but operated without real-time lint/sandbox guardrails during intermediate iterations.

### Key Findings

1. **In-Flight Defect Interception**: During testing, the worker created a temporary test script containing `except: pass`. Sage immediately fired a `[WATCH·missing_proof]` steer flagging the static AST rule violation and directed the worker to fix it before completion, preventing a failed benchmark run.
2. **Invariant Pinning**: Sage anchored all 5 core WAL recovery invariants (`[Pinned Goal]`) at step 1, preventing the worker from drifting into superficial single-module patches.
3. **End-to-End Verification**: Both workers passed 25/25 tests, but Sage ON provided supervised quality assurance and self-healing.

Full methodology and logs are documented in [benchmark/terminal-bench-wal/README.md](benchmark/terminal-bench-wal/README.md).
