"""Comprehensive test suite for timer lifecycle, detached CLI dispatches, and interactive stall detection."""

from datetime import datetime, timedelta, timezone
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sage.dispatches import (
    get_active_external_dispatches,
    get_stalled_background_tasks,
    has_active_external_dispatches,
)
from sage.transcript import (
    get_active_background_tasks,
    has_active_background_tasks,
    is_post_invocation_completion_candidate,
)
from sage.watchers import (
    TIMER_GRACE_SECONDS,
    _parse_timer_duration,
)


class TestTimerLifecycle(unittest.TestCase):
    """Verifies duration-bounded lifecycle for schedule timers."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.test_dir, "transcript.jsonl")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_parse_timer_duration(self):
        self.assertEqual(_parse_timer_duration("Timer: 120s, Prompt: wait"), 120.0)
        self.assertEqual(_parse_timer_duration("Timer: 15.5s, Prompt: test"), 15.5)
        self.assertEqual(_parse_timer_duration("Timer: 300, Prompt: test"), 300.0)
        self.assertEqual(_parse_timer_duration("generic task"), 60.0)

    def test_fresh_timer_is_detected_as_active(self):
        now = datetime.now(timezone.utc)
        step = {
            "type": "GENERIC",
            "content": "Tool is running as a background task with task id: conv1/task-100\nTask Description: Timer: 120s, Prompt: Check advisor completion",
            "created_at": (now - timedelta(seconds=10)).isoformat(),
        }
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps(step) + "\n")

        tasks = get_active_background_tasks(self.transcript_path, conv_id="conv1")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], "conv1/task-100")
        self.assertTrue(tasks[0]["is_timer"])
        self.assertEqual(tasks[0]["timer_duration"], 120.0)
        self.assertTrue(has_active_background_tasks(self.transcript_path, conv_id="conv1"))

    def test_expired_timer_is_dropped(self):
        now = datetime.now(timezone.utc)
        # Timer for 8s created 400s ago (beyond 8 + 15s grace)
        step = {
            "type": "GENERIC",
            "content": "Tool is running as a background task with task id: conv1/task-101\nTask Description: Timer: 8s, Prompt: Short sleep",
            "created_at": (now - timedelta(seconds=400)).isoformat(),
        }
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps(step) + "\n")

        tasks = get_active_background_tasks(self.transcript_path, conv_id="conv1")
        self.assertEqual(len(tasks), 0)
        self.assertFalse(has_active_background_tasks(self.transcript_path, conv_id="conv1"))

    def test_timer_cancelled_is_retired(self):
        now = datetime.now(timezone.utc)
        steps = [
            {
                "type": "GENERIC",
                "content": "Tool is running as a background task with task id: conv1/task-102\nTask Description: Timer: 120s, Prompt: Wait",
                "created_at": (now - timedelta(seconds=5)).isoformat(),
            },
            {
                "type": "GENERIC",
                "content": "Task conv1/task-102 timer cancelled by user",
                "created_at": now.isoformat(),
            },
        ]
        with open(self.transcript_path, "w") as f:
            for s in steps:
                f.write(json.dumps(s) + "\n")

        tasks = get_active_background_tasks(self.transcript_path, conv_id="conv1")
        self.assertEqual(len(tasks), 0)

    def test_timer_fired_via_sender_message_is_retired(self):
        now = datetime.now(timezone.utc)
        steps = [
            {
                "type": "GENERIC",
                "content": "Tool is running as a background task with task id: conv1/task-103\nTask Description: Timer: 60s, Prompt: Wakeup",
                "created_at": (now - timedelta(seconds=30)).isoformat(),
            },
            {
                "type": "SYSTEM_MESSAGE",
                "content": "[Message] timestamp=2026-09-19T03:00:00Z sender=conv1/task-103 priority=MESSAGE_PRIORITY_HIGH content=Wakeup",
                "created_at": now.isoformat(),
            },
        ]
        with open(self.transcript_path, "w") as f:
            for s in steps:
                f.write(json.dumps(s) + "\n")

        tasks = get_active_background_tasks(self.transcript_path, conv_id="conv1")
        self.assertEqual(len(tasks), 0)


class TestExternalDispatches(unittest.TestCase):
    """Verifies detached external CLI dispatch tracking."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.task_dir = os.path.join(self.test_dir, "dispatch-task-1")
        os.makedirs(self.task_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("sage.dispatches._is_pid_alive", return_value=True)
    def test_active_detached_dispatch_detected(self, mock_pid_alive):
        steps = [
            {
                "type": "GENERIC",
                "content": (
                    "The command exited with code 0.\n"
                    "Output:\n"
                    "PID: 12345\n"
                    f"TASK: {self.task_dir}\n"
                    f"EXIT_CODE: {self.task_dir}/exit.code (appears when done; 124 = timed out)\n"
                ),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        dispatches = get_active_external_dispatches(steps)
        self.assertEqual(len(dispatches), 1)
        self.assertEqual(dispatches[0]["pid"], 12345)
        self.assertEqual(dispatches[0]["task_dir"], self.task_dir)
        self.assertTrue(has_active_external_dispatches(steps))

    @patch("sage.dispatches._is_pid_alive", return_value=True)
    def test_completed_detached_dispatch_cleared_by_exit_code(self, mock_pid_alive):
        exit_code_file = os.path.join(self.task_dir, "exit.code")
        with open(exit_code_file, "w") as f:
            f.write("0\n")

        steps = [
            {
                "type": "GENERIC",
                "content": (
                    "PID: 12345\n"
                    f"TASK: {self.task_dir}\n"
                    f"EXIT_CODE: {exit_code_file}\n"
                ),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        dispatches = get_active_external_dispatches(steps)
        self.assertEqual(len(dispatches), 0)
        self.assertFalse(has_active_external_dispatches(steps))

    @patch("sage.dispatches._is_pid_alive", return_value=False)
    def test_dead_pid_not_considered_active(self, mock_pid_alive):
        steps = [
            {
                "type": "GENERIC",
                "content": (
                    "PID: 99999\n"
                    f"TASK: {self.task_dir}\n"
                    f"EXIT_CODE: {self.task_dir}/exit.code\n"
                ),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        dispatches = get_active_external_dispatches(steps)
        self.assertEqual(len(dispatches), 0)


class TestInteractiveStallDetection(unittest.TestCase):
    """Verifies detection of background tasks hung waiting on stdin."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.test_dir, ".system_generated", "logs", "transcript.jsonl")
        self.tasks_dir = os.path.join(self.test_dir, ".system_generated", "tasks")
        os.makedirs(os.path.dirname(self.transcript_path), exist_ok=True)
        os.makedirs(self.tasks_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_npx_interactive_stall_detected_from_disk_log(self):
        log_file = os.path.join(self.tasks_dir, "task-2446.log")
        with open(log_file, "w") as f:
            f.write("Need to install the following packages:\nwrangler@4.135.0\nOk to proceed? (y) ")

        steps = [
            {
                "type": "GENERIC",
                "content": "Tool is running as a background task with task id: conv1/task-2446\nTask Description: npx wrangler pages deploy",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        with open(self.transcript_path, "w") as f:
            for s in steps:
                f.write(json.dumps(s) + "\n")

        stalled = get_stalled_background_tasks(steps, transcript_path=self.transcript_path, conv_id="conv1")
        self.assertEqual(len(stalled), 1)
        self.assertEqual(stalled[0]["task_id"], "conv1/task-2446")
        self.assertIn("Ok to proceed?", stalled[0]["stall_prompt"])

    def test_healthy_background_task_not_stalled(self):
        log_file = os.path.join(self.tasks_dir, "task-3000.log")
        with open(log_file, "w") as f:
            f.write("running cargo test...\ntest test_foo ... ok\n")

        steps = [
            {
                "type": "GENERIC",
                "content": "Tool is running as a background task with task id: conv1/task-3000\nTask Description: cargo test",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
        with open(self.transcript_path, "w") as f:
            for s in steps:
                f.write(json.dumps(s) + "\n")

        stalled = get_stalled_background_tasks(steps, transcript_path=self.transcript_path, conv_id="conv1")
        self.assertEqual(len(stalled), 0)


class TestRunnerStopGating(unittest.TestCase):
    """Verifies that sage.lite.runner respects subagents, dispatches, and stalls."""

    @patch("sage.lite.runner.emit_continue_response")
    @patch("sage.lite.runner.get_stalled_background_tasks")
    @patch("sage.lite.runner.acquire_conversation_lock", return_value=True)
    @patch("sage.lite.runner.get_transcript_path", return_value="/tmp/test.jsonl")
    @patch("sage.lite.runner._read_transcript_steps", return_value=[{"type": "USER_INPUT", "content": "hello"}])
    def test_runner_intervenes_on_stalled_task(self, mock_steps, mock_path, mock_lock, mock_stalled, mock_emit):
        mock_emit.side_effect = SystemExit(0)
        mock_stalled.return_value = [{
            "task_id": "conv1/task-999",
            "description": "npx wrangler",
            "stall_prompt": "Ok to proceed? (y)",
            "age_seconds": 10.0,
        }]
        from sage.lite.runner import run_lite_stop_audit
        try:
            run_lite_stop_audit(raw_payload=json.dumps({"conversationId": "conv1"}))
        except SystemExit:
            pass
        mock_emit.assert_called_once()
        self.assertIn("blocked waiting for interactive stdin input", mock_emit.call_args[0][0])

    @patch("sage.lite.runner.fail_safe_exit")
    @patch("sage.lite.runner.has_active_subagents", return_value=True)
    @patch("sage.lite.runner.acquire_conversation_lock", return_value=True)
    @patch("sage.lite.runner.get_transcript_path", return_value="/tmp/test.jsonl")
    @patch("sage.lite.runner._read_transcript_steps", return_value=[{"type": "USER_INPUT", "content": "hello"}])
    def test_runner_bypasses_audit_when_subagents_active(self, mock_steps, mock_path, mock_lock, mock_subagents, mock_exit):
        mock_exit.side_effect = SystemExit(0)
        from sage.lite.runner import run_lite_stop_audit
        try:
            run_lite_stop_audit(raw_payload=json.dumps({"conversationId": "conv1"}))
        except SystemExit:
            pass
        mock_exit.assert_called_once_with("Active subagents running")

    @patch("sage.lite.runner.fail_safe_exit")
    @patch("sage.lite.runner.has_active_external_dispatches", return_value=True)
    @patch("sage.lite.runner.acquire_conversation_lock", return_value=True)
    @patch("sage.lite.runner.get_transcript_path", return_value="/tmp/test.jsonl")
    @patch("sage.lite.runner._read_transcript_steps", return_value=[{"type": "USER_INPUT", "content": "hello"}])
    def test_runner_bypasses_audit_when_external_dispatches_active(self, mock_steps, mock_path, mock_lock, mock_dispatches, mock_exit):
        mock_exit.side_effect = SystemExit(0)
        from sage.lite.runner import run_lite_stop_audit
        try:
            run_lite_stop_audit(raw_payload=json.dumps({"conversationId": "conv1"}))
        except SystemExit:
            pass
        mock_exit.assert_called_once_with("Active external dispatches running")


if __name__ == "__main__":
    unittest.main()
