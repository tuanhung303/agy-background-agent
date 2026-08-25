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

A statusline shows the whole session at a glance, including model, live subagents, Sage counters, context burn against the 250k compaction ceiling, and quota countdowns:

```
3.7 flash [h] | agents:2 | adv:g1/a3/p7/r1 | ctx:221k/250k[1] | 38%[3h] | W:49%[2d]
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
| `AGY_SAGE_EFFORT` | `high` | Reasoning effort |
| `AGY_MAX_CONTEXT_TOKENS` | `250000` | Compaction ceiling shown in the statusline |

## Tests

```bash
uv run --with pytest pytest
```

659+ unit, integration, adversarial, and static-analysis tests, including a gate that keeps every module ≤ 199 lines with no semicolon packing.
