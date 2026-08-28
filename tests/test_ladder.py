"""Tests for sage.ladder — verification-depth escalation on pins and steers."""
import unittest

from sage.ladder import deepest_tier, next_rung_suffix
from sage.triage import classify_advice


def _pin(text="Build the export feature", complexity="complex_code"):
    return {
        "status": "watchout", "task_complexity": complexity,
        "category": "pinned_goal",
        "pinned_goal": f"{text}; proven by pytest tests/test_x.py green",
        "action": "Implement in src/export.py",
        "confidence": 0.9, "guidance": "Start with schema.",
    }


class TestLadderTiers(unittest.TestCase):
    def test_unit_claim_demands_integration(self):
        self.assertEqual(deepest_tier("proven by pytest"), "unit")
        self.assertIn("cross-component", next_rung_suffix("proven by pytest"))

    def test_static_claim_demands_unit_then_integration(self):
        self.assertEqual(deepest_tier("tsc --noEmit clean"), "static")
        self.assertIn("execute the actual code", next_rung_suffix("tsc --noEmit clean"))

    def test_full_depth_emits_nothing(self):
        self.assertEqual(next_rung_suffix("smoke test live run e2e complete"), "")

    def test_no_verification_named_starts_at_unit(self):
        self.assertEqual(deepest_tier(""), "none")
        self.assertIn("empirical proof", next_rung_suffix(""))


class TestLadderIntegration(unittest.TestCase):
    def _classify(self, ver, **kw):
        return classify_advice(ver, mode="midturn", anchor_emitted=False, **kw)

    def test_pinned_goal_in_deep_task_gets_ladder_suffix(self):
        res = self._classify(_pin())
        self.assertIn("DOD:", res["text"])
        self.assertIn("cross-component", res["text"])

    def test_simple_qa_is_ladder_exempt(self):
        ver = _pin(complexity="simple_qa")
        res = self._classify(ver)
        self.assertNotIn("DOD:", res["text"])

    def test_final_mode_exempt(self):
        res = classify_advice(_pin(), mode="final")
        self.assertNotIn("DOD:", res["text"])

    def test_dedup_key_stable_with_suffix(self):
        # Non-pinned steer: identical re-emission must dedup despite appended suffix
        # (pinned-goal emissions are deliberately dedup-exempt as anchors).
        ver = {"status": "watchout", "task_complexity": "complex_code",
               "category": "architectural_trap",
               "action": "Refactor module boundary",
               "guidance": "proven by pytest tests/test_boundary.py"}
        r1 = self._classify(ver)
        self.assertIn("DOD:", r1["text"])
        r2 = self._classify(ver, seen_advice=r1["seen"])
        self.assertEqual(r2["decision"], "hold_dedup")


if __name__ == "__main__":
    unittest.main()
