#!/usr/bin/env python3
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from advisor.guards import (
    fail_safe_exit,
    format_hook_message,
    is_steering_message,
    is_subagent_session,
)


class TestGuards(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.test_dir, "transcript.jsonl")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_is_steering_message(self):
        self.assertTrue(is_steering_message("[Reviewer Steering - Action Required]\nPlease fix."))
        self.assertTrue(is_steering_message("Stop hook blocked termination"))
        self.assertTrue(is_steering_message("steering - run pytest"))
        self.assertTrue(is_steering_message("steerer - run pytest"))
        self.assertTrue(is_steering_message("Steering: please fix"))
        self.assertTrue(is_steering_message("STEERING - Fix tests"))
        self.assertTrue(is_steering_message("Recap: tests passed"))
        self.assertTrue(is_steering_message("recap - tests passed"))
        self.assertTrue(is_steering_message("advisor - fix loop"))
        self.assertTrue(is_steering_message("adviser - fix loop"))
        self.assertTrue(is_steering_message("※ recap: You asked for tests, and I delivered it."))
        self.assertTrue(is_steering_message("※ steering: The user asked for tests and you skipped them."))
        self.assertTrue(is_steering_message("※ advisor: Fix loop."))
        self.assertTrue(is_steering_message("[advisor] fix loop"))
        self.assertTrue(is_steering_message("[verifier] fix loop"))
        self.assertTrue(is_steering_message("*Steering: check code"))
        self.assertTrue(is_steering_message("**steering - fix tests**"))
        self.assertTrue(is_steering_message("**recap:** all done"))
        self.assertTrue(is_steering_message("[steering] fix bug"))
        self.assertTrue(is_steering_message("[Reviewer Steer - Action Required]"))
        self.assertFalse(is_steering_message("Normal user message here"))
        self.assertFalse(is_steering_message("Steering wheel is broken"))
        self.assertFalse(is_steering_message(""))

    def test_format_hook_message_uses_komejirushi_prefix(self):
        self.assertEqual(format_hook_message("steering", "Run pytest now."), "※ steering: Run pytest now.")
        self.assertEqual(format_hook_message("steerer", "Run pytest now."), "※ steerer: Run pytest now.")
        self.assertEqual(format_hook_message("recap", "recap: Tests passed."), "※ recap: Tests passed.")
        self.assertEqual(format_hook_message("recap", "※ recap: You asked for X, and I delivered it."), "※ recap: You asked for X, and I delivered it.")
        self.assertEqual(format_hook_message("advisor", "advisor: Fix loop."), "※ advisor: Fix loop.")
        self.assertEqual(format_hook_message("adviser", "adviser: Fix loop."), "※ adviser: Fix loop.")
        self.assertEqual(format_hook_message("advisor", "[advisor] Fix loop."), "※ advisor: Fix loop.")
        self.assertEqual(format_hook_message("steering", "**steering:** Fix the test"), "※ steering: Fix the test")

    def test_is_post_invocation_flag_handling(self):
        from advisor.guards import is_post_invocation
        with patch("sys.argv", ["session-advisor.py", "post_invocation"]):
            self.assertTrue(is_post_invocation())
        with patch("sys.argv", ["session-advisor.py", "--event", "post_invocation"]):
            self.assertTrue(is_post_invocation())
        with patch("sys.argv", ["session-advisor.py", "post-invocation"]):
            self.assertTrue(is_post_invocation())
        with patch("sys.argv", ["session-advisor.py"]):
            self.assertFalse(is_post_invocation())

    def test_evaluate_turn_triggers_naive_timestamp(self):
        from datetime import datetime

        from advisor.guards import evaluate_turn_triggers
        naive_dt = datetime(2026, 8, 20, 10, 0, 0)
        with patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}):
            duration = evaluate_turn_triggers(5, naive_dt)
            self.assertGreater(duration, 0.0)

    def test_is_subagent_session_by_payload(self):
        self.assertTrue(is_subagent_session({"isSubagent": True}, None, "test"))
        self.assertTrue(is_subagent_session({"parentConversationId": "parent-123"}, None, "test"))
        self.assertTrue(is_subagent_session({"agentRole": "Branch Implementer"}, None, "test"))
        self.assertTrue(is_subagent_session({"role": "research"}, None, "test"))
        self.assertFalse(is_subagent_session({}, None, "Normal user query"))

    def test_is_subagent_session_by_prompt(self):
        self.assertTrue(is_subagent_session({}, None, "You are running as a subagent to help."))
        self.assertTrue(is_subagent_session({}, None, "Please act as a research subagent."))
        self.assertTrue(is_subagent_session({}, None, "invoked by a caller agent (name: parent)"))
        self.assertTrue(is_subagent_session({}, None, "<subagent_reminder>Don't stop</subagent_reminder>"))
        self.assertFalse(is_subagent_session({}, None, "Please optimize my database query."))

    def test_is_subagent_session_by_transcript(self):
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "GENERIC", "content": "you are running as a subagent"}) + "\n")
        self.assertTrue(is_subagent_session({}, self.transcript_path, "Clean prompt"))

    def test_is_subagent_session_real_antigravity_transcript_not_false_positive(self):
        # Real Antigravity transcript head containing tool definitions and checkpoint summaries
        steps = [
            {
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "content": "<USER_REQUEST>Implement feature X</USER_REQUEST>\n<TOOL_DECLARATION>send_message(recipientName, message)</TOOL_DECLARATION>",
            },
            {
                "type": "CHECKPOINT",
                "source": "SYSTEM",
                "content": "The following subagents were spawned: {\"role\": \"Module Implementer\", \"initialPrompt\": \"You are running as a subagent to fix bug\"}",
            },
            {
                "type": "PLANNER_RESPONSE",
                "content": "I will implement feature X.",
                "tool_calls": [{"name": "run_command", "args": {"CommandLine": "git status"}}],
            },
        ]
        with open(self.transcript_path, "w") as f:
            for s in steps:
                f.write(json.dumps(s) + "\n")

        # Main session payload must NOT be classified as subagent
        payload = {"transcriptPath": self.transcript_path, "conversationId": "main-conv-123"}
        self.assertFalse(is_subagent_session(payload, self.transcript_path, "Implement feature X", "<USER_REQUEST>Implement feature X</USER_REQUEST>"))

    def test_fail_safe_exit(self):
        with patch("sys.stdout") as mock_stdout, self.assertRaises(SystemExit) as cm:
            fail_safe_exit("Testing fail safe exit")
        self.assertEqual(cm.exception.code, 0)
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        self.assertIn('"decision": "stop"', written)

if __name__ == "__main__":
    unittest.main()
