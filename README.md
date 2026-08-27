<h1 align="center">agy-background-agent</h1>

<p align="center">
  A background supervisor for Antigravity coding agents.<br>
  Tracks execution steps, prevents premature completion, and flags drift.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/harness-Antigravity%20(AGY)-0F766E?style=flat-square" alt="Antigravity">
  <img src="https://img.shields.io/badge/python-%E2%89%A53.10-1D4ED8?style=flat-square" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/tests-659%2B-15803D?style=flat-square" alt="659+ tests">
  <img src="https://img.shields.io/badge/deps-zero-6D28D9?style=flat-square" alt="Zero deps">
</p>

---

Fast agents fail when they enter repetitive error loops, drift from task requirements, or stop early on mock tests. This repository provides Antigravity hooks that run beside the worker agent to catch these failures.

<p align="center">
  <img src="assets/architecture.svg" alt="agy-background-agent hook architecture" width="100%">
</p>

## Benchmark: sage ON/OFF on DeepSWE-style long-horizon tasks

This pilot benchmark measures whether the background supervisor helps a fast-tier agy worker on a long-horizon coding task. The evaluation runs on `kysely-window-grouping-helpers` from the DeepSWE-style task suite, containing 254 fail-to-pass and 22 pass-to-pass tests. Graders check byte-exact SQL compilation across PostgreSQL, MySQL, MSSQL, and SQLite using CTRF canonical node matching.

The test worker runs Gemini 3.7 Flash inside isolated worktrees. Setting `AGY_SAGE_DISABLED=1` via `--sage-off` unplugs the background supervisor per spawn without mutating shared configuration files.

<p align="center">
  <img src="assets/benchmark-deepswe.svg" alt="Benchmark: Sage ON/OFF on DeepSWE-style long-horizon tasks" width="100%">
</p>

### Findings

- Every run that included byte-exact verification requirements in the brief reached a 1.0 reward across both supervisor states (arm3, r2a, r2b, r2c).
- The arm4 run failed the binary threshold (0.0 reward) due to a single stray space in SQL syntax across 4 dialect tests, despite passing 250 of 254 assertions (98.4% pass fraction).
- Turn counts fall within a narrow band of 141 to 201 steps across all runs. Initial observations of turn count reductions were trace artifacts from session mapping, corrected by pairing workers to brain transcripts via brief paths.

Methodology review and harness architecture details are documented in [REVIEW-opus5.md](benchmark/deepswe-sage-ab/REVIEW-opus5.md).
