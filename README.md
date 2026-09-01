<h1 align="center">agy-background-agent</h1>

<p align="center">
  A background stop verifier for Antigravity coding agents.<br>
  Validates empirical proof, prevents premature completion, and guards against drift.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/harness-Antigravity%20(AGY)-0F766E?style=flat-square" alt="Antigravity">
  <img src="https://img.shields.io/badge/python-%E2%89%A53.10-1D4ED8?style=flat-square" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/tests-950%2B-15803D?style=flat-square" alt="950+ tests">
  <img src="https://img.shields.io/badge/deps-zero-6D28D9?style=flat-square" alt="Zero deps">
</p>

---

Fast agents fail when they enter repetitive error loops, drift from task requirements, claim completion on unverified self-reports, or stop early on mock tests. This repository provides Antigravity hooks operating in Lite Mode Stop Verifier to intercept premature stops and enforce empirical proof before allowing session completion.

<p align="center">
  <img src="assets/architecture.svg" alt="agy-background-agent hook architecture" width="100%">
</p>

## Hook System

- [hooks/session-sage.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/hooks/session-sage.py#L1-L60): Lifecycle hook entry point. Directly executes the Lite Mode Stop Verifier (`run_lite_stop_audit()`) at session stop time. Evaluates turn provenance, gates mutations, and validates empirical proof before permitting session exit.
- [hooks/sage-enforce.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/hooks/sage-enforce.py#L1-L124): Zero-delay `PreToolUse` gate. When delegation is active (`delegate_cmd_turn`), blocks inline mutation tools (`run_command`, `write_to_file`, `replace_file_content`) to force subagent dispatch via `invoke_subagent`.
- [hooks/command-timer.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/hooks/command-timer.py#L1-L328): Command execution duration tracker. Categorizes runs into 5 tiers (`0-10s OK`, `10-30s IMPROVE_NEXT_TIME`, `30-90s ADJUST_FILTER`, `90-900s HEAVY_RECOMMEND_BACKGROUND`, `>900s FORBIDDEN_EXCEEDED_LIMIT`) and injects ephemeral context feedback.

## Lite Mode Stop Verifier Architecture

The Lite Mode Stop Verifier (`sage/lite/`) provides an isolated, hermetic quality gate with zero MCP dependencies:

1. **Mutation Gating & Turn Provenance** ([sage/lite/gating.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/gating.py)): Extracts turn execution provenance from the transcript. If the turn contains no workspace mutations or file edits (e.g. pure question-answering or diagnostic queries), the verifier bypasses execution instantly with zero latency.
2. **Forked Session Isolation** ([sage/lite/fork.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/fork.py), [sage/executor.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/executor.py)): Clones the parent conversation SQLite database and brain artifacts into an isolated execution directory (`~/.gemini/antigravity-cli/sage_isolated_home`), preventing any pollution of the parent session history.
3. **Stop Gate Verification Cascade** ([sage/lite/verifier.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/verifier.py), [sage/lite/prompt.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/prompt.py)): Evaluates the worker's actions against the user's explicit Definition of Done (DoD) using fast Gemini models. Returns structured JSON verdicts (`PASS` or `FAIL`).
4. **Empirical Proof Validation** ([sage/lite/proof_validator.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/proof_validator.py)): Strictly rejects ungrounded self-report claims ("all tests pass", "verified manually"). Requires concrete proof (executed test binaries, command outputs, screenshot captures, or live DOM assertions).
5. **Contextual Action Directives** ([sage/lite/verifier.py:170](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/verifier.py#L170-L236)): When rejecting a premature stop, synthesizes specific imperative instructions (e.g. recommending test modules under `scripts/verify/<topic>/`) rather than generic rejection messages.
6. **3-Strike Circuit Breaker** ([sage/lite/runner.py:85](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/runner.py#L85-L89), [sage/config.py](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/config.py)): Tracks consecutive verification failures (`LITE_MAX_RETRIES=3`). If the worker fails to satisfy the quality gate after 3 attempts, the verifier fails open to allow clean stop without trapping the user.
7. **Knowledge Base Maintenance** ([sage/lite/verifier.py:111](file:///Users/__blitzzz/Documents/GitHub/agy-background-agent/sage/lite/verifier.py#L111-L168)): When a verified PASS includes knowledge updates, triggers the Persona/Knowledge Base maintainer to write durable learnings into persistent memory.

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

## Benchmark: sage ON/OFF on Terminal-Bench 2.1 (LLM Inference Batching Scheduler)

A benchmark on the hardest algorithmic systems optimization task in Terminal-Bench 2.1: `llm-inference-batching-scheduler`. The task requires packing 1,600 inference requests across two buckets into batches while adhering to hardware granularity alignment (multiple of 64 tokens), a hard limit of at most 8 unique tensor shapes across both buckets, and beating 4 strict analytical cost and latency thresholds.

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

Full methodology and logs are documented in [benchmark/terminal-bench-scheduler/README.md](benchmark/terminal-bench-scheduler/README.md).

## Benchmark: sage ON/OFF on Terminal-Bench 1.0 (WAL Recovery Ordering & Storage Engine)

A benchmark on the concurrency and crash recovery task in Terminal-Bench 1.0: `wal-recovery-ordering`. The task requires repairing a multi-stage Write-Ahead Log storage engine and crash recovery system to satisfy strict contiguous LSN prefix replay starting at 1, authoritative segment IDs, durability-before-acknowledgment watermarks, deep memory detachment, and strict AST static sandbox gates.

### Results

| Metric | Threshold / Target | Baseline Engine | Arm 2: Sage OFF | Arm 1: Sage ON | Delta / Observation |
|---|---|---|---|---|---|
| **Structural AST Gate** | 7/7 checks pass | FAIL (missing symbols) | **PASS** (7/7) | **PASS** (7/7) | Full AST sandbox compliance |
| **Performance Gate** | 5/5 runs $\le 0.1\text{s}$ | FAIL | **PASS** ($\le 0.022\text{s}$) | **PASS** ($\le 0.027\text{s}$) | Sub-linear scaling |
| **Functional & Hypothesis Tests** | 25/25 pass | 13/25 PASS (12 FAIL) | **25/25 PASS (100%)** | **25/25 PASS (100%)** | Full invariant recovery |
| **Defect Prevention Interception** | 0 violations | N/A | None (missed lint) | **Caught `except: pass` in `test_wal.py`** | Sage prevented AST gate failure |
| **Active Steer Interventions** | N/A | 0 | 0 (unsupervised) | **3 steers** (Goal, AST Watch, Recap) | Invariant pinning & guardrails |

Full methodology and logs are documented in [benchmark/terminal-bench-wal/README.md](benchmark/terminal-bench-wal/README.md).
