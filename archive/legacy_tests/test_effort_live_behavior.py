#!/usr/bin/env python3
"""
tests.test_effort_live_behavior - effort tier correctness as observed in the
2026-08-27 live run.

Findings locked here:
1. Routine unforced checkpoints must resolve a (Medium) candidate first
   (token efficiency) while forced/final calls keep (High).
2. The runner log line "Running mid-turn sage (... model=X)" reflects the
   SPEC (config REVIEWER_MODEL), not the re-tiered resolution — that is the
   pre-existing logging contract and must not regress.
3. Env override AGY_SAGE_ROUTINE_EFFORT=high restores all-High behavior.
"""
import json, os, tempfile, unittest
from unittest.mock import patch

from sage import sage as S
from sage.models import resolve_model_candidates, _retier_model


class TestRetierModel(unittest.TestCase):
    def test_high_to_medium(self):
        self.assertEqual(_retier_model("Gemini 3.7 Flash (High)", "medium"), "Gemini 3.7 Flash (Medium)")

    def test_no_suffix_untouched(self):
        self.assertEqual(_retier_model("gemini-custom", "low"), "gemini-custom")

    def test_invalid_effort_untouched(self):
        self.assertEqual(_retier_model("Gemini 3.7 Flash (High)", "ultra"), "Gemini 3.7 Flash (High)")


class TestLadderResolution(unittest.TestCase):
    def test_routine_medium_resolves_medium_first(self):
        cands = resolve_model_candidates(effort="medium")
        self.assertTrue(cands[0].endswith("(Medium)"), f"expected Medium first, got {cands[:2]}")

    def test_forced_high_resolves_high_first(self):
        cands = resolve_model_candidates(effort="high")
        self.assertTrue(cands[0].endswith("(High)"), f"expected High first, got {cands[:2]}")


class TestRoutineEffortEnv(unittest.TestCase):
    def test_default_medium(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGY_SAGE_ROUTINE_EFFORT", None)
            self.assertEqual(S._routine_effort(), "medium")

    def test_override_high(self):
        with patch.dict(os.environ, {"AGY_SAGE_ROUTINE_EFFORT": "high"}):
            self.assertEqual(S._routine_effort(), "high")

    def test_bad_value_falls_back_to_medium(self):
        with patch.dict(os.environ, {"AGY_SAGE_ROUTINE_EFFORT": "xhigh"}):
            self.assertEqual(S._routine_effort(), "medium")


if __name__ == "__main__":
    unittest.main()
