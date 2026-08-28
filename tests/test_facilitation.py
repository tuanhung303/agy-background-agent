#!/usr/bin/env python3
"""
tests.test_facilitation - Post-settle delegation command semantics (command + fail-closed recap gate).
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from sage.facilitation import (
    check_facilitation_compliance,
    facilitation_signal,
    immediate_delegate_message,
    immediate_settle_message,
)
from sage.session_state import load_and_sync_session_state, save_session_state


def _step(content, tool_calls=None):
    return {"type": "GENERIC", "content": content, "tool_calls": tool_calls or [],
            "created_at": "2026-08-28T10:00:00+07:00"}


def _user(content):
    return {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": content,
            "tool_calls": [], "created_at": "2026-08-28T09:59:00+07:00"}


def _cmd(text):
    return {"name": "run_command", "args": {"CommandLine": text}}


class TestFacilitationCommand(unittest.TestCase):
    def test_wording_cmd_facilitation(self):
        msg = immediate_settle_message()
        self.assertIn("[CMD·facilitation", msg)
        self.assertIn("DELEGATE execution to subagents via invoke_subagent", msg)
        self.assertNotIn("Do NOT execute inline", msg)

    def test_immediate_delegate_message_at_pin(self):
        msg = immediate_delegate_message(pinned_goal="Implement feature X")
        self.assertIn("[CMD·delegate", msg)
        self.assertIn("delegate ALL execution+tests to subagents via invoke_subagent", msg)
        self.assertIn("distill full payload: goal/scope/context_files/required_tests/DoD", msg)

    def test_settle_recap_payload_deduplicated_when_pin_command_emitted(self):
        msg = immediate_settle_message({"delegate_cmd_turn": 1})
        self.assertNotIn("distill full payload", msg)
        self.assertNotIn("ASK", msg)
        self.assertIn("[CMD·delegate·confirm]", msg)

    def test_signal_fires_after_settle_with_inline_execution(self):
        steps = [_user("next task"), _step("", [_cmd("uv run pytest -q")])]
        with patch("sage.facilitation._read_transcript_steps", return_value=steps):
            sig = facilitation_signal("/tmp/fake.jsonl", {"goal_settled": True})
        self.assertIn("[CMD·delegate·violation]", sig)
        self.assertIn("exec inline detected — delegate NOW", sig)

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

    def test_compliance_tracking_session_state(self):
        conv = "facilitation_persist_test_conv"
        with tempfile.TemporaryDirectory() as td:
            sf = os.path.join(td, "state.json")
            save_session_state(sf, {"turn_key": "old"}, goal_settled=True, facilitation_cmd_turn=1, facilitation_cmd_ignored=2)
            with patch("sage.session_state.get_state_file_path", return_value=sf), \
                    patch("sage.session_state._clear_sage_session"):
                _, _, state, is_same = load_and_sync_session_state(conv, "/nonexistent", "next task")
        self.assertFalse(is_same)
        self.assertFalse(state.get("goal_settled"))
        self.assertIsNone(state.get("facilitation_cmd_turn"))
        self.assertEqual(state.get("facilitation_cmd_ignored"), 0)

    def test_fail_closed_recap_gate_refuses_inline_after_settle(self):
        from sage import policies

        steps = [_user("next task"), _step("", [_cmd("uv run pytest -q")])]
        ctx = dict(
            conv_id="c", transcript_path="/tmp/fake.jsonl", clean_prompt="p",
            initial_line_count=3, total_tool_calls=30, turn_tool_names=["run_command"],
            user_prompt="goal", agent_steps=[], git_diff="",
            state={"mid_turn_steers": 0, "sage_error_streak": 0,
                   "last_verified_tools": 0, "goal_settled": True, "facilitation_cmd_turn": 1},
        )
        frozen = (
            patch.object(policies, "has_new_user_activity", return_value=False),
            patch.object(policies, "extract_session_and_turn_data",
                         return_value=(None, None, None, 30, None, None, None, 3)),
            patch.object(policies, "is_post_invocation_completion_candidate", return_value=False),
            patch("sage.facilitation._read_transcript_steps", return_value=steps),
        )
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "evaluate_mid_turn_progress",
                             return_value={"status": "on_track", "recap": "all done"}), \
                frozen[0], frozen[1], frozen[2], frozen[3]:
            act = policies.final_sage_gate(**ctx)
        self.assertEqual(act["action"], "emit")
        self.assertEqual(act["decision"], "steer")
        self.assertEqual(act["category"], "missing_proof")
        self.assertIn("Execute delegation via invoke_subagent NOW", act["text"])

    def test_fail_closed_recap_gate_passes_when_subagent_invoked(self):
        from sage import policies

        steps = [
            _user("next task"),
            _step("", [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "QA"}]}}]),
        ]
        ctx = dict(
            conv_id="c", transcript_path="/tmp/fake.jsonl", clean_prompt="p",
            initial_line_count=3, total_tool_calls=30, turn_tool_names=["invoke_subagent"],
            user_prompt="goal", agent_steps=[], git_diff="",
            state={"mid_turn_steers": 0, "sage_error_streak": 0,
                   "last_verified_tools": 0, "goal_settled": True, "facilitation_cmd_turn": 1},
        )
        frozen = (
            patch.object(policies, "has_new_user_activity", return_value=False),
            patch.object(policies, "extract_session_and_turn_data",
                         return_value=(None, None, None, 30, None, None, None, 3)),
            patch.object(policies, "is_post_invocation_completion_candidate", return_value=False),
            patch("sage.facilitation._read_transcript_steps", return_value=steps),
        )
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "evaluate_mid_turn_progress",
                             return_value={"status": "on_track", "recap": "all done"}), \
                frozen[0], frozen[1], frozen[2], frozen[3]:
            act = policies.final_sage_gate(**ctx)
        self.assertEqual(act["action"], "healthy")

    def test_fail_closed_recap_gate_allows_override_receipt(self):
        from sage import policies

        steps = [_user("next task"), _step("", [_cmd("uv run pytest -q")])]
        ctx = dict(
            conv_id="c", transcript_path="/tmp/fake.jsonl", clean_prompt="p",
            initial_line_count=3, total_tool_calls=30, turn_tool_names=["run_command"],
            user_prompt="goal", agent_steps=[], git_diff="",
            state={"mid_turn_steers": 0, "sage_error_streak": 0,
                   "last_verified_tools": 0, "goal_settled": True, "facilitation_cmd_turn": 1},
        )
        frozen = (
            patch.object(policies, "has_new_user_activity", return_value=False),
            patch.object(policies, "extract_session_and_turn_data",
                         return_value=(None, None, None, 30, None, None, None, 3)),
            patch.object(policies, "is_post_invocation_completion_candidate", return_value=False),
            patch("sage.facilitation._read_transcript_steps", return_value=steps),
        )
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "evaluate_mid_turn_progress",
                             return_value={"status": "on_track", "recap": "all done", "facilitation_override": True}), \
                frozen[0], frozen[1], frozen[2], frozen[3]:
            act = policies.final_sage_gate(**ctx)
        self.assertEqual(act["action"], "healthy")

    def test_midturn_repeat_escalation(self):
        steps = [_user("next task"), _step("", [_cmd("uv run pytest -q")])]
        with patch("sage.facilitation._read_transcript_steps", return_value=steps):
            sig0 = facilitation_signal("/tmp/fake.jsonl", {"goal_settled": True, "facilitation_cmd_ignored": 0})
            sig1 = facilitation_signal("/tmp/fake.jsonl", {"delegate_cmd_turn": 1, "facilitation_cmd_ignored": 1})
        self.assertIn("[CMD·delegate·violation]", sig0)
        self.assertIn("exec inline detected — delegate NOW", sig0)
        self.assertIn("[CMD·delegate·violation]", sig1)

    def test_mutation_kill_compliance_check_gate_mutation(self):
        """Mutation test: verify that disabling check_facilitation_compliance in final_sage_gate
        would allow unproven inline execution to pass as healthy (killing the defect)."""
        from sage import policies

        steps = [_user("next task"), _step("", [_cmd("uv run pytest -q")])]
        ctx = dict(
            conv_id="c", transcript_path="/tmp/fake.jsonl", clean_prompt="p",
            initial_line_count=3, total_tool_calls=30, turn_tool_names=["run_command"],
            user_prompt="goal", agent_steps=[], git_diff="",
            state={"mid_turn_steers": 0, "sage_error_streak": 0,
                   "last_verified_tools": 0, "goal_settled": True, "facilitation_cmd_turn": 1},
        )
        frozen = (
            patch.object(policies, "has_new_user_activity", return_value=False),
            patch.object(policies, "extract_session_and_turn_data",
                         return_value=(None, None, None, 30, None, None, None, 3)),
            patch.object(policies, "is_post_invocation_completion_candidate", return_value=False),
            patch("sage.facilitation._read_transcript_steps", return_value=steps),
            patch.object(policies, "MID_TURN_SAGE_ENABLED", 1),
            patch.object(policies, "evaluate_mid_turn_progress",
                         return_value={"status": "on_track", "recap": "all done"}),
        )
        with frozen[0], frozen[1], frozen[2], frozen[3], frozen[4], frozen[5]:
            real_act = policies.final_sage_gate(**ctx)
            with patch("sage.facilitation.check_facilitation_compliance",
                       return_value={"required": False, "compliant": True, "exec_calls": 0, "has_subagent": False}):
                mutated_act = policies.final_sage_gate(**ctx)

        self.assertEqual(real_act["action"], "emit")
        self.assertEqual(mutated_act["action"], "healthy")

    def test_statusline_surfaces_ignored_count(self):
        import re
        from statusline.statusline import get_sage_steer_badges, safe_id

        conv_id = "test_conv_fac_ignored"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        try:
            with open(state_file, "w") as f:
                json.dump({"turn_key": "tk1", "facilitation_cmd_ignored": 3}, f)
            badges = get_sage_steer_badges({"conversation_id": conv_id})
            self.assertEqual(len(badges), 1)
            plain = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", badges[0])
            self.assertEqual(plain, "● sage command ignored 3×")
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_fail_closed_recap_gate_refuses_inline_after_pin(self):
        from sage import policies

        steps = [_user("next task"), _step("", [_cmd("uv run pytest -q")])]
        ctx = dict(
            conv_id="c", transcript_path="/tmp/fake.jsonl", clean_prompt="p",
            initial_line_count=3, total_tool_calls=30, turn_tool_names=["run_command"],
            user_prompt="goal", agent_steps=[], git_diff="",
            state={"mid_turn_steers": 0, "sage_error_streak": 0,
                   "last_verified_tools": 0, "delegate_cmd_turn": 1},
        )
        frozen = (
            patch.object(policies, "has_new_user_activity", return_value=False),
            patch.object(policies, "extract_session_and_turn_data",
                         return_value=(None, None, None, 30, None, None, None, 3)),
            patch.object(policies, "is_post_invocation_completion_candidate", return_value=False),
            patch("sage.facilitation._read_transcript_steps", return_value=steps),
        )
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "evaluate_mid_turn_progress",
                             return_value={"status": "on_track", "recap": "all done"}), \
                frozen[0], frozen[1], frozen[2], frozen[3]:
            act = policies.final_sage_gate(**ctx)
        self.assertEqual(act["action"], "emit")
        self.assertEqual(act["decision"], "steer")
        self.assertEqual(act["category"], "missing_proof")
        self.assertIn("Execute delegation via invoke_subagent NOW", act["text"])

    def test_runner_pin_emits_delegate_command_and_sets_turn(self):
        import time
        from sage.runner import main
        from sage.session_state import get_state_file_path

        conv_id = f"test_pin_cmd_{int(time.time() * 1000)}"
        state_file = get_state_file_path(conv_id)
        try:
            with tempfile.TemporaryDirectory() as td:
                tr = os.path.join(td, "transcript.jsonl")
                with open(tr, "w") as f:
                    f.write(json.dumps({
                        "type": "USER_INPUT", "source": "USER_EXPLICIT",
                        "content": "Implement engine", "tool_calls": [],
                        "created_at": "2026-08-28T10:00:00Z"}) + "\n")
                    f.write(json.dumps({
                        "type": "PLANNER_RESPONSE", "content": "Running task",
                        "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "a.py"}}],
                        "created_at": "2026-08-28T10:00:05Z"}) + "\n")

                payload = {
                    "conversationId": conv_id,
                    "transcriptPath": tr,
                    "workspacePaths": [td],
                    "hookEventName": "PostInvocation",
                }

                pin_act = {
                    "action": "emit",
                    "decision": "watchout",
                    "category": "pinned_goal",
                    "pinned_goal": "Refactor optimizer",
                    "pinned_emitted": True,
                    "text": "[Pinned Goal] Refactor optimizer",
                }

                with patch("sage.runner.is_post_invocation", return_value=True), \
                     patch("sage.runner.is_post_invocation_completion_candidate", return_value=False), \
                     patch("sage.runner.sage_flow", return_value=pin_act), \
                     patch("sage.runner.sage_flow", return_value=pin_act), \
                     patch("sys.stdin.read", return_value=json.dumps(payload)), \
                     patch.dict(os.environ, {"AGY_HOOK_EVENT_NAME": "PostInvocation", "AGY_STOP_AUDIT_TEST": "1"}), \
                     patch("sys.stdout") as mock_stdout, \
                     self.assertRaises(SystemExit) as cm:
                    main()

                self.assertEqual(cm.exception.code, 0)
                written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
                data = json.loads(written.strip())
                msg = data.get("reason") or (data.get("injectSteps", [{}])[0].get("userMessage", ""))
                self.assertIn("[Pinned Goal] Refactor optimizer", msg)
                self.assertIn("[CMD·delegate", msg)
                self.assertIn("delegate ALL execution+tests to subagents", msg)

                with open(state_file, "r") as sf:
                    saved = json.load(sf)
                self.assertEqual(saved.get("delegate_cmd_turn"), 1)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_immediate_facilitation_dispatched_at_goal_settle_when_no_pin(self):
        import time
        from sage.runner import main

        conv_id = f"test_fac_settle_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{conv_id}.json"
        try:
            with tempfile.TemporaryDirectory() as td:
                tr = os.path.join(td, "transcript.jsonl")
                with open(tr, "w") as f:
                    f.write(json.dumps({
                        "type": "USER_INPUT", "source": "USER_EXPLICIT",
                        "content": "Implement feature X", "tool_calls": [],
                        "created_at": "2026-08-28T10:00:00Z"}) + "\n")
                    f.write(json.dumps({
                        "type": "PLANNER_RESPONSE", "content": "Running task",
                        "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "a.py"}}],
                        "created_at": "2026-08-28T10:00:05Z"}) + "\n")

                payload = {
                    "conversationId": conv_id,
                    "transcriptPath": tr,
                    "workspacePaths": [td],
                }

                with patch("sage.runner.final_sage_gate",
                           return_value={"action": "healthy", "recap": "All done"}), \
                     patch("sage.runner.final_sage_gate",
                           return_value={"action": "healthy", "recap": "All done"}), \
                     patch("sys.stdin.read", return_value=json.dumps(payload)), \
                     patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
                     patch("sys.stdout") as mock_stdout, \
                     self.assertRaises(SystemExit) as cm:
                    main()

                self.assertEqual(cm.exception.code, 0)
                written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
                data = json.loads(written.strip())
                msg = data.get("reason") or (data.get("injectSteps", [{}])[0].get("userMessage", ""))
                self.assertIn("[RECAP·on_track] All done", msg)
                self.assertIn("[CMD·facilitation", msg)
                self.assertIn("DELEGATE execution to subagents", msg)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)


    def test_simple_qa_exempt_from_facilitation_signal(self):
        steps = [_user("explain oauth flow"), _step("", [_cmd("python3 query.py")])]
        with patch("sage.facilitation._read_transcript_steps", return_value=steps):
            sig = facilitation_signal("/tmp/fake.jsonl", {"goal_settled": True, "task_complexity": "simple_qa"})
        self.assertEqual(sig, "")

    def test_simple_qa_exempt_from_facilitation_compliance(self):
        steps = [_user("put to clipboard"), _step("", [_cmd("cat msg.txt | pbcopy")])]
        with patch("sage.facilitation._read_transcript_steps", return_value=steps):
            comp = check_facilitation_compliance("/tmp/fake.jsonl", {"goal_settled": True, "task_complexity": "simple_qa", "delegate_cmd_turn": 1})
        self.assertFalse(comp["required"])
        self.assertTrue(comp["compliant"])

    def test_research_and_mcp_tools_do_not_trip_facilitation_violation(self):
        steps = [_user("check docs and copy"), _step("", [{"name": "call_mcp_tool", "args": {"tool": "clipboard_write"}}])]
        with patch("sage.facilitation._read_transcript_steps", return_value=steps):
            comp = check_facilitation_compliance("/tmp/fake.jsonl", {"goal_settled": True, "task_complexity": "complex_code", "delegate_cmd_turn": 1})
        self.assertTrue(comp["required"])
        self.assertTrue(comp["compliant"])
        self.assertEqual(comp["exec_calls"], 0)


if __name__ == "__main__":
    unittest.main()
