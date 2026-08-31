"""
Guards for the ask-the-user gating added after koota r10.

r10 (2026-08-31, hermetic base, Gemini 3.7 Medium): sage-OFF scored reward 1.0 in
132 turns; sage-ON scored 0 in 188 turns, one f2p test short. The journal showed
sage entering grill-me, rejecting the recap, and directing the agent to
`ask_question` the user — in a headless run with no user attached. Zero audit
coverage resulted: no delegation ordered, no blind review leg, and sage cannot
execute anything itself.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from sage.config import ASK_USER_MAX_EMISSIONS
from sage.events import NONINTERACTIVE_NOTE
from sage.interactivity import can_ask_user, in_print_mode
from sage.triage import classify_advice, compute_advice_key


class TestInteractivitySignal(unittest.TestCase):
    def test_explicit_env_wins_both_ways(self):
        with patch.dict(os.environ, {"SAGE_INTERACTIVE": "0"}):
            self.assertFalse(can_ask_user())
        with patch.dict(os.environ, {"SAGE_INTERACTIVE": "1"}):
            self.assertTrue(can_ask_user())
        for falsey in ("false", "no", "FALSE"):
            with patch.dict(os.environ, {"SAGE_INTERACTIVE": falsey}):
                self.assertFalse(can_ask_user(), falsey)

    def test_defaults_to_askable_when_environment_is_unrecognised(self):
        """Wrongly silencing a question is worse than wrongly asking one."""
        env = {k: v for k, v in os.environ.items() if k != "SAGE_INTERACTIVE"}
        with patch.dict(os.environ, env, clear=True), \
             patch("sage.interactivity.in_print_mode", return_value=False):
            self.assertTrue(can_ask_user())

    def test_print_mode_ancestor_blocks_asking(self):
        env = {k: v for k, v in os.environ.items() if k != "SAGE_INTERACTIVE"}
        with patch.dict(os.environ, env, clear=True), \
             patch("sage.interactivity.in_print_mode", return_value=True):
            self.assertFalse(can_ask_user())

    def test_print_mode_probe_never_raises(self):
        """Best-effort: a broken ps must degrade to 'interactive', not explode."""
        with patch("sage.interactivity.subprocess.run", side_effect=OSError("boom")):
            sage_iv = __import__("sage.interactivity", fromlist=["_PRINT_MODE_CACHE"])
            sage_iv._PRINT_MODE_CACHE.clear()
            self.assertFalse(in_print_mode())
            sage_iv._PRINT_MODE_CACHE.clear()


class TestAskUserCategoriesAreBounded(unittest.TestCase):
    """grill_me / confused_goal skip the single-shot rule but must not be infinite."""

    def _fire_count(self, category):
        raw = {"status": "watchout", "category": category, "action": "interview the user",
               "guidance": "unconfirmed design fork", "confidence": 0.95}
        key = compute_advice_key(category, "interview the user", "unconfirmed design fork")
        fires = 0
        for count in range(0, 20):
            if classify_advice(raw, seen_advice={key: count})["decision"] == "hold_dedup":
                break
            fires += 1
        return fires

    def test_grill_me_is_capped(self):
        n = self._fire_count("grill_me")
        self.assertEqual(n, ASK_USER_MAX_EMISSIONS)
        self.assertLess(n, 20, "grill_me fired without bound — it ate a whole run in r10")

    def test_confused_goal_is_capped(self):
        self.assertEqual(self._fire_count("confused_goal"), ASK_USER_MAX_EMISSIONS)

    def test_still_allows_more_than_one_ask(self):
        """An unanswered question is worth one repeat; capping at 1 would regress."""
        self.assertGreaterEqual(ASK_USER_MAX_EMISSIONS, 2)


class TestNoninteractiveSignalReachesTheSage(unittest.TestCase):
    def _transcript(self, td):
        p = os.path.join(td, "tr.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT",
                                "content": "implement the feature"}) + "\n")
            f.write(json.dumps({"type": "PLANNER_RESPONSE", "content": "working",
                                "tool_calls": [{"name": "write_to_file",
                                                "args": {"TargetFile": "a.py"}}]}) + "\n")
        return p

    def test_signal_carries_noninteractive_tag_when_nobody_can_answer(self):
        from sage import policies
        captured = {}

        def fake_eval(*a, **kw):
            captured["signals"] = kw.get("signals") or ""
            return {"status": "on_track"}

        with tempfile.TemporaryDirectory() as td:
            tr = self._transcript(td)
            state = {"mid_turn_steers": 0, "sage_error_streak": 0, "last_verified_tools": 0}
            with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                 patch.object(policies, "can_ask_user", return_value=False), \
                 patch.object(policies, "evaluate_mid_turn_progress", side_effect=fake_eval), \
                 patch.object(policies, "has_new_user_activity", return_value=False), \
                 patch.object(policies, "classify_advice",
                              return_value={"decision": "hold", "text": "ok", "seen": {}}), \
                 patch.object(policies, "extract_session_and_turn_data",
                              return_value=(None,) * 3 + (5,) + (None,) * 3 + (2,)), \
                 patch.object(policies, "is_post_invocation_completion_candidate", return_value=False):
                policies.sage_flow(
                    "midturn", conv_id="c1", transcript_path=tr, clean_prompt="implement",
                    initial_line_count=2, total_tool_calls=5, turn_tool_names=["write_to_file"],
                    user_prompt="implement", agent_steps=[], git_diff="Changed lines: 10",
                    state=state, forced=True)

        self.assertIn("[EVT·noninteractive]", captured.get("signals", ""))
        self.assertIn("Never emit ask_question", captured["signals"])

    def test_no_tag_when_a_user_is_attached(self):
        from sage import policies
        captured = {}

        def fake_eval(*a, **kw):
            captured["signals"] = kw.get("signals") or ""
            return {"status": "on_track"}

        with tempfile.TemporaryDirectory() as td:
            tr = self._transcript(td)
            state = {"mid_turn_steers": 0, "sage_error_streak": 0, "last_verified_tools": 0}
            with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
                 patch.object(policies, "can_ask_user", return_value=True), \
                 patch.object(policies, "evaluate_mid_turn_progress", side_effect=fake_eval), \
                 patch.object(policies, "has_new_user_activity", return_value=False), \
                 patch.object(policies, "classify_advice",
                              return_value={"decision": "hold", "text": "ok", "seen": {}}), \
                 patch.object(policies, "extract_session_and_turn_data",
                              return_value=(None,) * 3 + (5,) + (None,) * 3 + (2,)), \
                 patch.object(policies, "is_post_invocation_completion_candidate", return_value=False):
                policies.sage_flow(
                    "midturn", conv_id="c1", transcript_path=tr, clean_prompt="implement",
                    initial_line_count=2, total_tool_calls=5, turn_tool_names=["write_to_file"],
                    user_prompt="implement", agent_steps=[], git_diff="Changed lines: 10",
                    state=state, forced=True)

        self.assertNotIn("[EVT·noninteractive]", captured.get("signals", ""))


class TestPlanDirectiveGrillsAgentFirst(unittest.TestCase):
    def test_plan_directive_no_longer_orders_an_unconditional_user_interview(self):
        from sage.events import PLAN_FINAL_STOP_DIRECTIVE
        self.assertIn("Grill the AGENT first", PLAN_FINAL_STOP_DIRECTIVE)
        self.assertIn("noninteractive", PLAN_FINAL_STOP_DIRECTIVE)
        # ask_question must now be conditional, not the standing instruction.
        self.assertIn("ONLY for a genuine product decision", PLAN_FINAL_STOP_DIRECTIVE)

    def test_noninteractive_note_is_self_explanatory(self):
        self.assertIn("[EVT·noninteractive]", NONINTERACTIVE_NOTE)
        self.assertIn("read-only tools", NONINTERACTIVE_NOTE)

    def test_prompt_documents_grill_agent_then_user(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        content = open(os.path.join(root, "sage", "sage_prompt.md"), encoding="utf-8").read()
        self.assertIn("Grill the AGENT first, not the user", content)
        self.assertIn("[EVT·noninteractive]", content)
        # The trigger is deliberately WIDE now (synonyms), because the action is
        # self-directed discovery rather than an interrogation of the user.
        self.assertIn("planning, designing, proposing an approach, or any synonym", content)


if __name__ == "__main__":
    unittest.main()
