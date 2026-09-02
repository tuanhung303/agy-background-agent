"""
tests.test_deferrals - Unit tests for banned deferral phrases and question dumping prevention.
"""
import unittest

from sage.sanitizer import (
    detect_deferral_in_text,
    detect_transcript_deferral,
    detect_user_approval,
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

    def test_detect_additional_deferrals_and_question_dumping(self):
        cases = [
            ("would you like me to do xyz next?", "would you like me to"),
            ("do you want me to sync the advice?", "do you want me to"),
            ("some errors are out of scope, should we address them?", "out of scope"),
            ("we will revisit this part later.", "revisit"),
            ("good enough for now.", "good enough for now"),
            ("would you prefer to implement this part next?", "would you prefer"),
        ]
        for prompt, expected in cases:
            matches = detect_deferral_in_text(prompt)
            self.assertTrue(len(matches) > 0, f"Failed to detect in: {prompt}")

    def test_clean_response_not_flagged(self):
        clean_text = "Implemented the requested feature and all 632 unit tests passed cleanly."
        self.assertEqual(detect_deferral_in_text(clean_text), [])

    def test_detect_transcript_deferral(self):
        steps = [
            {"type": "USER_INPUT", "content": "implement new feature"},
            {"type": "PLANNER_RESPONSE", "content": "I finished part A, would you like me to do part B as well?"},
        ]
        res = detect_transcript_deferral(steps)
        self.assertTrue(res["matched"])
        self.assertTrue(len(res["phrases"]) > 0)

    def test_permission_seeking_endings_are_question_dumping(self):
        # Regression (2026-08-26): "tell me if you want me to incorporate these
        # revisions next" slipped past the gate and the sage recapped healthy
        # while critical/major PRD fixes were still unapplied.
        cases = [
            "you can review the working prd at kb.md. tell me if you want me to incorporate these revisions into the specification next.",
            "the draft is ready. do you want me to apply it?",
            "all findings are listed above; let me know if you want me to fix them.",
        ]
        for text in cases:
            res = detect_transcript_deferral([{"type": "PLANNER_RESPONSE", "content": text}])
            self.assertTrue(res["matched"], f"deferral missed: {text[:60]}")
            self.assertEqual(res["category"], "question_dumping")

    def test_informational_tell_me_is_not_flagged(self):
        clean = [
            "tell me about the storage hierarchy in the PRD.",
            "incorporated all revisions into the specification next morning.",
        ]
        for text in clean:
            res = detect_transcript_deferral([{"type": "PLANNER_RESPONSE", "content": text}])
            self.assertFalse(res["matched"], f"false positive: {text[:60]}")

    def test_classify_advice_overrides_on_track_when_deferral_present(self):
        ver_res = {
            "status": "on_track",
            "healthy": True,
            "category": "general",
            "recap": "Everything looks good.",
        }
        deferral = {
            "matched": True,
            "snippet": "would you like me to do xyz as well",
            "phrases": ["would you like me to do xyz as well"],
        }
        classified = classify_advice(ver_res, deferral=deferral)
        self.assertEqual(classified["decision"], "watchout")
        self.assertEqual(classified["category"], "missing_proof")
        self.assertIn("would you like me to do xyz as well", classified["text"])


    def test_lexical_variants_normalization(self):
        cases = [
            "This is out-of-scope for now.",
            "This is out  of  scope.",
            "This is out\u200bof scope.",
            "This error is out-of-scope.",
        ]
        for text in cases:
            matches = detect_deferral_in_text(text)
            self.assertTrue(len(matches) > 0, f"Failed for lexical variant: {text}")

    def test_washout_avoidance_turn_wide_scan(self):
        steps = [
            {"type": "USER_INPUT", "content": "implement feature"},
            {"type": "PLANNER_RESPONSE", "content": "I finished part 1, would you like me to do part 2?"},
            {"type": "GENERIC", "content": "Tool output"},
            {"type": "PLANNER_RESPONSE", "content": "Done."},
        ]
        res = detect_transcript_deferral(steps)
        self.assertTrue(res["matched"], "Failed to catch deferral in earlier response of the same turn")

    def test_delegated_command_extraction(self):
        steps = [
            {"type": "USER_INPUT", "content": "run tests"},
            {"type": "PLANNER_RESPONSE", "content": "Done, please run command: `pytest tests/test_deferrals.py` to verify."},
        ]
        res = detect_transcript_deferral(steps)
        self.assertTrue(res["matched"])
        self.assertEqual(res["delegated_cmd"], "pytest tests/test_deferrals.py")
        classified = classify_advice({"status": "on_track", "healthy": True}, deferral=res)
        self.assertEqual(classified["decision"], "watchout")
        self.assertIn("pytest tests/test_deferrals.py", classified["text"])

    def test_tail_todo_extraction(self):
        steps = [
            {"type": "USER_INPUT", "content": "complete task"},
            {"type": "PLANNER_RESPONSE", "content": "Completed all main tasks.\n\n## Remaining Work\n- Check production logs"},
        ]
        res = detect_transcript_deferral(steps)
        self.assertTrue(res["matched"])
        self.assertEqual(res["tail_todo"], "## Remaining Work")
        classified = classify_advice({"status": "on_track", "healthy": True}, deferral=res)
        self.assertEqual(classified["decision"], "watchout")
        self.assertIn("execute remaining work directly", classified["text"])

    def test_dedup_does_not_suppress_active_deferral_at_final_gate(self):
        deferral = {
            "matched": True,
            "snippet": "would you like me to do xyz as well",
            "phrases": ["would you like me to do xyz as well"],
        }
        # First stop emission
        res1 = classify_advice({"status": "on_track", "healthy": True}, seen_advice={}, deferral=deferral)
        self.assertEqual(res1["decision"], "watchout")
        # Second stop emission with same key
        res2 = classify_advice({"status": "on_track", "healthy": True}, seen_advice=res1["seen"], deferral=deferral)
        # Must still emit watchout and NOT hold_dedup on round 2
        self.assertEqual(res2["decision"], "watchout")


class TestUserApproval(unittest.TestCase):
    """Post-approval deferral escalation: user said 'go ahead', agent must not re-ask."""

    def test_detect_english_approval(self):
        cases = [
            "go ahead",
            "Go ahead and implement it",
            "yes",
            "OK",
            "approved, proceed",
            "keep going",
            "sure, do it",
        ]
        for prompt in cases:
            res = detect_user_approval(prompt)
            self.assertTrue(res["approved"], f"approval missed: {prompt!r}")

    def test_detect_affirmative_approval(self):
        cases = [
            "please implement it",
            "just do it",
            "feel free to proceed",
            "confirmed",
            "continue",
            "yeah, go ahead",
        ]
        for prompt in cases:
            res = detect_user_approval(prompt)
            self.assertTrue(res["approved"], f"approval missed: {prompt!r}")

    def test_questions_and_conditionals_are_not_approval(self):
        cases = [
            "should we retrain the model?",
            "do you want to train the model?",
            "if you want, we can start with the GR client",
            "if needed, we can do it later",
            "do you want me to go ahead?",  # question-shaped even with 'go ahead'
        ]
        for prompt in cases:
            res = detect_user_approval(prompt)
            self.assertFalse(res["approved"], f"false approval: {prompt!r}")

    def test_no_approval_in_empty_prompt(self):
        self.assertFalse(detect_user_approval("")["approved"])
        self.assertFalse(detect_user_approval(None)["approved"])


if __name__ == "__main__":
    unittest.main()
