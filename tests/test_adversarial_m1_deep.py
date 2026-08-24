#!/usr/bin/env python3
"""
tests.test_adversarial_m1_deep - Exhaustive adversarial permutations for runner, hooks, locks, and AST.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from sage.guards import (
    is_subagent_session,
)
from sage.locking import (
    release_lock,
)
from sage.runner import run_session_stop_audit


class TestAdversarialDeepRunnerSignals(unittest.TestCase):
    """Exhaustive signal matrix testing for run_session_stop_audit under simulated mocks."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="adv_runner_test_")

    def tearDown(self):
        release_lock()
        import shutil
        if os.path.exists(self.tmp_dir):
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_fresh_environment(self, test_name="conv"):
        transcript_file = os.path.join(self.tmp_dir, f"{test_name}_transcript.jsonl")
        state_file = os.path.join(self.tmp_dir, f"{test_name}_audit_state.json")
        with open(transcript_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "content": "Adversarial test prompt",
                "created_at": "2026-08-24T00:00:00Z"
            }) + "\n")
            for i in range(5):
                f.write(json.dumps({
                    "type": "PLANNER_RESPONSE",
                    "content": f"Step {i}",
                    "tool_calls": [{"name": "view_file", "args": {"path": f"f_{i}.py"}}],
                    "created_at": "2026-08-24T00:01:00Z"
                }) + "\n")
        import uuid
        payload = json.dumps({
            "conversationId": f"adv_{test_name}_{uuid.uuid4().hex[:8]}",
            "transcriptPath": transcript_file,
            "fullyIdle": True,
            "workspacePaths": [self.tmp_dir],
        })
        return transcript_file, state_file, payload

    @patch("sage.runner.acquire_conversation_lock", return_value=True)
    @patch("sage.runner.get_active_subagents", return_value=[])
    @patch("sage.runner.get_active_background_tasks", return_value=[])
    @patch("sage.runner.is_subagent_session", return_value=False)
    @patch("sage.runner.get_git_diff", return_value="")
    def test_midturn_advisor_all_action_permutations(
        self, mock_diff, mock_sub, mock_bg, mock_subagents, mock_lock
    ):
        """Test every midturn advisor_flow action branch: exit, yield, progressed, error, hold_dedup, emit, healthy."""
        actions_to_test = [
            ("exit", {"action": "exit", "reason": "Advisor interval not met"}, "fail_safe"),
            ("yield", {"action": "yield", "reason": "Fresh user activity"}, "fail_safe"),
            ("progressed", {"action": "progressed", "tools": 12, "lines": 20}, "fail_safe"),
            ("error", {"action": "error"}, "fail_safe"),
            ("hold_dedup", {"action": "hold_dedup", "seen": {"h1": 2}}, "fail_safe"),
            ("emit_steer", {"action": "emit", "decision": "steer", "text": "Fix this bug", "seen": {}}, "emit_continue"),
            ("emit_watchout", {"action": "emit", "decision": "watchout", "text": "Watch memory", "seen": {}}, "emit_continue"),
            ("healthy", {"action": "healthy", "text": "Looking good"}, "fail_safe"),
        ]

        for label, act_dict, expected_handler in actions_to_test:
            with self.subTest(action=label):
                t_file, s_file, payload = self._create_fresh_environment(f"midturn_{label}")
                with patch("sage.runner.is_post_invocation", return_value=True), \
                     patch("sage.runner.is_post_invocation_completion_candidate", return_value=False), \
                     patch("sage.runner.advisor_flow", return_value=act_dict), \
                     patch("sage.runner.fail_safe_exit", side_effect=SystemExit(0)) as mock_fail_safe, \
                     patch("sage.runner.emit_continue_response", side_effect=SystemExit(0)) as mock_emit_cont:

                    with self.assertRaises(SystemExit):
                        run_session_stop_audit(payload)

                    if expected_handler == "fail_safe":
                        mock_fail_safe.assert_called_once()
                    elif expected_handler == "emit_continue":
                        mock_emit_cont.assert_called_once()
                        emitted_arg = mock_emit_cont.call_args[0][0]
                        self.assertIn(act_dict["text"], emitted_arg)

    @patch("sage.runner.acquire_conversation_lock", return_value=True)
    @patch("sage.runner.get_active_subagents", return_value=[])
    @patch("sage.runner.get_active_background_tasks", return_value=[])
    @patch("sage.runner.is_subagent_session", return_value=False)
    @patch("sage.runner.get_git_diff", return_value="")
    def test_final_advisor_gate_all_outcome_permutations(
        self, mock_diff, mock_sub, mock_bg, mock_subagents, mock_lock
    ):
        """Test final advisor gate outcomes: healthy recap, steer emit, dedup hold, model error, and skip."""
        outcomes = [
            # 1. Advisor approves -> record recap -> emit_recap_response
            (
                "healthy",
                {"action": "healthy", "recap": "All objectives fulfilled successfully."},
                "recap",
            ),
            # 2. Advisor emits a steer -> emit_continue_response (agent continues)
            (
                "steer",
                {"action": "emit", "decision": "steer", "text": "Add unit tests.", "seen": {}},
                "continue",
            ),
            # 3. Repeated identical advice -> hold_dedup -> fail_safe_exit
            (
                "hold_dedup",
                {"action": "hold_dedup", "seen": {"k1": 2}},
                "fail_safe",
            ),
            # 4. Advisor cascade failure -> fail open -> fail_safe_exit
            (
                "error",
                {"action": "error"},
                "fail_safe",
            ),
            # 5. Advisor gate skipped (disabled / circuit breaker) -> fail_safe_exit
            (
                "skip",
                {"action": "skip", "reason": "advisor disabled"},
                "fail_safe",
            ),
        ]

        for label, gate_return, expected_exit in outcomes:
            with self.subTest(outcome=label):
                t_file, s_file, payload = self._create_fresh_environment(f"advisor_gate_{label}")
                with patch("sage.runner.is_post_invocation", return_value=False), \
                     patch("sage.runner.final_advisor_gate", return_value=gate_return), \
                     patch("sage.runner.fail_safe_exit", side_effect=SystemExit(0)) as mock_fail_safe, \
                     patch("sage.runner.emit_continue_response", side_effect=SystemExit(0)) as mock_cont, \
                     patch("sage.runner.emit_recap_response", side_effect=SystemExit(0)) as mock_recap:

                    with patch("sage.runner.load_and_sync_session_state", return_value=(
                        "Adversarial test prompt", s_file,
                        {"last_audited_line_count": 0},
                        True
                    )):
                        with self.assertRaises(SystemExit):
                            run_session_stop_audit(payload)

                        if expected_exit == "recap":
                            mock_recap.assert_called_once()
                            self.assertIn("All objectives fulfilled successfully.", mock_recap.call_args[0][0])
                        elif expected_exit == "continue":
                            mock_cont.assert_called_once()
                            self.assertIn("Add unit tests.", mock_cont.call_args[0][0])
                        elif expected_exit == "fail_safe":
                            mock_fail_safe.assert_called_once()


class TestAdversarialSubagentDetection(unittest.TestCase):
    """Adversarial challenge for is_subagent_session detection logic."""

    def test_detects_subagents_in_all_variations(self):
        """Ensure subagents are caught across payloads, prompts, roles, and transcript content."""
        cases = [
            # In payload boolean
            ({"isSubagent": True}, "", "", True),
            ({"is_subagent": True}, "", "", True),
            # In payload parent conversation ID
            ({"parentConversationId": "parent-123"}, "", "", True),
            ({"parent_conversation_id": "parent-123"}, "", "", True),
            # In agent role
            ({"agentRole": "subagent_worker"}, "", "", True),
            ({"role": "implementer"}, "", "", True),
            ({"role": "codebase researcher"}, "", "", True),
            ({"role": "qa"}, "", "", True),
            ({"role": "scout"}, "", "", True),
            # In user prompt markers
            ({}, "Instructions: <subagent_reminder> you are a worker </subagent_reminder>", "", True),
            ({}, "You are running as a subagent invoked by caller agent (name: parent)", "", True),
            ({}, "Context for branch implementer: do work", "", True),
            # Clean primary agent
            ({}, "Refactor this module and run pytest", "", False),
        ]

        for payload, prompt, raw_prompt, expected in cases:
            with self.subTest(payload=payload, prompt=prompt[:30]):
                res = is_subagent_session(payload, None, prompt, raw_prompt)
                self.assertEqual(res, expected)


if __name__ == "__main__":
    unittest.main()
