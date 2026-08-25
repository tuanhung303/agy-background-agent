# Project: AGY Background Agent (Strategic Sage & Stop Audit Hardening)

## Architecture
- `hooks/session-sage.py`: Hook entry point for Antigravity session-stop and post-invocation Sage events (with backward-compatible session-advisor symlinks).
- `sage/runner.py`: Lifecycle coordinator for mid-turn Sage and the final Sage gate (recap or steer; the Sage is the sole stop-time gate).
- `sage/sage.py`: Wise strategist Sage prompt builder, model caller, and JSON response parser.
- `sage/sage_prompt.md`: System prompt, goal governance instructions, and few-shot examples for strategic Sage.
- `sage/goals.py`: Pinned goal synthesis, in-flight revised goal tracking, and derived task management.
- `sage/triage.py`: Confidence thresholding, category promotion/demotion, SHA-1 deduplication, tag formatting, and deferral overriding.
- `sage/watchers.py`: Active subagent tracking and background task monitoring.
- `sage/guards.py`: Fast-path exits (subagent sessions, minimal tool counts, benchmark runs, destructive action filters).
- `sage/policies.py`: Decision policy pipelines for background watching, mid-turn Sage, and final Sage gates.
- `sage/transcript.py`: Transcript parsing, turn identity hashing, tool count, error, and loop heuristics.
- `sage/models.py`: Data models, dynamic model discovery, version cascade, and structured schemas for advice and audit verdicts.
- `sage/executor.py`: Subprocess execution of `agy` CLI with isolated home (`SAGE_ISOLATED_HOME`), fallback cascades, and caching.
- `sage/session_state.py`: Atomic session state persistence, turn hashing, and reset management.
- `sage/sanitizer.py`: Tool output budget truncation, secret redaction, 5-class banned deferral taxonomy, and turn-wide response scanning.
- `sage/sensitive.py`, `sage/locking.py`, `sage/config.py`, `sage/git.py`, `sage/task_structure.py`, `sage/events.py`.
- `tests/`: 35 test modules asserting unit, integration, static analysis, and adversarial invariants (659+ tests).

## Code Layout
- `sage/*.py`: Core library modules (20 modules). Mandatory constraint: strictly <= 199 lines per file and zero semicolon line packing.
- `hooks/*.py`: Antigravity hook scripts.
- `statusline/*.py`: Session statusline integration.
- `tests/test_*.py`: Unittest test cases (659+ tests).
- `scripts/`: Benchmark, hermetic test, telemetry, and verification harnesses.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Semicolon Elimination & Modular Refactoring | Remove all semicolons in `runner.py`, `transcript.py`, etc., extracting helpers to ensure strict <= 199 lines without compression | M1 | Survey |
| F2 | Static Analysis Semicolon & AST Gate | Update `test_static_analysis.py` to forbid semicolon statement packing and enforce <= 199 lines per file | M1 | Survey / R3 |
| F3 | Structured Advice Categories | Formalize categories: `loop_detection`, `irreversible_risk`, `parallelize_subagent`, `architectural_trap`, `general` with schema normalization | M2 | R1 |
| F4 | Actionable Strategic Guidance | Provide concrete, runnable commands and exact file paths in Sage advice | M2 | R1 |
| F5 | Low Latency & Zero Unnecessary Holds | Ensure healthy paths return `injectSteps: []` immediately with no steering holds | M2 | R1 |
| F6 | Intelligent Task Structure Analysis | Proactively detect parallelizable workstreams (multi-module, isolated research, test writing, QA) | M3 | R2 |
| F7 | Structured `invoke_subagent` Suggestions | Guide primary agent with concrete `invoke_subagent` syntax, role catalog, and schema | M3 | R2 |
| F8 | Subagent Lifecycle & Watcher Hardening | Track active subagents, subagent age, prevent premature stop during subagent execution | M3 | R2 |
| F9 | Subagent Session Guard | Bypass parent-only audit/Sage behavior inside spawned worker sessions | M3 | R2 |
| F10 | Comprehensive Test Suite & Zero Regression | 100% pass across all unit, integration, and static analysis tests (659+ tests) | M4 | R3 |
| F11 | E2E Testing Suite (Tiers 1-4) | Requirement-driven opaque-box test suite for all features | E2E Track | Project Pattern |
| F12 | Adversarial Coverage Hardening (Tier 5) | White-box stress testing and edge-case validation | Final Milestone | Project Pattern |
| F13 | Pinned Goal & Revised Goal Governance | Wise strategist profile, baseline pinned goal synthesis, in-flight revised goal tracking, derived tasks, and regression validation | Final Milestone | User Request |
| F14 | Sage Final Gate (Sole Stop-Time Gate) | Sage-only final approval emitting `[RECAP·<cat>] <recap>` on clean finish; Final Stop Gate enforces prompt coverage, live empirical evidence, and knowledge write-back | Final Milestone | User Request |
| F15 | 5-Class Anti-Deferral & Question-Dumping Blocker | Blocks passive question dumping (`question_dumping`, `scope_evasion`, `aspirational_gap`, `delegated_execution`, `tail_todo`) at stop gates | Hardening | User Request |
| F16 | Subprocess Isolation & Parent Protection | Executes `agy` child CLI in `SAGE_ISOLATED_HOME` with strict symlink safety and parent session corruption guards | Hardening | Reliability |
| F17 | Turn-Wide Scan & Lexical Normalization | Prevents deferral washout across multiple responses in a turn with unicode/whitespace normalization | Hardening | Reliability |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| E2E | E2E Testing Suite | Build 4-tier requirement-driven test suite & publish `TEST_READY.md` | none | DONE |
| M1 | Semicolon Decompression & Refactoring | Refactor `runner.py` and `transcript.py`, update `test_static_analysis.py` | none | DONE |
| M2 | Strategic Partner & Sage Optimization (R1) | Update `sage_prompt.md`, `triage.py`, `models.py`, `sage.py` for structured categories and actionable guidance | M1 | DONE |
| M3 | Subagent Suggestion & Delegation Flow (R2) | Implement task structure heuristics, `invoke_subagent` schemas, subagent watchers | M2 | DONE |
| M4 | Test Hardening & Static Verification (R3) | Comprehensive tests for categories, delegation, static analysis verification | M3 | DONE |
| Final | Goal Governance, Final Recap & Hardening | Anchor/revised goals (F13), Sage Final Recap & Stop Fallback (F14), 100% E2E + Tier 5 pass | M4, E2E | DONE |
| Hardened | 5-Class Anti-Deferral & Subprocess Isolation | Anti-deferral taxonomy (F15), SAGE_ISOLATED_HOME (F16), turn-wide scan (F17), 659+ tests pass | Final | DONE |

## Interface Contracts
### `sage.models.AdvisorAdvice` ↔ `sage.triage.evaluate_advisor_advice` / `classify_advice`
- Input: `ver_res: dict`, `seen_advice: dict`, `steer_min_conf: float`, `escalate_min_conf: float`, `deferral: dict`
- Output: `TriageDecision(decision="steer"|"watchout"|"hold"|"hold_dedup", text=str, category=str, confidence=float, advice_key=str, seen=dict)`
- Valid categories: `loop_detection`, `irreversible_risk`, `parallelize_subagent` (alias `parallelize`), `architectural_trap`, `general`, `missing_deliverable`, `algorithmic_bottleneck`, `scope_drift`, `fake_verification`.

### `sage.goals` ↔ `sage.session_state` & `sage.sage`
- Function: `sync_goal_state(state: dict, user_prompt: str, tool_count: int, tool_names: Set[str]) -> dict`
- Function: `format_goal_context(anchor_goal: Optional[str], revised_goal: Optional[str], derived_tasks: Optional[List[str]]) -> str`
- Tracks baseline anchor goal from initial prompt, in-flight revisions from `SESSION HISTORY`, and secondary derived tasks.

### `sage.policies.final_sage_gate` ↔ `sage.runner`
- Input: `conv_id, transcript_path, clean_prompt, initial_line_count, total_tool_calls, turn_tool_names, user_prompt, agent_steps, git_diff, state`
- Output: Action dict with `"action": "healthy"|"emit"|"yield"|"progressed"|"hold_dedup"|"error"`, `"recap": str`, `"category": str`, `"confidence": float`
- On healthy action, runner emits `[RECAP·<cat>] <recap>` with `terminationBehavior: "terminate"`. The Sage is the sole stop-time gate; on `skip`/`error` the runner fails open and allows a clean stop.

### `sage.transcript` ↔ `sage.sage`
- Function: `get_parallelizable_signals(steps: List[dict]) -> dict`
- Identifies: multi-file modifications across disjoint directories, sequential tool chains without dependencies.

### `sage.watchers` ↔ `sage.runner`
- Function: `get_active_subagents(steps: List[dict], conv_id: Optional[str] = None) -> List[dict]`
- Output: List of active subagents with `id`, `role`, `status`, `age_seconds`.
