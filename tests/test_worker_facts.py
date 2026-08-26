#!/usr/bin/env python3
"""
tests.test_worker_facts - DELEGATED WORKERS raw-evidence block for the sage prompt.

Regression (2026-08-26): sage recapped healthy on a premature "opus completed"
claim because (a) the prompt window no longer contained the pane spawn command,
and (b) worker state was a bare label with no evidence. The reworked block must
carry RAW terminal tails + transcript line citations so the sage can judge for
itself, and must be channel-configurable via AGY_SAGE_WORKER_SPAWN_RE.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from sage.sage import build_sage_prompt
from sage.workers import extract_worker_facts


def _cmd(text, ts="2026-08-26T17:00:00+07:00"):
    return {"type": "GENERIC", "content": "", "tool_calls": [
        {"name": "run_command", "args": {"CommandLine": text}}], "created_at": ts}


def _out(text, ts="2026-08-26T17:00:05+07:00"):
    return {"type": "GENERIC", "content": text, "tool_calls": [], "created_at": ts}


def _write_transcript(steps):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for s in steps:
        tmp.write(json.dumps(s) + "\n")
    tmp.close()
    return tmp.name


ORCA_JSON_IDLE = json.dumps({"result": {"terminal": {
    "handle": "term_abc12345", "status": "running",
    "tail": ["────", "❯", "⏵⏵ bypass permissions on"]}}}, ensure_ascii=False)


class TestExtractWorkerFacts(unittest.TestCase):
    def test_spawn_read_idle_cycle_with_line_citations(self):
        path = _write_transcript([
            _cmd('orca terminal create --command "claude" --json'),   # spawn, no handle yet
            _out('{"result":{"terminal":{"handle":"term_abc12345"}}}'),  # handle revealed
            _cmd("orca terminal send --terminal term_abc12345 --text 'review'"),
            _cmd("orca terminal read --terminal term_abc12345 --screen --json"),
            _out(ORCA_JSON_IDLE),
        ])
        try:
            facts = extract_worker_facts(_load(path), path)
        finally:
            os.unlink(path)
        self.assertIn("term_abc12345", facts)
        self.assertIn("spawned@line1", facts)
        self.assertIn("last_output@line5", facts)
        self.assertIn("❯", facts)                      # raw screen evidence, not a label
        self.assertIn("SETTLED (idle/close observed)", facts)
        # settled but tail is just the prompt chrome -> delivered: no
        self.assertIn("delivered: no", facts)
        self.assertIn("stopped pane is not a finished review", facts)

    def test_streaming_pane_blocks_completion(self):
        path = _write_transcript([
            _cmd('orca terminal create --command "claude" --json'),
            _out(json.dumps({"result": {"terminal": {"handle": "term_ab12cd34ef",
                 "tail": ["✻ Sprouting… still thinking with high effort"]}}}, ensure_ascii=False)),
        ])
        try:
            facts = extract_worker_facts(_load(path), path)
        finally:
            os.unlink(path)
        self.assertIn("NOT SETTLED", facts)
        self.assertIn("still thinking", facts)          # raw tail carried through
        self.assertIn("WARNING", facts)

    def test_final_claim_is_cited(self):
        steps = [
            _cmd('orca terminal create --command "claude" --json'),
            _out('{"result":{"terminal":{"handle":"term_ef56ab78cd"}}}'),
            {"type": "PLANNER_RESPONSE", "content": "opus completed its critique.", "created_at": "2026-08-26T17:01:00+07:00"},
        ]
        path = _write_transcript(steps)
        try:
            facts = extract_worker_facts(_load(path), path)
        finally:
            os.unlink(path)
        self.assertIn("executor_final_claim@line3", facts)
        self.assertIn("NOT SETTLED", facts)

    def test_prefers_head_of_screen_over_tail(self):
        # Opus review finding: the useful content sits at the HEAD of a review;
        # the tail is usually chrome. Long screens must keep the head.
        tails = ["HEAD_SIGNATURE " + "a" * 4500, "TAIL_CHROME ❯"]
        path = _write_transcript([
            _cmd('orca terminal create --command "claude" --json'),
            _out(json.dumps({"result": {"terminal": {
                "handle": "term_0123456789", "status": "running", "tail": tails}}},
                ensure_ascii=False)),
        ])
        try:
            facts = extract_worker_facts(_load(path), path)
        finally:
            os.unlink(path)
        self.assertIn("HEAD_SIGNATURE", facts)
        self.assertNotIn("TAIL_CHROME", facts)

    def test_exited_status_settles_and_nos_not_settled_warning(self):
        # Regression: executor proves `status: exited` via direct query — the
        # facts block must stop claiming NOT SETTLED (it caused sage to hammer
        # fake_verification steers while the pane was provably finished).
        steps = [
            _cmd("orca terminal create --command claude --json"),
            _out('{"result":{"terminal":{"handle":"term_ab12cd34ef"}}}'),
            _cmd("orca terminal read --terminal term_ab12cd34ef --limit 10 --json"),
            _out('{"result":{"terminal":{"handle":"term_ab12cd34ef","status":"exited",'
                 '"tail":["verdict: LGTM with 2 nits","line one: fix A.","line two: fix B.","line three: fix C."]}}}'),
        ]
        try:
            path = _write_transcript(steps)
            facts = extract_worker_facts(_load(path), path)
        finally:
            os.unlink(path)
        self.assertNotIn("NOT SETTLED", facts)
        self.assertIn("SETTLED", facts)

    def test_exited_status_in_content_also_settles(self):
        steps = [
            _cmd("orca terminal create --command claude --worktree /tmp"),
            _out("handle term_ab12cd34ef created"),
            _out('orca terminal read term_ab12cd34ef: {"status":"exited"} tail [x]'),
        ]
        facts = extract_worker_facts(steps)
        self.assertNotIn("NOT SETTLED", facts)

    def test_channel_configurable_via_env(self):
        steps = [_cmd("remote-cli run --session rs99 --detach")]
        with patch.dict(os.environ, {"AGY_SAGE_WORKER_SPAWN_RE": r"\bremote-cli\s+run\b.*--detach;;\borca\s+terminal\s+create\b"}):
            path = _write_transcript(steps)
            try:
                facts = extract_worker_facts(_load(path), path)
            finally:
                os.unlink(path)
        self.assertIn("rs99", facts)

    def test_help_probes_are_not_workers(self):
        path = _write_transcript([_cmd("orca terminal create --help")])
        try:
            self.assertEqual(extract_worker_facts(_load(path), path), "")
        finally:
            os.unlink(path)


class TestPromptInjection(unittest.TestCase):
    def test_prompt_carries_raw_evidence_block(self):
        facts = ("worker[term_x] spawned@line7: claude\n"
                 "  last_output@line9: ❯ idle\n  state: NOT SETTLED\nWARNING: term_x NOT SETTLED")
        prompt = build_sage_prompt("c", "goal", "tiny", is_update=True, worker_facts=facts)
        self.assertIn("DELEGATED WORKERS", prompt)
        self.assertIn("trust these over any 'completed' claim", prompt)
        self.assertIn("@line9", prompt)

    def test_no_workers_no_block(self):
        self.assertNotIn("DELEGATED WORKERS",
                         build_sage_prompt("c", "goal", "steps", is_update=True))


def _load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")]


if __name__ == "__main__":
    unittest.main()
