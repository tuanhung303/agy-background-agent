# Project: AGY Background Agent (Strategic Advisor & Stop Audit Hardening)

## Architecture
- `hooks/session-advisor.py`: Hook entry point for Antigravity session-stop and post-invocation advisor events.
- `advisor/runner.py`: Lifecycle coordinator for mid-turn advisor and the final advisor gate (recap or steer; the advisor is the sole stop-time gate).
- `advisor/advisor.py`: Wise strategist advisor prompt builder, model caller, and JSON response parser.
- `advisor/advisor_prompt.md`: System prompt, goal governance instructions, and few-shot examples for strategic advisor.
- `advisor/goals.py`: Pinned goal synthesis, in-flight revised goal tracking, and derived task management.
- `advisor/triage.py`: Confidence thresholding, category promotion/demotion, SHA-1 deduplication, tag formatting.
- `advisor/watchers.py`: Active subagent tracking and background task monitoring.
- `advisor/guards.py`: Fast-path exits (subagent sessions, minimal tool counts, benchmark runs, destructive action filters).
- `advisor/policies.py`: Decision policy pipelines for background watching, mid-turn advisor, and final advisor gates.
- `advisor/transcript.py`: Transcript parsing, turn identity hashing, tool count, error, and loop heuristics.
- `advisor/models.py`: Data models and structured schemas for advice and audit verdicts.
- `advisor/executor.py`: Subprocess execution of `agy` CLI with fallback cascades and caching.
- `advisor/session_state.py`: Atomic session state persistence, turn hashing, and reset management.
- `advisor/sensitive.py`, `advisor/locking.py`, `advisor/config.py`, `advisor/git.py`, `advisor/sanitizer.py`, `advisor/task_structure.py`.
- `tests/`: 20+ test modules asserting unit, integration, static analysis, and adversarial invariants (578+ tests).

## Code Layout
- `advisor/*.py`: Core library modules. Mandatory constraint: strictly <= 199 lines per file and zero semicolon line packing.
- `hooks/*.py`: Antigravity hook scripts.
- `tests/test_*.py`: Unittest test cases.
- `scripts/`: Benchmark, hermetic test, and verification harnesses.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Semicolon Elimination & Modular Refactoring | Remove all semicolons in `runner.py`, `transcript.py`, etc., extracting helpers to ensure strict <= 199 lines without compression | M1 | Survey |
| F2 | Static Analysis Semicolon & AST Gate | Update `test_static_analysis.py` to forbid semicolon statement packing and enforce <= 199 lines per file | M1 | Survey / R3 |
| F3 | Structured Advice Categories | Formalize categories: `loop_detection`, `irreversible_risk`, `parallelize_subagent`, `architectural_trap`, `general` with schema normalization | M2 | R1 |
| F4 | Actionable Strategic Guidance | Provide concrete, runnable commands and exact file paths in advisor advice | M2 | R1 |
| F5 | Low Latency & Zero Unnecessary Holds | Ensure healthy paths return `injectSteps: []` immediately with no steering holds | M2 | R1 |
| F6 | Intelligent Task Structure Analysis | Proactively detect parallelizable workstreams (multi-module, isolated research, test writing, QA) | M3 | R2 |
| F7 | Structured `invoke_subagent` Suggestions | Guide primary agent with concrete `invoke_subagent` syntax, role catalog, and schema | M3 | R2 |
| F8 | Subagent Lifecycle & Watcher Hardening | Track active subagents, subagent age, prevent premature stop during subagent execution | M3 | R2 |
| F9 | Subagent Session Guard | Bypass parent-only audit/advisor behavior inside spawned worker sessions | M3 | R2 |
| F10 | Comprehensive Test Suite & Zero Regression | 100% pass across all unit, integration, and static analysis tests (578+ tests) | M4 | R3 |
| F11 | E2E Testing Suite (Tiers 1-4) | Requirement-driven opaque-box test suite for all features | E2E Track | Project Pattern |
| F12 | Adversarial Coverage Hardening (Tier 5) | White-box stress testing and edge-case validation | Final Milestone | Project Pattern |
| F13 | Pinned Goal & Revised Goal Governance | Wise strategist profile, baseline pinned goal synthesis, in-flight revised goal tracking, derived tasks, and regression validation | Final Milestone | User Request |
| F14 | Advisor Final Gate (Sole Stop-Time Gate) | Advisor-only final approval emitting `[RECAP·<cat>] <recap>` on clean finish; Final Stop Gate enforces prompt coverage, live empirical evidence, and knowledge-system/skill-registry write-back; steerer/auditor role removed | Final Milestone | User Request |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| E2E | E2E Testing Suite | Build 4-tier requirement-driven test suite & publish `TEST_READY.md` | none | DONE |
| M1 | Semicolon Decompression & Refactoring | Refactor `runner.py` and `transcript.py`, update `test_static_analysis.py` | none | DONE |
| M2 | Strategic Partner & Advisor Optimization (R1) | Update `advisor_prompt.md`, `triage.py`, `models.py`, `advisor.py` for structured categories and actionable guidance | M1 | DONE |
| M3 | Subagent Suggestion & Delegation Flow (R2) | Implement task structure heuristics, `invoke_subagent` schemas, subagent watchers | M2 | DONE |
| M4 | Test Hardening & Static Verification (R3) | Comprehensive tests for categories, delegation, static analysis verification | M3 | DONE |
| Final | Goal Governance, Final Recap & Hardening | Anchor/revised goals (F13), Advisor Final Recap & Stop Fallback (F14), 100% E2E + Tier 5 pass | M4, E2E | DONE |

## Interface Contracts
### `advisor.models.AdvisorAdvice` ↔ `advisor.triage.evaluate_advisor_advice` / `classify_advice`
- Input: `ver_res: dict`, `seen_advice: dict`, `steer_min_conf: float`, `escalate_min_conf: float`
- Output: `TriageDecision(decision="steer"|"watchout"|"hold"|"hold_dedup", text=str, category=str, confidence=float, advice_key=str, seen=dict)`
- Valid categories: `loop_detection`, `irreversible_risk`, `parallelize_subagent` (alias `parallelize`), `architectural_trap`, `general`, `missing_deliverable`, `algorithmic_bottleneck`, `scope_drift`, `fake_verification`.

### `advisor.goals` ↔ `advisor.session_state` & `advisor.advisor`
- Function: `sync_goal_state(state: dict, user_prompt: str, tool_count: int, tool_names: Set[str]) -> dict`
- Function: `format_goal_context(anchor_goal: Optional[str], revised_goal: Optional[str], derived_tasks: Optional[List[str]]) -> str`
- Tracks baseline anchor goal from initial prompt, in-flight revisions from `SESSION HISTORY`, and secondary derived tasks.

### `advisor.policies.final_advisor_gate` ↔ `advisor.runner`
- Input: `conv_id, transcript_path, clean_prompt, initial_line_count, total_tool_calls, turn_tool_names, user_prompt, agent_steps, git_diff, state`
- Output: Action dict with `"action": "healthy"|"emit"|"yield"|"progressed"|"hold_dedup"|"error"`, `"recap": str`, `"category": str`, `"confidence": float`
- On healthy action, runner emits `[RECAP·<cat>] <recap>` with `terminationBehavior: "terminate"`. The advisor is the sole stop-time gate; on `skip`/`error` the runner fails open and allows a clean stop.

### `advisor.transcript` ↔ `advisor.advisor`
- Function: `get_parallelizable_signals(steps: List[dict]) -> dict`
- Identifies: multi-file modifications across disjoint directories, sequential tool chains without dependencies.

### `advisor.watchers` ↔ `advisor.runner`
- Function: `get_active_subagents(steps: List[dict], conv_id: Optional[str] = None) -> List[dict]`
- Output: List of active subagents with `id`, `role`, `status`, `age_seconds`.
