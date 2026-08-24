#!/bin/bash
# bench_env.sh - Benchmark profile for the sage stop-audit hook.
# Sources tightened thresholds so short bench runs engage Tier-2 (sage + recap)
# without touching production defaults. Usage:
#   source scripts/benchmarks/bench_env.sh && agy --model "Gemini 3.7 Flash (Medium)" -p "..."
#
# NOTE: only effective for DIRECT headless runs from this shell. Hook processes
# spawned by agy outside the TUI env need the overlay file instead — see
# sage.config._load_env_overlay and scripts/verification/KNOWN_ISSUES.md item 2.
#
# Effects vs prod defaults:
#   AGY_STOP_AUDIT_MIN_TOOLS      15 -> 6   (audit triggers on small tool counts)
#   AGY_STOP_AUDIT_MIN_DURATION  600 -> 120 (or ~2 min of turn time)
#   AGY_SAGE_TOOL_INTERVAL        10 -> 6   (tighter mid-turn sage cadence)
#   AGY_SAGE_MODEL          pinned reviewer (3.7 Flash Medium) is inherited from config.

export AGY_STOP_AUDIT_MIN_TOOLS="${AGY_STOP_AUDIT_MIN_TOOLS:-6}"
export AGY_STOP_AUDIT_MIN_DURATION="${AGY_STOP_AUDIT_MIN_DURATION:-120}"
export AGY_SAGE_TOOL_INTERVAL="${AGY_SAGE_TOOL_INTERVAL:-6}"
export AGY_ADVISOR_TOOL_INTERVAL="${AGY_ADVISOR_TOOL_INTERVAL:-$AGY_SAGE_TOOL_INTERVAL}"
echo "[bench_env] MIN_TOOLS=$AGY_STOP_AUDIT_MIN_TOOLS MIN_DURATION=$AGY_STOP_AUDIT_MIN_DURATION SAGE_INTERVAL=$AGY_SAGE_TOOL_INTERVAL"
