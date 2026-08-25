"""
tests.test_deferrals - Unit tests for banned deferral phrases and question dumping prevention.
"""
import unittest

from sage.sanitizer import (
    detect_deferral_in_text,
    detect_transcript_deferral,
    strip_code_blocks,
)
from sage.triage import classify_advice


class TestDeferrals(unittest.TestCase):
    def test_strip_code_blocks(self):
        text = "Here is text\n```python\n# out of scope in code\nprint('hello')\n```\nOutside text."
        stripped = strip_code_blocks(text)
        self.assertNotIn("# out of scope in code", stripped)
        self.assertIn("Outside text", stripped)

    def test_detect_english_deferral_phrases(self):
        cases = [
            ("This feature is out of scope for now.", "out of scope"),
            ("We can leave this as a future change.", "future change"),
            ("This is left for user judgment.", "left for user judgment"),
            ("It is good enough for now.", "good enough for now"),
            ("This defect is non-blocking.", "non-blocking"),
            ("Let's accept the gap in styling.", "accept the gap"),
            ("Would you like me to implement the remaining tests?", "Would you like me to"),
            ("Should we deploy this now?", "Should we"),
        ]
        for prompt, expected in cases:
            matches = detect_deferral_in_text(prompt)
            self.assertTrue(len(matches) > 0, f"Failed to detect in: {prompt}")
            self.assertTrue(any(expected.lower() in m.lower() for m in matches))

    def test_detect_vietnamese_deferral_and_question_dumping(self):
        cases = [
            ("còn xyz có muốn làm không anh?", "còn xyz có muốn làm không"),
            ("advise đã sync có muốn làm không em?", "advise đã sync có muốn làm không"),
            ("1 số lỗi ... không thuộc scope có muốn address không?", "không thuộc scope"),
            ("phần này để sau làm nhé.", "để sau làm"),
            ("tạm thời như vậy là ổn rồi.", "tạm thời như vậy"),
            ("anh có muốn triển khai tiếp phần này không?", "anh có muốn triển khai"),
        ]
        for prompt, expected in cases:
            matches = detect_deferral_in_text(prompt)
            self.assertTrue(len(matches) > 0, f"Failed to detect in: {prompt}")

    def test_clean_response_not_flagged(self):
        clean_text = "Implemented the requested feature and all 632 unit tests passed cleanly."
        self.assertEqual(detect_deferral_in_text(clean_text), [])

    def test_detect_transcript_deferral(self):
        steps = [
            {"type": "USER_INPUT", "content": "triển khai tính năng mới"},
            {"type": "PLANNER_RESPONSE", "content": "Em đã làm xong phần A, còn phần B anh có muốn làm không?"},
        ]
        res = detect_transcript_deferral(steps)
        self.assertTrue(res["matched"])
        self.assertTrue(len(res["phrases"]) > 0)

    def test_classify_advice_overrides_on_track_when_deferral_present(self):
        ver_res = {
            "status": "on_track",
            "healthy": True,
            "category": "general",
            "recap": "Everything looks good.",
        }
        deferral = {
            "matched": True,
            "snippet": "còn xyz có muốn làm không",
            "phrases": ["còn xyz có muốn làm không"],
        }
        classified = classify_advice(ver_res, deferral=deferral)
        self.assertEqual(classified["decision"], "watchout")
        self.assertEqual(classified["category"], "missing_deliverable")
        self.assertIn("còn xyz có muốn làm không", classified["text"])


if __name__ == "__main__":
    unittest.main()
