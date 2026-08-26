#!/usr/bin/env python3
"""
tests.test_effort_ladder - effort-tiered model selection (iteration 3).

agy encodes reasoning effort in the MODEL NAME ("... (High)"/"(Medium)") and
REJECTS a mismatched `--effort` CLI flag (verified: `agy --effort medium --model
"Gemini 3.7 Flash (High)"` -> "invalid model selection"). The token-efficiency
ladder therefore routes through model SELECTION: routine unforced mid-turn
checks resolve the "(Medium)" variant; forced/final/deferral calls keep High.
"""

import os
import unittest
from unittest.mock import patch

from sage.models import _retier_model, resolve_model_candidates
from sage.sage import _routine_effort


class TestRetierModel(unittest.TestCase):
    def test_high_spec_retiers_to_medium(self):
        self.assertEqual(_retier_model("Gemini 3.7 Flash (High)", "medium"),
                         "Gemini 3.7 Flash (Medium)")

    def test_medium_spec_retiers_to_high(self):
        self.assertEqual(_retier_model("Gemini 3.7 Flash (Medium)", "high"),
                         "Gemini 3.7 Flash (High)")

    def test_case_insensitive_and_whitespace_tolerant(self):
        self.assertEqual(_retier_model("Gemini 3.7 Flash (HIGH) ", "low"),
                         "Gemini 3.7 Flash (Low)")

    def test_invalid_effort_leaves_name(self):
        self.assertEqual(_retier_model("Gemini 3.7 Flash (High)", "max"), "Gemini 3.7 Flash (High)")
        self.assertEqual(_retier_model("Gemini 3.7 Flash (High)", None), "Gemini 3.7 Flash (High)")

    def test_alias_names_untouched(self):
        # A bare alias/spec with no tier suffix must pass through unchanged.
        self.assertEqual(_retier_model("auto", "medium"), "auto")


class TestResolutionLadder(unittest.TestCase):
    def setUp(self):
        for k in ("AGY_SAGE_MODEL", "AGY_ADVISOR_MODEL", "AGY_SAGE_EFFORT",
                  "AGY_ADVISOR_EFFORT", "AGY_STOP_AUDIT_MODEL"):
            os.environ.pop(k, None)

    def test_routine_request_resolves_medium_first(self):
        cands = resolve_model_candidates(effort="medium")
        self.assertEqual(cands[0], "Gemini 3.7 Flash (Medium)")
        self.assertIn("Gemini 3.7 Flash (High)", cands)  # escalation still reachable

    def test_forced_request_resolves_high_first(self):
        cands = resolve_model_candidates(effort="high")
        self.assertEqual(cands[0], "Gemini 3.7 Flash (High)")

    def test_no_effort_keeps_legacy_default(self):
        cands = resolve_model_candidates()
        self.assertEqual(cands[0], "Gemini 3.7 Flash (High)")


class TestRoutineEffortEnv(unittest.TestCase):
    def test_default_is_medium(self):
        os.environ.pop("AGY_SAGE_ROUTINE_EFFORT", None)
        self.assertEqual(_routine_effort(), "medium")

    def test_whitelist_fallback(self):
        os.environ["AGY_SAGE_ROUTINE_EFFORT"] = "bogus"
        try:
            self.assertEqual(_routine_effort(), "medium")
        finally:
            os.environ.pop("AGY_SAGE_ROUTINE_EFFORT", None)

    def test_valid_override_honored(self):
        os.environ["AGY_SAGE_ROUTINE_EFFORT"] = "LOW"
        try:
            self.assertEqual(_routine_effort(), "low")
        finally:
            os.environ.pop("AGY_SAGE_ROUTINE_EFFORT", None)


if __name__ == "__main__":
    unittest.main()
