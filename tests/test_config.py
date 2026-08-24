#!/usr/bin/env python3
import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

import advisor.config as config
from advisor.config import (
    DEFAULT_MODEL_FALLBACKS,
    _safe_float,
    _safe_int,
)


class TestConfig(unittest.TestCase):
    def test_safe_int_valid(self):
        with patch.dict(os.environ, {"TEST_INT_VAR": "42"}):
            self.assertEqual(_safe_int("TEST_INT_VAR", 10), 42)

    def test_safe_int_invalid_string_uses_default(self):
        with patch.dict(os.environ, {"TEST_INT_VAR": "not_an_int"}):
            self.assertEqual(_safe_int("TEST_INT_VAR", 15), 15)

    def test_safe_int_missing_uses_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_safe_int("NONEXISTENT_VAR", 99), 99)

    def test_safe_float_valid(self):
        with patch.dict(os.environ, {"TEST_FLOAT_VAR": "300.5"}):
            self.assertEqual(_safe_float("TEST_FLOAT_VAR", 600.0), 300.5)

    def test_safe_float_invalid_string_uses_default(self):
        with patch.dict(os.environ, {"TEST_FLOAT_VAR": "invalid_float"}):
            self.assertEqual(_safe_float("TEST_FLOAT_VAR", 600.0), 600.0)

    def test_safe_float_missing_uses_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_safe_float("NONEXISTENT_FLOAT_VAR", 123.4), 123.4)

    def test_env_file_overlay_fills_unset_keys_only(self):
        import importlib
        env_file = os.path.join(tempfile.mkdtemp(), "overlay.env")
        with open(env_file, "w") as f:
            f.write("AGY_STOP_AUDIT_MIN_TOOLS=42\n# comment\nHOME=/evil\nBAD_KEY=x\n")
        saved_env = {k: os.environ.get(k) for k in ("AGY_ADVISOR_ENV_FILE", "AGY_STOP_AUDIT_MIN_TOOLS")}
        try:
            os.environ["AGY_ADVISOR_ENV_FILE"] = env_file
            os.environ.pop("AGY_STOP_AUDIT_MIN_TOOLS", None)
            cfg = importlib.reload(config)
            self.assertEqual(cfg.TOOL_CALL_THRESHOLD, 42)          # file applied
            self.assertNotEqual(os.environ.get("HOME"), "/evil")  # non-AGY_ ignored
            os.environ["AGY_STOP_AUDIT_MIN_TOOLS"] = "7"     # real env beats file
            cfg = importlib.reload(config)
            self.assertEqual(cfg.TOOL_CALL_THRESHOLD, 7)
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            importlib.reload(config)

    def test_model_defaults_and_env_binding(self):
        self.assertIn("Gemini 3.7 Flash (High)", DEFAULT_MODEL_FALLBACKS)
        self.assertIn("Gemini 3.7 Flash (Medium)", DEFAULT_MODEL_FALLBACKS)

        with patch.dict(os.environ, {"AGY_ADVISOR_MODEL": "custom-chain", "AGY_ADVISOR_EFFORT": "medium"}):
            importlib.reload(config)
            self.assertEqual(config.REVIEWER_MODEL_SPEC, "custom-chain")
            self.assertEqual(config.REVIEWER_EFFORT, "medium")
            self.assertEqual(config.REVIEWER_MODEL, "custom-chain")

        # Reload back to clean state
        with patch.dict(os.environ, {}, clear=True):
            importlib.reload(config)
            self.assertEqual(config.REVIEWER_MODEL_SPEC, "Gemini 3.7 Flash (Medium)")
            self.assertEqual(config.REVIEWER_EFFORT, "high")


if __name__ == "__main__":
    unittest.main()
