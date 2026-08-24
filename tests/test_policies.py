#!/usr/bin/env python3
"""
tests.test_policies - Unit coverage for extracted decision policies.
"""

import unittest
from unittest.mock import patch

from sage import policies

def _ctx(**kw):
    base = dict(
        conv_id="c", transcript_path="/nonexistent", clean_prompt="p",
        initial_line_count=3, total_tool_calls=30, turn_tool_names=["Bash"],
        user_prompt="goal", agent_steps=[], git_diff="",
        state={"mid_turn_steers": 0, "sage_error_streak": 0, "last_verified_tools": 0},
    )
    base.update(kw)
    if "state" not in kw:
        base["state"] = dict(base["state"])
    return base


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
        return policies.final_sage_gate(**_ctx(**kw))

    def test_interval_gates_midturn_but_not_final(self):
        ctx = _ctx(total_tool_calls=5)
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1):
            act = policies.sage_flow("midturn", **{**ctx, "forced": False})
            self.assertEqual(act["action"], "exit")
            self.assertIn("delta below", act["reason"])
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
        ctx = _ctx(total_tool_calls=3)  # delta = 3 < SAGE_TOOL_INTERVAL (10)
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

    def test_weighted_scoring_triggers_on_mutations_early(self):
        # 4 edit calls = 4 * 2.5 = 10.0 score -> triggers audit even though total_tool_calls (4) < SAGE_TOOL_INTERVAL (10)
        ctx = _ctx(total_tool_calls=4)
        frozen = _frozen(latest_tools=4)
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "calculate_turn_tool_score", return_value=(10.0, 4)), \
                patch.object(policies, "evaluate_mid_turn_progress", return_value={"status": "on_track"}) as mock_eval, \
                patch.object(policies, "classify_advice", return_value={"decision": "hold", "text": "ok", "seen": {}}), \
                frozen[0], frozen[1], frozen[2]:
            act = policies.sage_flow("midturn", **ctx)
        self.assertEqual(act["action"], "healthy")
        mock_eval.assert_called_once()

    def test_weighted_scoring_exits_when_below_score_and_count_threshold(self):
        # 4 read calls = 4 * 0.5 = 2.0 score < 10.0, raw delta = 4 < 10 -> exits cleanly
        ctx = _ctx(total_tool_calls=4)
        frozen = _frozen(latest_tools=4)
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "calculate_turn_tool_score", return_value=(2.0, 4)), \
                frozen[0], frozen[1], frozen[2]:
            act = policies.sage_flow("midturn", **ctx)
        self.assertEqual(act["action"], "exit")
        self.assertIn("Mid-turn tool delta below threshold", act["reason"])
    def test_signal_note_newline_separation(self):
        ctx = _ctx(total_tool_calls=30, signal_note="[EVT·error_loop s3] err=1\nASK root cause.")
        par_sig = {
            "parallelizable": True,
            "categories": ["disjoint_files"],
            "signal_text": "PARALLELIZABLE: Independent workstreams detected. Suggest invoke_subagent with roles: Implementer.",
        }
        frozen = _frozen(latest_tools=30)
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "get_parallelizable_signals", return_value=par_sig), \
                patch.object(policies, "evaluate_mid_turn_progress", return_value={"status": "on_track"}) as mock_eval, \
                patch.object(policies, "classify_advice", return_value={"decision": "hold", "text": "ok", "seen": {}}), \
                frozen[0], frozen[1], frozen[2]:
            act = policies.sage_flow("midturn", **ctx)
        self.assertEqual(act["action"], "healthy")
        passed_signals = mock_eval.call_args.kwargs.get("signals")
        self.assertIn("[EVT·error_loop s3]", passed_signals)
        self.assertIn("ASK root cause.\nPARALLELIZABLE:", passed_signals)

    def test_structural_parallel_categories_edge_triggering(self):
        ctx = _ctx(total_tool_calls=3)
        par_sig = {
            "parallelizable": True,
            "categories": ["disjoint_files"],
            "signal_text": "PARALLELIZABLE: Disjoint files",
        }
        state = dict(ctx["state"])
        f1 = _frozen(latest_tools=3)
        # First call: new structural category triggers forced evaluation
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "get_parallelizable_signals", return_value=par_sig), \
                patch.object(policies, "evaluate_mid_turn_progress", return_value={"status": "on_track"}) as mock_eval, \
                patch.object(policies, "classify_advice", return_value={"decision": "hold", "text": "ok", "seen": {}}), \
                f1[0], f1[1], f1[2]:
            act = policies.sage_flow("midturn", **{**ctx, "state": state})
            self.assertEqual(act["action"], "healthy")
            self.assertTrue(mock_eval.call_args.kwargs.get("is_forced"))

        # Second call with same state (state["last_par_fp"] recorded) and low tool delta: exits
        f2 = _frozen(latest_tools=3)
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "get_parallelizable_signals", return_value=par_sig), \
                patch.object(policies, "calculate_turn_tool_score", return_value=(0.0, 0)), \
                f2[0], f2[1], f2[2]:
            act2 = policies.sage_flow("midturn", **{**ctx, "state": state})
            self.assertEqual(act2["action"], "exit")

        # Third call: details change (new workstream) -> forces evaluation again
        par_sig3 = {
            "parallelizable": True,
            "categories": ["disjoint_files"],
            "details": ["3 disjoint directories: api, core, web"],
            "signal_text": "PARALLELIZABLE: Disjoint files (3 dirs)",
        }
        f3 = _frozen(latest_tools=3)
        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                patch.object(policies, "get_parallelizable_signals", return_value=par_sig3), \
                patch.object(policies, "evaluate_mid_turn_progress", return_value={"status": "on_track"}) as mock_eval3, \
                patch.object(policies, "classify_advice", return_value={"decision": "hold", "text": "ok", "seen": {}}), \
                f3[0], f3[1], f3[2]:
            act3 = policies.sage_flow("midturn", **{**ctx, "state": state})
            self.assertEqual(act3["action"], "healthy")
            self.assertTrue(mock_eval3.call_args.kwargs.get("is_forced"))


TestFinalAdvisorGate = TestFinalSageGate


if __name__ == "__main__":
    unittest.main()
