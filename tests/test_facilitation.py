#!/usr/bin/env python3
"""
tests.test_facilitation - Post-settle delegation advice (advice-only).

Regression guard for facilitation mode: after sage approves a recap
(goal_settled), any inline execution turn must produce an advisory
[EVT·facilitation] signal pushing the main agent to delegate via
invoke_subagent — without ever blocking the gate (advice-only contract).
"""

import unittest
from unittest.mock import patch

from sage.facilitation import facilitation_signal


def _step(content, tool_calls=None):
    return {"type": "GENERIC", "content": content, "tool_calls": tool_calls or [],
            "created_at": "2026-08-28T10:00:00+07:00"}


def _user(content):
    return {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": content,
            "tool_calls": [], "created_at": "2026-08-28T09:59:00+07:00"}


def _cmd(text):
    return {"name": "run_command", "args": {"CommandLine": text}}


class TestFacilitationSignal(unittest.TestCase):
    def test_signal_fires_after_settle_with_inline_execution(self):
        steps = [_user("next task"), _step("", [_cmd("uv run pytest -q")])]
        sig = facilitation_signal("/tmp/fake.jsonl", {"goal_settled": True})
        with patch("sage.facilitation._read_transcript_steps", return_value=steps):
            sig = facilitation_signal("/tmp/fake.jsonl", {"goal_settled": True})
        self.assertIn("facilitation", sig)
        self.assertIn("delegate", sig)

    def test_no_signal_before_settle(self):
        steps = [_user("next task"), _step("", [_cmd("uv run pytest -q")])]
        with patch("sage.facilitation._read_transcript_steps", return_value=steps):
            sig = facilitation_signal("/tmp/fake.jsonl", {})
        self.assertEqual(sig, "")

    def test_no_signal_when_subagent_already_used(self):
        steps = [
            _user("next task"),
            _step("", [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "QA"}]}}]),
        ]
        with patch("sage.facilitation._read_transcript_steps", return_value=steps):
            sig = facilitation_signal("/tmp/fake.jsonl", {"goal_settled": True})
        self.assertEqual(sig, "")

    def test_no_signal_for_read_only_turn(self):
        steps = [
            _user("next task"),
            _step("", [{"name": "view_file", "args": {"AbsolutePath": "a.py"}}]),
        ]
        with patch("sage.facilitation._read_transcript_steps", return_value=steps):
            sig = facilitation_signal("/tmp/fake.jsonl", {"goal_settled": True})
        self.assertEqual(sig, "")

    def test_kill_mutation_gate_never_blocks_on_facilitation(self):
        """Mutation kill: facilitation must stay advice-only. If the signal ever
        changes a verdict to exit/emit-block, the final gate action stays
        'healthy' here because policies only append text, never act on it."""
        from sage import policies

        ctx = dict(
            conv_id="c", transcript_path="/nonexistent", clean_prompt="p",
            initial_line_count=3, total_tool_calls=30, turn_tool_names=["Bash"],
            user_prompt="goal", agent_steps=[], git_diff="",
            state={"mid_turn_steers": 0, "sage_error_streak": 0,
                   "last_verified_tools": 0, "goal_settled": True},
        )
        frozen = (
            patch.object(policies, "has_new_user_activity", return_value=False),
            patch.object(policies, "extract_session_and_turn_data",
                         return_value=(None, None, None, 30, None, None, None, 3)),
            patch.object(policies, "is_post_invocation_completion_candidate", return_value=False),
        )
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "evaluate_mid_turn_progress",
                             return_value={"status": "on_track"}), \
                patch.object(policies, "_facilitation_signal",
                             return_value="[EVT·facilitation s2] delegate!"), \
                frozen[0], frozen[1], frozen[2]:
            act = policies.final_sage_gate(**ctx)
        self.assertEqual(act["action"], "healthy")


if __name__ == "__main__":
    unittest.main()
