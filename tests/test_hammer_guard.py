#!/usr/bin/env python3
"""
tests.test_hammer_guard - sage same-category steer suppression.

Regression (2026-08-26): three consecutive fake_verification steers hammered
the executor while it was producing tool evidence (direct `status: exited`
queries, merged PRD). Reworded guidance creates a fresh advice_key each time,
so key-based dedup never counted the repeats. The guard: same category + fresh
executor tools since the last steer -> suppress until that evidence lands.
"""

import unittest

from sage.policies import _hammer_suppressed


class TestHammerGuard(unittest.TestCase):
    def test_same_category_with_fresh_tools_is_suppressed(self):
        state = {"last_steer_category": "fake_verification", "last_steer_tools": 14}
        self.assertTrue(_hammer_suppressed(state, "fake_verification", 17))

    def test_same_category_without_new_tools_is_allowed(self):
        state = {"last_steer_category": "fake_verification", "last_steer_tools": 17}
        self.assertFalse(_hammer_suppressed(state, "fake_verification", 17))
        self.assertFalse(_hammer_suppressed(state, "fake_verification", 16))

    def test_different_category_is_allowed(self):
        state = {"last_steer_category": "loop_detection", "last_steer_tools": 3}
        self.assertFalse(_hammer_suppressed(state, "fake_verification", 9))

    def test_no_history_is_allowed(self):
        self.assertFalse(_hammer_suppressed({}, "fake_verification", 5))


if __name__ == "__main__":
    unittest.main()
