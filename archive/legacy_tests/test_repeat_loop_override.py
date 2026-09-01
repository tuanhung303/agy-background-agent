#!/usr/bin/env python3
"""
tests.test_repeat_loop_override - Repeat-loop cadence override (iteration 1).

The mid-turn cadence gate fires on accumulated tool-score. Cheap retry loops
(a 1.5-weight `pytest` re-run never sums to 10) hide under that threshold and
burn context silently. This locks the fix class, not one transcript:

  1. Signature normalization collapses retry-churn variants (counters,
     timestamps, seconds) into one repeat signature...
  2. ...while genuinely different commands stay distinct (full-args hash).
  3. sage_flow forces ONE evaluation per fresh >=2-tool window when repeats
     are live — without the user-force flag and bounded across turns.
"""

import json
import os
import tempfile
import unittest

from sage.policies import sage_flow
from sage.transcript import _tool_sig, has_repeated_tool_calls


def _run_cmd(cmd):
    return {"name": "run_command", "args": {"args": {"command": cmd}}}


def _write_steps(steps):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for s in steps:
        tmp.write(json.dumps(s) + "\n")
    tmp.close()
    return tmp.name


class TestToolSigNormalization(unittest.TestCase):
    def test_retry_counter_variants_collapse(self):
        sigs = {_tool_sig(_run_cmd(f"pytest -x 2>&1 | tail -20 && echo i{i} FAILED")) for i in range(4)}
        self.assertEqual(len(sigs), 1)

    def test_timestamped_polls_collapse(self):
        sigs = {
            _tool_sig(_run_cmd(f"gh run view --log | grep done && date={ts}"))
            for ts in ("2026-08-27T10:00Z", "2026-08-27T10:01Z")
        }
        self.assertEqual(len(sigs), 1)

    def test_different_long_commands_stay_distinct(self):
        """Full-args hash: no truncated-prefix collision on shared long paths."""
        base = "/Users/dev/project/deeply/nested/module/tests"
        a = _tool_sig({"name": "edit_file", "args": {"file_path": f"{base}/policies.py", "new_string": "    return alpha"}})
        b = _tool_sig({"name": "edit_file", "args": {"file_path": f"{base}/policies.py", "new_string": "    return beta"}})
        c = _tool_sig({"name": "edit_file", "args": {"file_path": f"{base}/policies.py", "new_string": "    return gamma"}})
        self.assertEqual(len({a, b, c}), 3)

    def test_same_substance_after_timestamp_stays_distinct(self):
        """Anchor fix: snapshot ids after a timestamp must not be swallowed."""
        a = _tool_sig(_run_cmd("restore 2026-08-27T10:00Z/snapshot-alpha"))
        b = _tool_sig(_run_cmd("restore 2026-08-27T10:00Z/snapshot-beta"))
        self.assertNotEqual(a, b)

    def test_status_tools_are_ignored(self):
        self.assertIsNone(_tool_sig({"name": "manage_task", "args": {"op": "list"}}))

    def test_malformed_args_get_question_marker(self):
        t = {"name": "run_command", "args": None}  # json.dumps(None) works but args-like dict missing; falsy -> marker? No: dumps("null") fine. Use unserializable:
        class Unserializable:
            def __str__(self): return "x"
        t2 = {"name": "run_command", "args": {Unserializable(): 1}}
        sig = _tool_sig(t2)
        self.assertTrue(sig is None or sig.endswith("|?"))


class TestRepeatDetectionEndToEnd(unittest.TestCase):
    def test_mutating_retry_loop_detected_at_three_calls(self):
        steps = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "fix"}]
        for i in range(3):
            steps.append({"type": "PLANNER_RESPONSE", "content": "retry",
                          "tool_calls": [_run_cmd(f"pytest -x 2>&1 | tail -20 && echo i{i} FAILED")]})
        path = _write_steps(steps)
        try:
            self.assertTrue(has_repeated_tool_calls(path))
        finally:
            os.unlink(path)

    def test_forward_progress_not_flagged(self):
        steps = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "fix"}]
        for mod in ("auth", "billing", "reports"):
            steps.append({"type": "PLANNER_RESPONSE", "content": "w",
                          "tool_calls": [{"name": "edit_file", "args": {"file_path": f"sage/{mod}.py", "new_string": f"def fix_{mod}(): pass"}}]})
        path = _write_steps(steps)
        try:
            self.assertFalse(has_repeated_tool_calls(path))
        finally:
            os.unlink(path)


ON_TRACK = {"status": "on_track", "guidance": "", "confidence": 0.9}
LOOP_STEER = {"status": "off_track", "category": "loop_detection",
              "action": "Break the retry loop; diagnose root cause first",
              "evidence": "identical failing command repeated",
              "confidence": 0.95, "guidance": "Stop rerunning the same command."}


class TestLoopOverrideCadenceGate(unittest.TestCase):
    """sage_flow must force evaluation on live repeat loops under score threshold."""

    def setUp(self):
        self.steps = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "fix auth"}]
        self.calls = []

    def _transcript_with_loops(self, n):
        for i in range(n):
            self.steps.append({"type": "PLANNER_RESPONSE", "content": "retry",
                               "tool_calls": [_run_cmd(f"pytest -x && echo i{i} FAILED")]})
        return _write_steps(self.steps)

    def test_below_threshold_exits_without_loop(self):
        # Healthy read tools: low score, NO repeats -> normal early exit.
        steps = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "explore"}]
        steps.append({"type": "PLANNER_RESPONSE", "content": "r",
                      "tool_calls": [{"name": "view_file", "args": {}}, {"name": "grep_search", "args": {}}]})
        path = _write_steps(steps)
        state = {}
        try:
            res = sage_flow("midturn", conv_id="c1", transcript_path=path, clean_prompt="explore",
                            initial_line_count=len(steps), total_tool_calls=2, turn_tool_names={"view_file"},
                            user_prompt="explore", agent_steps=[], git_diff="", state=state)
        finally:
            os.unlink(path)
        self.assertEqual(res["action"], "exit")
        self.assertIn("below threshold", res["reason"])

    def test_live_loop_forces_evaluation_once_per_window(self):
        from unittest.mock import patch
        import sage.policies as P
        path = self._transcript_with_loops(3)
        state = {}
        emissions = []
        try:
            with patch.object(P, "evaluate_mid_turn_progress", return_value=LOOP_STEER) as m:
                res = sage_flow("midturn", conv_id="c2", transcript_path=path, clean_prompt="fix auth",
                                initial_line_count=len(self.steps), total_tool_calls=3,
                                turn_tool_names={"run_command"}, user_prompt="fix auth",
                                agent_steps=[], git_diff="", state=state)
                self.assertEqual(res["action"], "emit")          # forced through despite score < threshold
                self.assertTrue(m.call_args.kwargs.get("is_forced") or m.call_args[1].get("is_forced"))
                emissions.append(res["text"])
                # last_steer_category is recorded by the RUNNER post-emit
                # (record_sage_emit(**gu)); sage_flow only reports the category.
                self.assertEqual(res.get("category"), "loop_detection")
                self.assertIn("last_loop_eval_tools", state)  # window marker set by flow itself
                # Override window consumed: same tool count again -> bounded, no refire
                with patch.object(P, "evaluate_mid_turn_progress", return_value=LOOP_STEER) as m2:
                    res2 = sage_flow("midturn", conv_id="c2", transcript_path=path, clean_prompt="fix auth",
                                     initial_line_count=len(self.steps), total_tool_calls=3,
                                     turn_tool_names={"run_command"}, user_prompt="fix auth",
                                     agent_steps=[], git_diff="", state=state)
                    self.assertEqual(res2["action"], "exit")     # fresh-window gate holds
                    m2.assert_not_called()
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
