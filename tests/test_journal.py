"""
tests.test_journal - Comprehensive tests for centralized sage event journal.
"""
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from sage import journal
from sage.facilitation import immediate_settle_message
from sage.runner import main


class TestJournalAPI(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.journal_file = os.path.join(self.td.name, "events.jsonl")
        self.env_patch = patch.dict(os.environ, {"AGY_SAGE_JOURNAL": self.journal_file})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.td.cleanup()

    def test_write_and_read_basic(self):
        journal.write("violation_inject", conv_id="c1", tool="run_command", count=1, detail="Inline blocked")
        journal.write("violation_suppressed", conv_id="c1", tool="run_command", count=1)
        journal.write("recap_pass", conv_id="c2")

        all_entries = journal.read()
        self.assertEqual(len(all_entries), 3)

        c1_entries = journal.read(conv_id="c1")
        self.assertEqual(len(c1_entries), 2)
        self.assertEqual(c1_entries[0]["event"], "violation_inject")
        self.assertEqual(c1_entries[0]["tool"], "run_command")
        self.assertEqual(c1_entries[0]["count"], 1)
        self.assertEqual(c1_entries[0]["detail"], "Inline blocked")
        self.assertIn("ts", c1_entries[0])

        self.assertEqual(c1_entries[1]["event"], "violation_suppressed")
        self.assertEqual(c1_entries[1]["count"], 1)

        pass_entries = journal.read(event="recap_pass")
        self.assertEqual(len(pass_entries), 1)
        self.assertEqual(pass_entries[0]["conv_id"], "c2")

    def test_read_tail_limit(self):
        for i in range(10):
            journal.write("steer_emitted", conv_id="c1", detail=f"cat_{i}")
        entries = journal.read(tail=3)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[-1]["detail"], "cat_9")

    def test_rotation_over_threshold(self):
        with patch.object(journal, "ROTATE_BYTES", 300):
            # Write 2 entries: each is ~165 bytes. 2 entries = ~330 bytes (> 300)
            journal.write("test_event", conv_id="c1", detail="message_0" * 8)
            self.assertFalse(os.path.exists(self.journal_file + ".prev"))
            journal.write("test_event", conv_id="c1", detail="message_1" * 8)
            self.assertFalse(os.path.exists(self.journal_file + ".prev"))
            # 3rd write sees file size > 300, rotates to .prev, writes into fresh active file
            journal.write("test_event", conv_id="c1", detail="message_2" * 8)
            self.assertTrue(os.path.exists(self.journal_file + ".prev"))
            entries = journal.read(conv_id="c1")
            self.assertEqual(len(entries), 3)

    def test_write_never_raises_on_invalid_path(self):
        with patch.dict(os.environ, {"AGY_SAGE_JOURNAL": "/nonexistent/dir/file.jsonl"}):
            journal.write("test_event", conv_id="c1")
            entries = journal.read()
            self.assertEqual(entries, [])


class TestJournalWiring(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.journal_file = os.path.join(self.td.name, "events.jsonl")
        self.env_patch = patch.dict(os.environ, {
            "AGY_SAGE_JOURNAL": self.journal_file,
            "AGY_STOP_AUDIT_TEST": "1",
        })
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.td.cleanup()

    def test_facilitation_repeat_writes_cmd_repeat(self):
        state = {"conversation_id": "c_repeat_1"}
        immediate_settle_message(state=state, repeat=1)
        entries = journal.read(conv_id="c_repeat_1")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["event"], "cmd_repeat")

    def test_facilitation_first_settle_does_not_write_cmd_repeat(self):
        state = {"conversation_id": "c_repeat_0"}
        immediate_settle_message(state=state, repeat=0)
        entries = journal.read(conv_id="c_repeat_0")
        self.assertEqual(len(entries), 0)

    def test_runner_midturn_pin_emits_delegate_cmd_and_steer(self):
        conv_id = f"test_pin_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{conv_id}.json"
        try:
            tr = os.path.join(self.td.name, "transcript.jsonl")
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
                "workspacePaths": [self.td.name],
                "hookEventName": "PostInvocation",
            }
            pin_act = {
                "action": "emit",
                "decision": "watchout",
                "category": "pinned_goal",
                "pinned_goal": "Refactor optimizer core",
                "pinned_emitted": True,
                "text": "[Pinned Goal] Refactor optimizer core",
            }
            with patch("sage.runner.is_post_invocation", return_value=True), \
                 patch("sage.runner.is_post_invocation_completion_candidate", return_value=False), \
                 patch("sage.runner.sage_flow", return_value=pin_act), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch.dict(os.environ, {"AGY_HOOK_EVENT_NAME": "PostInvocation"}), \
                 patch("sys.stdout"), \
                 self.assertRaises(SystemExit):
                main()

            entries = journal.read(conv_id=conv_id)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["event"], "delegate_cmd")
            self.assertEqual(entries[0]["detail"], "Refactor optimizer core")
            self.assertEqual(entries[1]["event"], "steer_emitted")
            self.assertEqual(entries[1]["detail"], "pinned_goal")
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_runner_final_gate_pass_writes_recap_pass(self):
        conv_id = f"test_pass_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{conv_id}.json"
        try:
            tr = os.path.join(self.td.name, "transcript.jsonl")
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
                "workspacePaths": [self.td.name],
            }
            with patch("sage.runner.final_sage_gate",
                       return_value={"action": "healthy", "recap": "All tests passed cleanly"}), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sys.stdout"), \
                 self.assertRaises(SystemExit):
                main()

            entries = journal.read(conv_id=conv_id)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["event"], "recap_pass")
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_runner_final_gate_rejected_writes_recap_rejected(self):
        conv_id = f"test_reject_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{conv_id}.json"
        try:
            tr = os.path.join(self.td.name, "transcript.jsonl")
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
                "workspacePaths": [self.td.name],
            }
            reject_gate = {
                "action": "emit",
                "decision": "steer",
                "category": "missing_proof",
                "text": "Execute delegation via invoke_subagent NOW",
            }
            with patch("sage.runner.final_sage_gate", return_value=reject_gate), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sys.stdout"), \
                 self.assertRaises(SystemExit):
                main()

            entries = journal.read(conv_id=conv_id)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["event"], "recap_rejected")
            self.assertIn("Execute delegation", entries[0]["detail"])
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)


if __name__ == "__main__":
    unittest.main()
