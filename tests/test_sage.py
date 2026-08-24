#!/usr/bin/env python3
"""
tests.test_sage - Unit tests for mid-turn progress verifier and prompt handling.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sage.sage import (
    _clear_sage_session,
    _normalize_sage_dict,
    build_sage_prompt,
    evaluate_mid_turn_progress,
    get_or_create_sage_session,
    parse_sage_output,
    run_sage_model,
    save_sage_session,
    # Backward-compatible aliases
    _clear_advisor_session,
    _normalize_advisor_dict,
    build_advisor_prompt,
    get_or_create_advisor_session,
    parse_advisor_output,
    run_advisor_model,
    save_advisor_session,
)


class TestSage(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.conv_id = "test_sage_conv_123"

    def tearDown(self):
        _clear_sage_session(self.conv_id)
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_tool_interval_threshold_triggering(self):
        state = {"last_verified_tools": 0}
        # Below interval (5 < 10) -> skipped
        res = evaluate_mid_turn_progress(
            self.conv_id, None, 5, {"write_to_file"}, "Fix bug", ["edit"], "", state, is_forced=False
        )
        self.assertTrue(res.get("healthy"))
        self.assertTrue(res.get("skipped"))
        self.assertEqual(res.get("tool_delta"), 5)

        # Forced override -> executes model
        mock_output = {"healthy": True, "blind_spots": [], "guidance": ""}
        with patch("sage.sage.run_sage_model", return_value=mock_output) as mock_run:
            res_forced = evaluate_mid_turn_progress(
                self.conv_id, None, 5, {"write_to_file"}, "Fix bug", ["edit"], "", state, is_forced=True
            )
            mock_run.assert_called_once()
            self.assertTrue(res_forced.get("healthy"))
            self.assertFalse(res_forced.get("skipped", False))

        # At or above interval (10 >= 10) -> executes model
        with patch("sage.sage.run_sage_model", return_value=mock_output) as mock_run:
            res_interval = evaluate_mid_turn_progress(
                self.conv_id, None, 10, {"write_to_file"}, "Fix bug", ["edit"], "", state, is_forced=False
            )
            mock_run.assert_called_once()
            self.assertTrue(res_interval.get("healthy"))

    def test_parse_sage_output_formats(self):
        # 1. Clean JSON
        raw1 = '{"healthy": true}'
        self.assertEqual(parse_sage_output(raw1), {"healthy": True, "blind_spots": [], "guidance": "", "status": "on_track"})

        # 2. Markdown fenced JSON
        raw2 = '```json\n{"healthy": false, "blind_spots": ["infinite loop"], "guidance": "break condition"}\n```'
        self.assertEqual(
            parse_sage_output(raw2),
            {"healthy": False, "blind_spots": ["infinite loop"], "guidance": "break condition", "status": "off_track"},
        )

        # 3. Embedded JSON in text
        raw3 = 'Analysis complete. Result: {"healthy": false, "blind_spots": ["drift"], "guidance": "focus on task"} - end of analysis.'
        self.assertEqual(
            parse_sage_output(raw3),
            {"healthy": False, "blind_spots": ["drift"], "guidance": "focus on task", "status": "off_track"},
        )

        # 4. Tri-state JSON formats (watchout, off_track, on_track)
        raw_watchout = '{"status": "watchout", "watchouts": ["potential migration trap"], "guidance": "check column alias"}'
        parsed_w = parse_sage_output(raw_watchout)
        self.assertEqual(parsed_w.get("status"), "watchout")
        self.assertEqual(parsed_w.get("watchouts"), ["potential migration trap"])
        self.assertEqual(parsed_w.get("guidance"), "check column alias")
        self.assertTrue(parsed_w.get("healthy"))

        raw_offtrack = '{"status": "off_track", "blind_spots": ["guessing column"], "guidance": "check schema"}'
        parsed_off = parse_sage_output(raw_offtrack)
        self.assertEqual(parsed_off.get("status"), "off_track")
        self.assertFalse(parsed_off.get("healthy"))
        self.assertEqual(parsed_off.get("blind_spots"), ["guessing column"])

        raw_ontrack = '{"status": "on_track"}'
        parsed_on = parse_sage_output(raw_ontrack)
        self.assertEqual(parsed_on.get("status"), "on_track")
        self.assertTrue(parsed_on.get("healthy"))

        # Hyphenated / spaced status
        raw_hyphen = '{"status": "off-track", "blind_spots": ["loop"], "guidance": "fix"}'
        parsed_hyphen = parse_sage_output(raw_hyphen)
        self.assertEqual(parsed_hyphen.get("status"), "off_track")
        self.assertFalse(parsed_hyphen.get("healthy"))

        # Watchout with guidance only (no watchouts list)
        raw_w_guidance = '{"status": "watchout", "guidance": "check column alias"}'
        parsed_wg = parse_sage_output(raw_w_guidance)
        self.assertEqual(parsed_wg.get("status"), "watchout")
        self.assertEqual(parsed_wg.get("guidance"), "check column alias")

        # Empty watchout -> normalized to on_track
        raw_bare_w = '{"status": "watchout"}'
        parsed_bare = parse_sage_output(raw_bare_w)
        self.assertEqual(parsed_bare.get("status"), "on_track")
        self.assertTrue(parsed_bare.get("healthy"))

        # CamelCase / compact status normalization
        raw_camel_off = '{"status": "offTrack", "blind_spots": ["loop"], "guidance": "fix"}'
        parsed_camel_off = parse_sage_output(raw_camel_off)
        self.assertEqual(parsed_camel_off.get("status"), "off_track")
        self.assertFalse(parsed_camel_off.get("healthy"))

        raw_upper_off = '{"status": "OFFTRACK", "blind_spots": ["drift"]}'
        parsed_upper_off = parse_sage_output(raw_upper_off)
        self.assertEqual(parsed_upper_off.get("status"), "off_track")

        raw_camel_on = '{"status": "onTrack"}'
        parsed_camel_on = parse_sage_output(raw_camel_on)
        self.assertEqual(parsed_camel_on.get("status"), "on_track")

        # Destructive command suppression in guidance
        raw_destructive = '{"status": "off_track", "guidance": "run rm -rf / to fix"}'
        parsed_dest = parse_sage_output(raw_destructive)
        self.assertEqual(parsed_dest.get("status"), "off_track")
        self.assertIn("[Destructive command suppressed]", parsed_dest.get("guidance"))

        # Destructive command suppression in action field
        raw_destructive_action = '{"status": "off_track", "action": "rm -rf /tmp/test", "guidance": "clean files"}'
        parsed_dest_act = parse_sage_output(raw_destructive_action)
        self.assertEqual(parsed_dest_act.get("action"), "[Destructive action suppressed] Use safe verification.")

        # Destructive SQL drop in action
        raw_destructive_sql = '{"status": "watchout", "action": "DROP DATABASE production", "guidance": "reset database"}'
        parsed_dest_sql = parse_sage_output(raw_destructive_sql)
        self.assertEqual(parsed_dest_sql.get("action"), "[Destructive action suppressed] Use safe verification.")

        # Simultaneous destructive action and destructive guidance
        raw_destructive_both = '{"status": "off_track", "action": "git reset --hard HEAD~1", "guidance": "sudo rm -rf /"}'
        parsed_dest_both = parse_sage_output(raw_destructive_both)
        self.assertEqual(parsed_dest_both.get("action"), "[Destructive action suppressed] Use safe verification.")
        self.assertIn("[Destructive command suppressed]", parsed_dest_both.get("guidance"))

        # Every sage field that can reach emitted text must be sanitized.
        raw_destructive_metadata = json.dumps({
            "status": "off_track",
            "blind_spots": ["Run rm -rf /tmp/project"],
            "watchouts": ["DROP TABLE production"],
            "evidence": "git reset --hard HEAD~1",
            "confidence": 0.99,
        })
        parsed_metadata = parse_sage_output(raw_destructive_metadata)
        for field in ("blind_spots", "watchouts"):
            self.assertIn("[Destructive command suppressed]", parsed_metadata[field][0])
        self.assertIn("[Destructive command suppressed]", parsed_metadata["evidence"])
        self.assertNotIn("rm -rf", json.dumps(parsed_metadata))
        self.assertNotIn("DROP TABLE", json.dumps(parsed_metadata))
        self.assertNotIn("reset --hard", json.dumps(parsed_metadata))

        # Structured categories and confidence preservation
        raw_structured = '{"status": "watchout", "category": "parallelize_subagent", "action": "invoke_subagent", "confidence": 0.85, "evidence": "two disjoint tasks", "guidance": "dispatch workers"}'
        parsed_struct = parse_sage_output(raw_structured)
        self.assertEqual(parsed_struct.get("category"), "parallelize_subagent")
        self.assertEqual(parsed_struct.get("confidence"), 0.85)
        self.assertEqual(parsed_struct.get("evidence"), "two disjoint tasks")

        # 5. Malformed / Empty output -> safe failover
        self.assertEqual(parse_sage_output(""), {"healthy": True, "blind_spots": [], "guidance": "", "status": "on_track"})
        self.assertEqual(parse_sage_output("Invalid text not json"), {"healthy": True, "blind_spots": [], "guidance": "", "status": "on_track"})
        self.assertEqual(parse_sage_output('{"unknown": 123}'), {"healthy": True, "blind_spots": [], "guidance": "", "status": "on_track"})

    def test_target_goal_survives_long_session_history(self):
        """user_prompt is SESSION HISTORY + goal; front-slicing pinned stale prior requests."""
        from sage.sage import build_sage_prompt, extract_target_goal
        goal = "MIGRATE billing schema to v2"
        priors = [f"Prior request {i} " + "z" * 180 for i in range(1, 21)]
        hist = "\n".join(f"- Prior request {i+1}: {p[:200]}" for i, p in enumerate(priors))
        composite = f"SESSION HISTORY:\n{hist}\n\n[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\n{goal}"
        self.assertEqual(extract_target_goal(composite), goal)
        self.assertIn(goal, build_sage_prompt("c", composite, "steps", is_update=True))
        self.assertIn(goal, build_sage_prompt("c", composite, "steps", is_update=False))
        self.assertEqual(extract_target_goal(f"[LATEST ACTIVE USER REQUEST]:\n{goal}"), goal)

    def test_delta_prompt_is_self_contained_on_resume_failure(self):
        """A cleared session replays the delta prompt as a fresh conversation; it must carry the schema."""
        from sage.sage import build_sage_prompt
        p = build_sage_prompt("c", "[LATEST ACTIVE USER REQUEST]:\ndo work", "steps", is_update=True)
        for token in ("on_track", "watchout", "off_track", "Status legend"):
            self.assertIn(token, p)

    def test_git_diff_keeps_status_summary_and_patch_tail(self):
        from sage.sage import build_sage_prompt
        diff = "Workspace (/repo):\nStatus:\nM a.py, D c.py\nDiff:\n" + "\n".join(f"+line {i} " + "q" * 80 for i in range(200))
        p = build_sage_prompt("c", "goal", "s", is_update=True, git_diff=diff)
        self.assertIn("M a.py", p)
        self.assertIn("line 199", p)
        self.assertIn("[diff truncated]", p)

    def test_normalizer_always_exposes_status(self):
        for payload, want in (
            ({"watchouts": ["x"], "guidance": "y"}, "watchout"),
            ({"blind_spots": ["x"]}, "off_track"),
            ({"healthy": False}, "off_track"),
            ({"unknown": 1}, "on_track"),
            ({"status": "off-track", "blind_spots": ["l"]}, "off_track"),
            ({"status": "OFF TRACK", "blind_spots": ["l"]}, "off_track"),
        ):
            with self.subTest(payload=payload):
                self.assertEqual(_normalize_sage_dict(payload).get("status"), want)

    def test_model_failure_is_distinguishable_from_on_track(self):
        """default_on_failure must carry status=error so runner can preserve the interval window."""
        import sage.sage as adv
        captured = {}

        def fake_cascade(conv, prompt, prefixes, norm, default_on_failure=None, **kw):
            captured["default"] = default_on_failure
            return default_on_failure

        orig = adv.run_model_cascade
        adv.run_model_cascade = fake_cascade
        try:
            res = adv.run_sage_model("conv-x", "goal", "steps")
        finally:
            adv.run_model_cascade = orig
        self.assertEqual(captured["default"].get("status"), "error")
        self.assertEqual(res.get("status"), "error")
        self.assertTrue(res.get("healthy"))

    def test_session_persistence_lifecycle(self):
        self.assertIsNone(get_or_create_sage_session(self.conv_id))
        save_sage_session(self.conv_id, "verifier_session_abc")
        self.assertEqual(get_or_create_sage_session(self.conv_id), "verifier_session_abc")
        _clear_sage_session(self.conv_id)
        self.assertIsNone(get_or_create_sage_session(self.conv_id))

    def test_session_state_structural_fingerprint_persistence(self):
        from sage.session_state import save_session_state, load_and_sync_session_state, get_state_file_path
        from sage.transcript import get_active_turn_identity, clean_user_prompt
        import hashlib
        state_file = get_state_file_path("conv_fp_test")
        try:
            prompt = "user prompt"
            clean_p = clean_user_prompt(prompt)
            turn_id = get_active_turn_identity("/tmp/nonexistent_transcript.jsonl")
            p_hash = hashlib.md5(clean_p.encode("utf-8")).hexdigest()
            t_key = hashlib.sha256(f"{turn_id}\x00{clean_p}".encode("utf-8")).hexdigest()
            state = {"turn_key": t_key, "prompt_hash": p_hash}
            save_session_state(state_file, state, last_par_cats=["disjoint_files"], last_par_fp=[["disjoint_files"], ["2 dirs"]])
            _, _, reloaded, is_same = load_and_sync_session_state("conv_fp_test", "/tmp/nonexistent_transcript.jsonl", prompt)
            self.assertTrue(is_same)
            self.assertEqual(reloaded.get("last_par_cats"), ["disjoint_files"])
            self.assertEqual(reloaded.get("last_par_fp"), [["disjoint_files"], ["2 dirs"]])

            # On new turn, structural fingerprint resets
            _, _, reloaded_new, is_same_new = load_and_sync_session_state("conv_fp_test", "/tmp/nonexistent_transcript.jsonl", "new distinct prompt")
            self.assertFalse(is_same_new)
            self.assertEqual(reloaded_new.get("last_par_cats"), [])
            self.assertEqual(reloaded_new.get("last_par_fp"), [])
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_build_sage_prompt(self):
        prompt = build_sage_prompt(
            conv_id="conv_xyz",
            user_prompt="Build feature X",
            agent_steps_summary="Step 1: ran test\nStep 2: edited file",
            is_update=False,
            git_diff="diff --git a/foo.py b/foo.py",
        )
        self.assertIn("conv_xyz", prompt)
        self.assertIn("Build feature X", prompt)
        self.assertIn("Step 1: ran test", prompt)
        self.assertIn("diff --git a/foo.py", prompt)

        prompt_update = build_sage_prompt(
            conv_id="conv_xyz",
            user_prompt="Build feature X",
            agent_steps_summary="Step 3: more work",
            is_update=True,
        )
        self.assertIn("SAGE UPDATE", prompt_update)

    @patch("subprocess.run")
    @patch("sage.sage.clean_resume_history")
    def test_run_sage_model_success(self, mock_clean, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '{"healthy": false, "blind_spots": ["syntax error"], "guidance": "fix syntax"}'
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        res = run_sage_model(self.conv_id, "Fix bug", "Ran tools", git_diff="")
        self.assertFalse(res["healthy"])
        self.assertEqual(res["blind_spots"], ["syntax error"])
        self.assertEqual(res["guidance"], "fix syntax")

    @patch("subprocess.run")
    def test_run_sage_model_cli_error_failover(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "Model quota exceeded"
        mock_run.return_value = mock_proc

        res = run_sage_model(self.conv_id, "Fix bug", "Ran tools", git_diff="")
        self.assertTrue(res["healthy"])
        self.assertEqual(res["blind_spots"], [])

    @patch("subprocess.run")
    def test_run_sage_model_fallback_cascade_when_primary_fails(self, mock_run):
        proc_fail = MagicMock()
        proc_fail.returncode = 1
        proc_fail.stdout = ""
        proc_fail.stderr = "Model not supported"

        proc_success = MagicMock()
        proc_success.returncode = 0
        proc_success.stdout = json.dumps({"healthy": False, "blind_spots": ["regression"], "guidance": "run tests"})
        proc_success.stderr = ""

        mock_run.side_effect = [proc_fail, proc_success]

        res = run_sage_model(self.conv_id, "Fix bug", "Ran tools", git_diff="")
        self.assertFalse(res["healthy"])
        self.assertEqual(res["blind_spots"], ["regression"])
        self.assertEqual(res["guidance"], "run tests")
        self.assertGreaterEqual(mock_run.call_count, 2)

    @patch("sage.sage.resolve_model_candidates", return_value=["modelA", "modelB"])
    @patch("sage.sage.acquire_spawn_lock")
    @patch("time.time")
    @patch("subprocess.run")
    def test_run_sage_model_overall_timeout_budget_halts_fallbacks(self, mock_run, mock_time, _mock_lock, _mock_cands):
        mock_time.side_effect = [0.0, 0.0, 0.0] + [119.0] * 10
        proc_fail = MagicMock()
        proc_fail.returncode = 1
        proc_fail.stdout = ""
        proc_fail.stderr = "Model error"
        mock_run.return_value = proc_fail

        res = run_sage_model(self.conv_id, "Fix bug", "Ran tools", git_diff="")
        self.assertTrue(res["healthy"])
        self.assertEqual(mock_run.call_count, 1)

    @patch("sage.sage.resolve_model_candidates", return_value=["modelA", "modelB"])
    @patch("sage.sage.get_or_create_sage_session", return_value="failed_sage_sess")
    @patch("sage.sage.acquire_spawn_lock")
    @patch("subprocess.run")
    def test_run_sage_model_acquires_spawn_lock_when_resume_fails(self, mock_run, mock_lock, _mock_sess, _mock_cands):
        proc_fail = MagicMock()
        proc_fail.returncode = 1
        proc_fail.stdout = ""
        proc_fail.stderr = "Resume session error"

        proc_success = MagicMock()
        proc_success.returncode = 0
        proc_success.stdout = json.dumps({"healthy": True})

        mock_run.side_effect = [proc_fail, proc_success]
        mock_lock.return_value = MagicMock()

        res = run_sage_model(self.conv_id, "Fix bug", "Ran tools", git_diff="")
        self.assertTrue(res["healthy"])
        self.assertTrue(mock_lock.called)

    def test_parse_sage_output_recap_field(self):
        # On track with custom recap
        raw = json.dumps({
            "status": "on_track",
            "category": "general",
            "recap": "Built auth middleware, 25 tests pass, verified live curl localhost:8080",
            "confidence": 0.98,
        })
        parsed = parse_sage_output(raw)
        self.assertEqual(parsed.get("status"), "on_track")
        self.assertTrue(parsed.get("healthy"))
        self.assertEqual(parsed.get("recap"), "Built auth middleware, 25 tests pass, verified live curl localhost:8080")

        # Destructive command inside recap gets sanitized
        raw_destructive = json.dumps({
            "status": "on_track",
            "category": "general",
            "recap": "Ran rm -rf / and finished all work cleanly.",
        })
        parsed_dest = parse_sage_output(raw_destructive)
        self.assertNotIn("rm -rf /", parsed_dest.get("recap"))
        self.assertIn("[Destructive command suppressed]", parsed_dest.get("recap"))

    def test_mid_task_context_fatigue_delegation_signal(self):
        from sage.task_structure import get_parallelizable_signals
        steps = [
            {"type": "USER_INPUT", "source": "USER", "content": "Refactor module and run test suites"},
        ]
        # 13 tool calls in the turn modifying files across disjoint directories
        for i in range(13):
            steps.append({
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": f"/tmp/pkg/{'api' if i % 2 else 'core'}/file_{i}.py"}}],
            })
        sig = get_parallelizable_signals(steps)
        self.assertTrue(sig.get("parallelizable"))
        self.assertIn("context_fatigue_delegation", sig.get("categories", []))
        self.assertIn("Implementer", sig.get("suggested_roles", []))
        self.assertIn("QA", sig.get("suggested_roles", []))

    def test_signal_priority_preserves_error_loop_over_fatigue(self):
        from unittest.mock import patch
        import sage.policies as policies

        captured = {}
        def fake_eval(*a, **k):
            captured.update(k)
            return {"status": "on_track"}

        par = {
            "parallelizable": True,
            "categories": ["context_fatigue_delegation"],
            "signal_text": "PARALLELIZABLE: Delegation opportunity (mid-task tool accumulation (14 tools)). Suggest invoke_subagent with roles: Implementer, QA.",
        }

        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
             patch.object(policies, "get_parallelizable_signals", return_value=par), \
             patch.object(policies, "calculate_turn_tool_score", return_value=(0.0, 0)), \
             patch.object(policies, "evaluate_mid_turn_progress", side_effect=fake_eval), \
             patch.object(policies, "has_new_user_activity", return_value=False), \
             patch.object(policies, "extract_session_and_turn_data", return_value=("", "", [], 14, set(), 0, 0, 0)), \
             patch.object(policies, "is_post_invocation_completion_candidate", return_value=False), \
             patch.object(policies, "classify_advice", return_value={"decision": "hold", "text": "ok", "seen": {}}):
            policies.sage_flow(
                "midturn", conv_id="c", transcript_path="/tmp/x.jsonl",
                clean_prompt="p", initial_line_count=0, total_tool_calls=14, turn_tool_names={"run_command"},
                user_prompt="u", agent_steps=[], git_diff="", state={},
                forced=True,
                signal_note="[EVT·error_loop s3] loop=1 · err=1\nASK root cause. exact fix cmd. NO blind retry.",
            )

        signals_sent = captured.get("signals", "")
        self.assertIn("error_loop", signals_sent)
        self.assertIn("PARALLELIZABLE", signals_sent)

    def test_pure_parallel_signal_formatting_with_fact_tag(self):
        from unittest.mock import patch
        import sage.policies as policies

        captured = {}
        def fake_eval(*a, **k):
            captured.update(k)
            return {"status": "on_track"}

        par = {
            "parallelizable": True,
            "categories": ["disjoint_files"],
            "signal_text": "PARALLELIZABLE: Independent workstreams detected (2 disjoint directories: api, core). Suggest invoke_subagent with roles: Implementer.",
        }

        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
             patch.object(policies, "get_parallelizable_signals", return_value=par), \
             patch.object(policies, "calculate_turn_tool_score", return_value=(0.0, 0)), \
             patch.object(policies, "evaluate_mid_turn_progress", side_effect=fake_eval), \
             patch.object(policies, "has_new_user_activity", return_value=False), \
             patch.object(policies, "extract_session_and_turn_data", return_value=("", "", [], 5, set(), 0, 0, 0)), \
             patch.object(policies, "is_post_invocation_completion_candidate", return_value=False), \
             patch.object(policies, "classify_advice", return_value={"decision": "hold", "text": "ok", "seen": {}}):
            policies.sage_flow(
                "midturn", conv_id="c", transcript_path="/tmp/x.jsonl",
                clean_prompt="p", initial_line_count=0, total_tool_calls=5, turn_tool_names={"write_to_file"},
                user_prompt="u", agent_steps=[], git_diff="", state={},
                forced=True,
                signal_note="",
            )

        signals_sent = captured.get("signals", "")
        self.assertIn("[EVT·parallel_opportunity s1]", signals_sent)
        self.assertIn("PARALLELIZABLE:", signals_sent)

    def test_get_parallelizable_signals_handles_malformed_tool_calls(self):
        from sage.task_structure import get_parallelizable_signals
        steps = [
            {"type": "USER_INPUT", "source": "USER", "content": "test"},
            None,
            "invalid_step",
            {"type": "PLANNER_RESPONSE", "tool_calls": None},
            {"type": "PLANNER_RESPONSE", "tool_calls": 123},
            {"type": "PLANNER_RESPONSE", "tool_calls": True},
            {"type": "PLANNER_RESPONSE", "tool_calls": [None, 123, "string", {}, {"name": "write_to_file", "args": {"TargetFile": "/tmp/a/b.py"}}]},
        ]
        sig = get_parallelizable_signals(steps)
        self.assertIsInstance(sig, dict)
        self.assertIn("parallelizable", sig)

    def test_single_file_fatigue_does_not_suggest_implementer(self):
        from sage.task_structure import get_parallelizable_signals
        steps = [{"type": "USER_INPUT", "source": "USER", "content": "fix bug"}]
        # 10 reads on same file + 2 pytest executions = 12 tools
        for _ in range(10):
            steps.append({"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "read_url_content", "args": {"Url": "http://x"}}]})
        steps.append({"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest tests/test_auth.py"}}]})
        steps.append({"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest tests/test_api.py"}}]})
        sig = get_parallelizable_signals(steps)
        self.assertTrue(sig["parallelizable"])
        self.assertIn("QA", sig["suggested_roles"])
        self.assertNotIn("Implementer", sig["suggested_roles"])
        self.assertIn("context_fatigue_delegation", sig["categories"])
        self.assertTrue(sig["signal_text"].startswith("PARALLELIZABLE: Independent workstreams detected"))

    def test_file_editing_tool_aliases_trigger_disjoint_files(self):
        from sage.task_structure import get_parallelizable_signals
        aliases = [
            "replace_file_content", "write_to_file", "write_file",
            "edit_file", "create_file", "notebook_edit", "patch", "apply_diff",
            "modify_file", "multi_replace_file_content",
        ]
        for alias in aliases:
            steps = [
                {"type": "USER_INPUT", "source": "USER", "content": "edit"},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": alias, "args": {"TargetFile": "/tmp/dirA/a.py"}}]},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": alias, "args": {"TargetFile": "/tmp/dirB/b.py"}}]},
            ]
            sig = get_parallelizable_signals(steps)
            self.assertIn("disjoint_files", sig["categories"], f"Failed for alias {alias}")
            self.assertIn("Implementer", sig["suggested_roles"])

    def test_exec_tool_aliases_trigger_test_detection(self):
        from sage.task_structure import get_parallelizable_signals
        exec_aliases = ["run_command", "bash", "exec", "terminal"]
        for alias in exec_aliases:
            steps = [
                {"type": "USER_INPUT", "source": "USER", "content": "run tests"},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/tmp/pkg/mod.py"}}]},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": alias, "args": {"CommandLine": "pytest tests/unit"}}]},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": alias, "args": {"CommandLine": "npm test"}}]},
            ]
            sig = get_parallelizable_signals(steps)
            self.assertIn("independent_verification", sig["categories"], f"Failed for exec alias {alias}")
            self.assertIn("QA", sig["suggested_roles"])

    def test_final_gate_diff_cnt_regex_extraction(self):
        from unittest.mock import patch
        import sage.policies as policies

        captured = {}
        def fake_eval(*a, **k):
            captured.update(k)
            return {"status": "healthy"}

        with patch.object(policies, "MID_TURN_SAGE_ENABLED", 1), \
             patch.object(policies, "evaluate_mid_turn_progress", side_effect=fake_eval), \
             patch.object(policies, "has_new_user_activity", return_value=False), \
             patch.object(policies, "extract_session_and_turn_data", return_value=("", "", [], 5, set(), 0, 0, 0)), \
             patch.object(policies, "classify_advice", return_value={"decision": "healthy", "text": "ok", "seen": {}}):
            policies.final_sage_gate(
                conv_id="c", transcript_path="/tmp/x.jsonl",
                clean_prompt="p", initial_line_count=0, total_tool_calls=5, turn_tool_names={"write_to_file"},
                user_prompt="u", agent_steps=[],
                git_diff="Workspace (/tmp/ws):\nStatus:\nM file.py\nChanged lines: 42 + (partial: >50 untracked files)\nDiff:\n+hello",
                state={},
            )

        signals_sent = captured.get("signals", "")
        self.assertIn("[EVT·final_stop s3]", signals_sent)
        self.assertIn("diff=~50L", signals_sent)

    def test_runner_emits_structured_error_loop_summon(self):
        import json
        from unittest.mock import patch
        from sage.runner import run_session_stop_audit

        payload = json.dumps({
            "conversationId": "test_conv_err_loop",
            "fullyIdle": True,
            "transcriptPath": "/tmp/dummy_trans.jsonl",
        })

        captured = {}
        def fake_flow(*a, **k):
            captured.update(k)
            return {"action": "healthy", "text": "ok"}

        with patch("sage.runner.acquire_conversation_lock", return_value=True), \
             patch("sage.runner.get_transcript_path", return_value="/tmp/dummy.jsonl"), \
             patch("sage.runner.extract_session_and_turn_data", return_value=("do work", "do work", [], 5, {"run_command"}, 0, 0, 10)), \
             patch("sage.runner.load_and_sync_session_state", return_value=("do work", "/tmp/state.json", {}, False)), \
             patch("sage.runner.get_active_subagents", return_value=[]), \
             patch("sage.runner.get_active_background_tasks", return_value=[]), \
             patch("sage.runner.is_subagent_session", return_value=False), \
             patch("sage.runner.is_post_invocation", return_value=True), \
             patch("sage.runner.is_post_invocation_completion_candidate", return_value=False), \
             patch("sage.runner.has_recent_tool_errors", return_value=True), \
             patch("sage.runner.has_repeated_tool_calls", return_value=False), \
             patch("sage.runner.advisor_flow", side_effect=fake_flow), \
             patch("sage.runner.save_session_state"), \
             patch("sys.exit", side_effect=SystemExit):
            try:
                run_session_stop_audit(payload)
            except SystemExit:
                pass

        sig = captured.get("signal_note", "")
        self.assertTrue(sig.startswith("[EVT·error_loop s3] err=1"), f"Unexpected sig: {sig}")
        self.assertIn("ASK root cause. exact fix cmd. NO blind retry.", sig)


if __name__ == "__main__":
    unittest.main()
