# Test Infrastructure & Strategy: AGY Stop Audit & Strategic Advisor

## 1. Test Philosophy & Architecture

The test suite for the **AGY Stop Audit & Strategic Advisor** adheres to a strict **opaque-box, requirement-driven testing philosophy** derived directly from `ORIGINAL_REQUEST.md` and `PROJECT.md`. The testing architecture treats the auditing system as an autonomous decision engine with rigorous contract boundaries.

### Core Principles
1. **Opaque-Box Contract Verification**: Every test targets observable public interfaces, hook contract payloads, lifecycle state transitions, structured advisor JSON outputs, and AST constraints rather than internal private variables.
2. **Requirement-Driven Grounding**: Every test case directly derives from user-specified requirements (R1: Strategic Partner & Slow-Thinking Advisor, R2: Intelligent Subagent Suggestion & Delegation Flow, R3: Rigorous Empirical Verification & Zero Regression) and feature specifications F1 through F10.
3. **Anti-Facade Guarantee**: Tests never write assertions that pass unconditionally. Tests assert explicit state transformations, token/tag formats (`[STEER·category·conf]`), regex matches, SHA-1 key deduplication ledgers, and exit decision payloads (`{"decision": "stop"}`, `{"decision": "continue", "reason": ...}`, `{"injectSteps": [...]}`).
4. **Hermetic Isolation**: Tests run in clean temporary directories, isolate environment variables using `patch.dict`, manage ephemeral mock transcript JSONL files, and release all `fcntl` file locks and tmp state upon cleanup.
5. **Deterministic Verification**: For non-deterministic properties (e.g. timestamps, randomized session IDs, temporary filenames), tests assert structural format and invariants while matching deterministic outputs against derived authoritative oracles.

---

## 2. Feature Inventory & Coverage Matrix

| Feature | Title | Milestone | Scope / Contract | Tier 1 (Feature) | Tier 2 (Boundary) | Tier 3 (Cross-Feature) | Tier 4 (Workload) |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| **F1** | Semicolon Elimination & Refactoring | M1 | Strict line length <= 199, zero semicolon statement compression | 5 tests | - | Yes | - |
| **F2** | Static Analysis & AST Gate | M1 | Python AST validity, line caps, no bare prints in library modules | 5 tests | Yes | - | - |
| **F3** | Structured Advice Categories | M2 | Normalization of `loop_detection`, `irreversible_risk`, `parallelize_subagent`, `architectural_trap`, `general` | 5 tests | Yes | Yes | Yes |
| **F4** | Actionable Strategic Guidance | M2 | Concrete runnable commands, file paths, destructive action suppression | 5 tests | - | Yes | Yes |
| **F5** | Low Latency & Zero Unnecessary Holds | M2 | Healthy trajectories emit `injectSteps: []` or `{"decision": "stop"}` with 0 delay | 5 tests | Yes | Yes | Yes |
| **F6** | Intelligent Task Structure Analysis | M3 | Loop detection heuristics, error streak classification, tool sequence tracking | 5 tests | Yes | Yes | Yes |
| **F7** | Structured `invoke_subagent` Guidance | M3 | Concrete subagent schemas, role catalog (`Scout`, `Implementer`, `QA`), spawn signals | 5 tests | - | Yes | Yes |
| **F8** | Subagent Lifecycle & Watchers | M3 | Active subagent tracking, age calculation, premature stop prevention | 5 tests | Yes | Yes | Yes |
| **F9** | Subagent Session Guard | M3 | Worker-session detection and parent-only audit/advisor bypass | 4 tests | Yes | - | Yes |
| **F10** | Stop Audit Lifecycle & Zero Regression | M4 | Turn triggers, duration/tool thresholds, sensitive keyword triggers, session state | 5 tests | Yes | Yes | Yes |

---

## 3. 4-Tier Test Suite Specification

### Tier 1: Feature Coverage (>= 5 Tests Per Feature for F1..F10)
- **F1 (Semicolon Elimination & Modular Refactoring)**:
  - `test_f1_no_semicolons_in_core_modules`: Ensures no semicolon statement separators exist in core modules.
  - `test_f1_ast_single_statement_per_line`: Parses AST and verifies no multiple simple statements are packed into a single line.
  - `test_f1_runner_module_line_budget`: Asserts `runner.py` is under 199 lines without code squishing.
  - `test_f1_transcript_module_line_budget`: Asserts `transcript.py` is under 199 lines.
  - `test_f1_modular_helper_separation`: Verifies extracted helper modules (`policies.py`, `watchers.py`, `triage.py`) are cleanly separated.

- **F2 (Static Analysis Semicolon & AST Gate)**:
  - `test_f2_valid_python_ast_all_sources`: AST parses all `.py` files across `stop_audit`, `hooks`, `statusline`, and `tests`.
  - `test_f2_no_syntax_errors_in_any_module`: Compiles and verifies zero syntax errors.
  - `test_f2_no_bare_prints_in_library`: Verifies `stop_audit` library modules do not contain bare `print()` calls.
  - `test_f2_ast_docstrings_intact`: Asserts all public functions contain docstrings.
  - `test_f2_all_modules_under_line_limit`: Asserts all `advisor/*.py` modules are strictly <= 199 lines.

- **F3 (Structured Advice Categories)**:
  - `test_f3_loop_detection_category_normalization`: Normalizes and tags `loop_detection` with `[STEER·loop_detection]`.
  - `test_f3_irreversible_risk_category_escalation`: Escalates high-confidence `irreversible_risk` to off-track steer.
  - `test_f3_parallelize_category_handling`: Handles both `parallelize` and `parallelize_subagent` category identifiers.
  - `test_f3_architectural_trap_category`: Verifies `architectural_trap` and `algorithmic_bottleneck` categories.
  - `test_f3_confidence_tag_formatting`: Verifies confidence scores are formatted into tag headers (e.g. `·conf0.90`).

- **F4 (Actionable Strategic Guidance)**:
  - `test_f4_concrete_command_in_action_field`: Asserts advisor output contains runnable shell command recommendations.
  - `test_f4_exact_file_paths_in_guidance`: Asserts advisor output highlights specific file paths to inspect.
  - `test_f4_destructive_command_suppression_action`: Verifies destructive commands (e.g. `rm -rf`, `git reset --hard`) in action are suppressed.
  - `test_f4_destructive_command_suppression_guidance`: Verifies destructive commands in guidance are suppressed with safe alternative.
  - `test_f4_evidence_snippet_clamping`: Verifies evidence is cleanly clamped to budget with ellipsis.

- **F5 (Low Latency & Zero Unnecessary Holds)**:
  - `test_f5_healthy_trajectory_returns_hold_decision`: Asserts on-track advisor verdict returns `decision: hold`.
  - `test_f5_post_invocation_healthy_returns_empty_steps`: In post-invocation mode, healthy evaluation outputs `{"injectSteps": []}` immediately.
  - `test_f5_tool_delta_interval_fast_path`: Skips LLM invocation when tool calls delta is less than `ADVISOR_TOOL_INTERVAL`.
  - `test_f5_stop_hook_clean_exit_when_passed`: Asserts final stop hook exits immediately with `{"decision": "stop"}` on passed audit.
  - `test_f5_empty_stdin_fail_safe_exit`: Asserts empty stdin causes fast-path exit with zero delay.

- **F6 (Intelligent Task Structure Analysis)**:
  - `test_f6_repeated_tool_calls_loop_detection`: Detects repetitive identical tool calls with same arguments.
  - `test_f6_interleaved_legitimate_tools_no_false_positive`: Ensures alternating legitimate tools do not trigger false loops.
  - `test_f6_polling_tools_exempt_from_loop_detector`: Verifies `manage_task`, `status`, `list_dir` are exempt from false loop flags.
  - `test_f6_consecutive_tool_errors_detection`: Detects consecutive error patterns (`exit code 1`, `traceback`, `command not found`).
  - `test_f6_active_goal_pinning_across_multi_turn`: Extracts latest active goal even with extensive session history.

- **F7 (Structured `invoke_subagent` Suggestions)**:
  - `test_f7_invoke_subagent_tool_call_parsing`: Correctly parses `invoke_subagent` tool calls from transcript.
  - `test_f7_subagent_role_catalog_extraction`: Extracts roles (`Scout`, `Implementer`, `QA`, `Subagent`).
  - `test_f7_subagent_id_resolution`: Tracks subagent IDs and assigns synthetic IDs for pending invocations.
  - `test_f7_parallel_subagent_recommendation_in_signals`: Embeds parallel subagent signals when parallelizable tasks are found.
  - `test_f7_subagent_spawn_lock_acquisition_release`: Verifies subagent spawn lock is cleanly acquired and released.

- **F8 (Subagent Lifecycle & Watcher Hardening)**:
  - `test_f8_active_subagent_prevents_premature_stop`: Active subagents block session termination.
  - `test_f8_subagent_completion_via_sender_message`: Detects subagent completion via incoming sender messages.
  - `test_f8_subagent_idle_detection`: Tracks subagent idle notices (`subagent X has gone idle`).
  - `test_f8_subagent_termination_detection`: Tracks explicitly killed subagents (`Killed subagent X`).
  - `test_f8_background_task_grace_period`: Active fresh background tasks (< 300s) receive a grace period.

- **F9 (Subagent Session Guard)**:
  - `test_f9_subagent_session_payload_flag_bypass`: Bypasses audit/advisor when `isSubagent: true` in payload.
  - `test_f9_subagent_session_parent_conv_id_bypass`: Bypasses audit/advisor when `parentConversationId` is present.
  - `test_f9_subagent_worker_role_bypass`: Bypasses audit/advisor when role is `worker`, `implementer`, `scout`, or `qa`.
  - `test_f9_subagent_reminder_in_transcript_bypass`: Detects `<subagent_reminder>` tags in transcript and bypasses.


- **F10 (Comprehensive Test Suite & Zero Regression)**:
  - `test_f10_turn_duration_threshold_trigger`: Evaluates turn duration threshold (`TURN_DURATION_THRESHOLD`).
  - `test_f10_tool_call_count_threshold_trigger`: Evaluates tool count threshold (`TOOL_CALL_THRESHOLD`).
  - `test_f10_sensitive_keyword_tool_scan_word_boundary`: Detects sensitive keywords (`git`, `aws`, `kubectl`, `terraform`) with word boundaries.
  - `test_f10_session_state_persistence_and_locking`: Validates atomic state persistence (`atomic_write_json`) and 0600 file permissions.
  - `test_f10_deduplication_ledger_persistence`: Validates advice deduplication counts persist across consecutive turns.

---

### Tier 2: Boundary & Corner Cases
- `test_tier2_empty_transcript_file`: Handles zero-byte and whitespace-only transcripts gracefully.
- `test_tier2_massive_transcript_truncation`: Stress-tests large transcripts (> 1000 lines, 100KB+ tool outputs) with clamp limits.
- `test_tier2_rapid_tool_error_streak_circuit_breaker`: Opens advisor circuit breaker after `ADVISOR_MAX_ERROR_STREAK` consecutive errors.
- `test_tier2_boundary_confidence_scores`: Tests confidence values at 0.0, 1.0, 0.7, 0.85, 100%, 0%, negative, and non-numeric strings.
- `test_tier2_timeout_and_time_duration_extremes`: Tests epoch zero, future timestamps, None timestamps, and missing timezones.
- `test_tier2_zero_tool_session_fast_exit`: 0-tool sessions exit immediately without triggering LLM audit.
- `test_tier2_malformed_json_and_broken_stdin`: Stdin containing invalid JSON or empty input cleanly executes fail-safe exit.

---

### Tier 3: Cross-Feature Interactions
- `test_tier3_advisor_triage_dedup_pipeline`: Tests full pipeline from raw advisor LLM JSON to triage classification, tag formatting, and SHA-1 deduplication.
- `test_tier3_subagent_spawn_tracking_to_advisor_gate`: Tests primary agent spawning subagents, transcript updates, watcher tracking, and advisor hold until subagent completion.
- `test_tier3_sensitive_tool_to_advisor_final_gate`: Tests sensitive tool execution surfacing through the advisor final gate's Final Stop Gate evidence signal note.
- `test_tier3_background_task_grace_to_stale_steer`: Tests fresh background task receiving grace period then becoming stale (> 300s) and triggering steering.

---

### Tier 4: Real-World Workload Scenarios
- `test_tier4_multi_turn_conversational_workflow`: Simulates multi-turn session with changing user requests, verified tool counts, and turn identity tracking.
- `test_tier4_loop_steering_hold_and_release`: Simulates agent entering repetitive test error loop, advisor issuing steer, agent running fix, and advisor returning to healthy hold.
- `test_tier4_irreversible_risk_warning_and_mitigation`: Simulates dangerous command execution attempt, advisor issuing high-confidence steer, and agent adopting safe alternative.
- `test_tier4_parallel_subagent_dispatch_workflow`: Simulates multi-component parallel workload, advisor suggesting `invoke_subagent` with concrete schema, and primary agent executing dispatch.

---

## 4. Verification & Execution

To execute the full 4-Tier E2E test suite:

```bash
PYTHONPATH=. python3 -m unittest tests/test_e2e_suite.py
```

To execute the entire project test suite:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -p 'test_*.py'
```
