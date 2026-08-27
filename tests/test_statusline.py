#!/usr/bin/env python3
"""
tests.test_statusline - Unit tests for statusline formatting, context calculation, and quota rendering.
"""
import os
import unittest
from unittest.mock import patch

from statusline.statusline import (
    DEFAULT_EFFECTIVE_MAX_CTX,
    calculate_seconds_left,
    clean_model_name,
    format_countdown,
    format_tokens,
    get_effective_max_context,
    is_agent_active,
    render_statusline,
)


class TestStatusline(unittest.TestCase):
    def test_format_tokens(self):
        self.assertEqual(format_tokens(0), "0")
        self.assertEqual(format_tokens(500), "500")
        self.assertEqual(format_tokens(1000), "1k")
        self.assertEqual(format_tokens(1500), "2k")
        self.assertEqual(format_tokens(250000), "250k")
        self.assertEqual(format_tokens(1000000), "1M")
        self.assertEqual(format_tokens(1500000), "2M")

    def test_format_countdown(self):
        self.assertEqual(format_countdown(0), "")
        self.assertEqual(format_countdown(-10), "")
        self.assertEqual(format_countdown(59), "[1m]")
        self.assertEqual(format_countdown(120), "[2m]")
        self.assertEqual(format_countdown(3660), "[2h]")
        self.assertEqual(format_countdown(90000), "[2d]")

    def test_calculate_seconds_left_fallback(self):
        self.assertEqual(calculate_seconds_left(None, fallback_seconds=120), 120)
        self.assertEqual(calculate_seconds_left("invalid-date", fallback_seconds=300), 300)

    def test_clean_model_name(self):
        self.assertEqual(clean_model_name(None), "agy")
        self.assertEqual(clean_model_name("Gemini 3.7 Flash (High)"), "3.7 flash [h]")
        self.assertEqual(clean_model_name("gemini-3.1-pro (Low)"), "3.1-pro [l]")
        self.assertEqual(clean_model_name("Gemini 3.5 Flash (Medium)"), "3.5 flash [m]")
        self.assertEqual(clean_model_name("gemini-3.7-flash-high"), "3.7-flash [h]")
        self.assertEqual(clean_model_name("gemini-3.7-flash-medium"), "3.7-flash [m]")
        self.assertEqual(clean_model_name("gemini-3.7-flash-low"), "3.7-flash [l]")

    def test_is_agent_active(self):
        self.assertTrue(is_agent_active({"state": "running"}))
        self.assertTrue(is_agent_active({"status": "active"}))
        self.assertTrue(is_agent_active({"alive": True}))
        self.assertFalse(is_agent_active({"state": "completed"}))
        self.assertFalse(is_agent_active({"status": "done"}))
        self.assertFalse(is_agent_active({"exit_code": 1}))

    def test_get_effective_max_context(self):
        self.assertEqual(get_effective_max_context(), DEFAULT_EFFECTIVE_MAX_CTX)
        self.assertEqual(get_effective_max_context(), 250_000)

        with patch.dict(os.environ, {"AGY_MAX_CONTEXT_TOKENS": "400000"}):
            self.assertEqual(get_effective_max_context(), 400_000)

        with patch.dict(os.environ, {"AGY_MAX_CONTEXT_TOKENS": "invalid"}):
            self.assertEqual(get_effective_max_context(), 250_000)

    def test_render_statusline_context_window_ceiling_250k(self):
        data = {
            "model": "Gemini 3.7 Flash (High)",
            "context_window": {
                "total_input_tokens": 210597,
                "context_window_size": 1048576,
                "current_usage": {
                    "input_tokens": 4338,
                    "cache_read_input_tokens": 204994,
                    "cache_creation_input_tokens": 0,
                    "output_tokens": 11340,
                },
            },
            "quota": {
                "gemini-5h": {
                    "remaining_fraction": 0.75,
                    "reset_in_seconds": 3600,
                },
            },
            "terminal_width": 100,
        }

        output = render_statusline(data)
        # Should render 221k / 250k instead of 1.0M
        self.assertIn("221k/250k", output)
        self.assertNotIn("/1.0M", output)
        self.assertIn("3.7 flash [h]", output)
        self.assertIn("25%", output)

    def test_render_statusline_checkpoint_cp_badge(self):
        import re
        data = {
            "model": "Gemini 3.7 Flash (High)",
            "checkpoint_count": 3,
            "context_window": {"total_input_tokens": 50000},
        }
        output = render_statusline(data)
        plain = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", output)
        self.assertIn("ctx:50k/250k[3]", plain)
        self.assertNotIn("cp[3]", plain)

    def test_render_statusline_fallback(self):
        import re
        output = render_statusline({})
        plain = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", output)
        self.assertIn("0/250k", plain)
        self.assertIn("0%", plain)
        self.assertNotIn("sage", plain)
        self.assertNotIn("str[", plain)
        self.assertNotIn("rcp[", plain)

    def test_get_advisor_steer_badges_hold_and_fired(self):
        import json
        import re

        from statusline.statusline import get_advisor_steer_badges, safe_id

        conv_id = "test_conv_status_123"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"

        def clean(s):
            return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", s)

        try:
            # 1. Idle/Hold state -> hidden in stealth mode
            with open(state_file, "w") as f:
                json.dump({
                    "turn_key": "tk1",
                    "mid_turn_steers": 0,
                    "advisor_holds": 2,
                    "recap_count": 1,
                    "sage_status": "hold",
                }, f)

            badges = get_advisor_steer_badges({"conversation_id": conv_id})
            self.assertEqual(len(badges), 0)

            # 2. Injecting/Fired state -> ◐ sage:inject (coral)
            with open(state_file, "w") as f:
                json.dump({
                    "turn_key": "tk1",
                    "session_mid_turn_steers": 2,
                    "sage_status": "fired",
                    "recap_emitted": False,
                }, f)
            badges = get_advisor_steer_badges({"conversation_id": conv_id})
            self.assertEqual(len(badges), 1)
            self.assertEqual(clean(badges[0]), "◐ sage:inject")
            self.assertEqual(badges[0], "\033[38;2;255;127;80m◐ sage:inject\033[0m")

            # 3. Active Recap Injection -> ◐ sage:inject (coral)
            with open(state_file, "w") as f:
                json.dump({
                    "turn_key": "tk1",
                    "sage_status": "recap",
                    "recap_emitted": False,
                }, f)
            badges = get_advisor_steer_badges({"conversation_id": conv_id})
            self.assertEqual(len(badges), 1)
            self.assertEqual(clean(badges[0]), "◐ sage:inject")
            self.assertEqual(badges[0], "\033[38;2;255;127;80m◐ sage:inject\033[0m")

            # 4. Post-recap completed state -> hidden in stealth mode
            with open(state_file, "w") as f:
                json.dump({
                    "turn_key": "tk1",
                    "sage_status": "recap",
                    "recap_emitted": True,
                }, f)
            badges = get_advisor_steer_badges({"conversation_id": conv_id})
            self.assertEqual(len(badges), 0)

            # 5. Evaluating State -> ● sage:eval (bright blue) + error streak suffix
            with open(state_file, "w") as f:
                json.dump({
                    "turn_key": "tk1",
                    "sage_status": "evaluating",
                    "sage_error_streak": 3,
                }, f)
            badges = get_advisor_steer_badges({"conversation_id": conv_id})
            self.assertEqual(len(badges), 1)
            self.assertIn("\033[1;34m● sage:eval\033[0m", badges[0])
            self.assertIn("\033[31m/err[3]\033[0m", badges[0])
            self.assertEqual(clean(badges[0]), "● sage:eval/err[3]")
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)


if __name__ == "__main__":
    unittest.main()
