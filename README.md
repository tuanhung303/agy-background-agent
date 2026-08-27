<h1 align="center">agy-background-agent</h1>

<p align="center">
  <strong>A wise strategist riding shotgun with your Antigravity agent.</strong><br>
  <em>Because two is better than one.</em><br>
  Watches the trajectory. Holds the goal. Refuses a fake finish.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/harness-Antigravity%20(AGY)-0F766E?style=flat-square" alt="Antigravity">
  <img src="https://img.shields.io/badge/python-%E2%89%A53.10-1D4ED8?style=flat-square" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/tests-659%2B-15803D?style=flat-square" alt="659+ tests">
  <img src="https://img.shields.io/badge/deps-zero-6D28D9?style=flat-square" alt="Zero deps">
</p>

---

Fast agents fail in two ways: they **drift** mid-turn (error loops, tangents, dropped requirements) and they **stop early**, claiming success on mocked evidence or dumping deferrals. This repo provides a set of Antigravity hooks that sit beside the agent to close both gaps.

<p align="center">
  <img src="assets/architecture.svg" alt="agy-background-agent hook architecture" width="100%">
</p>

## Strategic Sage (Advisor)

A slow thinker next to a fast executor. Fires every 10 tool calls (or weighted tool score cadence), or immediately on error streaks, repeated tool loops, sensitive commands, and parallelizable workstreams.

| Status | Meaning |
| :--- | :--- |
| `on_track` | Clean progress. Silent, zero hold. |
| `watchout` | Heads-up: architectural trap, bottleneck, destructive command, missing deliverable, deferral attempt. |
| `off_track` | Hard correction: stuck loop, scope drift, hallucinated progress, mocked verification. |

Advice arrives as one terse, runnable line without lectures:

```
※ sage: [WATCH·parallelize_subagent] Dispatch invoke_subagent per suite
        | Ev: 3 independent test suites sequential | Why: independent legs belong in subagents
```

Confidence gates the noise: below `0.70` an `off_track` is demoted to `watchout`, `irreversible_risk` above `0.85` escalates to a hard steer, and SHA-1 dedup prevents repetitive messages.

## Goal Governance

Requirements often go missing quietly across long sessions. Three tracks prevent that:

- **Pinned goal**: Synthesized from the first request, held across every turn.
- **Revised goal**: In-flight scope changes, folded in without dropping the baseline.
- **Derived tasks**: Side workstreams mapped back to approved objectives.

All three ride in the Sage's context, requiring regression coverage before calling anything done.

## Stop Audit & Anti-Deferral

When the agent signals termination, the Sage is the **sole gate** with no separate auditor to overrule. It approves only when all four conditions hold:

1. **Prompt coverage**: Every directive in the request is addressed.
2. **Live empirical evidence**: A real binary, CLI, or script executed. Mocked tests alone do not count.
3. **Zero Deferral Escape**: Blocks passive question dumping across 5 classes (`question_dumping`, `scope_evasion`, `aspirational_gap`, `delegated_execution`, `tail_todo`).
4. **Knowledge write-back**: When `skills/`, `SKILL.md`, or `.okf/` are modified, the catalog is regenerated and validated.

Pass leads to `[RECAP·<cat>] <recap>` and an instant clean finish. Fail triggers `※ sage: …` with `force_continue`, sending the agent back to work. If the Sage is disabled, errors, or trips its circuit breaker, the stop **fails open** so guardrails never cause a deadlock.

Lifecycle guards run underneath: stop is blocked while subagents are live, background commands receive a 300s grace period, worker sessions bypass parent hooks, subprocesses execute safely in `SAGE_ISOLATED_HOME`, and a race guard yields silently on new user input.

## Command Timer & Statusline

Shell latency, bucketed and coached in real time:

| Duration | Verdict |
| :--- | :--- |
| ≤ 10s | `OK` (quiet) |
| 10–30s | `IMPROVE_NEXT_TIME` (tighter piping, scoped paths) |
| 30–90s | `ADJUST_FILTER` (refine query or glob) |
| 90s – 15m | `HEAVY_RECOMMEND_BACKGROUND` (go async, paginate, cache) |
| > 15m | `FORBIDDEN_EXCEEDED_LIMIT` (blocking too long) |

A stealth statusline shows session telemetry cleanly without visual noise — remaining hidden while Sage is idle, and dynamically surfacing when evaluating (`● sage:eval`) or injecting advice (`◐ sage:inject`):

```
3.7 flash [h] | agents:2                        ● sage:eval | ctx:221k/250k[1] | 38%[3h] | W:49%[2d]
```

When idle, the bar preserves space purely for token burn and quota tracking:

```
3.7 flash [h] | agents:2                                      ctx:221k/250k[1] | 38%[3h] | W:49%[2d]
```

## Quickstart

```bash
git clone https://github.com/tuanhung303/agy-background-agent.git
cd agy-background-agent
./scripts/install.sh
```

This symlinks the hooks and statusline into `~/.config/agy/` and `~/.gemini/config/hooks/`, and registers `Stop`, `PostInvocation`, `PreToolUse`, and `PostToolUse` entries in `~/.gemini/config/hooks.json`.

**Tune it** via environment variables, `~/.config/agy/sage.env`, or `~/.config/agy/advisor.env`:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `AGY_MID_TURN_SAGE_ENABLED` | `1` | Master switch for the mid-turn Sage |
| `AGY_SAGE_TOOL_INTERVAL` | `10` | Tool calls between evaluations |
| `AGY_SAGE_STEER_MIN_CONFIDENCE` | `0.7` | Floor to emit a hard steer |
| `AGY_SAGE_ESCALATE_MIN_CONFIDENCE` | `0.85` | Floor to escalate `irreversible_risk` |
| `AGY_SAGE_MAX_ERROR_STREAK` | `3` | Failures before the circuit breaker opens |
| `AGY_SAGE_MODEL` | `auto` | Sage model alias or model ID |
| `AGY_SAGE_EFFORT` | `high` | Reasoning effort for forced/final evaluations |
| `AGY_SAGE_ROUTINE_EFFORT` | `medium` | Effort tier for routine unforced mid-turn checks (low/medium/high; bad values fall back to medium). Routed via model re-tiering — agy bakes effort into the model name, so a mismatched `--effort` flag is never sent. |
| `AGY_MAX_CONTEXT_TOKENS` | `250000` | Compaction ceiling shown in the statusline |

## Tests

```bash
uv run --with pytest pytest
```

659+ unit, integration, adversarial, and static-analysis tests, including a gate that keeps every module ≤ 199 lines with no semicolon packing.

## Empirical eval harness

Scenario-driven evaluation against the REAL policy pipeline (guards → cadence →
deferral scan → classify → hammer guard), model call mocked per scenario script:

```bash
python3 scripts/eval/run_eval.py                 # all scenarios in scripts/eval/scenarios/
python3 scripts/eval/run_eval.py loop_early      # single scenario
python3 scripts/eval/run_eval.py --json out.json # machine-readable results
```

Adding coverage = drop a JSON scenario file (tools, model-call script, expect
contract: fire timing, category, emission caps, effort ladder, dedup ratio).
Exit code 0 only when every scenario passes — CI-safe.

## Benchmark: sage ON/OFF on DeepSWE-style long-horizon tasks

AB pilot (`benchmark/deepswe-sage-ab/`) measuring whether the Sage improves a
fast-tier agy worker on an original, long-horizon coding task —
`kysely-window-grouping-helpers` from the DeepSWE-style local task set (254
fail-to-pass + 22 pass-to-pass tests; byte-exact SQL compile checks across
postgres/mysql/mssql/sqlite; CTRF node-id canonical matching; binary reward,
graded with the official verifier frame). Worker: Gemini 3.7 Flash agents in
isolated worktrees. Sage control is per-spawn: `--sage-off` sets
`AGY_SAGE_DISABLED=1`, read by `hooks/session-sage.py` as a hot-unplug gate —
no shared hooks-file mutation, other live sessions unaffected.

| Run | Sage | F2P | P2P | Reward | f2p frac | Turns |
|---|---|---|---|---|---|---|
| arm3 | OFF | 254/254 | 22/22 | 1.0 | 1.00 | 141 |
| arm4 | ON | 250/254 | 22/22 | 0.0 | 0.984 | 142 |
| r2a | OFF | 254/254 | 22/22 | 1.0 | 1.00 | 201 |
| r2b | ON | 254/254 | 22/22 | 1.0 | 1.00 | 165 |
| r2c | ON | 254/254 | 22/22 | 1.0 | 1.00 | 154 |

**Findings (pilot scale — not statistically significant):**

- Every run that included a byte-exact-verification requirement in the brief
  reached full reward regardless of sage state (arm3, r2a, r2b, r2c).
- The single failure (arm4) predates two harness fixes and was one stray-space
  SQL emission replicated across 4 dialect-shared assertions — binary reward
  collapses a 98.4% pass to 0.0, which is why `f2p frac` rides beside it.
- With fixes applied, both sage states reach full reward. Turn counts are in
  the same band for both states (141–201 turns) — earlier "sage cuts turns"
  reads were an artifact of transcript-to-session mis-mapping, now fixed by
  mapping each worker to its brain transcript via brief-path matching.

Known limitations: single task per cell (n ≤ 2 after grouping), no variance CI
at this scale, model identified by provider tier label, brief text published in
the harness (`deepswe_ab.sh`). Full review of methodology gaps and
parallelization plan: [REVIEW-opus5.md](benchmark/deepswe-sage-ab/REVIEW-opus5.md).
