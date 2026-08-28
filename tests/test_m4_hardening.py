#!/usr/bin/env python3
"""
tests.test_m4_hardening - Comprehensive unit hardening suite for Milestone M4 (R3).
Covers deep unit tests for destructive action detection, advisor category normalization,
triage escalation, policy circuit breakers, task structure extraction, and guards lifecycle.
"""

import io
import json
import unittest
from unittest.mock import patch

from sage.sage import (
    _normalize_sage_dict,
    build_sage_prompt,
)
from sage.guards import (
    DESTRUCTIVE_ACTION_RE,
    check_payload_and_lifecycle,
    format_hook_message,
    is_destructive_action,
    is_subagent_session,
)
from sage.policies import sage_flow, background_watch
from sage.task_structure import (
    _extract_file_path,
    _extract_research_target,
    _extract_test_target,
    get_parallelizable_signals,
)
from sage.triage import (
    classify_advice,
    compute_advice_key,
)


class TestDestructiveActionGuards(unittest.TestCase):
    """Deep validation of destructive action regex detection across all syntax branches."""

    def test_destructive_rm_rf_variants(self):
        destructive_cmds = [
            "rm -rf /tmp/test",
            "rm -fr /tmp/test",
            "rm -rfi /tmp/test",
            "rm -rf *",
            "sudo rm -rf /var/log",
            "sudo rm -fr /var/log",
            "sudo rm /etc/passwd",
        ]
        for cmd in destructive_cmds:
            with self.subTest(cmd=cmd):
                self.assertTrue(is_destructive_action(cmd), f"Should detect destructive command: {cmd}")
                self.assertIsNotNone(DESTRUCTIVE_ACTION_RE.search(cmd))

    def test_destructive_system_and_disk_commands(self):
        disk_cmds = [
            "mkfs /dev/sda1",
            "mkfs.ext4 /dev/sdb",
            "dd if=/dev/zero of=/dev/sda bs=1M",
            "dd if=/dev/urandom of=/dev/nvme0n1",
            ":(){ :|:& };:",
            ":() { :|:& };:",
            "chmod -R 777 /var/www",
            "chmod -R 777 .",
        ]
        for cmd in disk_cmds:
            with self.subTest(cmd=cmd):
                self.assertTrue(is_destructive_action(cmd), f"Should detect destructive command: {cmd}")

    def test_destructive_git_commands(self):
        git_cmds = [
            "git reset --hard",
            "git reset --hard HEAD~1",
            "git reset --hard origin/main",
            "git push --force origin main",
            "git push origin master --force",
        ]
        for cmd in git_cmds:
            with self.subTest(cmd=cmd):
                self.assertTrue(is_destructive_action(cmd), f"Should detect destructive git command: {cmd}")

    def test_destructive_sql_commands(self):
        sql_cmds = [
            "DROP DATABASE production",
            "drop database test_db",
            "DROP TABLE users",
            "drop table audit_logs",
            "TRUNCATE TABLE sessions",
            "truncate table events",
        ]
        for cmd in sql_cmds:
            with self.subTest(cmd=cmd):
                self.assertTrue(is_destructive_action(cmd), f"Should detect destructive SQL command: {cmd}")

    def test_non_destructive_safe_commands_allowed(self):
        safe_cmds = [
            "git push --force-with-lease origin main",
            "git push origin feature",
            "git status",
            "git diff HEAD~1",
            "git reset HEAD file.py",
            "rm file.txt",
            "rm -i test.py",
            "ls -la /tmp",
            "pytest tests/",
            "python3 -m unittest discover",
            "cargo test --all",
            "cat /var/log/syslog",
            "grep -rn 'pattern' .",
            "chmod 644 file.txt",
            "chmod +x script.sh",
            "SELECT * FROM users",
            "UPDATE users SET active=1",
        ]
        for cmd in safe_cmds:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_destructive_action(cmd), f"Should NOT flag safe command: {cmd}")


class TestAdvisorAllCategoriesNormalization(unittest.TestCase):
    """Validates normalization and metadata preservation across all 9 advisor categories."""

    def test_all_nine_categories_preserved(self):
        categories = [
            "loop_detection",
            "irreversible_risk",
            "parallelize_subagent",
            "parallelize",
            "architectural_trap",
            "general",
            "missing_proof",
            "algorithmic_bottleneck",
            "scope_drift",
            "fake_verification",
        ]
        for cat in categories:
            with self.subTest(category=cat):
                raw = {
                    "status": "off_track",
                    "category": cat,
                    "action": "run pytest",
                    "guidance": "Fix failing assertion",
                    "confidence": 0.9,
                    "evidence": "Failing test output",
                    "escalation": "first_warning",
                }
                norm = _normalize_sage_dict(raw)
                self.assertEqual(norm["category"], cat)
                self.assertEqual(norm["confidence"], 0.9)
                self.assertEqual(norm["action"], "run pytest")
                self.assertEqual(norm["escalation"], "first_warning")
                self.assertFalse(norm["healthy"])
                self.assertEqual(norm["status"], "off_track")

    def test_blind_spots_and_watchouts_string_and_list_normalization(self):
        # Raw string blind_spots
        norm1 = _normalize_sage_dict({
            "status": "off_track",
            "blind_spots": "Unchecked None return value",
        })
        self.assertEqual(norm1["blind_spots"], ["Unchecked None return value"])

        # List blind_spots
        norm2 = _normalize_sage_dict({
            "status": "off_track",
            "blind_spots": ["Issue A", "Issue B"],
        })
        self.assertEqual(norm2["blind_spots"], ["Issue A", "Issue B"])

        # Raw string watchouts
        norm3 = _normalize_sage_dict({
            "status": "watchout",
            "watchouts": "Subagent may stall",
        })
        self.assertEqual(norm3["watchouts"], ["Subagent may stall"])

        # Singular watchout key
        norm4 = _normalize_sage_dict({
            "status": "watchout",
            "watchout": "Long running test",
        })
        self.assertEqual(norm4["watchouts"], ["Long running test"])

    def test_destructive_command_suppression_in_normalization(self):
        norm = _normalize_sage_dict({
            "status": "off_track",
            "action": "rm -rf /tmp/build",
            "guidance": "git reset --hard HEAD~1",
        })
        self.assertIn("[Destructive action suppressed]", norm["action"])
        self.assertIn("[Destructive command suppressed]", norm["guidance"])

    def test_build_advisor_prompt_with_signals_parameter(self):
        prompt = build_sage_prompt(
            conv_id="test-conv-123",
            user_prompt="Refactor database layer",
            agent_steps_summary="Step 1: modified schema",
            signals="PARALLELIZABLE: 2 independent subtasks detected",
        )
        self.assertIn("ACTIVE SIGNALS:", prompt)
        self.assertIn("PARALLELIZABLE: 2 independent subtasks detected", prompt)


class TestTriageHardening(unittest.TestCase):
    """Unit tests for triage confidence, custom emission limits, deduplication, and clamping."""

    def test_custom_max_emissions_override(self):
        # max_emissions = 1 for standard category
        raw = {
            "status": "off_track",
            "category": "loop_detection",
            "action": "check loop",
            "guidance": "agent is repeating same edit",
            "confidence": 0.85,
        }
        res1 = classify_advice(raw, seen_advice={}, max_emissions=1)
        self.assertEqual(res1["decision"], "steer")
        seen1 = res1["seen"]

        res2 = classify_advice(raw, seen_advice=seen1, max_emissions=1)
        self.assertEqual(res2["decision"], "hold_dedup")

    def test_irreversible_risk_doubled_emission_limit(self):
        # irreversible_risk allows up to max_emissions * 2 emissions
        raw = {
            "status": "off_track",
            "category": "irreversible_risk",
            "action": "verify migration safety",
            "guidance": "migration may drop production column",
            "confidence": 0.95,
        }
        seen = {}
        # Emission 1
        res1 = classify_advice(raw, seen_advice=seen, max_emissions=1)
        self.assertEqual(res1["decision"], "steer")
        seen = res1["seen"]

        # Emission 2 (allowed because effective_max = 2)
        res2 = classify_advice(raw, seen_advice=seen, max_emissions=1)
        self.assertEqual(res2["decision"], "steer")
        seen = res2["seen"]

        # Emission 3 (blocked)
        res3 = classify_advice(raw, seen_advice=seen, max_emissions=1)
        self.assertEqual(res3["decision"], "hold_dedup")

    def test_escalation_variants_bypass_dedup(self):
        raw = {
            "status": "off_track",
            "category": "architectural_trap",
            "action": "refactor coupling",
            "guidance": "circular dependency between modules",
            "confidence": 0.88,
        }
        seen = {compute_advice_key("architectural_trap", "refactor coupling", "circular dependency between modules"): 1}

        # Without escalation flag, standard non-repeatable category deduplicates on count >= 1
        res_no_esc = classify_advice(raw, seen_advice=seen, max_emissions=2)
        # Note: for is_steer with count=1 < max_emissions=2, it emits
        self.assertEqual(res_no_esc["decision"], "steer")

        # When count reaches max_emissions (2), "ignored_advice" or "escalated" bypasses if count < max
        seen_full = {compute_advice_key("architectural_trap", "refactor coupling", "circular dependency between modules"): 2}
        raw_ignored = dict(raw, escalation="ignored_advice")
        # count >= effective_max (2) blocks even with escalation unless effective_max is higher
        res_blocked = classify_advice(raw_ignored, seen_advice=seen_full, max_emissions=2)
        self.assertEqual(res_blocked["decision"], "hold_dedup")

    def test_message_formatting_when_action_equals_guidance(self):
        raw_same = {
            "status": "off_track",
            "category": "general",
            "action": "Run test suite",
            "guidance": "Run test suite",
            "confidence": 0.8,
        }
        res_same = classify_advice(raw_same, seen_advice={})
        self.assertEqual(res_same["text"], "run test suite")

        raw_diff = {
            "status": "off_track",
            "category": "general",
            "action": "Run test suite",
            "guidance": "Tests will verify changes without regressions",
            "confidence": 0.8,
        }
        res_diff = classify_advice(raw_diff, seen_advice={})
        self.assertIn("tests will verify changes without regressions -> run test suite", res_diff["text"])

    def test_strict_length_clamping_under_2000_chars(self):
        raw_long = {
            "status": "off_track",
            "category": "architectural_trap",
            "action": "A" * 200,
            "guidance": "G" * 500,
            "evidence": "E" * 500,
            "confidence": 0.95,
        }
        res = classify_advice(raw_long, seen_advice={})
        self.assertLessEqual(len(res["text"]), 2000)


class TestPolicyHardening(unittest.TestCase):
    """Validates advisor flow circuit breakers, max steer limits, and background watch."""

    def test_advisor_flow_error_streak_circuit_breaker(self):
        state = {"advisor_error_streak": 3, "mid_turn_steers": 0}
        res_midturn = sage_flow(
            "midturn",
            conv_id="conv-1",
            transcript_path="/nonexistent",
            clean_prompt="task",
            initial_line_count=0,
            total_tool_calls=15,
            turn_tool_names=["run_command"],
            user_prompt="do task",
            agent_steps=[],
            git_diff="",
            state=state,
        )
        self.assertEqual(res_midturn["action"], "exit")
        self.assertIn("circuit breaker", res_midturn["reason"])

        res_final = sage_flow(
            "final",
            conv_id="conv-1",
            transcript_path="/nonexistent",
            clean_prompt="task",
            initial_line_count=0,
            total_tool_calls=15,
            turn_tool_names=["run_command"],
            user_prompt="do task",
            agent_steps=[],
            git_diff="",
            state=state,
        )
        self.assertEqual(res_final["action"], "skip")
        self.assertIn("circuit breaker", res_final["reason"])

    def test_advisor_flow_max_mid_turn_steers_ceiling(self):
        state = {"advisor_error_streak": 0, "mid_turn_steers": 5}
        with patch("sage.policies.MAX_MID_TURN_STEERS", 5):
            res_midturn = sage_flow(
                "midturn",
                conv_id="conv-1",
                transcript_path="/nonexistent",
                clean_prompt="task",
                initial_line_count=0,
                total_tool_calls=15,
                turn_tool_names=["run_command"],
                user_prompt="do task",
                agent_steps=[],
                git_diff="",
                state=state,
            )
            self.assertEqual(res_midturn["action"], "exit")
            self.assertIn("max mid-turn steers reached", res_midturn["reason"])


    def test_background_watch_policy_routing(self):
        # Empty active tasks
        self.assertEqual(background_watch([], set()), {"action": "none"})

        # Fresh active task (< 300s) -> grace
        fresh_tasks = [{"task_id": "t1", "description": "build", "age_seconds": 120.0}]
        self.assertEqual(background_watch(fresh_tasks, set()), {"action": "grace"})

        # Stale active task (> 300s) not steered -> steer
        stale_tasks = [{"task_id": "t2", "description": "train", "age_seconds": 450.0}]
        res_steer = background_watch(stale_tasks, set())
        self.assertEqual(res_steer["action"], "steer")
        self.assertEqual(res_steer["task_id"], "t2")

        # Stale active task already steered -> already_steered
        self.assertEqual(background_watch(stale_tasks, {"t2"}), {"action": "already_steered"})


class TestTaskStructureExtractors(unittest.TestCase):
    """Validates target extraction helpers in task structure analysis."""

    def test_extract_file_path_all_variants(self):
        self.assertEqual(_extract_file_path({"TargetFile": "/path/a.py"}), "/path/a.py")
        self.assertEqual(_extract_file_path({"AbsolutePath": "/path/b.py"}), "/path/b.py")
        self.assertEqual(_extract_file_path({"TargetFiles": ["/path/c.py", "/path/d.py"]}), "/path/c.py")
        self.assertEqual(_extract_file_path({"target_file": "/path/e.py"}), "/path/e.py")
        self.assertEqual(_extract_file_path({"path": "/path/f.py"}), "/path/f.py")
        self.assertEqual(_extract_file_path({"file": "/path/g.py"}), "/path/g.py")
        self.assertIsNone(_extract_file_path({}))
        self.assertIsNone(_extract_file_path(None))
        self.assertIsNone(_extract_file_path("not a dict"))

    def test_extract_research_target_all_variants(self):
        self.assertEqual(_extract_research_target("search_web", {"query": "python ast"}), "search_web:python ast")
        self.assertEqual(_extract_research_target("search_web", {"Query": "python ast"}), "search_web:python ast")
        self.assertEqual(_extract_research_target("read_url_content", {"Url": "https://example.com"}), "read_url_content:https://example.com")
        self.assertEqual(_extract_research_target("read_url_content", {"url": "https://example.com"}), "read_url_content:https://example.com")
        self.assertEqual(_extract_research_target("grep_search", {"Pattern": "def foo"}), "grep_search:def foo")
        self.assertEqual(_extract_research_target("grep_search", {"pattern": "def foo"}), "grep_search:def foo")
        self.assertEqual(_extract_research_target("search_web", {}), "search_web")
        self.assertIsNone(_extract_research_target("search_web", None))

    def test_extract_test_target_all_runners(self):
        self.assertEqual(_extract_test_target({"CommandLine": "pytest tests/"}), "pytest tests/")
        self.assertEqual(_extract_test_target({"command": "python -m unittest tests/test_foo.py"}), "python -m unittest tests/test_foo.py")
        self.assertEqual(_extract_test_target({"cmd": "cargo test --workspace"}), "cargo test --workspace")
        self.assertEqual(_extract_test_target({"cmd": "npm test"}), "npm test")
        self.assertEqual(_extract_test_target({"cmd": "go test ./..."}), "go test ./...")
        self.assertEqual(_extract_test_target({"cmd": "vitest run"}), "vitest run")
        self.assertEqual(_extract_test_target({"cmd": "jest"}), "jest")
        self.assertIsNone(_extract_test_target({"CommandLine": "ls -la"}))
        self.assertIsNone(_extract_test_target(None))

    def test_get_parallelizable_signals_disjoint_and_single_directories(self):
        # Disjoint directories (>=2 directories with >=2 files) -> parallelizable
        steps_disjoint = [
            {"type": "TOOL_USE", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/src/module_a/foo.py"}}]},
            {"type": "TOOL_USE", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/src/module_b/bar.py"}}]},
        ]
        sig_disjoint = get_parallelizable_signals(steps_disjoint)
        self.assertTrue(sig_disjoint["parallelizable"])
        self.assertIn("disjoint_files", sig_disjoint["categories"])
        self.assertIn("Implementer", sig_disjoint["suggested_roles"])

        # Single directory with multiple files -> NOT parallelizable by directory rule alone
        steps_single = [
            {"type": "TOOL_USE", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/src/module_a/foo.py"}}]},
            {"type": "TOOL_USE", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/src/module_a/bar.py"}}]},
        ]
        sig_single = get_parallelizable_signals(steps_single)
        self.assertFalse(sig_single["parallelizable"])


class TestGuardsLifecycleHardening(unittest.TestCase):
    """Validates hook message formatting, subagent session detection, and lifecycle checks."""

    def test_format_hook_message_valid_and_invalid_kinds(self):
        for kind in ("steering", "steerer", "recap", "adviser", "advisor"):
            with self.subTest(kind=kind):
                msg = format_hook_message(kind, "Investigate failure")
                self.assertTrue(msg.startswith(f"※ {kind.lower()}:"))
                self.assertIn("Investigate failure", msg)

        # Invalid kind raises ValueError
        with self.assertRaises(ValueError):
            format_hook_message("invalid_kind", "content")

    def test_format_hook_message_strips_redundant_prefixes(self):
        self.assertEqual(format_hook_message("steering", "※ steering: Fix bug"), "※ steering: Fix bug")
        self.assertEqual(format_hook_message("sage", "[sage] Check AST"), "※ sage: Check AST")
        self.assertEqual(format_hook_message("advisor", "[advisor] Check AST"), "※ advisor: Check AST")
        self.assertEqual(format_hook_message("recap", "**recap** Summary of work"), "※ recap: Summary of work")

    def test_is_subagent_session_detection(self):
        self.assertTrue(is_subagent_session({"isSubagent": True}, None, "root prompt"))
        self.assertTrue(is_subagent_session({"parentConversationId": "parent-123"}, None, "root prompt"))
        self.assertTrue(is_subagent_session({"agentRole": "worker"}, None, "root prompt"))
        self.assertTrue(is_subagent_session({"role": "scout"}, None, "root prompt"))
        self.assertTrue(is_subagent_session({}, None, "<subagent_reminder>you are a subagent</subagent_reminder>"))
        self.assertTrue(is_subagent_session({}, None, "you are running as a subagent"))
        self.assertFalse(is_subagent_session({}, None, "normal user request to optimize database"))

    @patch("sage.guards.fail_safe_exit")
    def test_check_payload_and_lifecycle_termination_reasons(self, mock_exit):
        mock_exit.side_effect = SystemExit(0)
        reasons = [
            "max_steps_exceeded",
            "error",
            "canceled",
            "cancelled",
            "user_abort",
            "user_interrupt",
            "abort",
            "system_error",
        ]
        for reason in reasons:
            with self.subTest(reason=reason):
                with patch("sys.stdin", io.StringIO(json.dumps({"terminationReason": reason}))):
                    with self.assertRaises(SystemExit):
                        check_payload_and_lifecycle()
                mock_exit.assert_called_with(f"Skipping on termination reason: {reason}")


class TestM4VerificationRunner(unittest.TestCase):
    def test_canonical_runner_executes_m2_empirical_suite(self):
        import scripts.verification.run_m4_verification as verification

        modules = []

        def fake_run_suite(_name, module):
            modules.append(module)
            return True, 1, 0.0

        with patch.object(verification, "verify_static_invariants", return_value=True), \
                patch.object(verification, "run_suite", side_effect=fake_run_suite), \
                patch.object(verification, "run_full_discovery", return_value=(True, 1, 0.0)), \
                patch("builtins.print"):
            verification.main()

        self.assertTrue(any("test_m2_empirical_stress" in m for m in modules))


if __name__ == "__main__":
    unittest.main()
