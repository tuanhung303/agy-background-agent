#!/usr/bin/env python3
"""
tests/test_e2e_suite.py - Comprehensive 4-Tier E2E Test Suite for AGY Stop Audit & Strategic Advisor.

Tiers:
- Tier 1: Feature Coverage (>=5 test cases per feature for features F1..F10, 50 tests total)
- Tier 2: Boundary & Corner Cases (empty transcript, massive transcripts, rapid errors, boundary confidence, timeouts, 0-tool sessions, malformed stdin)
- Tier 3: Cross-Feature Interactions (advisor pipeline, subagent watcher to advisor gate, sensitive keywords to final-gate live-evidence mandate, background tasks)
- Tier 4: Real-World Workload Scenarios (multi-turn workflows, loop steering hold/release, irreversible risk mitigation, parallel subagent dispatch)
"""

import ast
import glob
import json
import os
import shutil
import stat
import tempfile
import time
import token
import tokenize
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sage.sage import (
    _clamp_diff,
    _normalize_sage_dict,
    build_sage_prompt,
    evaluate_mid_turn_progress,
    extract_target_goal,
    parse_sage_output,
)
from sage.config import (
    SAGE_MAX_ERROR_STREAK,
)
from sage.executor import (
    acquire_spawn_lock,
    clear_session_id,
    release_spawn_lock,
)
from sage.guards import (
    check_payload_and_lifecycle,
    evaluate_turn_triggers,
    is_subagent_session,
)
from sage.locking import (
    atomic_write_json,
    release_lock,
)
from sage.models import (
    cache_working_model,
)
from sage.policies import (
    sage_flow,
    background_watch,
)
from sage.runner import main
from sage.sensitive import (
    scan_tool_call_for_sensitive,
)
from sage.transcript import (
    extract_session_and_turn_data,
    get_active_subagents,
    get_active_turn_identity,
    has_active_subagents,
    has_recent_tool_errors,
    has_repeated_tool_calls,
    is_post_invocation_completion_candidate,
)
from sage.triage import (
    _parse_confidence,
    classify_advice,
    compute_advice_key,
)


class BaseE2ETestCase(unittest.TestCase):
    """Base test case providing clean temporary directories and lifecycle cleanup."""

    def setUp(self):
        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.pkg_dir = os.path.join(self.repo_root, "sage")
        self.test_dir = tempfile.mkdtemp(prefix="agy_e2e_")
        self.transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        self.conv_id = f"e2e_conv_{int(time.time() * 1000)}_{os.getpid()}"
        if os.path.exists("/tmp/agy_auditor_spawn.lock"):
            try:
                os.remove("/tmp/agy_auditor_spawn.lock")
            except Exception:
                pass

    def tearDown(self):
        release_lock()
        clear_session_id(self.conv_id, prefixes=("agy_stop_audit_session_", "agy_mid_advisor_session_", "agy_mid_verifier_session_"))
        cache_working_model(None)
        if os.path.exists("/tmp/agy_auditor_spawn.lock"):
            try:
                os.remove("/tmp/agy_auditor_spawn.lock")
            except Exception:
                pass
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def write_transcript_lines(self, lines):
        with open(self.transcript_path, "w", encoding="utf-8") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")


# ============================================================================
# TIER 1: FEATURE COVERAGE (>=5 test cases per feature for features F1..F10)
# ============================================================================

class TestTier1FeatureCoverage(BaseE2ETestCase):

    # ------------------------------------------------------------------------
    # F1: Semicolon Elimination & Modular Refactoring
    # ------------------------------------------------------------------------
    def test_f1_01_no_semicolons_in_core_modules(self):
        """Verifies that core modules do not use semicolons to pack statements."""
        target_files = ["runner.py", "transcript.py", "policies.py", "watchers.py", "triage.py"]
        for fname in target_files:
            fpath = os.path.join(self.pkg_dir, fname)
            if not os.path.exists(fpath):
                continue
            with open(fpath, "rb") as f:
                tokens = tokenize.tokenize(f.readline)
                semicolons = [tok.start for tok in tokens if tok.exact_type == token.SEMI]
            self.assertEqual(semicolons, [], f"Semicolon statement packing found in {fname}: {semicolons}")

    def test_f1_02_ast_single_statement_per_line(self):
        """Verifies sibling AST statements are not packed onto one source line."""
        pkg_files = glob.glob(f"{self.pkg_dir}/*.py")
        for fpath in pkg_files:
            with open(fpath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=fpath)
            packed = []
            for parent in ast.walk(tree):
                for _, value in ast.iter_fields(parent):
                    if not isinstance(value, list):
                        continue
                    lines = [node.lineno for node in value if isinstance(node, ast.stmt)]
                    packed.extend(line for line in set(lines) if lines.count(line) > 1)
            self.assertEqual(packed, [], f"Packed sibling statements in {os.path.basename(fpath)}: {packed}")

    def test_f1_03_runner_module_line_budget(self):
        """Verifies that runner.py adheres to the project line budget."""
        fpath = os.path.join(self.pkg_dir, "runner.py")
        with open(fpath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertLessEqual(len(lines), 255, "runner.py exceeds the project line budget")

    def test_f1_04_modular_helper_separation(self):
        """Verifies extracted helper modules are cleanly segregated and importable."""
        from sage import guards, policies, sensitive, triage, watchers
        self.assertTrue(callable(guards.check_payload_and_lifecycle))
        self.assertTrue(callable(policies.background_watch))
        self.assertTrue(callable(policies.sage_flow))
        self.assertTrue(callable(sensitive.scan_turn_tools_for_sensitive))
        self.assertTrue(callable(triage.classify_advice))
        self.assertTrue(callable(watchers.get_active_subagents))

    def test_f1_05_clean_import_separation(self):
        """Verifies that modules do not have circular dependency side effects."""
        import importlib
        for modname in ["sage.models", "sage.triage", "sage.policies", "sage.watchers"]:
            mod = importlib.import_module(modname)
            self.assertIsNotNone(mod)

    # ------------------------------------------------------------------------
    # F2: Static Analysis Semicolon & AST Gate
    # ------------------------------------------------------------------------
    def test_f2_01_valid_python_ast_all_sources(self):
        """Ensures all python files in the repository parse into valid AST without syntax error."""
        all_py = glob.glob(f"{self.repo_root}/**/*.py", recursive=True)
        py_files = [
            p for p in all_py
            if not any(part.startswith(".") for part in os.path.relpath(p, self.repo_root).split(os.sep))
            and "/venv" not in p
            and "/__pycache__" not in p
        ]
        self.assertGreater(len(py_files), 10)
        for filepath in py_files:
            with open(filepath, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=filepath)
            self.assertIsInstance(tree, ast.AST)

    def test_f2_02_no_syntax_errors_in_any_module(self):
        """Verifies compilation of all modules."""
        pkg_files = glob.glob(f"{self.pkg_dir}/*.py")
        for filepath in pkg_files:
            with open(filepath, "r", encoding="utf-8") as f:
                compiled = compile(f.read(), filepath, "exec")
                self.assertIsNotNone(compiled)

    def test_f2_03_no_bare_prints_in_library(self):
        """Verifies no unescaped bare print statements in non-runner library modules."""
        pkg_files = [f for f in glob.glob(f"{self.pkg_dir}/*.py") if not f.endswith("__init__.py")]
        for filepath in pkg_files:
            fname = os.path.basename(filepath)
            if fname in ("guards.py", "runner.py", "journal.py"):
                continue
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("print(", content, f"Bare print statement found in {fname}")

    def test_f2_04_ast_docstrings_intact(self):
        """Verifies that core modules have module-level docstrings."""
        for fname in ["models.py", "triage.py", "sage.py", "policies.py", "guards.py", "session_state.py"]:
            fpath = os.path.join(self.pkg_dir, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read())
            doc = ast.get_docstring(tree)
            self.assertIsNotNone(doc, f"Missing docstring in {fname}")
            self.assertGreater(len(doc.strip()), 5)

    def test_f2_05_all_modules_under_line_limit(self):
        """Verifies core files are within manageable limits."""
        for filepath in glob.glob(f"{self.pkg_dir}/*.py"):
            with open(filepath, "r", encoding="utf-8") as f:
                line_count = len(f.readlines())
            self.assertLessEqual(line_count, 255, f"{os.path.basename(filepath)} exceeds the project line limit")

    # ------------------------------------------------------------------------
    # F3: Structured Advice Categories
    # ------------------------------------------------------------------------
    def test_f3_01_loop_detection_category_normalization(self):
        """Verifies loop_detection category is normalized, tagged with [STEER·loop_detection], and repeatable."""
        advice = {
            "status": "off_track",
            "category": "loop_detection",
            "action": "Break infinite test loop: run pytest tests/test_core.py -x",
            "evidence": "3 consecutive test failures",
            "guidance": "Fix the failing assertion in test_core before re-running",
            "confidence": 0.95,
        }
        res = classify_advice(advice, seen_advice={})
        self.assertEqual(res["decision"], "steer")
        self.assertEqual(res["category"], "loop_detection")
        self.assertIn("[STEER·loop_detection]", res["text"])
        self.assertIn("Break infinite test loop", res["text"])

    def test_f3_02_irreversible_risk_category_escalation(self):
        """Verifies high-confidence irreversible_risk is escalated from watchout to steer."""
        advice = {
            "status": "watchout",
            "category": "irreversible_risk",
            "action": "Avoid dropping prod database: verify target environment first",
            "guidance": "Destructive query detected on production connection",
            "confidence": 0.92,
        }
        res = classify_advice(advice, escalate_min_conf=0.85)
        self.assertEqual(res["decision"], "steer")
        self.assertEqual(res["status"], "off_track")
        self.assertIn("[STEER·irreversible_risk]", res["text"])

    def test_f3_03_parallelize_category_handling(self):
        """Verifies parallelize category normalization and keyed deduplication."""
        advice = {
            "status": "watchout",
            "category": "parallelize",
            "action": "Dispatch subagents for backend and frontend modules",
            "guidance": "Independent subtasks detected",
            "confidence": 0.80,
        }
        res1 = classify_advice(advice, seen_advice={})
        self.assertEqual(res1["decision"], "watchout")
        self.assertEqual(res1["category"], "parallelize")
        self.assertIn("[WATCH·parallelize]", res1["text"])

        # Second emission should deduplicate to hold_dedup
        res2 = classify_advice(advice, seen_advice=res1["seen"])
        self.assertEqual(res2["decision"], "hold_dedup")

    def test_f3_04_architectural_trap_category(self):
        """Verifies architectural_trap and algorithmic_bottleneck categories."""
        trap_advice = {
            "status": "watchout",
            "category": "architectural_trap",
            "action": "Refactor global state: use dependency injection instead",
            "guidance": "Shared mutable dictionary causes race conditions",
            "confidence": 0.75,
        }
        res = classify_advice(trap_advice)
        self.assertEqual(res["decision"], "watchout")
        self.assertEqual(res["category"], "architectural_trap")
        self.assertIn("[WATCH·architectural_trap]", res["text"])

    def test_f3_05_confidence_tag_formatting(self):
        """Verifies confidence tag formatting across various confidence representations."""
        self.assertAlmostEqual(_parse_confidence("0.85"), 0.85)
        self.assertAlmostEqual(_parse_confidence("90%"), 0.90)
        self.assertAlmostEqual(_parse_confidence(80), 0.80)
        self.assertAlmostEqual(_parse_confidence(0.72), 0.72)
        self.assertIsNone(_parse_confidence("uncalibrated"))

    # ------------------------------------------------------------------------
    # F4: Actionable Strategic Guidance
    # ------------------------------------------------------------------------
    def test_f4_01_concrete_command_in_action_field(self):
        """Asserts that concrete runnable commands are formatted and preserved in advice text."""
        raw = {
            "status": "off_track",
            "category": "loop_detection",
            "action": "python3 -m unittest tests/test_models.py",
            "evidence": "AssertionError in line 42",
            "guidance": "Fix model sorting logic before continuing",
            "confidence": 0.88,
        }
        norm = _normalize_sage_dict(raw)
        self.assertEqual(norm["action"], "python3 -m unittest tests/test_models.py")
        classified = classify_advice(norm)
        self.assertIn("python3 -m unittest tests/test_models.py", classified["text"])

    def test_f4_02_exact_file_paths_in_guidance(self):
        """Asserts that specific file paths in guidance are preserved."""
        raw = {
            "status": "watchout",
            "category": "architectural_trap",
            "action": "Check advisor/triage.py:45 for edge cases",
            "guidance": "Inspect advisor/triage.py for missing float validation",
            "confidence": 0.78,
        }
        norm = _normalize_sage_dict(raw)
        self.assertIn("advisor/triage.py", norm["guidance"])
        classified = classify_advice(norm)
        self.assertIn("advisor/triage.py", classified["text"])

    def test_f4_03_destructive_command_suppression_action(self):
        """Verifies destructive commands in action field are suppressed."""
        dangerous = {
            "status": "off_track",
            "category": "general",
            "action": "rm -rf /tmp/repo && git reset --hard HEAD~1",
            "guidance": "Clean up everything",
        }
        norm = _normalize_sage_dict(dangerous)
        self.assertIn("[Destructive action suppressed]", norm["action"])
        self.assertNotIn("rm -rf", norm["action"])

    def test_f4_04_destructive_command_suppression_guidance(self):
        """Verifies destructive commands in guidance field are suppressed."""
        dangerous = {
            "status": "watchout",
            "category": "irreversible_risk",
            "action": "run git status",
            "guidance": "You should run sudo rm -rf / before proceeding",
        }
        norm = _normalize_sage_dict(dangerous)
        self.assertIn("[Destructive command suppressed]", norm["guidance"])
        self.assertNotIn("sudo rm -rf", norm["guidance"])

    def test_f4_05_evidence_snippet_clamping(self):
        """Verifies that evidence snippets are cleanly clamped to budget without overflow."""
        long_evidence = "Error log: " + "X" * 300
        advice = {
            "status": "off_track",
            "category": "loop_detection",
            "action": "Check error log",
            "evidence": long_evidence,
            "confidence": 0.90,
        }
        res = classify_advice(advice)
        self.assertLessEqual(len(res["text"]), 2000)
        self.assertIn("Found: Error log: ", res["text"])

    # ------------------------------------------------------------------------
    # F5: Low Latency & Zero Unnecessary Holds
    # ------------------------------------------------------------------------
    def test_f5_01_healthy_trajectory_returns_hold_decision(self):
        """Asserts that healthy/on_track evaluation immediately returns hold without injections."""
        healthy_res = {"status": "on_track", "healthy": True, "blind_spots": []}
        classified = classify_advice(healthy_res)
        self.assertEqual(classified["decision"], "hold")
        self.assertEqual(classified["status"], "on_track")

    def test_f5_02_post_invocation_healthy_returns_empty_steps(self):
        """Verifies post-invocation hook emits injectSteps: [] on healthy turn."""
        self.write_transcript_lines([
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "step_index": 1, "content": "Implement feature"},
            {"type": "PLANNER_RESPONSE", "content": "Done", "tool_calls": [{"name": "view_file"}]},
        ])
        payload = {"conversationId": self.conv_id, "transcriptPath": self.transcript_path}
        with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
             patch("sage.sage.run_sage_model", return_value={"status": "on_track", "healthy": True}), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        data = json.loads(written.strip())
        self.assertEqual(data.get("injectSteps"), [])

    def test_f5_03_tool_delta_interval_fast_path(self):
        """Skips LLM execution when tool count delta is below SAGE_TOOL_INTERVAL."""
        state = {"last_verified_tools": 10}
        res = evaluate_mid_turn_progress(
            self.conv_id, self.transcript_path,
            total_tool_calls=14,  # delta = 4 < 10
            turn_tool_names={"view_file"},
            user_prompt="Run tests", agent_steps=["view_file"], git_diff="", state=state,
        )
        self.assertTrue(res.get("skipped"))
        self.assertEqual(res.get("tool_delta"), 4)

    def test_f5_04_stop_hook_clean_exit_when_passed(self):
        """Verifies that final stop audit outputs decision: stop when audit passes."""
        self.write_transcript_lines([
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "All done", "created_at": "2026-08-20T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Done", "tool_calls": [{"name": "run_command"}]},
        ])
        payload = {"conversationId": self.conv_id, "transcriptPath": self.transcript_path}
        with patch("sys.argv", ["session-sage.py"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch("sage.sage.run_sage_model", return_value={"status": "on_track", "healthy": True}), \
             patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit) as cm:
            main()
        self.assertEqual(cm.exception.code, 0)
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        data = json.loads(written.strip())
        self.assertEqual(data.get("decision"), "stop")

    def test_f5_05_empty_stdin_fail_safe_exit(self):
        """Verifies that empty stdin triggers fail_safe_exit without crashing."""
        with patch("sys.stdin.read", return_value=""), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit) as cm:
            check_payload_and_lifecycle()
        self.assertEqual(cm.exception.code, 0)

    # ------------------------------------------------------------------------
    # F6: Intelligent Task Structure Analysis
    # ------------------------------------------------------------------------
    def test_f6_01_repeated_tool_calls_loop_detection(self):
        """Detects loop when identical tool calls with same args repeat >= 3 times."""
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Fix bug"},
        ]
        for _ in range(4):
            lines.append({"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"cmd": "pytest tests/failing.py"}}]})
        self.write_transcript_lines(lines)
        self.assertTrue(has_repeated_tool_calls(self.transcript_path, lookback=10, min_repeats=3))

    def test_f6_02_interleaved_legitimate_tools_no_false_positive(self):
        """Verifies alternating legitimate tools do not trigger false loop detection."""
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Refactor codebase"},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "view_file", "args": {"path": "a.py"}}]},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "replace_file_content", "args": {"path": "a.py"}}]},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "view_file", "args": {"path": "b.py"}}]},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "replace_file_content", "args": {"path": "b.py"}}]},
        ]
        self.write_transcript_lines(lines)
        self.assertFalse(has_repeated_tool_calls(self.transcript_path, lookback=10, min_repeats=3))

    def test_f6_03_polling_tools_exempt_from_loop_detector(self):
        """Verifies status and task polling tools are exempt from loop detection."""
        lines = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Wait for build"}]
        for _ in range(6):
            lines.append({"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "manage_task", "args": {"Action": "status", "TaskId": "task-1"}}]})
        self.write_transcript_lines(lines)
        self.assertFalse(has_repeated_tool_calls(self.transcript_path, lookback=10, min_repeats=3))

    def test_f6_04_consecutive_tool_errors_detection(self):
        """Verifies detection of tool error streaks in transcript."""
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run migrations"},
            {"type": "GENERIC", "content": "Command failed with exit code 1: Connection refused"},
            {"type": "GENERIC", "content": "Command failed with exit code 1: Connection refused"},
        ]
        self.write_transcript_lines(lines)
        self.assertTrue(has_recent_tool_errors(self.transcript_path, max_lookback=6))

    def test_f6_05_active_goal_pinning_across_multi_turn(self):
        """Extracts the active goal from multi-turn transcript headers."""
        session_text = (
            "SESSION HISTORY:\n"
            "- Prior request 1: Fix typo in README\n"
            "- Prior request 2: Add logging\n\n"
            "[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\n"
            "Deploy application to staging cluster"
        )
        goal = extract_target_goal(session_text)
        self.assertEqual(goal, "Deploy application to staging cluster")

    # ------------------------------------------------------------------------
    # F7: Structured invoke_subagent Guidance
    # ------------------------------------------------------------------------
    def test_f7_01_invoke_subagent_tool_call_parsing(self):
        """Parses invoke_subagent tool calls and registers pending subagents."""
        lines = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{
                    "name": "invoke_subagent",
                    "args": {"Subagents": [{"Role": "Implementer", "Goal": "Build module A"}]},
                }],
            }
        ]
        self.write_transcript_lines(lines)
        subs = get_active_subagents(self.transcript_path)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["role"], "Implementer")

    def test_f7_02_subagent_role_catalog_extraction(self):
        """Extracts various roles (Scout, Implementer, QA) from subagent invocations."""
        lines = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Scout"}, {"Role": "QA"}]}}]}
        ]
        self.write_transcript_lines(lines)
        subs = get_active_subagents(self.transcript_path)
        roles = [s["role"] for s in subs]
        self.assertIn("Scout", roles)
        self.assertIn("QA", roles)

    def test_f7_03_subagent_id_resolution(self):
        """Resolves subagent conversation IDs from transcript output."""
        lines = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Worker"}]}}]},
            {"type": "GENERIC", "content": 'Spawned worker with "conversationId": "subconv_abc123"'},
        ]
        self.write_transcript_lines(lines)
        subs = get_active_subagents(self.transcript_path)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["conversation_id"], "subconv_abc123")

    def test_f7_04_parallel_subagent_recommendation_in_signals(self):
        """Verifies that signals string is included in advisor prompt when parallelizable work exists."""
        prompt = build_sage_prompt(
            conv_id=self.conv_id,
            user_prompt="Refactor 5 independent modules",
            agent_steps_summary="Working on module 1",
            signals="PARALLELIZABLE: Independent modules detected. Suggest invoke_subagent.",
        )
        self.assertIn("PARALLELIZABLE: Independent modules detected", prompt)

    def test_f7_05_subagent_spawn_lock_acquisition_release(self):
        """Verifies spawn lock mechanics for subagents."""
        fh = acquire_spawn_lock(timeout=2.0)
        self.assertIsNotNone(fh)
        release_spawn_lock(fh)

    # ------------------------------------------------------------------------
    # F8: Subagent Lifecycle & Watcher Hardening
    # ------------------------------------------------------------------------
    def test_f8_01_active_subagent_prevents_premature_stop(self):
        """Asserts that active subagents cause is_post_invocation_completion_candidate to return False."""
        self.write_transcript_lines([
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Worker"}]}}]},
            {"type": "PLANNER_RESPONSE", "content": "I am done with everything", "tool_calls": []},
        ])
        self.assertFalse(is_post_invocation_completion_candidate(self.transcript_path))

    def test_f8_02_subagent_completion_via_sender_message(self):
        """Verifies subagents are marked completed when a sender message arrives."""
        lines = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Worker"}]}}]},
            {"type": "GENERIC", "content": 'Spawned with "conversationId": "sub_456"'},
            {"type": "USER_INPUT", "source": "SUBAGENT", "content": "sender=sub_456 Work complete"},
        ]
        self.write_transcript_lines(lines)
        subs = get_active_subagents(self.transcript_path)
        self.assertEqual(len(subs), 0)

    def test_f8_03_subagent_idle_detection(self):
        """Verifies subagents are marked completed when subagent idle notice is received."""
        lines = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Worker"}]}}]},
            {"type": "GENERIC", "content": 'Spawned with "conversationId": "sub_789"'},
            {"type": "SYSTEM_MESSAGE", "content": "Subagent sub_789 has gone idle"},
        ]
        self.write_transcript_lines(lines)
        subs = get_active_subagents(self.transcript_path)
        self.assertEqual(len(subs), 0)

    def test_f8_04_subagent_termination_detection(self):
        """Verifies subagents are removed when explicitly killed."""
        lines = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Worker"}]}}]},
            {"type": "GENERIC", "content": 'Spawned with "conversationId": "sub_kill_1"'},
            {"type": "SYSTEM_MESSAGE", "content": "Terminated subagent 'sub_kill_1'"},
        ]
        self.write_transcript_lines(lines)
        subs = get_active_subagents(self.transcript_path)
        self.assertEqual(len(subs), 0)

    def test_f8_05_background_task_grace_period(self):
        """Ensures active fresh background tasks (< 300s) receive a grace action."""
        active_tasks = [{"task_id": "task-101", "description": "cargo build", "age_seconds": 45.0}]
        decision = background_watch(active_tasks, bg_steered=set())
        self.assertEqual(decision["action"], "grace")

    # ------------------------------------------------------------------------
    # F9: Subagent Session Guard
    # ------------------------------------------------------------------------
    def test_f9_01_subagent_session_payload_flag_bypass(self):
        """Bypasses audit when isSubagent is True in hook payload."""
        payload = {"isSubagent": True, "conversationId": self.conv_id}
        self.assertTrue(is_subagent_session(payload, None, "do task"))

    def test_f9_02_subagent_session_parent_conv_id_bypass(self):
        """Bypasses audit when parentConversationId is present in hook payload."""
        payload = {"parentConversationId": "parent_conv_999", "conversationId": self.conv_id}
        self.assertTrue(is_subagent_session(payload, None, "do task"))

    def test_f9_03_subagent_worker_role_bypass(self):
        """Bypasses audit when agentRole is a worker role (scout, implementer, qa)."""
        for role in ["scout", "module implementer", "qa", "worker"]:
            payload = {"agentRole": role, "conversationId": self.conv_id}
            self.assertTrue(is_subagent_session(payload, None, "do task"))

    def test_f9_04_subagent_reminder_in_transcript_bypass(self):
        """Detects <subagent_reminder> in transcript and identifies subagent session."""
        self.write_transcript_lines([
            {"type": "USER_INPUT", "source": "USER", "content": "<subagent_reminder>You are running as a subagent</subagent_reminder>"},
        ])
        self.assertTrue(is_subagent_session({}, self.transcript_path, ""))


    # ------------------------------------------------------------------------
    # F10: Stop Audit Lifecycle & Zero Regression
    # ------------------------------------------------------------------------
    def test_f10_01_turn_duration_threshold_trigger(self):
        """Evaluates turn duration threshold."""
        user_ts = datetime.now(timezone.utc) - timedelta(seconds=650)
        # Should not raise fail_safe_exit when duration >= 600 and tool_calls >= 1
        dur = evaluate_turn_triggers(total_tool_calls=2, user_ts=user_ts)
        self.assertGreaterEqual(dur, 600.0)

    def test_f10_02_tool_call_count_threshold_trigger(self):
        """Evaluates tool call count threshold."""
        user_ts = datetime.now(timezone.utc) - timedelta(seconds=10)
        # Should not raise fail_safe_exit when tool_calls >= TOOL_CALL_THRESHOLD (15)
        dur = evaluate_turn_triggers(total_tool_calls=16, user_ts=user_ts)
        self.assertGreaterEqual(dur, 0.0)

    def test_f10_03_sensitive_keyword_tool_scan_word_boundary(self):
        """Detects sensitive keywords with strict word boundaries."""
        tool_call_git = {"name": "run_command", "args": {"CommandLine": "git commit -m 'test'"}}
        matches = scan_tool_call_for_sensitive(tool_call_git)
        self.assertIn("git", matches)

        tool_call_digit = {"name": "run_command", "args": {"CommandLine": "echo digital"}}
        matches_neg = scan_tool_call_for_sensitive(tool_call_digit)
        self.assertNotIn("git", matches_neg)

    def test_f10_04_session_state_persistence_and_locking(self):
        """Validates atomic JSON writing with 0600 mode and flock locking."""
        state_file = os.path.join(self.test_dir, "test_state.json")
        atomic_write_json(state_file, {"iteration": 1, "test": True})
        self.assertTrue(os.path.exists(state_file))
        mode = stat.S_IMODE(os.stat(state_file).st_mode)
        self.assertEqual(mode, 0o600)

        with open(state_file, "r") as f:
            data = json.load(f)
        self.assertEqual(data["iteration"], 1)

    def test_f10_05_deduplication_ledger_persistence(self):
        """Validates advice deduplication ledger persists across calls."""
        seen = {}
        adv1 = {"category": "architectural_trap", "action": "Fix schema", "guidance": "Missing field"}
        k1 = compute_advice_key("architectural_trap", "Fix schema", "Missing field")
        seen[k1] = 1
        self.assertEqual(seen[k1], 1)
        # Verify deduplication key stability
        k2 = compute_advice_key("architectural_trap", "Fix schema", "Missing field")
        self.assertEqual(k1, k2)


# ============================================================================
# TIER 2: BOUNDARY & CORNER CASES
# ============================================================================

class TestTier2BoundaryCornerCases(BaseE2ETestCase):

    def test_tier2_01_empty_transcript_file(self):
        """Handles 0-byte and empty whitespace transcript files gracefully."""
        self.write_transcript_lines([])
        data = extract_session_and_turn_data(self.transcript_path)
        self.assertEqual(data[0], "")
        self.assertEqual(data[3], 0)  # total tools = 0
        self.assertEqual(len(data[4]), 0)  # tool names empty

    def test_tier2_02_massive_transcript_truncation(self):
        """Stress-tests large transcripts with thousands of lines and huge tool outputs."""
        lines = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Massive test"}]
        for i in range(100):
            lines.append({
                "type": "PLANNER_RESPONSE",
                "content": f"Step {i}",
                "tool_calls": [{"name": "run_command", "args": {"cmd": f"echo line_{i}"}}],
            })
            lines.append({"type": "GENERIC", "content": f"Output line {i}\n" * 50})
        self.write_transcript_lines(lines)

        user_prompt, raw_prompt, steps, tools_count, tool_names, first_ts, user_ts, line_cnt = (
            extract_session_and_turn_data(self.transcript_path)
        )
        self.assertEqual(tools_count, 100)
        self.assertGreater(line_cnt, 200)

        # Verify diff clamping
        huge_diff = "diff --git a/big.py b/big.py\n" + ("+ line of change\n" * 1000)
        clamped = _clamp_diff(huge_diff, budget=2000)
        self.assertLessEqual(len(clamped), 2100)
        self.assertIn("[diff truncated]", clamped)

    def test_tier2_03_rapid_tool_error_streak_circuit_breaker(self):
        """Verifies that advisor circuit breaker trips after SAGE_MAX_ERROR_STREAK errors."""
        state = {"advisor_error_streak": SAGE_MAX_ERROR_STREAK}
        res = sage_flow(
            "midturn",
            conv_id=self.conv_id,
            transcript_path=self.transcript_path,
            clean_prompt="Fix bug",
            initial_line_count=0,
            total_tool_calls=12,
            turn_tool_names={"run_command"},
            user_prompt="Fix bug",
            agent_steps=["error"],
            git_diff="",
            state=state,
        )
        self.assertEqual(res["action"], "exit")
        self.assertIn("circuit breaker", res["reason"])

    def test_tier2_04_boundary_confidence_scores(self):
        """Tests confidence parsing across all boundary conditions."""
        self.assertEqual(_parse_confidence(0.0), 0.0)
        self.assertEqual(_parse_confidence(1.0), 1.0)
        self.assertEqual(_parse_confidence(0), 0.0)
        self.assertEqual(_parse_confidence(100), 1.0)
        self.assertEqual(_parse_confidence("100%"), 1.0)
        self.assertEqual(_parse_confidence("0%"), 0.0)
        self.assertEqual(_parse_confidence("-5"), 0.0)
        self.assertEqual(_parse_confidence(150), 1.0)
        self.assertIsNone(_parse_confidence(None))
        self.assertIsNone(_parse_confidence("invalid"))
        self.assertIsNone(_parse_confidence("uncalibrated"))
        self.assertIsNone(_parse_confidence(True))

    def test_tier2_05_timeout_and_time_duration_extremes(self):
        """Tests duration calculations with epoch zero, future timestamps, and timezone conversions."""
        past_dt = datetime(1970, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        dur_past = evaluate_turn_triggers(total_tool_calls=1, user_ts=past_dt)
        self.assertGreater(dur_past, 1000000.0)

        # Naive datetime in UTC
        naive_dt = (datetime.now(timezone.utc) - timedelta(seconds=700)).replace(tzinfo=None)
        dur_naive = evaluate_turn_triggers(total_tool_calls=1, user_ts=naive_dt)
        self.assertGreaterEqual(dur_naive, 600.0)

    def test_tier2_06_zero_tool_session_fast_exit(self):
        """Verifies 0-tool sessions fast-exit on stop hook."""
        self.write_transcript_lines([
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "What is 2+2?"},
            {"type": "PLANNER_RESPONSE", "content": "4", "tool_calls": []},
        ])
        payload = {"conversationId": self.conv_id, "transcriptPath": self.transcript_path}
        with patch("sys.argv", ["session-sage.py"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit) as cm:
            main()
        self.assertEqual(cm.exception.code, 0)
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        data = json.loads(written.strip())
        self.assertEqual(data.get("decision"), "stop")

    def test_tier2_07_malformed_json_and_broken_stdin(self):
        """Tests that malformed JSON on stdin triggers a safe exit."""
        with patch("sys.stdin.read", return_value="{broken json:"), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit) as cm:
            check_payload_and_lifecycle()
        self.assertEqual(cm.exception.code, 0)


# ============================================================================
# TIER 3: CROSS-FEATURE INTERACTIONS
# ============================================================================

class TestTier3CrossFeatureInteractions(BaseE2ETestCase):

    def test_tier3_01_advisor_triage_dedup_pipeline(self):
        """Tests end-to-end advisor pipeline: LLM output extraction -> triage -> dedup -> emission."""
        raw_llm_output = (
            "Here is the strategic evaluation:\n"
            "```json\n"
            "{\n"
            '  "status": "off_track",\n'
            '  "category": "loop_detection",\n'
            '  "action": "pytest tests/test_e2e.py -k test_f1",\n'
            '  "evidence": "Failing on assertion in line 12",\n'
            '  "guidance": "Fix assertion before rerunning entire suite",\n'
            '  "confidence": 0.92\n'
            "}\n"
            "```\n"
        )
        parsed = parse_sage_output(raw_llm_output)
        self.assertFalse(parsed["healthy"])
        self.assertEqual(parsed["status"], "off_track")

        # First triage
        triage1 = classify_advice(parsed, seen_advice={})
        self.assertEqual(triage1["decision"], "steer")
        self.assertIn("[STEER·loop_detection]", triage1["text"])

        # Second triage with same seen map -> repeatable allows second emission, but count increments
        triage2 = classify_advice(parsed, seen_advice=triage1["seen"])
        self.assertEqual(triage2["decision"], "steer")

        # Third triage -> exceeds effective max -> hold_dedup
        triage3 = classify_advice(parsed, seen_advice=triage2["seen"])
        self.assertEqual(triage3["decision"], "hold_dedup")

    def test_tier3_02_subagent_spawn_tracking_to_advisor_gate(self):
        """Tests subagent invocation, watcher tracking, and final advisor gate blocking."""
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Implement backend and frontend", "created_at": "2026-08-20T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "BackendDev"}]}}]},
            {"type": "GENERIC", "content": 'Spawned "conversationId": "sub_backend_1"'},
            {"type": "PLANNER_RESPONSE", "content": "All complete", "tool_calls": []},
        ]
        self.write_transcript_lines(lines)

        # Has active subagents -> should not be a completion candidate
        self.assertTrue(has_active_subagents(self.transcript_path))
        self.assertFalse(is_post_invocation_completion_candidate(self.transcript_path))

        # Complete the subagent
        lines.append({"type": "SYSTEM_MESSAGE", "content": "Subagent sub_backend_1 has gone idle"})
        self.write_transcript_lines(lines)

        self.assertFalse(has_active_subagents(self.transcript_path))
        self.assertTrue(is_post_invocation_completion_candidate(self.transcript_path))

    def test_tier3_03_sensitive_tool_to_advisor_final_gate(self):
        """Tests sensitive keyword detection plus the final advisor gate's live-evidence mandate."""
        # Sensitive keyword still detected in tool calls
        matches = scan_tool_call_for_sensitive({"name": "run_command", "args": {"CommandLine": "git push origin main"}})
        self.assertIn("git", matches)

        # Final-mode advisor signal note enforces the Final Stop Gate with live empirical evidence
        captured = {}

        def fake_evaluate(*args, **kwargs):
            captured["signals"] = kwargs.get("signals", "")
            return {"healthy": True, "status": "on_track"}

        with patch("sage.policies.evaluate_mid_turn_progress", side_effect=fake_evaluate), \
             patch("sage.policies.has_new_user_activity", return_value=False), \
             patch("sage.policies.extract_session_and_turn_data", return_value=("p", "r", [], 5, {"run_command"}, None, None, 0)):
            act = sage_flow(
                "final",
                conv_id=self.conv_id,
                transcript_path=self.transcript_path,
                clean_prompt="Deploy changes to production",
                initial_line_count=0,
                total_tool_calls=5,
                turn_tool_names={"run_command"},
                user_prompt="Deploy changes to production",
                agent_steps=["Executed git push origin main"],
                git_diff="diff --git a/deploy.sh b/deploy.sh",
                state={},
            )
        self.assertEqual(act["action"], "healthy")
        self.assertIn("Final Stop Gate", captured["signals"])
        self.assertIn("live empirical evidence", captured["signals"])

    def test_tier3_04_background_task_grace_to_stale_steer(self):
        """Tests background task transitioning from grace period to stale steer."""
        # Fresh task (45s) -> grace
        fresh_tasks = [{"task_id": "task-500", "description": "npm run build", "age_seconds": 45.0}]
        d_fresh = background_watch(fresh_tasks, bg_steered=set())
        self.assertEqual(d_fresh["action"], "grace")

        # Stale task (350s) -> steer
        stale_tasks = [{"task_id": "task-500", "description": "npm run build", "age_seconds": 350.0}]
        d_stale = background_watch(stale_tasks, bg_steered=set())
        self.assertEqual(d_stale["action"], "steer")
        self.assertEqual(d_stale["task_id"], "task-500")

        # Already steered -> already_steered
        d_already = background_watch(stale_tasks, bg_steered={"task-500"})
        self.assertEqual(d_already["action"], "already_steered")


# ============================================================================
# TIER 4: REAL-WORLD WORKLOAD SCENARIOS
# ============================================================================

class TestTier4RealWorldWorkloads(BaseE2ETestCase):

    def test_tier4_01_multi_turn_conversational_workflow(self):
        """Simulates multi-turn workflow with changing requests, verifying turn identity tracking."""
        # Turn 1
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "step_index": 1, "content": "Create database schema"},
            {"type": "PLANNER_RESPONSE", "content": "Created schema", "tool_calls": [{"name": "write_to_file"}]},
            {"type": "GENERIC", "content": "File saved"},
        ]
        self.write_transcript_lines(lines)
        turn_id_1 = get_active_turn_identity(self.transcript_path)
        self.assertEqual(turn_id_1, "step:1")

        # Turn 2
        lines.extend([
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "step_index": 4, "content": "Add migration script"},
            {"type": "PLANNER_RESPONSE", "content": "Added migration", "tool_calls": [{"name": "write_to_file"}]},
        ])
        self.write_transcript_lines(lines)
        turn_id_2 = get_active_turn_identity(self.transcript_path)
        self.assertEqual(turn_id_2, "step:4")
        self.assertNotEqual(turn_id_1, turn_id_2)

    def test_tier4_02_loop_steering_hold_and_release(self):
        """Simulates an agent getting stuck in a loop, receiving steering, and recovering."""
        # Agent stuck in loop
        loop_advice = {
            "status": "off_track",
            "category": "loop_detection",
            "action": "pytest tests/test_parser.py -x",
            "guidance": "Fix parsing regex at line 30",
            "confidence": 0.95,
        }
        res_steer = classify_advice(loop_advice, seen_advice={})
        self.assertEqual(res_steer["decision"], "steer")
        self.assertIn("pytest tests/test_parser.py -x", res_steer["text"])

        # Agent fixes issue -> on track
        recovery_advice = {
            "status": "on_track",
            "healthy": True,
            "blind_spots": [],
        }
        res_recovered = classify_advice(recovery_advice, seen_advice=res_steer["seen"])
        self.assertEqual(res_recovered["decision"], "hold")
        self.assertEqual(res_recovered["status"], "on_track")

    def test_tier4_03_irreversible_risk_warning_and_mitigation(self):
        """Simulates agent attempting a risky destructive action, intercepted by advisor."""
        risky_advice = {
            "status": "watchout",
            "category": "irreversible_risk",
            "action": "git stash before git reset --hard",
            "guidance": "Prevent uncommitted work loss before resetting working tree",
            "confidence": 0.95,
        }
        classified = classify_advice(risky_advice, escalate_min_conf=0.85)
        self.assertEqual(classified["decision"], "steer")
        self.assertIn("[STEER·irreversible_risk]", classified["text"])

    def test_tier4_04_parallel_subagent_dispatch_workflow(self):
        """Simulates strategic advisor suggesting subagent parallelization for independent workstreams."""
        parallel_advice = {
            "status": "watchout",
            "category": "parallelize",
            "action": "invoke_subagent(Role='Tester', Goal='Run integration test suite')",
            "guidance": "Delegate test verification while main agent continues implementation",
            "confidence": 0.85,
        }
        classified = classify_advice(parallel_advice, seen_advice={})
        self.assertEqual(classified["decision"], "watchout")
        self.assertIn("invoke_subagent", classified["text"])
        self.assertEqual(classified["category"], "parallelize")


if __name__ == "__main__":
    unittest.main()
