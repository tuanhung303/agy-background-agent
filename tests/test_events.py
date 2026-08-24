"""
tests.test_events - Unit tests for event-based advisor summon context formatting.
"""

import unittest
from sage.events import (
    EVENT_FINAL_STOP,
    EVENT_HEARTBEAT,
    EVENT_TOOL_THRESHOLD,
    EVENT_ERROR_LOOP,
    EVENT_SENSITIVE_TOOL,
    EVENT_STALE_TASK,
    EVENT_PARALLEL_OPP,
    STYLE_VERBOSE,
    assert_polarity_intact,
    caveman,
    format_summon_message,
)


class TestAdvisorEvents(unittest.TestCase):
    """Tests dynamic, context-aware fact-ranked event summon message formatting."""

    def test_format_final_stop_event(self):
        msg = format_summon_message(EVENT_FINAL_STOP)
        self.assertIn("[EVT·final_stop s3]", msg)
        self.assertIn("Final stop:", msg)
        self.assertIn("Final Stop Gate", msg)
        self.assertIn("live empirical evidence", msg)
        self.assertIn("ship this code to production", msg)
        self.assertIn("distribute this to the customer", msg)

    def test_format_final_stop_event_with_facts(self):
        msg = format_summon_message(EVENT_FINAL_STOP, total_tools=24, diff=150)
        self.assertIn("[EVT·final_stop s3] tools=24 · diff=~100L", msg)
        self.assertIn("Final stop:", msg)
        self.assertIn("Final Stop Gate", msg)

    def test_format_heartbeat_event(self):
        msg = format_summon_message(EVENT_HEARTBEAT, duration=180.0, total_tools=18)
        self.assertIn("[EVT·heartbeat s2]", msg)
        self.assertIn("dur=~2m", msg)
        self.assertIn("tools=18", msg)
        self.assertIn("ASK waiting on bg task", msg)

    def test_format_tool_threshold_event(self):
        msg = format_summon_message(EVENT_TOOL_THRESHOLD, total_tools=14, mix=["write_to_file", "run_command"])
        self.assertIn("[EVT·tool_threshold s1]", msg)
        self.assertIn("tools=14", msg)
        self.assertIn("mix=run_command/write_to_file", msg)
        self.assertNotIn("ASK ", msg)
        self.assertNotIn("score=", msg)
        self.assertNotIn("delta=", msg)
        self.assertNotIn("goal=", msg)

    def test_format_error_loop_event(self):
        msg = format_summon_message(EVENT_ERROR_LOOP, tool_name="run_command", error_sig="exit code 127", error_streak=5)
        self.assertIn("[EVT·error_loop s3]", msg)
        self.assertIn("sig=exit code 127", msg)
        self.assertIn("tool=run_command", msg)
        self.assertIn("fails=5", msg)
        self.assertIn("ASK root cause. exact fix cmd. NO blind retry.", msg)

    def test_format_error_loop_escalated(self):
        msg = format_summon_message(EVENT_ERROR_LOOP, tool_name="run_command", error_sig="exit code 127", error_streak=5, rep=2)
        self.assertIn("ASK prior steer ignored. change approach, NOT retry count.", msg)

    def test_format_sensitive_tool_event(self):
        msg = format_summon_message(EVENT_SENSITIVE_TOOL, keyword="git", command_snippet="git push origin main --force")
        self.assertIn("[EVT·sensitive_tool s3]", msg)
        self.assertIn("kw=git", msg)
        self.assertIn("cmd=git push origin main --force", msg)
        self.assertIn("ASK target env + preconditions + rollback verified BEFORE mutation.", msg)

    def test_format_stale_task_event(self):
        msg = format_summon_message(EVENT_STALE_TASK, task_id="task-1", task_desc="cargo build", age_seconds=320.0)
        self.assertIn("[EVT·stale_task s2]", msg)
        self.assertIn("task=task-1", msg)
        self.assertIn("bg=cargo build", msg)
        self.assertIn("age=~5m", msg)
        self.assertIn("ASK producing output or hung? keep watch or kill.", msg)

    def test_format_parallel_opp_event(self):
        msg = format_summon_message(EVENT_PARALLEL_OPP, signal_text="disjoint test suites")
        self.assertIn("[EVT·parallel_opportunity s1]", msg)
        self.assertIn("disjoint test suites", msg)
        self.assertNotIn("ASK ", msg)

    def test_format_fallback_event(self):
        msg = format_summon_message("unknown_event", fallback_signal="Custom signal")
        self.assertEqual(msg, "Custom signal")

    def test_polarity_protection(self):
        self.assertTrue(assert_polarity_intact("ASK NO blind retry. MUST verify BEFORE mutation."))
        self.assertFalse(assert_polarity_intact("ASK retry is not permitted"))

    def test_secret_redaction(self):
        msg = format_summon_message(EVENT_SENSITIVE_TOOL, command_snippet="curl -H 'token: sec12345'")
        self.assertNotIn("sec12345", msg)
        self.assertIn("[redacted]", msg)

    def test_bucket_lines(self):
        from sage.events import bucket_lines
        self.assertEqual(bucket_lines(0), "0L")
        self.assertEqual(bucket_lines(10), "~10L")
        self.assertEqual(bucket_lines(50), "~50L")
        self.assertEqual(bucket_lines(100), "~100L")
        self.assertEqual(bucket_lines(500), "~500L")
        self.assertEqual(bucket_lines(750), "~1kL")
        self.assertEqual(bucket_lines(1000), "~1kL")
        self.assertEqual(bucket_lines(1001), ">1kL")
        self.assertEqual(bucket_lines(3000), ">1kL")


if __name__ == "__main__":
    unittest.main()
