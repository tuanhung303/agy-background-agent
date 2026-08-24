#!/usr/bin/env python3
"""
tests.test_policies - Unit coverage for extracted decision policies.
"""

import unittest
from unittest.mock import patch

from sage import policies

CTX = dict(
    conv_id="c", transcript_path="/nonexistent", clean_prompt="p",
    initial_line_count=3, total_tool_calls=30, turn_tool_names=["Bash"],
    user_prompt="goal", agent_steps=[], git_diff="",
    state={"mid_turn_steers": 0, "sage_error_streak": 0, "last_verified_tools": 0},
)


def _frozen(latest_tools=30, latest_lines=3, candidate=False):
    """Patch transcript reads so the freshness checks see a quiet session."""
    return (
        patch.object(policies, "has_new_user_activity", return_value=False),
        patch.object(policies, "extract_session_and_turn_data",
                     return_value=(None, None, None, latest_tools, None, None, None, latest_lines)),
        patch.object(policies, "is_post_invocation_completion_candidate", return_value=candidate),
    )


class TestBackgroundWatch(unittest.TestCase):
    def test_no_tasks_is_none(self):
        self.assertEqual(policies.background_watch([], set())["action"], "none")

    def test_stale_task_steers_with_identity(self):
        tasks = [{"task_id": "t1", "description": "build", "age_seconds": 400.0}]
        act = policies.background_watch(tasks, set())
        self.assertEqual(act["action"], "steer")
        self.assertEqual(act["task_id"], "t1")

    def test_already_steered_not_resteered(self):
        tasks = [{"task_id": "t1", "description": "build", "age_seconds": 400.0}]
        self.assertEqual(policies.background_watch(tasks, {"t1"})["action"], "already_steered")

    def test_fresh_task_is_grace(self):
        tasks = [{"task_id": "t1", "description": "build", "age_seconds": 10.0}]
        self.assertEqual(policies.background_watch(tasks, set())["action"], "grace")


class TestFinalSageGate(unittest.TestCase):
    def _gate(self, **kw):
        return policies.final_sage_gate(**{**CTX, **kw})

    def test_interval_gates_midturn_but_not_final(self):
        ctx = {**CTX, "total_tool_calls": 5}
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1):
            act = policies.sage_flow("midturn", **{**ctx, "forced": False})
            self.assertEqual(act["action"], "exit")
            self.assertIn("interval", act["reason"])
            frozen = _frozen(latest_tools=5)
            with patch.object(policies, "evaluate_mid_turn_progress",
                              return_value={"status": "on_track"}) as ev, \
                    frozen[0], frozen[1], frozen[2]:
                gate = policies.final_sage_gate(**ctx)
            self.assertEqual(gate["action"], "healthy")
            ev.assert_called_once()

    def test_yield_and_progressed_preserved(self):
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "has_new_user_activity", return_value=True), \
                patch.object(policies, "evaluate_mid_turn_progress", return_value={"status": "on_track"}):
            self.assertEqual(self._gate()["action"], "yield")
        frozen = _frozen(latest_tools=31)
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "evaluate_mid_turn_progress", return_value={"status": "on_track"}), \
                frozen[0], frozen[1], frozen[2]:
            act = self._gate()
        self.assertEqual(act["action"], "progressed")
        self.assertEqual(act["tools"], 31)

    def test_emit_and_hold_dedup_mapping(self):
        for decision, want in (("steer", "emit"), ("watchout", "emit"), ("hold_dedup", "hold_dedup")):
            frozen = _frozen()
            with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                    patch.object(policies, "evaluate_mid_turn_progress", return_value={"status": "s"}), \
                    patch.object(policies, "classify_advice",
                                 return_value={"decision": decision, "text": "T", "seen": {"k": 1}}), \
                    frozen[0], frozen[1], frozen[2]:
                act = self._gate()
            self.assertEqual(act["action"], want)
            if want == "emit":
                self.assertEqual(act["decision"], decision)
                self.assertEqual(act["text"], "T")

    def test_healthy_hold_carries_note_and_recap(self):
        frozen = _frozen()
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "evaluate_mid_turn_progress", return_value={"status": "on_track", "recap": "Built module, 10 tests passed"}), \
                patch.object(policies, "classify_advice",
                             return_value={"decision": "hold", "text": "all good", "recap": "Built module, 10 tests passed", "seen": {}}), \
                frozen[0], frozen[1], frozen[2]:
            act = self._gate()
        self.assertEqual(act["action"], "healthy")
        self.assertEqual(act["recap"], "Built module, 10 tests passed")
        self.assertTrue("Sage final assessment" in act["note"] or "Advisor final assessment" in act["note"])
        self.assertIn("all good", act["note"])

    def test_sage_flow_accelerates_on_parallel_signals(self):
        ctx = {**CTX, "total_tool_calls": 3}  # delta = 3 < SAGE_TOOL_INTERVAL (10)
        par_sig = {
            "parallelizable": True,
            "signal_text": "PARALLELIZABLE: Disjoint files detected",
        }
        frozen = _frozen(latest_tools=3)
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "get_parallelizable_signals", return_value=par_sig), \
                patch.object(policies, "evaluate_mid_turn_progress", return_value={"status": "on_track"}) as mock_eval, \
                patch.object(policies, "classify_advice", return_value={"decision": "hold", "text": "ok", "seen": {}}), \
                frozen[0], frozen[1], frozen[2]:
            act = policies.sage_flow("midturn", **ctx)
        self.assertEqual(act["action"], "healthy")
        mock_eval.assert_called_once()
        self.assertTrue(mock_eval.call_args.kwargs.get("is_forced"))
        self.assertIn("PARALLELIZABLE", mock_eval.call_args.kwargs.get("signals"))


TestFinalAdvisorGate = TestFinalSageGate


if __name__ == "__main__":
    unittest.main()
