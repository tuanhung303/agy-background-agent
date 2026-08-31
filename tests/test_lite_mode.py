"""tests.test_lite_mode - Test suite for Stop Hook Lite Mode verification."""
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sage.lite.fork import FAILED_FORKS_DIR, cleanup_fork_session, fork_conversation_session, prune_failed_forks_dir
from sage.lite.gating import extract_turn_mutations_and_context, is_mutating_tool_call
from sage.lite.prompt import build_lite_verifier_prompt
from sage.lite.runner import run_lite_stop_audit
from sage.lite.schemas import LiteVerdict
from sage.lite.verifier import run_lite_verification
from statusline.statusline import get_sage_steer_badges, render_statusline


class TestLiteSchemas(unittest.TestCase):
    def test_schema_from_dict(self):
        v_pass = LiteVerdict.from_dict({"verdict": "PASS", "action": ""})
        self.assertEqual(v_pass.verdict, "PASS")
        self.assertEqual(v_pass.action, "")

        v_fail = LiteVerdict.from_dict({"verdict": "FAIL", "action": "Run pytest now."})
        self.assertEqual(v_fail.verdict, "FAIL")
        self.assertEqual(v_fail.action, "Run pytest now.")

        v_none = LiteVerdict.from_dict(None)
        self.assertEqual(v_none.verdict, "PASS")


class TestLiteGating(unittest.TestCase):
    def test_read_only_tools_bypass(self):
        steps = [
            {"type": "USER_INPUT", "content": "How does auth work?"},
            {"type": "PLANNER_RESPONSE", "content": "Let me inspect the code.", "tool_calls": [
                {"name": "view_file", "args": {"AbsolutePath": "/src/auth.py"}},
                {"name": "grep_search", "args": {"Query": "def login", "SearchPath": "/src"}},
            ]},
            {"type": "PLANNER_RESPONSE", "content": "Auth works via JWT tokens."},
        ]
        has_mut, reason, user_p, agent_out = extract_turn_mutations_and_context(steps)
        self.assertFalse(has_mut)
        self.assertIn("No mutating tool calls", reason)
        self.assertEqual(user_p, "How does auth work?")
        self.assertEqual(agent_out, "Auth works via JWT tokens.")

    def test_mutating_write_tools_trigger(self):
        steps = [
            {"type": "USER_INPUT", "content": "Fix the bug in auth.py"},
            {"type": "PLANNER_RESPONSE", "content": "Editing file", "tool_calls": [
                {"name": "replace_file_content", "args": {"TargetFile": "/src/auth.py"}},
            ]},
            {"type": "PLANNER_RESPONSE", "content": "I fixed the bug in auth.py."},
        ]
        has_mut, reason, user_p, agent_out = extract_turn_mutations_and_context(steps)
        self.assertTrue(has_mut)
        self.assertIn("replace_file_content", reason)
        self.assertEqual(user_p, "Fix the bug in auth.py")
        self.assertEqual(agent_out, "I fixed the bug in auth.py.")

    def test_mutating_shell_command_triggers(self):
        steps = [
            {"type": "USER_INPUT", "content": "Add dependency"},
            {"type": "PLANNER_RESPONSE", "content": "Running command", "tool_calls": [
                {"name": "run_command", "args": {"CommandLine": "echo 'new_pkg' >> requirements.txt"}},
            ]},
        ]
        has_mut, reason, _, _ = extract_turn_mutations_and_context(steps)
        self.assertTrue(has_mut)

    def test_read_only_shell_command_bypasses(self):
        steps = [
            {"type": "USER_INPUT", "content": "Run tests"},
            {"type": "PLANNER_RESPONSE", "content": "Running pytest", "tool_calls": [
                {"name": "run_command", "args": {"CommandLine": "pytest tests/test_auth.py"}},
            ]},
        ]
        has_mut, _, _, _ = extract_turn_mutations_and_context(steps)
        self.assertFalse(has_mut)


class TestLitePrompt(unittest.TestCase):
    def test_prompt_construction(self):
        prompt = build_lite_verifier_prompt("Implement feature X", "Feature X is done.")
        self.assertIn("<user_request>\nImplement feature X\n</user_request>", prompt)
        self.assertIn("<last_agent_response>\nFeature X is done.\n</last_agent_response>", prompt)
        self.assertIn("PERMISSION SEEKING", prompt)
        self.assertIn("OUTSOURCING", prompt)
        self.assertIn("TRIVIAL QUESTIONS", prompt)
        self.assertIn('"verdict": "PASS" | "FAIL"', prompt)


class TestLiteFork(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_failed_fork_preservation_and_pruning(self):
        os.makedirs(FAILED_FORKS_DIR, exist_ok=True)
        # Clean test slate in /tmp/agy_failed_forks
        for f in os.listdir(FAILED_FORKS_DIR):
            try:
                os.remove(os.path.join(FAILED_FORKS_DIR, f))
            except OSError:
                pass

        # Create mock failed fork logs
        for i in range(25):
            fp = os.path.join(FAILED_FORKS_DIR, f"fork_{i}.jsonl")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(f"fail log {i}")

        prune_failed_forks_dir()
        remaining = os.listdir(FAILED_FORKS_DIR)
        self.assertLessEqual(len(remaining), 20)


class TestLiteRunner(unittest.TestCase):
    @patch("sage.lite.runner.fail_safe_exit")
    def test_runner_bypasses_read_only(self, mock_exit):
        mock_exit.side_effect = SystemExit(0)
        payload = {
            "conversationId": "test_conv_ro",
            "transcript_path": "/tmp/nonexistent.jsonl",
        }
        with patch("sage.lite.runner._read_transcript_steps", return_value=[
            {"type": "USER_INPUT", "content": "What is 2+2?"},
            {"type": "PLANNER_RESPONSE", "content": "4", "tool_calls": [
                {"name": "view_file", "args": {"AbsolutePath": "/tmp/a"}},
            ]},
        ]):
            try:
                run_lite_stop_audit(json.dumps(payload))
            except SystemExit:
                pass
            mock_exit.assert_called_once()
            self.assertIn("Lite Mode bypass", mock_exit.call_args[0][0])

    @patch("sage.lite.runner.emit_continue_response")
    @patch("sage.lite.runner.fork_conversation_session", return_value="fork_test_123")
    @patch("sage.lite.runner.cleanup_fork_session")
    @patch("sage.lite.runner.run_lite_verification")
    def test_runner_fail_emits_continue(self, mock_ver, mock_clean, mock_fork, mock_cont):
        mock_cont.side_effect = SystemExit(0)
        mock_ver.return_value = LiteVerdict(verdict="FAIL", action="Run pytest tests/test_app.py now.")
        payload = {
            "conversationId": "test_conv_mut",
            "transcript_path": "/tmp/nonexistent.jsonl",
        }
        with patch("sage.lite.runner._read_transcript_steps", return_value=[
            {"type": "USER_INPUT", "content": "Modify code"},
            {"type": "PLANNER_RESPONSE", "content": "Edited", "tool_calls": [
                {"name": "write_to_file", "args": {"TargetFile": "/src/app.py"}},
            ]},
        ]):
            try:
                run_lite_stop_audit(json.dumps(payload))
            except SystemExit:
                pass
            mock_cont.assert_called_once_with("Run pytest tests/test_app.py now.", is_post=True)


class TestLiteStatusline(unittest.TestCase):
    def test_statusline_reviewing_state(self):
        cid = "conv_stat_test"
        sf = f"/tmp/agy_sage_{cid}_test.json"
        with patch("statusline.statusline.safe_id", return_value=f"{cid}_test"):
            with open(sf, "w", encoding="utf-8") as f:
                json.dump({"sage_status": "reviewing"}, f)
            try:
                data = {"conversation_id": cid, "model": "Gemini 3.7 Flash (High)"}
                badges = get_sage_steer_badges(data)
                self.assertEqual(badges, [])  # Right badge hidden during review

                out = render_statusline(data)
                self.assertIn("reviewing agent output...", out)
            finally:
                if os.path.exists(sf):
                    os.remove(sf)

    def test_statusline_delivered_state(self):
        cid = "conv_stat_delivered_test"
        sf = f"/tmp/agy_sage_{cid}_test.json"
        with patch("statusline.statusline.safe_id", return_value=f"{cid}_test"):
            with open(sf, "w", encoding="utf-8") as f:
                json.dump({"lite_status": "delivered", "sage_status": "idle"}, f)
            try:
                data = {"conversation_id": cid, "model": "Gemini 3.7 Flash (High)"}
                out = render_statusline(data)
                self.assertIn("delivered", out)
            finally:
                if os.path.exists(sf):
                    os.remove(sf)

    def test_statusline_auto_continue_state(self):
        cid = "conv_stat_auto_cont_test"
        sf = f"/tmp/agy_sage_{cid}_test.json"
        with patch("statusline.statusline.safe_id", return_value=f"{cid}_test"):
            with open(sf, "w", encoding="utf-8") as f:
                json.dump({"lite_status": "auto-continue (x1)", "sage_status": "injecting"}, f)
            try:
                data = {"conversation_id": cid, "model": "Gemini 3.7 Flash (High)"}
                out = render_statusline(data)
                self.assertIn("auto-continue (x1)", out)
            finally:
                if os.path.exists(sf):
                    os.remove(sf)


if __name__ == "__main__":
    unittest.main()
