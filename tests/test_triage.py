import unittest

from sage.triage import _parse_confidence, classify_advice, compute_advice_key


class TestTriage(unittest.TestCase):
    def test_parse_confidence(self):
        self.assertAlmostEqual(_parse_confidence(0.85), 0.85)
        self.assertAlmostEqual(_parse_confidence("0.90"), 0.90)
        self.assertAlmostEqual(_parse_confidence("85%"), 0.85)
        self.assertAlmostEqual(_parse_confidence(95), 0.95)
        self.assertIsNone(_parse_confidence("invalid"))
        self.assertIsNone(_parse_confidence(None))

    def test_parse_confidence_boundary_and_corner_cases(self):
        # Float boundaries
        self.assertAlmostEqual(_parse_confidence(0.0), 0.0)
        self.assertAlmostEqual(_parse_confidence(1.0), 1.0)
        self.assertAlmostEqual(_parse_confidence(0.70), 0.70)
        self.assertAlmostEqual(_parse_confidence(0.699), 0.699)
        self.assertAlmostEqual(_parse_confidence(0.85), 0.85)
        self.assertAlmostEqual(_parse_confidence(0.849), 0.849)

        # Percentage strings and integers
        self.assertAlmostEqual(_parse_confidence("0%"), 0.0)
        self.assertAlmostEqual(_parse_confidence("100%"), 1.0)
        self.assertAlmostEqual(_parse_confidence("100.0%"), 1.0)
        self.assertAlmostEqual(_parse_confidence("50%"), 0.5)
        self.assertAlmostEqual(_parse_confidence(50), 0.5)
        self.assertAlmostEqual(_parse_confidence(100), 1.0)
        self.assertAlmostEqual(_parse_confidence(100.0), 1.0)

        # Out-of-bound clamping
        self.assertAlmostEqual(_parse_confidence(-0.5), 0.0)
        self.assertAlmostEqual(_parse_confidence("-10%"), 0.0)
        self.assertAlmostEqual(_parse_confidence(150), 1.0)
        self.assertAlmostEqual(_parse_confidence("150%"), 1.0)

        # Boolean protection (isinstance(bool, int) is True in Python, must return None)
        self.assertIsNone(_parse_confidence(True))
        self.assertIsNone(_parse_confidence(False))

        # Invalid types
        self.assertIsNone(_parse_confidence(""))
        self.assertIsNone(_parse_confidence("uncalibrated"))
        self.assertIsNone(_parse_confidence([]))
        self.assertIsNone(_parse_confidence({}))

    def test_compute_advice_key_punctuation_invariance(self):
        k1 = compute_advice_key("loop_detection", "run pytest -x", "tests failing")
        k2 = compute_advice_key("loop_detection", "run `pytest -x`", "tests failing!")
        self.assertEqual(k1, k2)

    def test_compute_advice_key_normalization(self):
        # Case, whitespace, and special character normalization
        k1 = compute_advice_key("LOOP_DETECTION", "  run   `pytest -v`  ", "failed 3x!!!")
        k2 = compute_advice_key("loop_detection", "run pytest -v", "failed 3x")
        self.assertEqual(k1, k2)
        self.assertEqual(len(k1), 12)

        # Empty/None fallback generates valid 12-char hex
        k_empty = compute_advice_key(None, None, None)
        self.assertEqual(len(k_empty), 12)

    def test_classify_advice_on_track(self):
        ver_res = {"status": "on_track", "healthy": True}
        res = classify_advice(ver_res)
        self.assertEqual(res["decision"], "hold")
        self.assertEqual(res["status"], "on_track")

    def test_classify_advice_confidence_demote(self):
        # Low confidence off_track demoted to watchout
        ver_res = {"status": "off_track", "healthy": False, "guidance": "fix loop", "confidence": "0.5"}
        res = classify_advice(ver_res, steer_min_conf=0.7)
        self.assertEqual(res["decision"], "watchout")
        self.assertEqual(res["status"], "watchout")

    def test_classify_advice_confidence_threshold_exact_boundaries(self):
        # Exact boundary 0.70: NOT demoted, stays steer
        ver_steer_boundary = {"status": "off_track", "category": "loop_detection", "action": "inspect schema", "confidence": 0.70}
        res_steer_70 = classify_advice(ver_steer_boundary, steer_min_conf=0.70)
        self.assertEqual(res_steer_70["decision"], "steer")
        self.assertEqual(res_steer_70["status"], "off_track")

        # Just below boundary 0.6999: demoted to watchout
        ver_steer_below = {"status": "off_track", "category": "loop_detection", "action": "inspect schema", "confidence": 0.6999}
        res_steer_below = classify_advice(ver_steer_below, steer_min_conf=0.70)
        self.assertEqual(res_steer_below["decision"], "watchout")
        self.assertEqual(res_steer_below["status"], "watchout")

        # Irreversible risk escalation boundary 0.85: promoted to steer
        ver_risk_85 = {"status": "watchout", "category": "irreversible_risk", "action": "git status", "confidence": 0.85}
        res_risk_85 = classify_advice(ver_risk_85, escalate_min_conf=0.85)
        self.assertEqual(res_risk_85["decision"], "steer")
        self.assertEqual(res_risk_85["status"], "off_track")

        # Irreversible risk below boundary 0.8499: stays watchout
        ver_risk_below = {"status": "watchout", "category": "irreversible_risk", "action": "git status", "confidence": 0.8499}
        res_risk_below = classify_advice(ver_risk_below, escalate_min_conf=0.85)
        self.assertEqual(res_risk_below["decision"], "watchout")
        self.assertEqual(res_risk_below["status"], "watchout")

        # Irreversible risk without action: does not escalate even with conf 0.95
        ver_risk_no_action = {"status": "watchout", "category": "irreversible_risk", "action": "", "guidance": "be careful", "confidence": 0.95}
        res_risk_no_action = classify_advice(ver_risk_no_action, escalate_min_conf=0.85)
        self.assertEqual(res_risk_no_action["decision"], "watchout")

    def test_classify_advice_escalate_irreversible_risk(self):
        # High confidence irreversible risk escalated to off_track steer
        ver_res = {"status": "watchout", "category": "irreversible_risk", "action": "stop rm", "guidance": "check git", "confidence": 0.9}
        res = classify_advice(ver_res, escalate_min_conf=0.85)
        self.assertEqual(res["decision"], "steer")
        self.assertIn("[STEER·irreversible_risk", res["text"])

    def test_classify_advice_never_emits_destructive_metadata(self):
        for payload in (
            {"status": "off_track", "blind_spots": ["Run rm -rf /tmp/project"], "confidence": 0.99},
            {"status": "watchout", "watchouts": ["DROP TABLE production"], "confidence": 0.99},
            {"status": "off_track", "action": "inspect", "evidence": "git reset --hard HEAD~1", "confidence": 0.99},
        ):
            with self.subTest(payload=payload):
                text = classify_advice(payload)["text"]
                self.assertIn("[Destructive command suppressed]", text)
                self.assertNotIn("rm -rf", text)
                self.assertNotIn("DROP TABLE", text)
                self.assertNotIn("reset --hard", text)

    def test_category_tag_formatting_all_nine_categories(self):
        # 1. loop_detection (steer)
        r = classify_advice({"status": "off_track", "category": "loop_detection", "action": "inspect schema", "confidence": 0.95})
        self.assertIn("[STEER·loop_detection]", r["text"])

        # 2. irreversible_risk (watchout and steer)
        r_w = classify_advice({"status": "watchout", "category": "irreversible_risk", "action": "git stash", "confidence": 0.80})
        self.assertIn("[WATCH·irreversible_risk]", r_w["text"])
        r_s = classify_advice({"status": "watchout", "category": "irreversible_risk", "action": "git stash", "confidence": 0.90})
        self.assertIn("[STEER·irreversible_risk]", r_s["text"])

        # 3. parallelize_subagent and parallelize (watchout)
        r_p1 = classify_advice({"status": "watchout", "category": "parallelize_subagent", "action": "invoke_subagent", "confidence": 0.85})
        self.assertIn("[WATCH·parallelize_subagent]", r_p1["text"])
        r_p2 = classify_advice({"status": "watchout", "category": "parallelize", "action": "invoke_subagent", "confidence": 0.85})
        self.assertIn("[WATCH·parallelize]", r_p2["text"])

        # 4. architectural_trap (watchout)
        r_a = classify_advice({"status": "watchout", "category": "architectural_trap", "action": "add lock", "confidence": 0.80})
        self.assertIn("[WATCH·architectural_trap]", r_a["text"])

        # 5. general (watchout and steer)
        r_g1 = classify_advice({"status": "watchout", "category": "general", "action": "check step", "confidence": 0.75})
        self.assertIn("[WATCH·general]", r_g1["text"])
        r_g2 = classify_advice({"status": "off_track", "category": "general", "action": "revert edit", "confidence": 0.90})
        self.assertIn("[STEER·general]", r_g2["text"])

        # 6. missing_deliverable (secondary tag, watchout)
        r_md = classify_advice({"status": "watchout", "category": "missing_deliverable", "action": "write notes.md", "confidence": 0.95})
        self.assertIn("[WATCH·missing_deliverable]", r_md["text"])

        # 7. algorithmic_bottleneck (secondary tag, watchout)
        r_ab = classify_advice({"status": "watchout", "category": "algorithmic_bottleneck", "action": "use cdcl", "confidence": 0.90})
        self.assertIn("[WATCH·algorithmic_bottleneck]", r_ab["text"])

        # 8. scope_drift (secondary tag, steer)
        r_sd = classify_advice({"status": "off_track", "category": "scope_drift", "action": "revert ui", "confidence": 0.92})
        self.assertIn("[STEER·scope_drift]", r_sd["text"])

        # 9. fake_verification (secondary tag, steer)
        r_fv = classify_advice({"status": "off_track", "category": "fake_verification", "action": "run cli", "confidence": 0.91})
        self.assertIn("[STEER·fake_verification]", r_fv["text"])

        # Uncalibrated (no confidence)
        r_noconf = classify_advice({"status": "watchout", "category": "architectural_trap", "action": "add lock"})
        self.assertIn("[WATCH·architectural_trap]", r_noconf["text"])
        self.assertNotIn("conf", r_noconf["text"])

    def test_classify_advice_keyed_dedup(self):
        ver_res = {"status": "watchout", "category": "architectural_trap", "action": "check schema", "guidance": "column alias missing"}
        # First emission
        res1 = classify_advice(ver_res, seen_advice={})
        self.assertEqual(res1["decision"], "watchout")
        k = res1["advice_key"]
        self.assertEqual(res1["seen"][k], 1)

        # Second emission (duplicate) -> hold_dedup
        res2 = classify_advice(ver_res, seen_advice=res1["seen"])
        self.assertEqual(res2["decision"], "hold_dedup")

    def test_classify_advice_escalation_bypass_dedup_non_repeatable(self):
        # Non-repeatable category (e.g. architectural_trap) bypasses dedup when escalating
        ver_res = {"status": "off_track", "category": "architectural_trap", "action": "fix bug", "guidance": "still broken", "escalation": "ignored_advice"}
        seen = {compute_advice_key("architectural_trap", "fix bug", "still broken"): 1}
        res = classify_advice(ver_res, seen_advice=seen)
        self.assertEqual(res["decision"], "steer")

    def test_parallelize_category_emits_once_then_dedups(self):
        ver_res = {"status": "watchout", "category": "parallelize", "action": "Dispatch invoke_subagent per suite", "guidance": "independent legs", "confidence": 0.8}
        r1 = classify_advice(ver_res, {})
        self.assertEqual(r1["decision"], "watchout")
        self.assertEqual(r1["category"], "parallelize")
        r2 = classify_advice(ver_res, r1["seen"])
        self.assertEqual(r2["decision"], "hold_dedup")

    def test_seen_advice_truncation_over_fifty_keys(self):
        # Populate seen_advice with 55 distinct keys with varying counts 1..55
        seen = {f"key_{i:03d}": i for i in range(1, 56)}
        ver_res = {"status": "watchout", "category": "loop_detection", "action": "new action", "guidance": "new guidance"}
        res = classify_advice(ver_res, seen_advice=seen)
        # Should be truncated to top 50 frequency keys
        self.assertEqual(len(res["seen"]), 50)
        # Verify that the highest counts (e.g. key_055 with 55) are preserved
        self.assertIn("key_055", res["seen"])
        self.assertIn("key_050", res["seen"])
        # Lowest counts (e.g. key_001 with 1, key_002 with 2) should have been evicted
        self.assertNotIn("key_001", res["seen"])

    def test_pinned_goal_emitted_in_scope_drift_text(self):
        ver_res = {
            "status": "off_track",
            "category": "scope_drift",
            "action": "Return to optimizer benchmarks",
            "evidence": "Editing unrelated frontend CSS",
            "confidence": 0.95,
            "pinned_goal": "Optimize AGY stop audit latency under 100ms",
            "goal_status": "drift_detected",
        }
    def test_advice_key_deduplication_across_guidance_variations(self):
        ver_res1 = {
            "status": "watchout",
            "category": "missing_deliverable",
            "action": "Run pytest tests/",
            "guidance": "Execute test suite to verify prompt and timeout changes pass cleanly.",
        }
        ver_res2 = {
            "status": "watchout",
            "category": "missing_deliverable",
            "action": "Run pytest tests/",
            "guidance": "Run pytest tests/ to confirm prompt, config, and executor changes pass cleanly before concluding.",
        }
        r1 = classify_advice(ver_res1, {})
        self.assertEqual(r1["decision"], "watchout")
        r2 = classify_advice(ver_res2, r1["seen"])
        self.assertEqual(r2["decision"], "hold_dedup")


if __name__ == "__main__":
    unittest.main()
