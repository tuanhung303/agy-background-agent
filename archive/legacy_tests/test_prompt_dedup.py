#!/usr/bin/env python3
"""
tests.test_prompt_dedup - sage-prompt context de-duplication.

The sage prompt must keep a FULL picture without re-sending bytes it already
sent: incremental step slicing (cross-fire; the model conversation has seen
the earlier steps) + one-line pointers replacing full pane-read envelopes
(intra-prompt; the same screens live raw in DELEGATED WORKERS).
"""

import json
import os
import tempfile
import unittest

from sage.sage import _dedupe_pane_reads
from sage.transcript import render_turn_steps_slice


def _row(ty, content, tools, ts="2026-08-26T17:00:00+07:00"):
    return {"type": ty, "content": content,
            "tool_calls": [{"name": n} for n in tools], "created_at": ts}


def _write(steps):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for s in steps:
        tmp.write(json.dumps(s) + "\n")
    tmp.close()
    return tmp.name


class TestRenderSlice(unittest.TestCase):
    def test_boundary_cuts_seen_steps(self):
        path = _write([
            _row("USER_INPUT", "make x", []),
            _row("GENERIC", "a", ["read_file"]),      # cum 1
            _row("GENERIC", "b", ["read_file"]),      # cum 2
            _row("GENERIC", "c", ["run_command"]),    # cum 3
            _row("GENERIC", "d", ["write_file"]),     # cum 4
        ])
        try:
            texts, unchanged = render_turn_steps_slice(path, since_tools=2)
        finally:
            os.unlink(path)
        self.assertEqual(unchanged, 3)
        self.assertEqual(len(texts), 2)
        self.assertIn("c", texts[0])

    def test_no_new_steps_returns_none(self):
        path = _write([
            _row("USER_INPUT", "make x", []),
            _row("GENERIC", "a", ["read_file"]),
            _row("GENERIC", "b", ["run_command"]),
        ])
        try:
            self.assertIsNone(render_turn_steps_slice(path, since_tools=2))
        finally:
            os.unlink(path)

    def test_single_new_step_kept(self):
        path = _write([
            _row("USER_INPUT", "make x", []),
            _row("GENERIC", "a", ["read_file"]),
            _row("GENERIC", "b", ["run_command"]),
        ])
        try:
            texts, unchanged = render_turn_steps_slice(path, since_tools=1)
        finally:
            os.unlink(path)
        self.assertEqual(len(texts), 1)
        self.assertEqual(unchanged, 2)

    def test_no_prior_check_returns_none(self):
        self.assertIsNone(render_turn_steps_slice(None, 0))
        self.assertIsNone(render_turn_steps_slice("/nonexistent", 5))


class TestPaneReadDedupe(unittest.TestCase):
    FACTS = "worker[term_ab12cd34ef] spawned@line1: terminal create\n" \
            "  last_output@line3: {\"tail\": [\"a\" * 3000]}\n  state: x | delivered: no"

    def test_big_read_envelope_becomes_pointer(self):
        big = "Tool output: " + "x" * 5000 + " term_ab12cd34ef tail Output:"
        out = _dedupe_pane_reads([big, "Response: plain | Tools: ['read']"], self.FACTS)
        self.assertEqual(len(out), 2)                     # line count preserved
        self.assertIn("DELEGATED WORKERS", out[0])
        self.assertNotIn("x" * 100, out[0])
        self.assertEqual(out[1], "Response: plain | Tools: ['read']")

    def test_non_read_steps_untouched(self):
        steps = ["Tool output: normal search results, no handles here"]
        self.assertEqual(_dedupe_pane_reads(steps, self.FACTS), steps)

    def test_no_workers_means_no_touch(self):
        steps = ["Tool output: " + "y" * 5000 + " term_ab12cd34ef tail"]
        self.assertEqual(_dedupe_pane_reads(steps, ""), steps)


if __name__ == "__main__":
    unittest.main()
