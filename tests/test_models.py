#!/usr/bin/env python3
"""
tests.test_models - Unit tests for dynamic model discovery, version sorting, and runtime cascade fallback.
"""

import unittest
from unittest.mock import MagicMock, patch

from sage.models import (
    DEFAULT_MODEL_FALLBACKS,
    _expand_alias,
    cache_working_model,
    get_available_models,
    get_cached_working_model,
    model_sort_key,
    parse_model_version,
    resolve_model_candidates,
)


class TestModels(unittest.TestCase):
    def setUp(self):
        cache_working_model(None)

    def tearDown(self):
        cache_working_model(None)

    def test_version_parsing_and_sorting(self):
        v40 = parse_model_version("Gemini 4.0 Flash (High)")
        v38 = parse_model_version("Gemini 3.8 Flash (High)")
        v37 = parse_model_version("Gemini 3.7 Flash (High)")
        v36 = parse_model_version("Gemini 3.6 Flash (High)")
        v35 = parse_model_version("Gemini 3.5 Flash (High)")
        v31_pro = parse_model_version("Gemini 3.1 Pro (High)")
        v_claude = parse_model_version("Claude Sonnet 4.6 (Thinking)")
        v_gpt = parse_model_version("GPT-OSS 120B (Medium)")

        self.assertEqual(v40[0], (4, 0))
        self.assertEqual(v38[0], (3, 8))
        self.assertEqual(v37[0], (3, 7))
        self.assertEqual(v36[0], (3, 6))
        self.assertEqual(v35[0], (3, 5))
        self.assertEqual(v31_pro[0], (3, 1))
        self.assertEqual(v_claude[0], (4, 6))
        self.assertEqual(v_gpt[0], (120, 0))

        # Check version precedence: 4.0 > 3.8 > 3.7 > 3.6 > 3.5
        self.assertGreater(model_sort_key("Gemini 4.0 Flash (High)"), model_sort_key("Gemini 3.8 Flash (High)"))
        self.assertGreater(model_sort_key("Gemini 3.8 Flash (High)"), model_sort_key("Gemini 3.7 Flash (High)"))
        self.assertGreater(model_sort_key("Gemini 3.7 Flash (High)"), model_sort_key("Gemini 3.6 Flash (High)"))
        self.assertGreater(model_sort_key("Gemini 3.6 Flash (High)"), model_sort_key("Gemini 3.5 Flash (High)"))

        unsorted_models = [
            "Gemini 3.5 Flash (High)",
            "Gemini 4.0 Flash (High)",
            "Gemini 3.7 Flash (High)",
            "Gemini 3.8 Flash (High)",
            "Gemini 3.6 Flash (High)",
        ]
        sorted_models = sorted(unsorted_models, key=model_sort_key, reverse=True)
        self.assertEqual(
            sorted_models,
            [
                "Gemini 4.0 Flash (High)",
                "Gemini 3.8 Flash (High)",
                "Gemini 3.7 Flash (High)",
                "Gemini 3.6 Flash (High)",
                "Gemini 3.5 Flash (High)",
            ],
        )

    def test_empty_or_invalid_model_version_parsing(self):
        self.assertEqual(parse_model_version(""), ((0, 0), 0, 0))
        self.assertEqual(parse_model_version(None), ((0, 0), 0, 0))

    def test_alias_resolution(self):
        mock_models = [
            "Gemini 4.0 Flash (High)",
            "Gemini 3.8 Flash (Medium)",
            "Gemini 3.7 Flash (Low)",
            "Gemini 3.1 Pro (High)",
        ]
        auto_high = _expand_alias("auto", mock_models, "high")
        self.assertIn("Gemini 4.0 Flash (High)", auto_high)
        self.assertEqual(auto_high[0], "Gemini 4.0 Flash (High)")

        latest_res = _expand_alias("latest", mock_models, "high")
        self.assertEqual(latest_res, auto_high)

        default_res = _expand_alias("default", mock_models, "high")
        self.assertEqual(default_res, auto_high)

        flash_med = _expand_alias("flash-medium", mock_models, "medium")
        self.assertEqual(flash_med[0], "Gemini 3.8 Flash (Medium)")

        flash_low = _expand_alias("flash-low", mock_models, "low")
        self.assertEqual(flash_low[0], "Gemini 3.7 Flash (Low)")

        pro_res = _expand_alias("pro", mock_models, "high")
        self.assertIn("Gemini 3.1 Pro (High)", pro_res)
        self.assertEqual(pro_res[0], "Gemini 3.1 Pro (High)")

    def test_comma_separated_fallback_lists(self):
        spec = "Gemini 4.0 Flash (High),Gemini 3.8 Flash (High),Gemini 3.7 Flash (High),auto"
        candidates = resolve_model_candidates(spec=spec, effort="high")
        self.assertGreaterEqual(len(candidates), 3)
        self.assertEqual(candidates[0], "Gemini 4.0 Flash (High)")
        self.assertEqual(candidates[1], "Gemini 3.8 Flash (High)")
        self.assertEqual(candidates[2], "Gemini 3.7 Flash (High)")

    def test_explicit_spec_keeps_cascade_fallbacks(self):
        candidates = resolve_model_candidates(spec="Gemini 3.7 Flash (Medium)", effort="high")
        self.assertEqual(candidates[0], "Gemini 3.7 Flash (Medium)")
        self.assertGreaterEqual(len(candidates), 3, "explicit spec must retain fallback chain")

    @patch("subprocess.run")
    def test_dynamic_discovery_from_mock_cli(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = (
            "Fetching available models...\n"
            "gemini-4.0-flash-high\tGemini 4.0 Flash (High)\n"
            "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
            "gemini-3.7-flash-high\tGemini 3.7 Flash (High)\n"
        )
        mock_run.return_value = mock_proc

        discovered = get_available_models(refresh=True)
        self.assertIn("Gemini 4.0 Flash (High)", discovered)
        self.assertIn("Gemini 3.8 Flash (High)", discovered)
        self.assertIn("Gemini 3.7 Flash (High)", discovered)

        candidates = resolve_model_candidates("auto", effort="high")
        self.assertEqual(candidates[0], "Gemini 4.0 Flash (High)")

    @patch("subprocess.run", side_effect=FileNotFoundError("agy not found"))
    def test_offline_fallback_when_cli_unavailable(self, _mock_run):
        models = get_available_models(refresh=True)
        self.assertEqual(models, list(DEFAULT_MODEL_FALLBACKS))

        candidates = resolve_model_candidates("auto")
        self.assertTrue(len(candidates) > 0)
        self.assertIn("Gemini 3.7 Flash (High)", candidates)

    def test_cache_working_model_prioritization(self):
        cache_working_model("Gemini 3.7 Flash (High)")
        self.assertEqual(get_cached_working_model(), "Gemini 3.7 Flash (High)")

        candidates = resolve_model_candidates("auto")
        self.assertEqual(candidates[0], "Gemini 3.7 Flash (High)")

    def test_cache_working_model_file_persistence(self):
        import os

        from sage.models import _WORKING_MODEL, _WORKING_MODEL_FILE
        cache_working_model("Gemini 3.6 Flash (High)")
        self.assertTrue(os.path.exists(_WORKING_MODEL_FILE))
        # Clear in-memory dictionary to test file recovery
        _WORKING_MODEL["model"] = None
        self.assertEqual(get_cached_working_model(), "Gemini 3.6 Flash (High)")

    def test_resolve_model_candidates_max_candidates_cap(self):
        candidates = resolve_model_candidates("auto", max_candidates=3)
        self.assertLessEqual(len(candidates), 3)


if __name__ == "__main__":
    unittest.main()
