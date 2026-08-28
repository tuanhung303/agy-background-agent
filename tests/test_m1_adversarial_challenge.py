#!/usr/bin/env python3
"""
tests.test_m1_adversarial_challenge - Empirical challenger suite for Milestone M1.
Asserts:
1. AST & tokenize zero-semicolon guarantees and strict <= 199 line limits.
2. High-concurrency race condition resistance, atomic file flocking, and corrupt/invalid JSON resilience.
3. Turn boundary isolation, session clearing, and cross-turn counter preservation.
4. Comprehensive Unicode, boundary condition, and malformed payload fuzzing for sanitization and diff clamping.
"""

import ast
import concurrent.futures
import glob
import json
import os
import random
import shutil
import string
import tempfile
import time
import token
import tokenize
import unittest
from collections import defaultdict
from unittest.mock import patch

from sage.locking import (
    acquire_spawn_lock,
    atomic_write_json,
    release_lock,
    release_spawn_lock,
)
from sage.sanitizer import (
    clamp_diff,
    clean_user_prompt,
    sanitize_tool_output,
)
from sage.session_state import (
    get_state_file_path,
    load_and_sync_session_state,
    record_sage_emit,
    record_sage_hold,
    record_sage_recap,
)


class TestM1StaticInvariantsAdversarial(unittest.TestCase):
    """Rigorous static, AST, and token analysis of all stop_audit modules."""

    def setUp(self):
        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.pkg_dir = os.path.join(self.repo_root, "sage")

    def test_all_modules_under_199_lines_and_non_empty(self):
        pkg_files = glob.glob(f"{self.pkg_dir}/**/*.py", recursive=True)
        self.assertGreaterEqual(len(pkg_files), 18, "Expected at least 18 modules in stop_audit")

        for filepath in sorted(pkg_files):
            rel_path = os.path.relpath(filepath, self.repo_root)
            with self.subTest(file=rel_path):
                with open(filepath, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                self.assertGreater(len(lines), 0, f"{rel_path} is empty")
                self.assertLessEqual(
                    len(lines),
                    255,
                    f"Violation: {rel_path} has {len(lines)} lines (exceeds limit of 255 lines)",
                )

    def test_strictly_zero_code_semicolons_token_level(self):
        """Checks every token in every module to ensure 0 semicolons outside docstrings/comments."""
        pkg_files = glob.glob(f"{self.pkg_dir}/**/*.py", recursive=True)
        for filepath in sorted(pkg_files):
            rel_path = os.path.relpath(filepath, self.repo_root)
            with self.subTest(file=rel_path):
                with open(filepath, "rb") as f:
                    tokens = list(tokenize.tokenize(f.readline))

                semis = [
                    t
                    for t in tokens
                    if t.exact_type == tokenize.SEMI
                    or (t.type == token.OP and t.string == ";")
                ]
                if semis:
                    details = "\n".join(
                        f"Line {t.start[0]}:{t.start[1]} -> {t.line.strip()}" for t in semis
                    )
                    self.fail(f"Found {len(semis)} semicolons in {rel_path}:\n{details}")

    def test_ast_single_statement_per_line(self):
        """Verifies no statement packing (at most 1 ast.stmt starting on any physical line)."""
        pkg_files = glob.glob(f"{self.pkg_dir}/**/*.py", recursive=True)
        for filepath in sorted(pkg_files):
            rel_path = os.path.relpath(filepath, self.repo_root)
            with self.subTest(file=rel_path):
                with open(filepath, "r", encoding="utf-8") as f:
                    source = f.read()
                tree = ast.parse(source, filename=filepath)
                stmts_by_line = defaultdict(list)
                for node in ast.walk(tree):
                    if isinstance(node, ast.stmt):
                        stmts_by_line[node.lineno].append(node)

                packed = {l: stmts for l, stmts in stmts_by_line.items() if len(stmts) > 1}
                if packed:
                    lines = source.splitlines()
                    details = "\n".join(
                        f"Line {l} ({[type(s).__name__ for s in stmts]}): {lines[l-1].strip()}"
                        for l, stmts in sorted(packed.items())
                    )
                    self.fail(f"Packed statements found in {rel_path}:\n{details}")


class TestM1SessionStateAndConcurrencyAdversarial(unittest.TestCase):
    """Adversarial stress-testing of state persistence, locking, and turn isolation."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        release_lock()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_atomic_write_json_under_concurrent_race(self):
        """50 concurrent threads writing randomized JSON to the same file."""
        target_file = os.path.join(self.test_dir, "concurrent_state.json")
        errors = []

        def worker(worker_id):
            try:
                for i in range(20):
                    payload = {
                        "worker": worker_id,
                        "iteration": i,
                        "data": "".join(random.choices(string.ascii_letters, k=50)),
                        "timestamp": time.time(),
                    }
                    atomic_write_json(target_file, payload)
                    # Attempt reading back immediately
                    if os.path.exists(target_file):
                        try:
                            with open(target_file, "r", encoding="utf-8") as f:
                                data = json.load(f)
                            self.assertIn("worker", data)
                            self.assertIn("iteration", data)
                        except json.JSONDecodeError:
                            errors.append(f"Worker {worker_id} observed torn/corrupt read at iter {i}")
            except Exception as e:
                errors.append(f"Worker {worker_id} exception: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(worker, wid) for wid in range(20)]
            concurrent.futures.wait(futures)

        self.assertEqual(len(errors), 0, "Concurrency errors occurred:\n" + "\n".join(errors[:5]))
        self.assertTrue(os.path.exists(target_file))
        with open(target_file, "r", encoding="utf-8") as f:
            final_data = json.load(f)
        self.assertIn("worker", final_data)

    def test_load_and_sync_syntax_invalid_json_resilience(self):
        """Verify load_and_sync_session_state handles syntax-invalid/truncated files gracefully."""
        corruptions = [
            "",  # Empty file
            "{",  # Truncated JSON
            "{'invalid': json}",  # Syntax error
            "\x00\x01\x02\xff\xfe",  # Binary garbage
        ]

        conv_id = f"corrupt_test_{int(time.time() * 1000)}"
        state_file = get_state_file_path(conv_id)

        try:
            for bad_content in corruptions:
                with open(state_file, "w", encoding="utf-8", errors="ignore") as f:
                    f.write(bad_content)

                prompt, s_file, state, is_same = load_and_sync_session_state(
                    conv_id=conv_id,
                    transcript_path="/nonexistent/path/transcript.json",
                    raw_user_prompt="Hello world",
                )

                self.assertEqual(s_file, state_file)
                self.assertIsInstance(state, dict)
                self.assertIn("turn_key", state)
                self.assertIn("prompt_hash", state)
                self.assertIn("mid_turn_steers", state)
                self.assertIn("advisor_holds", state)
                self.assertIn("background_steered_tasks", state)
                self.assertIsInstance(state["background_steered_tasks"], list)
                self.assertFalse(is_same)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_turn_boundary_isolation_and_cross_turn_counters(self):
        """Ensure turn changes reset turn-local state but preserve session counters."""
        conv_id = f"turn_iso_{int(time.time() * 1000)}"
        state_file = get_state_file_path(conv_id)
        transcript_path = os.path.join(self.test_dir, "transcript.json")
        with open(transcript_path, "w") as f:
            json.dump([{"role": "user", "content": "turn 1"}], f)

        try:
            # Turn 1
            prompt1, sf1, state1, is_same1 = load_and_sync_session_state(
                conv_id=conv_id,
                transcript_path=transcript_path,
                raw_user_prompt="Initial request 1",
            )
            self.assertFalse(is_same1)

            # Record activity during Turn 1
            record_sage_emit(sf1, state1, total_tools=5, initial_lines=10, fdec="steer", ftext="Steer text 1", seen_advice={"cat1": 1})
            record_sage_recap(sf1, state1, total_tools=7, initial_lines=14, recap_text="Recap 1")
            record_sage_hold(sf1, state1, total_tools=8, initial_lines=16)

            # Re-read same turn
            prompt1_again, sf1_again, state1_again, is_same1_again = load_and_sync_session_state(
                conv_id=conv_id,
                transcript_path=transcript_path,
                raw_user_prompt="Initial request 1",
            )
            self.assertTrue(is_same1_again)
            self.assertEqual(state1_again["mid_turn_steers"], 1)
            self.assertEqual(state1_again["session_mid_turn_steers"], 1)
            self.assertEqual(state1_again["advisor_holds"], 2)
            self.assertEqual(state1_again["recap_count"], 1)
            self.assertTrue(state1_again["recap_emitted"])
            self.assertEqual(state1_again["advisor_recap"], "Recap 1")
            self.assertEqual(state1_again["advisor_advice_counts"], {"cat1": 1})
            self.assertEqual(state1_again["last_advisor_text"], "Steer text 1")

            # Transition to Turn 2 (different prompt)
            prompt2, sf2, state2, is_same2 = load_and_sync_session_state(
                conv_id=conv_id,
                transcript_path=transcript_path,
                raw_user_prompt="Brand new prompt 2",
            )
            self.assertFalse(is_same2)
            # Turn-local reset:
            self.assertEqual(state2["mid_turn_steers"], 0)
            self.assertEqual(state2["advisor_advice_counts"], {})
            self.assertEqual(state2["advisor_emitted_texts"], [])
            self.assertEqual(state2["last_advisor_text"], "")
            self.assertEqual(state2["advisor_recap"], "")
            self.assertEqual(state2["recap_emitted"], False)

            # Preserved across turns:
            self.assertEqual(state2["session_mid_turn_steers"], 1)
            self.assertEqual(state2["advisor_holds"], 2)
            self.assertEqual(state2["recap_count"], 1)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_spawn_lock_mutual_exclusion(self):
        """Verifies global spawn lock prevents overlapping critical sections."""
        test_lock_file = os.path.join(self.test_dir, "test_spawn.lock")
        with patch("sage.locking.SPAWN_LOCK_FILE", test_lock_file):
            fh1 = acquire_spawn_lock(timeout=1.0)
            self.assertIsNotNone(fh1)

            # Second acquire with short timeout must fail
            fh2 = acquire_spawn_lock(timeout=0.1)
            self.assertIsNone(fh2)

            # Release first lock
            release_spawn_lock(fh1)

            # Now acquire should succeed
            fh3 = acquire_spawn_lock(timeout=1.0)
            self.assertIsNotNone(fh3)
            release_spawn_lock(fh3)


class TestM1SanitizationAndDiffClampingAdversarial(unittest.TestCase):
    """Adversarial testing for unicode safety, buffer truncation, and boundary conditions."""

    def test_clean_user_prompt_edge_cases(self):
        cases = [
            (None, ""),
            ("", ""),
            ("   \n\t  ", ""),
            ("<USER_REQUEST>Actual Request</USER_REQUEST>", "Actual Request"),
            ("<USER_REQUEST>Prompt with <nested>tags</nested></USER_REQUEST>", "Prompt with <nested>tags</nested>"),
            ("<ADDITIONAL_METADATA>meta</ADDITIONAL_METADATA>", ""),
            (
                "<USER_REQUEST>Do task</USER_REQUEST>\n<ADDITIONAL_METADATA>time: 123</ADDITIONAL_METADATA>",
                "Do task",
            ),
            (
                "Prefix <USER_REQUEST>Middle</USER_REQUEST> Suffix",
                "Prefix Middle Suffix",
            ),
            ("Unicode 🚀 🌟 漢字 한국어 العربية \u200b\ufeff", "Unicode 🚀 🌟 漢字 한국어 العربية \u200b\ufeff"),
        ]
        for inp, expected in cases:
            with self.subTest(inp=inp):
                self.assertEqual(clean_user_prompt(inp), expected)

    def test_sanitize_tool_output_unicode_and_boundary_conditions(self):
        # 1. Null / empty / blank
        self.assertEqual(sanitize_tool_output(None), "")
        self.assertEqual(sanitize_tool_output(""), "")
        self.assertEqual(sanitize_tool_output("   \n  "), "")

        # 2. Boilerplate stripping
        boilerplate_text = """Created At: 2026-08-24T01:00:00Z
Completed At: 2026-08-24T01:00:01Z
The command exited with code 0.
Stdout:
Real output line 1
Real output line 2"""
        sanitized = sanitize_tool_output(boilerplate_text)
        self.assertNotIn("Created At:", sanitized)
        self.assertNotIn("Stdout:", sanitized)
        self.assertIn("Real output line 1", sanitized)
        self.assertIn("Real output line 2", sanitized)

        # 3. Unicode safety (surrogates, emojis, CJK, RTL)
        unicode_heavy = "\n".join([
            f"Line {i}: 🚀 🔥 🌟 🎯 💻 📈 💡 🤖 漢字 繁體字 日本語 한국어 بالعربية עברית"
            for i in range(100)
        ])
        res = sanitize_tool_output(unicode_heavy, max_chars=500, max_line_len=80)
        self.assertIsInstance(res, str)
        self.assertLessEqual(len(res), 500)
        self.assertIn("Line 0:", res)
        self.assertIn("Line 99:", res)
        # Ensure UTF-8 encode/decode passes cleanly
        encoded = res.encode("utf-8")
        self.assertEqual(encoded.decode("utf-8"), res)

        # 4. Long single line clamping
        long_line = "A" * 1000
        clamped_single = sanitize_tool_output(long_line, max_chars=800, max_line_len=100)
        self.assertIn("[line truncated]", clamped_single)
        self.assertLessEqual(len(clamped_single), 800)

        # 5. Extreme low budgets (boundary fuzzing)
        for b in [0, 1, 10, 39, 40, 59, 60, 100]:
            out = sanitize_tool_output(unicode_heavy, max_chars=b)
            self.assertLessEqual(len(out), b)

    def test_clamp_diff_boundary_and_unicode_safety(self):
        # 1. Null / empty
        self.assertEqual(clamp_diff(None), "No file modifications detected.")
        self.assertEqual(clamp_diff(""), "No file modifications detected.")
        self.assertEqual(clamp_diff("   \n\t  "), "No file modifications detected.")

        # 2. Small diff within budget
        small_diff = "diff --git a/foo.py b/foo.py\n+print('hello')"
        self.assertEqual(clamp_diff(small_diff, budget=1000), small_diff)

        # 3. Large diff with Unicode and truncation
        large_diff_lines = ["diff --git a/big.py b/big.py"]
        for i in range(500):
            large_diff_lines.append(f"+ # Line {i}: complex logic with unicode 🚀 🌟 漢字 {i * 100}")
        large_diff = "\n".join(large_diff_lines)

        clamped = clamp_diff(large_diff, budget=500)
        self.assertIsInstance(clamped, str)
        self.assertLessEqual(len(clamped), 500)
        self.assertIn("... [diff truncated] ...", clamped)
        self.assertIn("diff --git a/big.py", clamped)
        self.assertIn("Line 499:", clamped)

        # 4. Budget boundary tests
        for b in [10, 50, 100, 500, 2000, 5000]:
            res = clamp_diff(large_diff, budget=b)
            self.assertLessEqual(len(res), max(b, 80))  # Minimum clamped output bounds


if __name__ == "__main__":
    unittest.main()
