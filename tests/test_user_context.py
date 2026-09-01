"""tests.test_user_context - Unit tests for substantive user context and compaction extraction."""
import unittest

from sage.user_context import (
    extract_substantive_user_context,
    is_trivial_acknowledgment,
)


class TestTrivialAcknowledgment(unittest.TestCase):
    def test_english_trivial_acks(self):
        acks = [
            "ok", "OK", "okay", "Ok.", "ok!", "k", "kk", "oke", "okie",
            "yes", "y", "yeah", "yep", "yup", "sure", "proceed",
            "continue", "go ahead", "approved", "lgtm", "looks good",
            "ship it", "push to remote", "done", "thanks", "thank you",
            "ok please", "yes please", "sure thanks",
        ]
        for ack in acks:
            with self.subTest(ack=ack):
                self.assertTrue(is_trivial_acknowledgment(ack), f"Expected '{ack}' to be trivial ack")

    def test_vietnamese_trivial_acks(self):
        acks = [
            "tiếp", "tiếp đi", "tiếp tục", "làm tiếp", "làm tiếp đi",
            "làm đi", "làm đi bạn", "chạy đi", "được rồi", "đc rồi",
            "oke r", "oke rồi", "ok rồi", "ok r", "xong", "cảm ơn",
            "đẩy lên", "push lên", "push đi", "commit đi",
        ]
        for ack in acks:
            with self.subTest(ack=ack):
                self.assertTrue(is_trivial_acknowledgment(ack), f"Expected '{ack}' to be trivial ack")

    def test_substantive_prompts_not_trivial(self):
        substantive = [
            "Implement feature X with REST API endpoint",
            "Fix bug where table partitions are missing in queries",
            "Add unit tests for auth module",
            "Refactor DatabaseConnection class in db.py",
            "Why is the verifier failing on line 45?",
            "Make the plan first, gather last user messages",
            "oke nhưng sửa thêm hàm validate_token nữa nhé",
            "tiếp tục với task số 2 trong requirements.md",
        ]
        for s in substantive:
            with self.subTest(prompt=s):
                self.assertFalse(is_trivial_acknowledgment(s), f"Expected '{s}' to be substantive")


class TestExtractSubstantiveUserContext(unittest.TestCase):
    def test_single_substantive_prompt(self):
        steps = [
            {"type": "USER_INPUT", "content": "Add unit tests for payment module", "created_at": "2026-09-01T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Working on tests", "tool_calls": []},
        ]
        ctx = extract_substantive_user_context(steps)
        self.assertEqual(ctx["true_user_prompt"], "Add unit tests for payment module")
        self.assertEqual(ctx["latest_user_prompt"], "Add unit tests for payment module")
        self.assertEqual(ctx["primary_goal"], "Add unit tests for payment module")
        self.assertFalse(ctx["is_latest_trivial"])
        self.assertFalse(ctx["has_compaction"])
        self.assertEqual(ctx["user_turn_count"], 1)

    def test_substantive_prompt_followed_by_short_ack(self):
        steps = [
            {"type": "USER_INPUT", "content": "Build complete REST API for user registration and JWT login", "created_at": "2026-09-01T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Created plan artifact", "tool_calls": []},
            {"type": "USER_INPUT", "content": "ok proceed", "created_at": "2026-09-01T10:05:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Implemented endpoints and tests", "tool_calls": []},
        ]
        ctx = extract_substantive_user_context(steps)
        self.assertTrue(ctx["is_latest_trivial"])
        self.assertEqual(ctx["primary_goal"], "Build complete REST API for user registration and JWT login")
        self.assertEqual(ctx["latest_user_prompt"], "ok proceed")
        self.assertIn("[PRIMARY USER GOAL]:", ctx["true_user_prompt"])
        self.assertIn("Build complete REST API for user registration and JWT login", ctx["true_user_prompt"])
        self.assertIn("[FOLLOW-UP INSTRUCTIONS & REFINEMENTS]:", ctx["true_user_prompt"])
        self.assertIn("ok proceed", ctx["true_user_prompt"])

    def test_multi_turn_with_substantive_followups_and_vietnamese_ack(self):
        steps = [
            {"type": "USER_INPUT", "content": "Tối ưu hóa database query cho bảng orders", "created_at": "2026-09-01T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Added indexes", "tool_calls": []},
            {"type": "USER_INPUT", "content": "Thêm cache Redis cho endpoint getOrders", "created_at": "2026-09-01T10:05:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Added Redis cache", "tool_calls": []},
            {"type": "USER_INPUT", "content": "tiếp đi", "created_at": "2026-09-01T10:10:00Z"},
        ]
        ctx = extract_substantive_user_context(steps)
        self.assertTrue(ctx["is_latest_trivial"])
        self.assertEqual(ctx["primary_goal"], "Thêm cache Redis cho endpoint getOrders")
        self.assertIn("[PRIMARY USER GOAL]:", ctx["true_user_prompt"])
        self.assertIn("Thêm cache Redis cho endpoint getOrders", ctx["true_user_prompt"])
        self.assertIn("tiếp đi", ctx["true_user_prompt"])

    def test_compaction_summary_handling(self):
        steps = [
            {
                "type": "CHECKPOINT",
                "summary": "<summary>User requested building a microservice with auth, database migrations, and Kafka producer. All migrations completed.</summary>",
                "created_at": "2026-09-01T09:00:00Z",
            },
            {"type": "PLANNER_RESPONSE", "content": "Previous state loaded", "tool_calls": []},
            {"type": "USER_INPUT", "content": "làm tiếp", "created_at": "2026-09-01T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Finished Kafka producer tests", "tool_calls": []},
        ]
        ctx = extract_substantive_user_context(steps)
        self.assertTrue(ctx["has_compaction"])
        self.assertIn("building a microservice with auth", ctx["compaction_summary"])
        self.assertIn("[COMPACTED CONVERSATION SUMMARY]:", ctx["true_user_prompt"])
        self.assertIn("làm tiếp", ctx["true_user_prompt"])

    def test_compaction_with_fresh_substantive_request(self):
        steps = [
            {
                "type": "SUMMARY",
                "summary_text": "Completed refactoring of logging system.",
                "created_at": "2026-09-01T09:00:00Z",
            },
            {"type": "USER_INPUT", "content": "Now add metrics collection with Prometheus counters", "created_at": "2026-09-01T10:00:00Z"},
        ]
        ctx = extract_substantive_user_context(steps)
        self.assertTrue(ctx["has_compaction"])
        self.assertIn("[COMPACTED CONVERSATION SUMMARY]:", ctx["true_user_prompt"])
        self.assertIn("Completed refactoring of logging system.", ctx["true_user_prompt"])
        self.assertIn("[ACTIVE USER REQUEST]:", ctx["true_user_prompt"])
        self.assertIn("Now add metrics collection with Prometheus counters", ctx["true_user_prompt"])

    def test_sliding_window_multiple_substantive_requests(self):
        steps = [
            {"type": "USER_INPUT", "content": "Request 1: Setup database", "created_at": "2026-09-01T10:01:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Done 1"},
            {"type": "USER_INPUT", "content": "Request 2: Add models", "created_at": "2026-09-01T10:02:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Done 2"},
            {"type": "USER_INPUT", "content": "Request 3: Add controllers and routes", "created_at": "2026-09-01T10:03:00Z"},
        ]
        ctx = extract_substantive_user_context(steps)
        self.assertFalse(ctx["is_latest_trivial"])
        self.assertIn("SESSION HISTORY:", ctx["true_user_prompt"])
        self.assertIn("Prior request 1: Request 1: Setup database", ctx["true_user_prompt"])
        self.assertIn("Prior request 2: Request 2: Add models", ctx["true_user_prompt"])
        self.assertIn("[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\nRequest 3: Add controllers and routes", ctx["true_user_prompt"])


if __name__ == "__main__":
    unittest.main()
