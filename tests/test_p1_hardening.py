#!/usr/bin/env python3
"""
Regression suite for the P1 defects found in the full-repository review.

Each test encodes a failure mode that was reproduced against the pre-fix code:
  1. watchers   - pending subagent ids collided, dropping a live subagent.
  2. transcript - blank `source` broke turn identity and turn tool scoping.
  3. session_state - colliding turn keys carried a stale recap latch into a new turn.
  4. triage     - the emission ceiling was escapable by relabelling the category.
  5. policies   - an identical re-emission had no backstop once its key aged out.
  6. executor   - ambiguous conversation attribution could rmtree a live brain dir.
  7. command-timer - a stale start record fabricated FORBIDDEN_EXCEEDED_LIMIT.
"""

import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from sage.executor import _find_new_conv_id
from sage.policies import sage_flow
from sage.session_state import load_and_sync_session_state, record_sage_recap
from sage.transcript import (
    extract_turn_tool_calls,
    get_active_turn_identity,
    is_explicit_user_input,
)
from sage.triage import classify_advice
from sage.watchers import get_active_subagents

HOOK_SCRIPT = Path(__file__).parent.parent / "hooks" / "command-timer.py"


def _invoke(subagents):
    return {"type": "PLANNER_RESPONSE", "content": "", "tool_calls": [
        {"name": "invoke_subagent", "args": {"Subagents": subagents}}]}


class TestSubagentTrackingCollision(unittest.TestCase):
    def test_spawn_failure_does_not_drop_a_live_subagent(self):
        steps = [
            _invoke([{"Role": "Scout"}]),
            _invoke([{"Role": "Implementer"}]),
            {"type": "GENERIC", "content": "Error: failed to invoke subagent (quota)"},
            _invoke([{"Role": "QA"}]),
        ]
        active = get_active_subagents(steps, conv_id="parent")
        roles = sorted(a["role"] for a in active)
        self.assertEqual(roles, ["Implementer", "QA"])
        self.assertEqual(len({a["subagent_id"] for a in active}), 2)

    def test_pending_ids_are_unique_across_many_batches(self):
        steps = []
        for _ in range(6):
            steps.append(_invoke([{"Role": "Worker"}, {"Role": "Worker"}]))
            steps.append({"type": "GENERIC", "content": "unable to spawn subagent"})
            steps.append(_invoke([{"Role": "Keeper"}]))
        active = get_active_subagents(steps, conv_id="parent")
        # The regression is id reuse silently overwriting a tracked subagent, so the
        # invariant is one distinct id per surviving entry (batch-to-failure pairing
        # stays FIFO and is deliberately not asserted here).
        self.assertEqual(len({a["subagent_id"] for a in active}), len(active))
        self.assertGreater(len(active), 0)


class TestTurnBoundaryPredicate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "transcript.jsonl")

    def _write(self, rows):
        with open(self.path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    def test_blank_source_still_counts_as_a_user_turn(self):
        self.assertTrue(is_explicit_user_input({"type": "USER_INPUT", "content": "go"}))
        self.assertTrue(is_explicit_user_input({"type": "USER_INPUT", "source": "", "content": "go"}))
        self.assertTrue(is_explicit_user_input({"type": "USER_INPUT", "source": "USER", "content": "go"}))
        self.assertFalse(is_explicit_user_input({"type": "PLANNER_RESPONSE", "content": "go"}))
        self.assertFalse(is_explicit_user_input({"type": "USER_INPUT", "source": "SYSTEM", "content": "go"}))
        self.assertFalse(is_explicit_user_input({"type": "USER_INPUT", "content": "※ sage: fix it"}))

    def test_turn_identity_and_tool_scoping_without_source_field(self):
        self._write([
            {"type": "USER_INPUT", "content": "continue", "created_at": "2026-08-20T10:00:00Z", "step_index": 1},
            {"type": "PLANNER_RESPONSE", "content": "a", "tool_calls": [{"name": "write_to_file"}]},
            {"type": "USER_INPUT", "content": "continue", "created_at": "2026-08-20T11:00:00Z", "step_index": 3},
            {"type": "PLANNER_RESPONSE", "content": "b", "tool_calls": [{"name": "write_to_file"}]},
        ])
        self.assertEqual(get_active_turn_identity(self.path), "step:3")
        self.assertEqual(len(extract_turn_tool_calls(self.path)), 1)

    def test_turn_identity_falls_back_to_created_at_then_line(self):
        self._write([{"type": "USER_INPUT", "content": "hi", "created_at": "2026-08-20T10:00:00Z"}])
        self.assertEqual(get_active_turn_identity(self.path), "created:2026-08-20T10:00:00Z")
        self._write([{"type": "USER_INPUT", "content": "hi"}])
        self.assertEqual(get_active_turn_identity(self.path), "line:1")


class TestTurnResetOnRepeatedPrompt(unittest.TestCase):
    def test_repeated_prompt_is_a_new_turn_not_a_stale_recap_latch(self):
        conv_id = f"p1_turnreset_{int(time.time() * 1000)}"
        d = tempfile.mkdtemp()
        path = os.path.join(d, "transcript.jsonl")
        turn1 = [
            {"type": "USER_INPUT", "content": "continue", "created_at": "2026-08-20T10:00:00Z", "step_index": 1},
            {"type": "PLANNER_RESPONSE", "content": "a", "tool_calls": [{"name": "write_to_file"}]},
        ]

        def write(rows):
            with open(path, "w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row) + "\n")

        write(turn1)
        _, state_file, state, _ = load_and_sync_session_state(conv_id, path, "continue")
        record_sage_recap(state_file, state, 1, 2, recap_text="[RECAP·general] done")
        try:
            write(turn1 + [
                {"type": "USER_INPUT", "content": "continue", "created_at": "2026-08-20T11:00:00Z", "step_index": 3},
                {"type": "PLANNER_RESPONSE", "content": "b", "tool_calls": [{"name": "write_to_file"}]},
            ])
            _, _, state2, is_same = load_and_sync_session_state(conv_id, path, "continue")
            self.assertFalse(is_same)
            self.assertFalse(state2.get("recap_emitted"))
            self.assertEqual(state2.get("mid_turn_steers"), 0)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)


class TestEmissionCeilingIsCategoryIndependent(unittest.TestCase):
    def test_relabelling_the_category_cannot_reissue_the_same_action(self):
        seen, emitted = {}, 0
        for category in ("general", "missing_proof", "scope_drift", "fake_verification",
                         "architectural_trap", "algorithmic_bottleneck", "loop_detection", "general"):
            res = classify_advice(
                {"status": "off_track", "category": category, "action": "Run `pytest -q`",
                 "confidence": 0.9, "evidence": "tests unrun"}, seen_advice=seen)
            seen = res["seen"]
            emitted += res["decision"] == "steer"
        self.assertLessEqual(emitted, 2)

    def test_guidance_only_advice_is_also_category_independent(self):
        seen, emitted = {}, 0
        for category in ("general", "scope_drift", "architectural_trap", "general"):
            res = classify_advice(
                {"status": "off_track", "category": category, "guidance": "Stop guessing; read the schema",
                 "confidence": 0.9}, seen_advice=seen)
            seen = res["seen"]
            emitted += res["decision"] == "steer"
        self.assertLessEqual(emitted, 2)

    def test_distinct_actions_still_steer_freely(self):
        seen, emitted = {}, 0
        for i in range(5):
            res = classify_advice(
                {"status": "off_track", "category": "general", "action": f"Fix `module_{i}.py`",
                 "confidence": 0.9}, seen_advice=seen)
            seen = res["seen"]
            emitted += res["decision"] == "steer"
        self.assertEqual(emitted, 5)


class TestIdenticalTextBackstop(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "transcript.jsonl")
        rows = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "do it"},
            {"type": "PLANNER_RESPONSE", "content": "working", "tool_calls": [{"name": "write_to_file"}]},
        ]
        with open(self.path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    def _flow(self, state, verdict):
        import sage.policies as policies
        original = policies.evaluate_mid_turn_progress
        policies.evaluate_mid_turn_progress = lambda *a, **k: verdict
        try:
            return sage_flow(
                "final", conv_id="c", transcript_path=self.path, clean_prompt="do it",
                initial_line_count=2, total_tool_calls=99, turn_tool_names=set(),
                user_prompt="do it", agent_steps=[], git_diff="", state=state)
        finally:
            policies.evaluate_mid_turn_progress = original

    def test_repeat_of_an_already_emitted_text_is_deduplicated(self):
        verdict = {"status": "off_track", "category": "general", "action": "Run `pytest -q`", "confidence": 0.9}
        first = self._flow({}, verdict)
        self.assertEqual(first["action"], "emit")
        repeat = self._flow({"sage_emitted_texts": [first["text"]]}, verdict)
        self.assertEqual(repeat["action"], "hold_dedup")

    def test_unseen_text_is_not_suppressed(self):
        verdict = {"status": "off_track", "category": "general", "action": "Run `pytest -q`", "confidence": 0.9}
        res = self._flow({"sage_emitted_texts": ["[STEER·general] Something else"]}, verdict)
        self.assertEqual(res["action"], "emit")


class TestConversationAttributionGuard(unittest.TestCase):
    def test_ambiguous_attribution_returns_none(self):
        d = tempfile.mkdtemp()
        before = set(os.listdir(d))
        Path(d, "sage-spawn.db").touch()
        self.assertEqual(_find_new_conv_id(d, before), "sage-spawn")
        Path(d, "user-live-session.db").touch()
        self.assertIsNone(_find_new_conv_id(d, before))

    def test_no_new_db_and_missing_dir_are_safe(self):
        d = tempfile.mkdtemp()
        self.assertIsNone(_find_new_conv_id(d, set(os.listdir(d))))
        self.assertIsNone(_find_new_conv_id(os.path.join(d, "absent"), set()))


class TestCommandTimerStateIsolation(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = dict(os.environ, XDG_RUNTIME_DIR=self.dir)
        self.state_dir = Path(self.dir) / "agy_cmd_timer"
        self.conv = "p1-timer"
        self.hashed = hashlib.sha256(self.conv.encode("utf-8")).hexdigest()[:24]

    def _run(self, action, payload):
        return subprocess.run(
            ["python3", str(HOOK_SCRIPT), action], input=json.dumps(payload),
            text=True, capture_output=True, env=self.env, check=True)

    def _feedback(self):
        f = self.state_dir / f"feedback_{self.hashed}.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []

    def _start(self, step_idx, command):
        self._run("pre_tool", {"conversationId": self.conv, "stepIdx": step_idx,
                               "toolCall": {"args": {"CommandLine": command}}})

    def _age_start(self, path, mono_seconds=0.0, wall_seconds=0.0):
        state = json.loads(path.read_text(encoding="utf-8"))
        state["startMonoNs"] -= int(mono_seconds * 1_000_000_000)
        state["startWall"] -= wall_seconds
        path.write_text(json.dumps(state), encoding="utf-8")

    def test_completed_cycle_leaves_no_reusable_start_record(self):
        self._start(1, "ls")
        self._run("post_tool", {"conversationId": self.conv, "stepIdx": 1})
        self.assertEqual(list(self.state_dir.glob("state_*")), [])
        self.assertEqual(self._feedback(), [])

    def test_mismatched_step_does_not_fabricate_a_violation(self):
        self._start(5, "sleep 1")
        (self.state_dir / f"state_{self.hashed}_step_5.json").unlink(missing_ok=True)
        self._age_start(self.state_dir / f"state_{self.hashed}_latest.json", mono_seconds=3000.0)
        self._run("post_tool", {"conversationId": self.conv, "stepIdx": 99})
        self.assertEqual(self._feedback(), [])

    def test_orphaned_start_record_is_rejected(self):
        self._start(7, "make all")
        self._age_start(self.state_dir / f"state_{self.hashed}_step_7.json",
                        mono_seconds=3000.0, wall_seconds=200000.0)
        self._run("post_tool", {"conversationId": self.conv, "stepIdx": 7})
        self.assertEqual(self._feedback(), [])

    def test_genuine_slow_command_is_still_reported(self):
        self._start(8, "pytest -q")
        self._age_start(self.state_dir / f"state_{self.hashed}_step_8.json", mono_seconds=200.0)
        self._run("post_tool", {"conversationId": self.conv, "stepIdx": 8})
        items = self._feedback()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["tier"], "HEAVY_RECOMMEND_BACKGROUND")
        self.assertEqual(items[0]["command"], "pytest -q")

    def test_corrupt_feedback_file_is_dropped_not_replayed(self):
        feedback = self.state_dir / f"feedback_{self.hashed}.json"
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        feedback.write_text("{not json", encoding="utf-8")
        res = self._run("pre_invocation", {"conversationId": self.conv})
        self.assertEqual(json.loads(res.stdout), {"injectSteps": []})
        self.assertFalse(feedback.exists())


class TestP0AndP1BugFixes(unittest.TestCase):
    def test_fenced_json_preamble_does_not_override_real_verdict(self):
        from sage.executor import extract_json_from_llm_output
        raw = (
            "Here is the schema I will follow:\n"
            "```json\n"
            '{"example": "ignore me", "note": "template only"}\n'
            "```\n"
            "Now the real verdict:\n"
            '{"status": "off_track", "category": "fake_verification", "action": "run the real binary", "confidence": 0.95}\n'
        )
        parsed = extract_json_from_llm_output(raw, schema_keys=("status", "healthy", "category"))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.get("status"), "off_track")
        self.assertEqual(parsed.get("category"), "fake_verification")
        self.assertEqual(parsed.get("action"), "run the real binary")

    def test_inter_agent_messages_are_not_user_inputs(self):
        steps = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Real user request"},
            {"type": "USER_INPUT", "source": "USER", "content": "[Message] sender=implementer_1 finished module A"},
            {"type": "USER_INPUT", "content": "Subagent worker_2 has gone idle"},
            {"type": "USER_INPUT", "content": "※ sage: [WATCH·general] Something"},
        ]
        self.assertTrue(is_explicit_user_input(steps[0]))
        self.assertFalse(is_explicit_user_input(steps[1]))
        self.assertFalse(is_explicit_user_input(steps[2]))
        self.assertFalse(is_explicit_user_input(steps[3]))

    def test_subagent_invoke_failure_drops_first_batch(self):
        steps = [
            _invoke([{"Role": "Scout"}]),
            _invoke([{"Role": "Implementer"}]),
            {"type": "GENERIC", "content": "Error: failed to invoke subagent (quota)"},
        ]
        active = get_active_subagents(steps, conv_id="parent")
        roles = [a["role"] for a in active]
        self.assertEqual(roles, ["Implementer"])

    def test_final_stop_with_pinned_goal_preserves_recap_and_on_track(self):
        ver_res = {
            "status": "on_track",
            "task_complexity": "complex_code",
            "pinned_goal": "Refactor auth module",
            "recap": "All work done and verified with pytest.",
            "confidence": 0.95,
        }
        res = classify_advice(ver_res, mode="final")
        self.assertEqual(res["decision"], "hold")
        self.assertEqual(res["status"], "on_track")
        self.assertEqual(res["recap"], "All work done and verified with pytest.")

    def test_cleanup_preserves_active_session_state_json(self):
        from sage.locking import cleanup_stale_tmp_files
        test_state = "/tmp/agy_sage_test_preserve.json"
        with open(test_state, "w", encoding="utf-8") as f:
            json.dump({"pinned_goal": "Preserve me"}, f)
        past = time.time() - 8000
        os.utime(test_state, (past, past))
        try:
            cleanup_stale_tmp_files(max_age_seconds=7200, state_max_age_seconds=604800)
            self.assertTrue(os.path.exists(test_state))
        finally:
            if os.path.exists(test_state):
                os.remove(test_state)


if __name__ == "__main__":
    unittest.main()
