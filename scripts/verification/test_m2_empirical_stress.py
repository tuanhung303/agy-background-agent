#!/usr/bin/env python3
"""
scripts.test_m2_empirical_stress - Rigorous adversarial stress testing suite for Milestone M2.
Executes white-box stress testing, boundary condition verification, fuzzing, and invariant checks.
"""

import copy
import hashlib
import json
import math
import os
import re
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sage.sage import (
    _normalize_advisor_dict,
    build_advisor_prompt,
    extract_target_goal,
    load_advisor_template,
    parse_advisor_output,
)
from sage.guards import DESTRUCTIVE_ACTION_RE, is_destructive_action
from sage.triage import (
    _parse_confidence,
    classify_advice,
    compute_advice_key,
)


class TestM2EmpiricalStress(unittest.TestCase):
    """Adversarial stress test harness for M2 components."""

    # =========================================================================
    # 1. Stress Testing _parse_confidence
    # =========================================================================
    def test_parse_confidence_fuzz_and_boundaries(self):
        # Standard values
        self.assertEqual(_parse_confidence(0.0), 0.0)
        self.assertEqual(_parse_confidence(1.0), 1.0)
        self.assertEqual(_parse_confidence(0.7), 0.7)
        self.assertEqual(_parse_confidence(0.85), 0.85)

        # Scale detection (1 < v <= 100)
        self.assertAlmostEqual(_parse_confidence(50), 0.5)
        self.assertAlmostEqual(_parse_confidence(99.9), 0.999)
        self.assertAlmostEqual(_parse_confidence(100), 1.0)
        self.assertAlmostEqual(_parse_confidence("100%"), 1.0)
        self.assertAlmostEqual(_parse_confidence("  75.5 % "), 0.755)
        self.assertAlmostEqual(_parse_confidence("0.001"), 0.001)

        # Extreme clamping
        self.assertEqual(_parse_confidence(-1000.0), 0.0)
        self.assertEqual(_parse_confidence(1000.0), 1.0)
        self.assertEqual(_parse_confidence("-50%"), 0.0)
        self.assertEqual(_parse_confidence("500%"), 1.0)

        # Type rejections
        self.assertIsNone(_parse_confidence(True))
        self.assertIsNone(_parse_confidence(False))
        self.assertIsNone(_parse_confidence(None))
        self.assertIsNone(_parse_confidence([]))
        self.assertIsNone(_parse_confidence({}))
        self.assertIsNone(_parse_confidence(object()))
        self.assertIsNone(_parse_confidence("invalid_text"))
        self.assertIsNone(_parse_confidence("---"))
        self.assertIsNone(_parse_confidence(""))
        self.assertIsNone(_parse_confidence("   "))

    # =========================================================================
    # 2. Stress Testing compute_advice_key
    # =========================================================================
    def test_compute_advice_key_invariants_and_fuzz(self):
        # 12-char hex invariance
        k1 = compute_advice_key("loop_detection", "run pytest", "tests failed")
        self.assertEqual(len(k1), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in k1))

        # Whitespace, case, and punctuation invariance
        k2 = compute_advice_key("  LOOP_DETECTION!  ", "Run `pytest`...", "Tests failed!!!")
        self.assertEqual(k1, k2)

        # Unicode / Emojis / Special characters handling
        k_unicode = compute_advice_key("🚨 risk", "rm -rf 🔥", "xóa dữ liệu")
        self.assertEqual(len(k_unicode), 12)
        self.assertTrue(all(c in "0123456789abcdef" for c in k_unicode))

        # Null / Empty values
        k_empty = compute_advice_key(None, None, None)
        k_empty_str = compute_advice_key("", "", "")
        self.assertEqual(k_empty, k_empty_str)
        self.assertEqual(len(k_empty), 12)

        # Massive input stress (100KB strings)
        massive_str = "abc " * 25000
        t0 = time.time()
        k_massive = compute_advice_key(massive_str, massive_str, massive_str)
        dur = time.time() - t0
        self.assertEqual(len(k_massive), 12)
        self.assertLess(dur, 0.05, "Hashing massive strings must be under 50ms")

    # =========================================================================
    # 3. Stress Testing classify_advice State Machine & Confidence Gating
    # =========================================================================
    def test_classify_advice_exact_boundaries(self):
        # Boundary 0.70 for steer
        res_699 = classify_advice({"status": "off_track", "category": "loop_detection", "confidence": 0.69999})
        self.assertEqual(res_699["decision"], "watchout")
        self.assertEqual(res_699["status"], "watchout")

        res_700 = classify_advice({"status": "off_track", "category": "loop_detection", "confidence": 0.70000})
        self.assertEqual(res_700["decision"], "steer")
        self.assertEqual(res_700["status"], "off_track")

        # Boundary 0.85 for irreversible_risk escalation
        res_849 = classify_advice({"status": "watchout", "category": "irreversible_risk", "action": "git status", "confidence": 0.84999})
        self.assertEqual(res_849["decision"], "watchout")

        res_850 = classify_advice({"status": "watchout", "category": "irreversible_risk", "action": "git status", "confidence": 0.85000})
        self.assertEqual(res_850["decision"], "steer")
        self.assertEqual(res_850["status"], "off_track")

        # Irreversible risk without action does NOT escalate even at conf=1.0
        res_no_act = classify_advice({"status": "watchout", "category": "irreversible_risk", "action": "", "guidance": "warning", "confidence": 1.0})
        self.assertEqual(res_no_act["decision"], "watchout")

    def test_classify_advice_deduplication_ledger_dynamics(self):
        # Test max_emissions=2 for loop_detection (steer category)
        ver = {"status": "off_track", "category": "loop_detection", "action": "inspect schema", "confidence": 0.9}
        seen = {}

        # 1st emission
        r1 = classify_advice(ver, seen)
        self.assertEqual(r1["decision"], "steer")
        k = r1["advice_key"]
        self.assertEqual(r1["seen"][k], 1)

        # 2nd emission -> allowed since effective_max=2
        r2 = classify_advice(ver, r1["seen"])
        self.assertEqual(r2["decision"], "steer")
        self.assertEqual(r2["seen"][k], 2)

        # 3rd emission -> exceeded max_emissions=2 -> hold_dedup
        r3 = classify_advice(ver, r2["seen"])
        self.assertEqual(r3["decision"], "hold_dedup")

        # Irreversible risk allows max_emissions * 2 = 4 emissions
        ver_risk = {"status": "watchout", "category": "irreversible_risk", "action": "git status", "confidence": 0.8}
        s_risk = {}
        for i in range(1, 5):
            r = classify_advice(ver_risk, s_risk)
            self.assertEqual(r["decision"], "watchout", f"Emission #{i} should be watchout")
            s_risk = r["seen"]
        # 5th emission -> hold_dedup
        r5 = classify_advice(ver_risk, s_risk)
        self.assertEqual(r5["decision"], "hold_dedup")

        # Non-repeatable watchout dedups after 1 emission unless escalating
        ver_watch = {"status": "watchout", "category": "architectural_trap", "action": "add lock", "confidence": 0.8}
        r_w1 = classify_advice(ver_watch, {})
        self.assertEqual(r_w1["decision"], "watchout")
        # 2nd emission without escalation -> hold_dedup
        r_w2 = classify_advice(ver_watch, r_w1["seen"])
        self.assertEqual(r_w2["decision"], "hold_dedup")
        # 3rd emission WITH escalation -> watchout (bypasses dedup)
        ver_watch_esc = dict(ver_watch, escalation="ignored_advice")
        r_w3 = classify_advice(ver_watch_esc, r_w1["seen"])
        self.assertEqual(r_w3["decision"], "watchout")

    def test_classify_advice_seen_ledger_immutability(self):
        # Passing seen_advice must NOT mutate caller dict in-place
        orig_seen = {"key_a": 1, "key_b": 2}
        orig_seen_copy = copy.deepcopy(orig_seen)
        ver = {"status": "watchout", "category": "general", "action": "check notes", "confidence": 0.8}
        res = classify_advice(ver, seen_advice=orig_seen)
        self.assertEqual(orig_seen, orig_seen_copy, "Caller seen_advice dict was mutated in-place!")
        self.assertNotEqual(res["seen"], orig_seen)

    def test_classify_advice_seen_ledger_truncation_50_keys(self):
        # Populate 100 keys with distinct frequency counts 1..100
        seen = {f"k_{i:03d}": i for i in range(1, 101)}
        ver = {"status": "watchout", "category": "general", "action": "new action", "confidence": 0.8}
        res = classify_advice(ver, seen_advice=seen)
        self.assertEqual(len(res["seen"]), 50)
        # Verify highest count keys (k_100..k_052) are preserved
        self.assertIn("k_100", res["seen"])
        self.assertIn("k_052", res["seen"])
        self.assertNotIn("k_001", res["seen"])
        self.assertNotIn("k_050", res["seen"])

    # =========================================================================
    # 4. Stress Testing Destructive Action Suppression
    # =========================================================================
    def test_destructive_action_suppression_exhaustive(self):
        destructive_cases = [
            "rm -rf /tmp/data",
            "rm -fr /tmp/data",
            "rm -vrf /tmp/data",
            "rm -vfr /tmp/data",
            "sudo rm /etc/passwd",
            "git reset --hard HEAD~1",
            "git reset --hard origin/main",
            "git push origin main --force",
            "DROP TABLE users",
            "drop database production",
            "TRUNCATE TABLE sessions",
            "chmod -R 777 /app",
            "mkfs /dev/sdb1",
        ]

        safe_cases = [
            "rm single_file.txt",
            "rm -f test_file.py",
            "git reset HEAD~1",
            "SELECT * FROM users;",
            "chmod 644 config.json",
            "python3 -m unittest discover",
        ]

        for cmd in destructive_cases:
            with self.subTest(cmd=cmd):
                self.assertTrue(is_destructive_action(cmd), f"Expected '{cmd}' to be detected as destructive")
                d = _normalize_advisor_dict({"status": "off_track", "action": cmd, "guidance": cmd})
                self.assertEqual(d["action"], "[Destructive action suppressed] Use safe verification.")
                self.assertEqual(d["guidance"], "[Destructive command suppressed] Avoid destructive commands; verify first.")

        for cmd in safe_cases:
            with self.subTest(cmd=cmd):
                self.assertFalse(is_destructive_action(cmd), f"Expected '{cmd}' to be recognized as SAFE")

    # =========================================================================
    # 5. Stress Testing parse_advisor_output & _normalize_advisor_dict
    # =========================================================================
    def test_parse_advisor_output_exotic_formats_and_fault_tolerance(self):
        # Empty and none inputs
        self.assertEqual(parse_advisor_output(None)["status"], "on_track")
        self.assertEqual(parse_advisor_output("")["status"], "on_track")
        self.assertEqual(parse_advisor_output("   ")["status"], "on_track")

        # Corrupted / Truncated JSON
        self.assertEqual(parse_advisor_output('{"status": "off_track", "blind_')["status"], "on_track")
        self.assertEqual(parse_advisor_output('<<<HTML>>> not json <<<HTML>>>')["status"], "on_track")

        # Deeply nested or markdown fenced JSON with surrounding chat
        complex_output = """
Here is my review of the current session:

```json
{
    "status": "off-track",
    "category": "loop_detection",
    "action": "inspect table schema via `list_tables`",
    "evidence": "3 consecutive SQL errors",
    "confidence": 0.95,
    "guidance": "Stop blind guessing.",
    "escalation": "first_warning"
}
```

Let me know if you need further assistance!
"""
        parsed = parse_advisor_output(complex_output)
        self.assertEqual(parsed["status"], "off_track")
        self.assertFalse(parsed["healthy"])
        self.assertEqual(parsed["category"], "loop_detection")
        self.assertEqual(parsed["confidence"], 0.95)
        self.assertEqual(parsed["action"], "inspect table schema via `list_tables`")
        self.assertEqual(parsed["evidence"], "3 consecutive SQL errors")

        # Various status aliases normalization
        aliases_map = {
            "fail": "off_track",
            "failed": "off_track",
            "broken": "off_track",
            "bug": "off_track",
            "steer": "off_track",
            "intervention": "off_track",
            "caution": "watchout",
            "warning": "watchout",
            "headsup": "watchout",
            "gotcha": "watchout",
            "pass": "on_track",
            "ok": "on_track",
            "good": "on_track",
            "hold": "on_track",
        }
        for alias, expected in aliases_map.items():
            with self.subTest(alias=alias):
                res = _normalize_advisor_dict({"status": alias, "guidance": "some notice"})
                self.assertEqual(res["status"], expected)

    # =========================================================================
    # 6. Stress Testing Goal Extraction & Prompt Building
    # =========================================================================
    def test_extract_target_goal_and_prompt_building(self):
        # Goal extraction from complex session history
        history = """
SESSION HISTORY:
- Prior request 1: Do task A
- Prior request 2: Do task B

[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:
Refactor triage module to adhere to <= 199 lines
"""
        goal = extract_target_goal(history)
        self.assertEqual(goal, "Refactor triage module to adhere to <= 199 lines")

        # Fallback when no marker present
        plain_request = "Implement CDCL solver in solver.py"
        self.assertEqual(extract_target_goal(plain_request), plain_request)

        # Build prompt with update vs initial
        p_init = build_advisor_prompt("conv_1", plain_request, "step 1\nstep 2", is_update=False, git_diff="diff a b")
        self.assertIn("conv_1", p_init)
        self.assertIn(plain_request, p_init)
        self.assertIn("step 1", p_init)

        p_upd = build_advisor_prompt("conv_1", plain_request, "step 3", is_update=True, git_diff="diff b c", signals="SIGNAL_PARALLEL")
        self.assertTrue("SAGE UPDATE" in p_upd or "ADVISOR UPDATE" in p_upd)
        self.assertIn("ACTIVE SIGNALS:\nSIGNAL_PARALLEL", p_upd)
        self.assertIn("Status legend:", p_upd)

    # =========================================================================
    # 7. Performance & Throughput Benchmark
    # =========================================================================
    def test_high_throughput_classification_benchmark(self):
        ver_res = {
            "status": "off_track",
            "category": "loop_detection",
            "action": "run pytest -k test_triage",
            "evidence": "2 test failures",
            "confidence": 0.95,
            "guidance": "Fix failing assertion at line 42",
        }
        seen = {}
        t0 = time.time()
        for i in range(5000):
            res = classify_advice(ver_res, seen)
            seen = res["seen"]
        elapsed = time.time() - t0
        ops_per_sec = 5000 / elapsed
        self.assertGreater(ops_per_sec, 2000, f"Throughput too low: {ops_per_sec:.0f} ops/sec")


if __name__ == "__main__":
    unittest.main()
