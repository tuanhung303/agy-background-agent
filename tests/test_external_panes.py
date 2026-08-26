#!/usr/bin/env python3
"""
tests.test_external_panes - External worker pane settlement tracking.

Regression (2026-08-26): agent declared "claude opus 5 completed its critique"
while the Opus Orca pane was still streaming; the sage recapped healthy and the
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
            _step("", [_cmd('orca terminal create --command "claude --model opus" '
                            '--json  # handle term_abc12345')]),
            _step("", [_cmd("orca terminal send --terminal term_abc12345 --text 'review please'")]),
        ]
        self.assertEqual(get_active_external_panes(steps), ["term_abc12345"])

    def test_idle_prompt_settles_pane(self):
        steps = [
            _step("", [_cmd('orca terminal create --command "claude" # term_abc12345')]),
            _step("output done\n\n❯\n\n bypass permissions on"),
        ]
        self.assertEqual(get_active_external_panes(steps), [])

    def test_close_settles_pane(self):
        steps = [
            _step("", [_cmd('orca terminal create --command "codex" # term_def67890')]),
            _step("", [_cmd("orca terminal close --terminal term_def67890 --json")]),
        ]
        self.assertEqual(get_active_external_panes(steps), [])

    def test_plain_bash_commands_are_not_panes(self):
        steps = [
            _step("", [_cmd("pytest -q")]),
            _step("", [_cmd("orca status --json")]),
        ]
        self.assertEqual(get_active_external_panes(steps), [])


if __name__ == "__main__":
    unittest.main()
