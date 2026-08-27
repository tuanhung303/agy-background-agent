"""Tests for sage.receipts — interpretation receipt enforcement on NEW goal pins."""
import unittest

from sage.receipts import enforce_interpretation_receipt


def _pin(**over):
    res = {"category": "pinned_goal", "status": "watchout",
           "pinned_goal": "Implement mutation testing harness", "anchor_goal": "x"}
    res.update(over)
    return res


class TestInterpretationReceipt(unittest.TestCase):
    def test_new_pin_without_receipt_demoted_to_confused_goal(self):
        res = enforce_interpretation_receipt(_pin(), {})
        self.assertEqual(res["category"], "confused_goal")
        self.assertEqual(res["status"], "watchout")
        self.assertNotIn("pinned_goal", res)
        self.assertIn("interpretation receipt", res["guidance"])

    def test_new_pin_with_valid_receipt_keeps_pin_and_stores_it(self):
        interp = "chosen_reading: real benchmark runs; proxy_rejected: mock scenario JSONs are replay fixtures"
        res = {"category": "pinned_goal", "pinned_goal": "G"}
        enforce_interpretation_receipt(res, {"interpretation": interp})
        self.assertEqual(res["category"], "pinned_goal")
        self.assertEqual(res["pinned_goal"], "G")
        self.assertEqual(res["interpretation"], interp)

    def test_na_placeholder_treated_as_missing(self):
        pass

    def test_too_short_receipt_counts_as_missing(self):
        res = enforce_interpretation_receipt(_pin(), {"interpretation": "n/a"})
        self.assertEqual(res["category"], "confused_goal")

    def test_non_pin_category_carrying_pinned_context_is_untouched(self):
        res = {"category": "scope_drift", "status": "watchout", "pinned_goal": "Original goal"}
        enforce_interpretation_receipt(res, {})
        self.assertEqual(res["category"], "scope_drift")
        self.assertEqual(res["pinned_goal"], "Original goal")

    def test_repin_allowed_when_goal_already_pinned(self):
        res = _pin()
        enforce_interpretation_receipt(res, {}, goal_already_pinned=True)
        self.assertEqual(res["category"], "pinned_goal")


if __name__ == "__main__":
    unittest.main()
