#!/usr/bin/env python3
"""
tests.test_m4_adversarial_hardening - Adversarial & Integration Hardening Suite for Milestone M4 (R3).
Stress tests destructive evasion attempts, confidence score permutations, rapid tool interleaving,
deep recursive loops, and ghost subagent watcher edge cases.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from advisor.advisor import (
    _normalize_advisor_dict,
    extract_target_goal,
)
from advisor.guards import (
    is_destructive_action,
)
from advisor.policies import advisor_flow, background_watch
from advisor.task_structure import (
    get_parallelizable_signals,
)
from advisor.transcript import (
    has_repeated_tool_calls,
)
from advisor.triage import (
    _parse_confidence,
    classify_advice,
)
from advisor.watchers import (
    get_active_background_tasks,
    get_active_subagents,
)


class TestDestructiveEvasionAttempts(unittest.TestCase):
    """Adversarial testing of destructive command evasion and suppression."""

    def test_split_and_reordered_rm_flags(self):
        evasion_attempts = [
            "rm -rf /tmp/target",
            "rm -fr /tmp/target",
            "rm -r -f /var/data",
            "rm -f -r /var/data",
            "rm --recursive --force /opt/app",
            "rm --force --recursive /opt/app",
            "rm -rfi /tmp/scratch",
            "sudo rm -rf /etc/config",
            "sudo rm /bin/sh",
        ]
        for cmd in evasion_attempts:
            with self.subTest(cmd=cmd):
                self.assertTrue(is_destructive_action(cmd), f"Should detect destructive command: {cmd}")
                norm = _normalize_advisor_dict({"status": "off_track", "action": cmd, "guidance": cmd})
                self.assertIn("[Destructive action suppressed]", norm["action"])
                self.assertIn("[Destructive command suppressed]", norm["guidance"])

    def test_git_force_push_short_and_long_flags(self):
        force_pushes = [
            "git push --force origin main",
            "git push origin master --force",
            "git push -f origin main",
            "git push origin feat -f",
            "git push -f",
            "git push -vf origin main",
        ]
        for cmd in force_pushes:
            with self.subTest(cmd=cmd):
                self.assertTrue(is_destructive_action(cmd), f"Should detect destructive git push: {cmd}")

    def test_force_with_lease_does_not_exempt_additional_force_flag(self):
        force_pushes = [
            "git push --force-with-lease --force origin main",
            "git push -f --force-with-lease origin main",
        ]
        for cmd in force_pushes:
            with self.subTest(cmd=cmd):
                self.assertTrue(is_destructive_action(cmd), f"Should detect destructive git push: {cmd}")

    def test_rm_flags_remain_destructive_after_other_options(self):
        for cmd in ("rm --preserve-root=no -rf /", "rm -I -rf /tmp/target"):
            with self.subTest(cmd=cmd):
                self.assertTrue(is_destructive_action(cmd), f"Should detect destructive rm: {cmd}")

    def test_safe_force_with_lease_permitted(self):
        lease_pushes = [
            "git push --force-with-lease origin main",
            "git push --force-with-lease",
            "git push origin feature --force-with-lease",
        ]
        for cmd in lease_pushes:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_destructive_action(cmd), f"Should allow safe force-with-lease: {cmd}")

    def test_sql_drop_and_truncate_variations(self):
        sql_variants = [
            "DROP DATABASE prod_db",
            "drop database analytics",
            "DROP TABLE users",
            "drop table customer_records",
            "TRUNCATE TABLE session_logs",
            "truncate table telemetry",
        ]
        for sql in sql_variants:
            with self.subTest(sql=sql):
                self.assertTrue(is_destructive_action(sql), f"Should detect destructive SQL: {sql}")

    def test_chained_compound_destructive_commands(self):
        compound_cmds = [
            "cd /tmp && rm -rf repo && cd -",
            "python3 test.py || git reset --hard HEAD~1",
            "echo 'cleaning' ; chmod -R 777 /var/www ; echo 'done'",
            "dd if=/dev/zero of=/dev/sda bs=4M",
            ":(){ :|:& };:",
        ]
        for cmd in compound_cmds:
            with self.subTest(cmd=cmd):
                self.assertTrue(is_destructive_action(cmd), f"Should detect chained destructive command: {cmd}")


class TestConfidencePermutationsAndEdgeCases(unittest.TestCase):
    """Adversarial stress testing for confidence score parsing and boundary gates."""

    def test_confidence_parsing_permutations(self):
        # Valid floats
        self.assertAlmostEqual(_parse_confidence(0.85), 0.85)
        self.assertAlmostEqual(_parse_confidence(0.0), 0.0)
        self.assertAlmostEqual(_parse_confidence(1.0), 1.0)
        self.assertAlmostEqual(_parse_confidence(0.70), 0.70)

        # Percentage strings
        self.assertAlmostEqual(_parse_confidence("95%"), 0.95)
        self.assertAlmostEqual(_parse_confidence("100%"), 1.0)
        self.assertAlmostEqual(_parse_confidence("0%"), 0.0)
        self.assertAlmostEqual(_parse_confidence("70.5%"), 0.705)

        # Scale 1-100 values
        self.assertAlmostEqual(_parse_confidence(85), 0.85)
        self.assertAlmostEqual(_parse_confidence("90"), 0.90)

        # Scientific notation
        self.assertAlmostEqual(_parse_confidence("1e-1"), 0.1)

        # Boolean protection (Booleans inherit from int in Python, must return None)
        self.assertIsNone(_parse_confidence(True))
        self.assertIsNone(_parse_confidence(False))

        # Invalid inputs
        self.assertIsNone(_parse_confidence(None))
        self.assertIsNone(_parse_confidence([]))
        self.assertIsNone(_parse_confidence({}))
        self.assertIsNone(_parse_confidence("invalid_confidence"))
        self.assertIsNone(_parse_confidence("NaN"))
        self.assertIsNone(_parse_confidence("Infinity"))

    def test_boundary_confidence_triage_decision_transitions(self):
        # Just below steer threshold (0.69999) -> watchout
        advice_low = {
            "status": "off_track",
            "category": "loop_detection",
            "action": "fix loop",
            "guidance": "agent is looping",
            "confidence": 0.69999,
        }
        res_low = classify_advice(advice_low, seen_advice={})
        self.assertEqual(res_low["decision"], "watchout")
        self.assertEqual(res_low["status"], "watchout")

        # Exactly at steer threshold (0.70) -> steer
        advice_steer = {
            "status": "off_track",
            "category": "loop_detection",
            "action": "fix loop",
            "guidance": "agent is looping",
            "confidence": 0.70,
        }
        res_steer = classify_advice(advice_steer, seen_advice={})
        self.assertEqual(res_steer["decision"], "steer")
        self.assertEqual(res_steer["status"], "off_track")

        # Irreversible risk below escalation threshold (0.84999) -> remains watchout
        advice_risk_low = {
            "status": "watchout",
            "category": "irreversible_risk",
            "action": "check migration",
            "guidance": "risk of table drop",
            "confidence": 0.84999,
        }
        res_risk_low = classify_advice(advice_risk_low, seen_advice={})
        self.assertEqual(res_risk_low["decision"], "watchout")

        # Irreversible risk at escalation threshold (0.85) -> promoted to steer
        advice_risk_high = {
            "status": "watchout",
            "category": "irreversible_risk",
            "action": "check migration",
            "guidance": "risk of table drop",
            "confidence": 0.85,
        }
        res_risk_high = classify_advice(advice_risk_high, seen_advice={})
        self.assertEqual(res_risk_high["decision"], "steer")
        self.assertEqual(res_risk_high["status"], "off_track")


class TestRapidFireToolInterleavingStress(unittest.TestCase):
    """Stress tests high-throughput tool interleaving and task structure analysis."""

    def test_multi_turn_goal_extraction_with_50_turns(self):
        turns = []
        for i in range(1, 51):
            turns.append(f"[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\nGoal turn {i} - do subtask {i}")
        full_prompt = "\n\n".join(turns)
        goal = extract_target_goal(full_prompt)
        self.assertIn("Goal turn 50", goal)
        self.assertNotIn("Goal turn 1", goal)

    def test_parallelizable_signals_under_heavy_interleaving(self):
        # 100 interleaved steps across 3 disjoint packages, 4 research queries, and 3 test suites
        steps = []
        for i in range(25):
            steps.append({"type": "TOOL_USE", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": f"/app/pkg_{i % 3}/module_{i}.py"}}]})
            steps.append({"type": "TOOL_USE", "tool_calls": [{"name": "search_web", "args": {"query": f"research topic {i % 4}"}}]})
            steps.append({"type": "TOOL_USE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": f"pytest tests/test_{i % 3}.py"}}]})
            steps.append({"type": "TOOL_USE", "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": f"/app/pkg_{i % 3}/module_{i}.py"}}]})

        sig = get_parallelizable_signals(steps)
        self.assertTrue(sig["parallelizable"])
        self.assertIn("disjoint_files", sig["categories"])
        self.assertIn("isolated_research", sig["categories"])
        self.assertIn("independent_verification", sig["categories"])
        self.assertIn("Implementer", sig["suggested_roles"])
        self.assertIn("Scout", sig["suggested_roles"])
        self.assertIn("QA", sig["suggested_roles"])

    def test_read_only_file_views_do_not_signal_disjoint_edits(self):
        steps = [{
            "type": "PLANNER_RESPONSE",
            "tool_calls": [
                {"name": "view_file", "args": {"path": "/repo/src/a.py"}},
                {"name": "view_file", "args": {"path": "/repo/tests/test_a.py"}},
            ],
        }]
        sig = get_parallelizable_signals(steps)
        self.assertFalse(sig["parallelizable"])
        self.assertNotIn("disjoint_files", sig["categories"])


class TestDeepRecursiveLoopsAndCircuitBreaker(unittest.TestCase):
    """Adversarial tests for tool loop detection and advisor error circuit breakers."""

    def _create_temp_transcript(self, steps):
        tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".jsonl")
        for s in steps:
            tmp.write(json.dumps(s) + "\n")
        tmp.flush()
        tmp.close()
        self.addCleanup(lambda: os.path.exists(tmp.name) and os.unlink(tmp.name))
        return tmp.name

    def test_repetitive_tool_calls_loop_detector(self):
        looping_steps = [
            {"type": "USER_INPUT", "source": "USER", "content": "run task"},
            {"type": "TOOL_USE", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": "a.py"}}]},
            {"type": "TOOL_USE", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": "a.py"}}]},
            {"type": "TOOL_USE", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": "a.py"}}]},
            {"type": "TOOL_USE", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": "a.py"}}]},
        ]
        tpath = self._create_temp_transcript(looping_steps)
        self.assertTrue(has_repeated_tool_calls(tpath))

        healthy_steps = [
            {"type": "USER_INPUT", "source": "USER", "content": "run task"},
            {"type": "TOOL_USE", "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": "a.py"}}]},
            {"type": "TOOL_USE", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": "a.py"}}]},
            {"type": "TOOL_USE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest"}}]},
            {"type": "TOOL_USE", "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": "b.py"}}]},
        ]
        hpath = self._create_temp_transcript(healthy_steps)
        self.assertFalse(has_repeated_tool_calls(hpath))

    def test_polling_tools_exempt_from_loop_detection(self):
        polling_steps = [
            {"type": "USER_INPUT", "source": "USER", "content": "run task"},
            {"type": "TOOL_USE", "tool_calls": [{"name": "manage_task", "args": {"Action": "status", "TaskId": "t1"}}]},
            {"type": "TOOL_USE", "tool_calls": [{"name": "manage_task", "args": {"Action": "status", "TaskId": "t1"}}]},
            {"type": "TOOL_USE", "tool_calls": [{"name": "manage_task", "args": {"Action": "status", "TaskId": "t1"}}]},
            {"type": "TOOL_USE", "tool_calls": [{"name": "manage_task", "args": {"Action": "status", "TaskId": "t1"}}]},
            {"type": "TOOL_USE", "tool_calls": [{"name": "list_dir", "args": {"DirectoryPath": "/tmp"}}]},
            {"type": "TOOL_USE", "tool_calls": [{"name": "list_dir", "args": {"DirectoryPath": "/tmp"}}]},
        ]
        ppath = self._create_temp_transcript(polling_steps)
        self.assertFalse(has_repeated_tool_calls(ppath))

    def test_error_streak_triggers_circuit_breaker(self):
        state = {"advisor_error_streak": 3, "mid_turn_steers": 0}
        act = advisor_flow(
            "midturn",
            conv_id="conv-err",
            transcript_path="/nonexistent",
            clean_prompt="task",
            initial_line_count=0,
            total_tool_calls=20,
            turn_tool_names=["replace_file_content"],
            user_prompt="do task",
            agent_steps=[],
            git_diff="",
            state=state,
        )
        self.assertEqual(act["action"], "exit")
        self.assertIn("circuit breaker open", act["reason"])


class TestGhostSubagentAndWatcherEdgeCases(unittest.TestCase):
    """Stress tests subagent lifecycle, complex IDs, idle signals, and stale task watchdogs."""

    def test_subagent_lifecycle_with_varied_ids_and_signals(self):
        steps = [
            # Spawn 3 subagents with varied ID conventions
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{
                    "name": "invoke_subagent",
                    "args": {"Subagents": [
                        {"Role": "Implementer", "Goal": "build module"},
                        {"Role": "QA", "Goal": "test module"},
                        {"Role": "Scout", "Goal": "research docs"},
                    ]},
                }],
            },
            # Map pending subagents to conversation IDs
            {
                "type": "GENERIC",
                "content": 'Spawned:\n"conversationId": "subagent_alpha_001"\n"conversationId": "beta_worker_99"\n"conversationId": "gamma_scout_xyz"',
            },
            # Subagent alpha sends a completion message
            {
                "type": "USER_INPUT",
                "content": "[Message] sender=subagent_alpha_001 content=Completed implementation.",
            },
            # Subagent beta sends an idle notification
            {
                "type": "SYSTEM_MESSAGE",
                "content": "Subagent beta_worker_99 has gone idle after completing QA tasks.",
            },
            # Subagent gamma is still active
        ]

        active = get_active_subagents(steps)
        active_ids = {s.get("conversation_id") or s.get("subagent_id") for s in active}
        self.assertIn("gamma_scout_xyz", active_ids)
        self.assertNotIn("subagent_alpha_001", active_ids)
        self.assertNotIn("beta_worker_99", active_ids)

    def test_background_task_watchdog_transition_and_staleness(self):
        now = datetime.now(timezone.utc)
        t_fresh = (now - timedelta(seconds=60)).isoformat()
        t_stale = (now - timedelta(seconds=400)).isoformat()

        steps = [
            {
                "type": "GENERIC",
                "created_at": t_stale,
                "content": "Tool is running as a background task with task id: conv1/task-101\nTask Description: training model",
            },
            {
                "type": "GENERIC",
                "created_at": t_fresh,
                "content": "Tool is running as a background task with task id: conv1/task-102\nTask Description: compiling assets",
            },
        ]

        active_tasks = get_active_background_tasks(steps, conv_id="conv1", parse_ts_func=datetime.fromisoformat)
        self.assertEqual(len(active_tasks), 2)

        # Unsteered stale task triggers steer
        decision1 = background_watch(active_tasks, bg_steered=set())
        self.assertEqual(decision1["action"], "steer")
        self.assertEqual(decision1["task_id"], "conv1/task-101")

        # After steering task-101, remaining fresh task gets grace period
        decision2 = background_watch(active_tasks, bg_steered={"conv1/task-101"})
        self.assertEqual(decision2["action"], "grace")

        # If only stale task exists and is already steered -> already_steered
        stale_only = [t for t in active_tasks if t["task_id"] == "conv1/task-101"]
        decision3 = background_watch(stale_only, bg_steered={"conv1/task-101"})
        self.assertEqual(decision3["action"], "already_steered")


if __name__ == "__main__":
    unittest.main()
