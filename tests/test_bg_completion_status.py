#!/usr/bin/env python3
"""
tests.test_bg_completion_status - status-aware background-task retirement.

Live regression (2026-08-27): a manage_task POLL block prints
    Completed At: <poll timestamp>
    Status: RUNNING
and a view_file of a task LOG echoes "Completed At:" plus the task-id path.
The old keyword-only matcher ("completed" in text) retired RUNNING tasks,
the active set emptied, is_post_invocation_completion_candidate went True,
and the final gate emitted "[RECAP] Work completed..." while ServiceNow
queries were still streaming — the premature-recap incident.

Locked here:
- RUNNING poll blocks never retire their task
- log/echo "Completed At:" without an explicit done phrase never retires
- genuine completions still do (sender=, finished with result, status lines,
  Task X completed / was canceled / terminated / killed / timer cancelled)
"""

import json
import os
import tempfile
import unittest

from sage.transcript import get_active_background_tasks


def _write(steps):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for s in steps:
        tmp.write(json.dumps(s) + "\n")
    tmp.close()
    return tmp.name


SPAWN = {"type": "GENERIC",
         "content": "Tool is running as a background task with task id: conv/task-51\n"
                    "Task Description: uv run --with playwright python sn_query.py"}
RUNNING_POLL = {"type": "GENERIC",
                "content": "Created At: 2026-08-27T07:07:41+07:00\nCompleted At: 2026-08-27T07:07:41+07:00\n"
                           "Task: conv/task-51\nStatus: RUNNING\nLog: .../tasks/task-51.log\nLast progress: never"}
LOG_VIEW = {"type": "GENERIC",
            "content": "Created At: 2026-08-27T07:07:42+07:00\nCompleted At: 2026-08-27T07:07:43+07:00\n"
                       "File Path: `file:///brain/conv/.system_generated/tasks/task-51.log`\n"
                       "Total Lines: 1\nShowing lines 1 to 1\n"}
FINISHED = {"type": "SYSTEM_MESSAGE",
            "content": "<SYSTEM_MESSAGE>[Message] timestamp=... sender=conv/task-51 "
                       "content=Task id \"conv/task-51\" finished with result:\nThe command exited with code 0."}
STILL_RUNNING_MARKER = ["task-51"]


class TestBgCompletionStatus(unittest.TestCase):
    def _actives(self, steps):
        path = _write([SPAWN] + steps)
        try:
            return [t["task_id"].split("/")[-1] for t in get_active_background_tasks(path)]
        finally:
            os.unlink(path)

    def test_running_poll_does_not_retire(self):
        self.assertEqual(self._actives([RUNNING_POLL]), STILL_RUNNING_MARKER)

    def test_log_view_completed_at_echo_does_not_retire(self):
        self.assertEqual(self._actives([RUNNING_POLL, LOG_VIEW]), STILL_RUNNING_MARKER)

    def test_finished_with_result_retires(self):
        self.assertEqual(self._actives([FINISHED]), [])

    def test_bare_task_completed_phrase_retires(self):
        c = {"type": "GENERIC", "content": "Task \"conv/task-51\" completed successfully."}
        self.assertEqual(self._actives([c]), [])

    def test_terminal_status_lines_retire(self):
        for phrase in ("Task task-51 terminated unexpectedly", "Task task-51 killed",
                       "Task task-51 status: done", "Task task-51 status: failed",
                       "Task task-51 timer cancelled"):
            self.assertEqual(self._actives([{"type": "GENERIC", "content": phrase}]), [], phrase)


if __name__ == "__main__":
    unittest.main()
