#!/usr/bin/env python3
"""
tests.test_worker_facts - DELEGATED WORKERS block for the sage prompt.

Regression (2026-08-26): sage recapped healthy on a premature "opus completed"
claim because its prompt window (last 10 steps, sanitized outputs) no longer
contained the pane spawn command. Worker facts are built deterministically
from the whole turn and injected as a dedicated, unclippable block.
"""

import unittest

from sage.sage import build_sage_prompt
from sage.workers import extract_worker_facts


def _cmd_step(text, ts="2026-08-26T17:00:00+07:00"):
    return {"type": "GENERIC", "content": "", "tool_calls": [
        {"name": "run_command", "args": {"CommandLine": text}}], "created_at": ts}


class TestExtractWorkerFacts(unittest.TestCase):
    def test_active_pane_produces_warning_block(self):
        steps = [
            _cmd_step('orca terminal create --command "claude" # term_abc12345'),
            _cmd_step("orca terminal send --terminal term_abc12345 --text 'review'"),
        ]
        facts = extract_worker_facts(steps)
        self.assertTrue(facts.startswith("KIND |"))
        self.assertIn("WARNING", facts)
        self.assertIn("term_abc12345", facts)

    def test_help_probes_are_not_workers(self):
        steps = [_cmd_step(""), ]  # content empty; commands carry the signal
        steps = [
            {"type": "GENERIC", "content": "", "tool_calls": [
                {"name": "run_command",
                 "args": {"CommandLine": "orca terminal send --help"}}],
             "created_at": "2026-08-26T17:00:00+07:00"},
        ]
        self.assertEqual(extract_worker_facts(steps), "")

    def test_no_workers_empty_string(self):
        self.assertEqual(extract_worker_facts([
            {"type": "GENERIC", "content": "pytest passed", "tool_calls": [],
             "created_at": "2026-08-26T17:00:00+07:00"}]), "")


class TestPromptInjection(unittest.TestCase):
    def test_prompt_carries_worker_facts_outside_clip_window(self):
        long_steps = "\n".join(f"Response {i}: filler" for i in range(4000 // 16))
        facts = "KIND | ID | CMD\norca-pane | term_x | ACTIVE STREAMING"
        prompt = build_sage_prompt("c", "goal", long_steps, is_update=True,
                                   worker_facts=facts)
        self.assertIn("DELEGATED WORKERS", prompt)
        self.assertIn("term_x", prompt)
        self.assertIn("trust these over any 'completed' claim", prompt)

    def test_prompt_without_workers_has_no_block(self):
        prompt = build_sage_prompt("c", "goal", "steps here", is_update=True)
        self.assertNotIn("DELEGATED WORKERS", prompt)


if __name__ == "__main__":
    unittest.main()
