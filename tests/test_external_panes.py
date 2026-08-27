#!/usr/bin/env python3
"""
tests.test_external_panes - External worker pane settlement tracking.

Regression (2026-08-26): agent declared "claude opus 5 completed its critique"
while the Opus worker pane was still streaming; the sage recapped healthy and the
user caught the lie manually. get_active_external_panes must report the pane as
active at that exact moment so the stop gate blocks with force_continue.
"""

import json
import os
import unittest

from sage.watchers import get_active_external_panes


def _step(content, tool_calls=None, created_at="2026-08-26T17:00:00+07:00"):
    return {"type": "GENERIC", "content": content, "tool_calls": tool_calls or [],
            "created_at": created_at}


def _cmd(text):
    return {"name": "run_command", "args": {"CommandLine": text}}


class TestGetActiveExternalPanes(unittest.TestCase):
    def test_created_and_sent_but_not_idle_is_active(self):
        steps = [
            _step("", [_cmd('terminal create --command "claude --model opus" '
                            '--json  # handle term_abc12345')]),
            _step("", [_cmd("terminal send --terminal term_abc12345 --text 'review please'")]),
        ]
        self.assertEqual(get_active_external_panes(steps), ["term_abc12345"])

    def test_idle_prompt_settles_pane(self):
        steps = [
            _step("", [_cmd('terminal create --command "claude" # term_abc12345')]),
            _step("output done\n\n❯\n\n bypass permissions on"),
        ]
        self.assertEqual(get_active_external_panes(steps), [])

    def test_close_settles_pane(self):
        steps = [
            _step("", [_cmd('terminal create --command "codex" # term_def67890')]),
            _step("", [_cmd("terminal close --terminal term_def67890 --json")]),
        ]
        self.assertEqual(get_active_external_panes(steps), [])

    def test_zsh_and_completed_settles_pane(self):
        steps = [
            _step("", [_cmd('terminal create --command "claude" # term_xyz12345')]),
            _step("user@mac % "),
        ]
        self.assertEqual(get_active_external_panes(steps), [])

        steps2 = [
            _step("", [_cmd('terminal create --command "claude" # term_xyz12345')]),
            _step("Command exited with code 0"),
        ]
        self.assertEqual(get_active_external_panes(steps2), [])

    def test_pane_block_stops_after_two_steers(self):
        """Kill-mutation for the runner steer ceiling: after 2 steers of the same
        pane, the stop gate must fall through (no more 'still streaming' block),
        otherwise the session hangs in an endless stop-block loop (2026-08-27)."""
        import json
        from unittest.mock import patch
        from sage.runner import run_session_stop_audit

        payload = json.dumps({
            "conversationId": "test_conv_pane_ceiling",
            "fullyIdle": True,
            "transcriptPath": "/tmp/dummy_trans.jsonl",
        })
        state = {"external_pane_steers": {"term_abc12345": 2}}
        exits = []

        with patch("sage.runner.acquire_conversation_lock", return_value=True), \
             patch("sage.runner.get_transcript_path", return_value="/tmp/dummy.jsonl"), \
             patch("sage.runner.extract_session_and_turn_data", return_value=("do work", "do work", [], 5, {"run_command"}, 0, 0, 10)), \
             patch("sage.runner.load_and_sync_session_state", return_value=("do work", "/tmp/state.json", state, False)), \
             patch("sage.runner.get_active_subagents", return_value=[]), \
             patch("sage.runner.get_active_external_panes", return_value=["term_abc12345"]), \
             patch("sage.runner.get_active_background_tasks", return_value=[]), \
             patch("sage.runner.is_subagent_session", return_value=False), \
             patch("sage.runner.is_post_invocation", return_value=True), \
             patch("sage.runner.is_post_invocation_completion_candidate", return_value=False), \
             patch("sage.runner.has_recent_tool_errors", return_value=False), \
             patch("sage.runner.has_repeated_tool_calls", return_value=False), \
             patch("sage.runner.advisor_flow", return_value={"action": "healthy", "text": "ok"}), \
             patch("sage.runner.save_session_state"), \
             patch("sage.runner.emit_continue_response") as emit_mock, \
             patch("sage.runner.fail_safe_exit", side_effect=lambda reason: exits.append(reason) or (_ for _ in ()).throw(SystemExit)):
            try:
                run_session_stop_audit(payload)
            except SystemExit:
                pass

        block_exits = [r for r in exits if "pane" in r.lower()]
        streaming_emits = [c for c in emit_mock.call_args_list if "still streaming" in str(c)]
        self.assertEqual(block_exits, [], f"steer ceiling breached: blocked again after 2 steers: {exits}")
        self.assertEqual(streaming_emits, [], "re-emitted 'still streaming' after steer ceiling reached")

    def test_pane_tracking_is_scoped_to_current_turn(self):
        """Kill-mutation for transcript turn-scoping: a stale open pane handle
        from a PREVIOUS user turn must not block the current stop — the old
        full-history scan made handles immortal, looping the stop gate."""
        from unittest.mock import patch
        from sage.transcript import get_active_external_panes

        steps = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "turn one", "tool_calls": []},
            _step("", [_cmd('terminal create --command "claude" # term_beef0123')]),
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "turn two", "tool_calls": []},
            _step("still working, no idle prompt yet in this turn"),
        ]
        with patch("sage.transcript._read_transcript_steps", return_value=steps):
            self.assertEqual(
                get_active_external_panes("/tmp/fake.jsonl"), [],
                "stale pane from previous turn leaked into current turn scan",
            )

        # Control: a pane created in the CURRENT turn is still tracked.
        steps_current = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "turn two", "tool_calls": []},
            _step("", [_cmd('terminal create --command "claude" # term_cafe1234')]),
            _step("still working, no idle prompt yet"),
        ]
        with patch("sage.transcript._read_transcript_steps", return_value=steps_current):
            self.assertEqual(get_active_external_panes("/tmp/fake.jsonl"), ["term_cafe1234"])


if __name__ == "__main__":
    unittest.main()
