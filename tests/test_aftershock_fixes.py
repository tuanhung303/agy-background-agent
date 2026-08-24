"""
test_aftershock_fixes.py - Verification for Aftershock hook reliability defect fixes.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from advisor.guards import is_post_invocation, is_subagent_session
from advisor.transcript import has_recent_tool_errors, is_post_invocation_completion_candidate


class TestAftershockFixes(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.test_dir, "transcript.jsonl")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_subagent_regex_false_positive_prevention(self):
        # A user prompt discussing subagents should NOT trigger subagent skip
        user_prompt = "You are a software architect helping me design a subagent system for data reconciliation."
        payload = {"isSubagent": False}
        self.assertFalse(is_subagent_session(payload, None, user_prompt, user_prompt))

    def test_subagent_structural_marker_detection(self):
        # Structural reminder tag MUST be detected
        user_prompt = "<subagent_reminder> You are a subagent with restricted tools </subagent_reminder>"
        payload = {}
        self.assertTrue(is_subagent_session(payload, None, user_prompt, user_prompt))

    def test_subagent_role_token_detection(self):
        # AGY internal role tokens MUST be detected
        user_prompt = "Task for branch implementer: build feature"
        payload = {}
        self.assertTrue(is_subagent_session(payload, None, user_prompt, user_prompt))

    def test_completion_candidate_with_trailing_checkpoints(self):
        # PLANNER_RESPONSE followed by trailing CHECKPOINT step should still be recognized as completion candidate
        with open(self.transcript_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "content": "Do something"
            }) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "I have completed all the requested tasks.",
                "tool_calls": []
            }) + "\n")
            f.write(json.dumps({
                "type": "CHECKPOINT",
                "content": "state snapshot"
            }) + "\n")

        self.assertTrue(is_post_invocation_completion_candidate(self.transcript_path))

    def test_has_recent_tool_errors_detection(self):
        with open(self.transcript_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "GENERIC", "content": "Exit code 127: command not found"}) + "\n")
            f.write(json.dumps({"type": "GENERIC", "content": "Error: failed to connect"}) + "\n")

        self.assertTrue(has_recent_tool_errors(self.transcript_path))

    def test_is_post_invocation_variants(self):
        with patch.object(sys, "argv", ["session-advisor.py", "post_invocation"]):
            self.assertTrue(is_post_invocation())
        with patch.object(sys, "argv", ["session-advisor.py", "postinvocation"]):
            self.assertTrue(is_post_invocation())
        with patch.object(sys, "argv", ["session-advisor.py", "post-invocation"]):
            self.assertTrue(is_post_invocation())
        with patch.object(sys, "argv", ["session-advisor.py", "post"]):
            self.assertTrue(is_post_invocation())
    def test_subagent_deep_transcript_marker_detection(self):
        # Marker placed past line 10 must be detected
        with open(self.transcript_path, "w", encoding="utf-8") as f:
            for i in range(20):
                f.write(json.dumps({"type": "PLANNER_RESPONSE", "content": f"Step {i}"}) + "\n")
            f.write(json.dumps({"type": "GENERIC", "content": "<subagent_reminder> Running in worker mode </subagent_reminder>"}) + "\n")
        self.assertTrue(is_subagent_session({}, self.transcript_path, "regular task", "regular task"))

    def test_subagent_extended_roles(self):
        self.assertTrue(is_subagent_session({"agentRole": "Module Implementer"}, None, "task", "task"))
        self.assertTrue(is_subagent_session({"agentRole": "worker"}, None, "task", "task"))
        self.assertTrue(is_subagent_session({"agentRole": "qa"}, None, "task", "task"))
        self.assertTrue(is_subagent_session({"role": "codebase researcher"}, None, "task", "task"))

    def test_turn_boundary_clears_stale_advisor_text(self):
        import time

        from advisor.locking import safe_id
        from advisor.runner import main
        conv_id = f"test_turn_bound_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_advisor_{safe_id(conv_id)}.json"
        with open(state_file, "w") as sf:
            json.dump({
                "turn_key": "OLD_TURN_KEY_123",
                "last_advisor_text": "STALE ADVICE FROM PRIOR TURN",
                "advisor_advice_counts": {"hash1": 3},
                "advisor_emitted_texts": ["STALE ADVICE"],
            }, sf)

        # New turn transcript
        with open(self.transcript_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Brand new goal"}) + "\n")
            for i in range(12):
                f.write(json.dumps({"type": "PLANNER_RESPONSE", "content": f"Working {i}", "tool_calls": [{"name": "Bash", "args": {}}]}) + "\n")
                f.write(json.dumps({"type": "GENERIC", "content": "done"}) + "\n")
            f.write(json.dumps({"type": "PLANNER_RESPONSE", "content": "Completed", "tool_calls": []}) + "\n")

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        captured = {}
        def fake_gate(*args, **kwargs):
            state = args[9] if len(args) > 9 else kwargs.get("state", {})
            captured["last_advisor_text"] = state.get("last_advisor_text")
            captured["advisor_advice_counts"] = state.get("advisor_advice_counts")
            captured["advisor_emitted_texts"] = state.get("advisor_emitted_texts")
            return {"action": "healthy", "recap": "Done"}

        try:
            with patch("sys.argv", ["session-advisor.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("advisor.policies.MID_TURN_ADVISOR_ENABLED", 1), \
                 patch("advisor.advisor.run_advisor_model", return_value={"healthy": True, "blind_spots": []}), \
                 patch("advisor.runner.final_advisor_gate", side_effect=fake_gate), \
                 patch("sys.stdout"):
                try: main()
                except SystemExit: pass

            self.assertNotIn("STALE ADVICE FROM PRIOR TURN", str(captured.get("last_advisor_text", "")))
            self.assertEqual(captured.get("advisor_advice_counts"), {})
            self.assertEqual(captured.get("advisor_emitted_texts"), [])
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)


if __name__ == "__main__":
    unittest.main()
