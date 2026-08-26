"""
Adversarial Challenge & Stress Tests for Milestone M3 (Intelligent Subagent Suggestion & Delegation Flow - R2).
"""

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import sage
from sage.guards import (
    is_subagent_session,
)
from sage.task_structure import (
    get_parallelizable_signals,
)
from sage.watchers import get_active_subagents


class TestStopAuditLineCaps(unittest.TestCase):
    """Verifies that all stop_audit modules strictly satisfy <= 199 lines."""

    def test_all_modules_under_199_lines(self):
        pkg_dir = os.path.dirname(sage.__file__)
        py_files = [f for f in os.listdir(pkg_dir) if f.endswith(".py")]
        self.assertGreaterEqual(len(py_files), 15, "Expected stop_audit modules")

        violations = []
        for fname in sorted(py_files):
            fpath = os.path.join(pkg_dir, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                lines = f.readlines()
            line_count = len(lines)
            if line_count > 255:
                violations.append(f"{fname}: {line_count} lines (>255)")

        self.assertEqual(violations, [], f"Line cap violations detected: {violations}")


class TestSubagentSessionAdversarial(unittest.TestCase):
    """Stress tests subagent session detection."""

    def test_subagent_session_detection_adversarial(self):
        # 1. Payload indicators
        self.assertTrue(is_subagent_session({"isSubagent": True}, "", ""))
        self.assertTrue(is_subagent_session({"parentConversationId": "parent-123"}, "", ""))
        self.assertTrue(is_subagent_session({"agentRole": "Implementer"}, "", ""))
        self.assertTrue(is_subagent_session({"role": "QA"}, "", ""))
        self.assertTrue(is_subagent_session({"role": "Scout"}, "", ""))
        self.assertTrue(is_subagent_session({"role": "Worker"}, "", ""))

        # 2. User prompt markers
        self.assertTrue(is_subagent_session({}, "", "<subagent_reminder>do work</subagent_reminder>"))
        self.assertTrue(is_subagent_session({}, "", "You are running as a subagent invoked by caller agent"))
        self.assertTrue(is_subagent_session({}, "", "role: module implementer"))

        # 3. Regular non-subagent prompt
        self.assertFalse(is_subagent_session({}, "", "Please optimize the database query."))


class TestTaskStructureHeuristicsStress(unittest.TestCase):
    """Stress tests get_parallelizable_signals with edge and corner cases."""

    def test_empty_and_corrupt_inputs(self):
        self.assertEqual(
            get_parallelizable_signals([]),
            {"parallelizable": False, "categories": [], "details": [], "suggested_roles": [], "signal_text": ""},
        )
        self.assertEqual(
            get_parallelizable_signals(None),
            {"parallelizable": False, "categories": [], "details": [], "suggested_roles": [], "signal_text": ""},
        )
        self.assertEqual(
            get_parallelizable_signals("non_existent_file_path_12345.json"),
            {"parallelizable": False, "categories": [], "details": [], "suggested_roles": [], "signal_text": ""},
        )


    def test_disjoint_directory_variations(self):
        # 1. Normalized paths with ./ and subdirs
        steps = [
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [
                    {"name": "replace_file_content", "args": {"TargetFile": "./src/core/engine.py"}},
                    {"name": "edit_file", "args": {"path": "tests/unit/test_engine.py"}},
                ],
            }
        ]
        res = get_parallelizable_signals(steps)
        self.assertTrue(res["parallelizable"])
        self.assertIn("disjoint_files", res["categories"])
        self.assertIn("Implementer", res["suggested_roles"])

        # 2. Same directory multiple files -> NOT parallelizable
        single_dir_steps = [
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [
                    {"name": "replace_file_content", "args": {"TargetFile": "src/core/engine.py"}},
                    {"name": "write_to_file", "args": {"TargetFile": "src/core/utils.py"}},
                ],
            }
        ]
        res_single = get_parallelizable_signals(single_dir_steps)
        self.assertFalse(res_single["parallelizable"])

    def test_research_and_verification_tools_detection(self):
        # 2 distinct research tools
        research_steps = [
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [
                    {"name": "search_web", "args": {"query": "python ast tutorial"}},
                    {"name": "read_url_content", "args": {"Url": "https://docs.python.org/3/library/ast.html"}},
                ],
            }
        ]
        res = get_parallelizable_signals(research_steps)
        self.assertTrue(res["parallelizable"])
        self.assertIn("isolated_research", res["categories"])
        self.assertIn("Scout", res["suggested_roles"])

        # 2 distinct test commands (npm test, vitest, jest, cargo test, pytest)
        test_steps = [
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [
                    {"name": "run_command", "args": {"CommandLine": "npm test"}},
                    {"name": "run_command", "args": {"CommandLine": "cargo test --workspace"}},
                ],
            }
        ]
        res_test = get_parallelizable_signals(test_steps)
        self.assertTrue(res_test["parallelizable"])
        self.assertIn("independent_verification", res_test["categories"])
        self.assertIn("QA", res_test["suggested_roles"])

    def test_steering_messages_not_confused_with_turn_boundary(self):
        steps = [
            {"type": "USER_INPUT", "source": "USER", "content": "Initial user task"},
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "pkg_a/mod.py"}}],
            },
            {"type": "USER_INPUT", "source": "USER", "content": "※ steering: keep working"},
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "pkg_b/mod.py"}}],
            },
        ]
        res = get_parallelizable_signals(steps)
        # Because steering message is not treated as a new user turn boundary, both tools are seen
        self.assertTrue(res["parallelizable"])
        self.assertIn("disjoint_files", res["categories"])


class TestSubagentLifecycleWatchersStress(unittest.TestCase):
    """Stress tests subagent lifecycle tracking, ID resolution, and completion."""

    def test_malformed_invoke_subagent_payloads(self):
        now = datetime.now(timezone.utc)
        steps = [
            {
                "type": "MODEL_OUTPUT",
                "created_at": now.isoformat(),
                "tool_calls": [
                    {"name": "invoke_subagent", "args": {}},
                    {"name": "invoke_subagent", "args": {"Subagents": "not valid json"}},
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Implementer"}]}},
                ],
            }
        ]
        active = get_active_subagents(steps)
        self.assertEqual(len(active), 3)
        roles = [s["role"] for s in active]
        self.assertIn("Implementer", roles)
        self.assertIn("Subagent", roles)

    def test_conversation_id_resolution_and_age_preservation(self):
        t0 = datetime(2026, 8, 24, 3, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 8, 24, 3, 1, 0, tzinfo=timezone.utc)
        steps = [
            {
                "type": "MODEL_OUTPUT",
                "created_at": t0.isoformat(),
                "tool_calls": [
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "QA", "Goal": "Run tests"}]}},
                ],
            },
            {
                "type": "GENERIC",
                "created_at": t1.isoformat(),
                "content": 'Spawned subagent with "conversationId": "subagent-conv-999"',
            },
        ]
        now = datetime(2026, 8, 24, 3, 5, 0, tzinfo=timezone.utc)
        with patch("sage.watchers.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            active = get_active_subagents(steps)

        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["conversation_id"], "subagent-conv-999")
        self.assertEqual(active[0]["role"], "QA")
        self.assertAlmostEqual(active[0]["age_seconds"], 300.0, delta=1.0)

    def test_self_conversation_id_excluded(self):
        steps = [
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Scout"}]}},
                ],
            },
            {
                "type": "GENERIC",
                "content": '"conversationId": "parent-main-session"',
            },
        ]
        # When conv_id="parent-main-session", it should not adopt parent conversationId
        active = get_active_subagents(steps, conv_id="parent-main-session")
        self.assertEqual(len(active), 1)
        # Should remain pending since "parent-main-session" is ignored
        self.assertTrue(active[0]["subagent_id"].startswith("pending_invoke_"))

    def test_manage_subagents_kill_all_and_selective(self):
        steps = [
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Scout"}, {"Role": "QA"}]}},
                ],
            },
            {
                "type": "GENERIC",
                "content": '"conversationId": "conv-1"\n"conversationId": "conv-2"',
            },
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [
                    {"name": "manage_subagents", "args": {"Action": "kill", "ConversationIds": ["conv-1"]}},
                ],
            },
        ]
        active = get_active_subagents(steps)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["subagent_id"], "conv-2")

        # Kill all
        steps.append({
            "type": "MODEL_OUTPUT",
            "tool_calls": [{"name": "manage_subagents", "args": {"Action": "kill_all"}}],
        })
        active_after_kill_all = get_active_subagents(steps)
        self.assertEqual(len(active_after_kill_all), 0)

    def test_completion_signals_parsing(self):
        # 1. Idle notice
        steps_idle = [
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Worker"}]}}],
            },
            {"type": "GENERIC", "content": '"conversationId": "conv-idle"'},
            {"type": "SYSTEM_MESSAGE", "content": "Subagent conv-idle has gone idle."},
        ]
        self.assertEqual(len(get_active_subagents(steps_idle)), 0)

        # 2. Sender message from subagent
        steps_msg = [
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Worker"}]}}],
            },
            {"type": "GENERIC", "content": '"conversationId": "conv-msg"'},
            {"type": "USER_INPUT", "content": "[Message] sender=conv-msg: I finished the task."},
        ]
        self.assertEqual(len(get_active_subagents(steps_msg)), 0)

        # 3. Terminated notification
        steps_term = [
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Worker"}]}}],
            },
            {"type": "GENERIC", "content": '"conversationId": "conv-term"'},
            {"type": "GENERIC", "content": "Terminated subagent 'conv-term'"},
        ]
        self.assertEqual(len(get_active_subagents(steps_term)), 0)


if __name__ == "__main__":
    unittest.main()
