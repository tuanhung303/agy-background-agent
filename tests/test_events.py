"""
tests.test_events - Unit tests for event-based advisor summon context formatting.
"""

import unittest
from advisor.events import (
    EVENT_FINAL_STOP,
    EVENT_HEARTBEAT,
    EVENT_TOOL_THRESHOLD,
    EVENT_ERROR_LOOP,
    EVENT_SENSITIVE_TOOL,
    EVENT_STALE_TASK,
    EVENT_PARALLEL_OPP,
    format_summon_message,
)


class TestAdvisorEvents(unittest.TestCase):
    """Tests human-readable event summon message formatting."""

    def test_format_final_stop_event(self):
        msg = format_summon_message(EVENT_FINAL_STOP)
        self.assertIn("Final stop:", msg)
        self.assertIn("Final Stop Gate", msg)
        self.assertIn("live empirical evidence", msg)
        self.assertIn("ship this code to production", msg)
        self.assertIn("distribute this to the customer", msg)

    def test_format_heartbeat_event(self):
        msg = format_summon_message(EVENT_HEARTBEAT, duration=180.0)
        self.assertIn("running tools for 180 seconds", msg)
        self.assertIn("hang or deadlock", msg)

    def test_format_tool_threshold_event(self):
        msg = format_summon_message(EVENT_TOOL_THRESHOLD, total_tools=15, delta_tools=10, pinned_goal="Fix bug")
        self.assertIn("heavy sequence of 15 tool calls (delta: 10)", msg)
        self.assertIn("Fix bug", msg)

    def test_format_error_loop_event(self):
        msg = format_summon_message(EVENT_ERROR_LOOP, tool_name="run_command", error_sig="exit code 127")
        self.assertIn("5 consecutive tool failures", msg)
        self.assertIn("run_command", msg)
        self.assertIn("exit code 127", msg)

    def test_format_sensitive_tool_event(self):
        msg = format_summon_message(EVENT_SENSITIVE_TOOL, keyword="git", command_snippet="git push origin main")
        self.assertIn("keyword 'git'", msg)
        self.assertIn("git push origin main", msg)

    def test_format_stale_task_event(self):
        msg = format_summon_message(EVENT_STALE_TASK, task_id="task-1", task_desc="cargo build", age_seconds=320.0)
        self.assertIn("background task 'task-1'", msg)
        self.assertIn("cargo build", msg)
        self.assertIn("320s", msg)

    def test_format_parallel_opp_event(self):
        msg = format_summon_message(EVENT_PARALLEL_OPP, signal_text="disjoint test suites")
        self.assertIn("opportunity for parallel execution was detected", msg)
        self.assertIn("disjoint test suites", msg)

    def test_format_fallback_event(self):
        msg = format_summon_message("unknown_event", fallback_signal="Custom signal")
        self.assertEqual(msg, "Custom signal")


if __name__ == "__main__":
    unittest.main()
