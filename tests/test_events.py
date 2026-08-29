"""
tests.test_events - Unit tests for event-based advisor summon context formatting.
"""

import unittest
from sage.events import (
    EVENT_CONFUSED_GOAL,
    EVENT_DELEGATE,
    EVENT_DELEGATE_VIOLATION,
    EVENT_FACILITATION,
    EVENT_FACILITATION_REPEAT,
    EVENT_FANOUT,
    EVENT_FATIGUE,
    EVENT_FINAL_STOP,
    EVENT_GOAL_CHANGE,
    EVENT_HEARTBEAT,
    EVENT_NEW_PROMPT,
    EVENT_PARALLEL_OPP,
    EVENT_TOOL_THRESHOLD,
    EVENT_ERROR_LOOP,
    EVENT_SENSITIVE_TOOL,
    EVENT_STALE_TASK,
    PLAYBOOK_SECTIONS,
    STYLE_VERBOSE,
    assert_polarity_intact,
    caveman,
    format_summon_message,
    playbook_reminder,
)


class TestAdvisorEvents(unittest.TestCase):
    """Tests dynamic, context-aware fact-ranked event summon message formatting."""

    def test_format_final_stop_event(self):
        msg = format_summon_message(EVENT_FINAL_STOP)
        self.assertIn("[EVT·final_stop s3]", msg)
        self.assertIn("Final stop:", msg)
        self.assertIn("Final Stop Gate", msg)
        self.assertIn("Prove-It-Works", msg)
        self.assertIn("verify outputs directly against real artifacts", msg)
        self.assertIn("reject proxies, self-reports, or 'it compiles'", msg)
        self.assertTrue(assert_polarity_intact(msg))

    def test_format_final_stop_event_with_facts(self):
        msg = format_summon_message(EVENT_FINAL_STOP, total_tools=24, diff=150)
        self.assertIn("[EVT·final_stop s3] tools=24 · diff=~100L", msg)
        self.assertIn("Final stop:", msg)
        self.assertIn("Prove-It-Works", msg)

    def test_format_final_stop_event_plan_mode(self):
        msg = format_summon_message(EVENT_FINAL_STOP, total_tools=5, is_plan=True)
        self.assertIn("[EVT·final_stop s3] plan=1 · tools=5", msg)
        self.assertIn("Final stop in /plan mode:", msg)
        self.assertIn("adversarial grill-me audit", msg)
        self.assertIn("category='grill_me'", msg)
        self.assertIn("ask_question", msg)
        self.assertTrue(assert_polarity_intact(msg))

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

    def test_format_delegate_violation_event(self):
        msg = format_summon_message(EVENT_DELEGATE_VIOLATION)
        self.assertIn("[CMD·delegate·violation s2]", msg)
        self.assertIn("ASK inline execution detected while delegation ordered. delegate via invoke_subagent or continue inline with stated justification.", msg)
        self.assertTrue(assert_polarity_intact(msg))

    def test_format_facilitation_repeat_event(self):
        msg = format_summon_message(EVENT_FACILITATION_REPEAT)
        self.assertIn("[CMD·facilitation·repeat s2]", msg)
        self.assertIn("ASK prior delegation order ignored. delegate via invoke_subagent.", msg)
        self.assertTrue(assert_polarity_intact(msg))

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
        self.assertEqual(bucket_lines(float("inf")), ">1kL")
        self.assertEqual(bucket_lines(float("-inf")), "?")
        self.assertEqual(bucket_lines(float("nan")), "?")
        self.assertEqual(bucket_lines(0.5), "?")
        self.assertEqual(bucket_lines(12.34), "?")
        self.assertEqual(bucket_lines(-5), "?")
        self.assertEqual(bucket_lines(100.0), "~100L")
        self.assertEqual(bucket_lines(None), "?")
        self.assertEqual(bucket_lines(True), "?")
        self.assertEqual(bucket_lines(False), "?")

    def test_format_error_loop_boolean_facts(self):
        msg = format_summon_message(EVENT_ERROR_LOOP, err=True)
        self.assertIn("[EVT·error_loop s3] err=1", msg)
        self.assertNotIn("loop=", msg)
        self.assertIn("ASK root cause. exact fix cmd. NO blind retry.", msg)

    def test_playbook_reminder_format_and_defaults(self):
        self.assertEqual(
            playbook_reminder(EVENT_NEW_PROMPT, note="user approved"),
            '[EVT·new_prompt] user approved | Playbook: follow "Momentum Doctrine" in your doctrine.',
        )
        self.assertEqual(
            playbook_reminder(EVENT_FINAL_STOP),
            '[EVT·final_stop] | Playbook: follow "Final Stop Gate" in your doctrine.',
        )

    def test_playbook_sections_six_standard_events(self):
        expected_mappings = {
            EVENT_NEW_PROMPT: "Momentum Doctrine",
            EVENT_FATIGUE: "Momentum Doctrine",
            EVENT_FINAL_STOP: "Final Stop Gate",
            EVENT_CONFUSED_GOAL: "Momentum Doctrine",
            EVENT_GOAL_CHANGE: "Revised Goal",
            EVENT_FANOUT: "Delegation & Fanout (parallelize_subagent)",
        }
        for evt, sec in expected_mappings.items():
            self.assertEqual(PLAYBOOK_SECTIONS.get(evt), sec)
            rem = playbook_reminder(evt)
            self.assertIn(f"[EVT·{evt}]", rem)
            self.assertIn(f'Playbook: follow "{sec}" in your doctrine.', rem)


if __name__ == "__main__":
    unittest.main()
