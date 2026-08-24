#!/usr/bin/env python3
"""
tests.test_tier5_challenger2 - Adversarial Coverage Hardening Suite (Tier 5 - Challenger 2).
White-box stress tests covering intelligence parsing, triage gating, task structure analysis,
multi-byte transcript corruptions, model selection cascades, statusline rendering,
and AST edge cases (lambdas, decorators, async, nested structures).
"""

import ast
import io
import json
import os
import shutil
import tempfile
import token
import tokenize
import unittest
from collections import defaultdict
from unittest.mock import patch

from statusline.statusline import (
    format_countdown,
    format_tokens,
    get_advisor_steer_badges,
    is_agent_active,
    render_statusline,
    visible_len,
)
from sage.sage import (
    _normalize_advisor_dict,
    build_advisor_prompt,
    extract_target_goal,
    parse_advisor_output,
)
from sage.models import (
    _expand_alias,
    cache_working_model,
    get_cached_working_model,
    parse_model_version,
    resolve_model_candidates,
)
from sage.task_structure import (
    _extract_file_path,
    _extract_research_target,
    _extract_test_target,
    get_parallelizable_signals,
)
from sage.transcript import (
    _read_transcript_steps,
    extract_session_and_turn_data,
    get_active_turn_identity,
    has_new_user_activity,
    has_recent_tool_errors,
    has_repeated_tool_calls,
    is_post_invocation_completion_candidate,
)
from sage.triage import (
    _parse_confidence,
    classify_advice,
    compute_advice_key,
)
from tests.test_static_analysis import PrintCallVisitor


class TestAdvisorParsingAdversarial(unittest.TestCase):
    """Adversarial testing of advisor output normalization and prompt building."""

    def test_json_inside_jsonc_codeblock(self):
        raw = """Here is the strategic assessment:
```jsonc
{
    "status": "watchout",
    "category": "architectural_trap",
    "action": "Use process-level flock in locking.py",
    "guidance": "Prevent race conditions in multi-agent environment.",
    "confidence": 0.88
}
```
"""
        parsed = parse_advisor_output(raw)
        self.assertEqual(parsed["status"], "watchout")
        self.assertEqual(parsed["category"], "architectural_trap")
        self.assertEqual(parsed["action"], "Use process-level flock in locking.py")

    def test_json_with_uppercase_codeblock_fence(self):
        raw = """```JSON
{"status": "off_track", "category": "loop_detection", "action": "Check test_models.py", "guidance": "Fix failing assertion"}
```"""
        parsed = parse_advisor_output(raw)
        self.assertEqual(parsed["status"], "off_track")
        self.assertFalse(parsed["healthy"])

    def test_unclosed_json_markdown_codeblock(self):
        raw = """```json
{"status": "on_track", "category": "general", "guidance": "Proceed with plan"}"""
        parsed = parse_advisor_output(raw)
        self.assertEqual(parsed["status"], "on_track")
        self.assertTrue(parsed["healthy"])

    def test_json_with_multibyte_utf8_and_emojis(self):
        raw = json.dumps({
            "status": "watchout",
            "category": "missing_deliverable",
            "action": "Tạo tệp báo cáo: `báo_cáo_tiến_độ_🚀.md`",
            "evidence": "Thiếu tài liệu bàn giao 🎯",
            "guidance": "Hoàn thiện tài liệu trước khi kết thúc turn.",
            "confidence": 0.95,
        })
        parsed = parse_advisor_output(raw)
        self.assertEqual(parsed["status"], "watchout")
        self.assertIn("🚀", parsed["action"])
        self.assertIn("🎯", parsed["evidence"])

    def test_advisor_status_case_and_alias_normalization(self):
        aliases = [
            ("WATCH-OUT", "watchout", True),
            ("off track", "off_track", False),
            ("HeadsUp", "watchout", True),
            ("  WATCHOUT  ", "watchout", True),
            ("interVention", "off_track", False),
            ("UnHealthy", "off_track", False),
            ("PASSED", "on_track", True),
            ("good", "on_track", True),
        ]
        for raw_val, exp_status, exp_healthy in aliases:
            with self.subTest(raw_val=raw_val):
                res = _normalize_advisor_dict({"status": raw_val, "action": "Check file.py"})
                self.assertEqual(res["status"], exp_status)
                self.assertEqual(res["healthy"], exp_healthy)

    def test_advisor_blind_spots_as_string_vs_list(self):
        res_str = _normalize_advisor_dict({"blind_spots": "Single string blindspot"})
        self.assertEqual(res_str["blind_spots"], ["Single string blindspot"])
        self.assertEqual(res_str["status"], "off_track")

        res_list = _normalize_advisor_dict({"blind_spots": ["Item 1", "Item 2"]})
        self.assertEqual(res_list["blind_spots"], ["Item 1", "Item 2"])

    def test_advisor_watchouts_singular_key_inclusion(self):
        res = _normalize_advisor_dict({
            "watchouts": ["Existing item"],
            "watchout": "Additional singular item",
            "status": "watchout",
        })
        self.assertIn("Existing item", res["watchouts"])
        self.assertIn("Additional singular item", res["watchouts"])

    def test_advisor_watchout_auto_downgrade_when_empty(self):
        res = _normalize_advisor_dict({
            "status": "watchout",
            "watchouts": [],
            "guidance": "",
            "action": "",
        })
        self.assertEqual(res["status"], "on_track")

    def test_destructive_action_suppression_in_advisor(self):
        res = _normalize_advisor_dict({
            "status": "off_track",
            "action": "rm -rf /tmp/data",
            "guidance": "git reset --hard HEAD~1",
        })
        self.assertIn("Destructive action suppressed", res["action"])
        self.assertIn("Destructive command suppressed", res["guidance"])

    def test_extract_target_goal_with_multiple_markers(self):
        prompt = (
            "SESSION HISTORY:\n- Prior request 1: Init\n\n"
            "[LATEST ACTIVE USER REQUEST]: Prior goal\n\n"
            "[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\n"
            "Implement adversarial suite for Tier 5"
        )
        goal = extract_target_goal(prompt)
        self.assertEqual(goal, "Implement adversarial suite for Tier 5")

    def test_extract_target_goal_with_empty_marker(self):
        prompt = "[LATEST ACTIVE USER REQUEST]:   \n\nFallback prompt body"
        goal = extract_target_goal(prompt)
        self.assertEqual(goal, "Fallback prompt body")

    def test_extract_target_goal_with_cjk_and_emojis(self):
        prompt = "[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\nKiểm tra mã nguồn và tối ưu hóa 🚀"
        goal = extract_target_goal(prompt)
        self.assertEqual(goal, "Kiểm tra mã nguồn và tối ưu hóa 🚀")

    def test_build_advisor_prompt_truncation_and_template_fallbacks(self):
        long_steps = "Step action log " * 500
        prompt = build_advisor_prompt("conv_123", "User request", long_steps, is_update=True, git_diff="diff --git a/f b/f", signals="SIG_1")
        self.assertIn("conv_123", prompt)
        self.assertIn("ACTIVE SIGNALS:\nSIG_1", prompt)
        self.assertLessEqual(len(prompt.split("AGENT ACTIONS (RECENT):\n")[1]), 6000)


class TestTriageAdversarial(unittest.TestCase):
    """Adversarial testing of triage classification, confidence parsing, and deduplication."""

    def test_confidence_parsing_booleans_not_numbers(self):
        self.assertIsNone(_parse_confidence(True))
        self.assertIsNone(_parse_confidence(False))

    def test_confidence_parsing_percentage_strings(self):
        self.assertEqual(_parse_confidence("95%"), 0.95)
        self.assertEqual(_parse_confidence(" 80 % "), 0.80)
        self.assertEqual(_parse_confidence("100%"), 1.0)
        self.assertEqual(_parse_confidence("0%"), 0.0)
        self.assertEqual(_parse_confidence("75.5%"), 0.755)

    def test_confidence_parsing_extreme_and_invalid_floats(self):
        self.assertIsNone(_parse_confidence("NaN"))
        self.assertIsNone(_parse_confidence("Infinity"))
        self.assertIsNone(_parse_confidence("-inf"))
        self.assertIsNone(_parse_confidence("invalid_str"))
        self.assertEqual(_parse_confidence(0.7), 0.7)
        self.assertEqual(_parse_confidence("85"), 0.85)
        self.assertEqual(_parse_confidence(1.0), 1.0)
        self.assertEqual(_parse_confidence(0.0), 0.0)

    def test_off_track_demoted_to_watchout_on_low_confidence(self):
        advice = {
            "status": "off_track",
            "category": "loop_detection",
            "action": "Inspect logs",
            "confidence": 0.65,
        }
        res = classify_advice(advice)
        self.assertEqual(res["decision"], "watchout")
        self.assertEqual(res["status"], "watchout")

    def test_off_track_retained_on_none_confidence(self):
        advice = {
            "status": "off_track",
            "category": "loop_detection",
            "action": "Inspect logs",
            "confidence": None,
        }
        res = classify_advice(advice)
        self.assertEqual(res["decision"], "steer")
        self.assertEqual(res["status"], "off_track")

    def test_off_track_retained_on_exact_threshold(self):
        advice = {
            "status": "off_track",
            "category": "loop_detection",
            "action": "Inspect logs",
            "confidence": 0.70,
        }
        res = classify_advice(advice)
        self.assertEqual(res["decision"], "steer")
        self.assertEqual(res["status"], "off_track")

    def test_watchout_promoted_to_off_track_for_irreversible_risk(self):
        advice = {
            "status": "watchout",
            "category": "irreversible_risk",
            "action": "Stash uncommitted changes before reset",
            "confidence": 0.85,
        }
        res = classify_advice(advice)
        self.assertEqual(res["decision"], "steer")
        self.assertEqual(res["status"], "off_track")

    def test_watchout_not_promoted_on_sub_threshold_or_missing_action(self):
        # Sub-threshold confidence (0.84 < 0.85)
        res_sub = classify_advice({
            "status": "watchout",
            "category": "irreversible_risk",
            "action": "Stash changes",
            "confidence": 0.84,
        })
        self.assertEqual(res_sub["decision"], "watchout")

        # Missing action
        res_no_act = classify_advice({
            "status": "watchout",
            "category": "irreversible_risk",
            "action": "",
            "confidence": 0.95,
        })
        self.assertEqual(res_no_act["decision"], "watchout")

    def test_irreversible_risk_double_emission_budget(self):
        seen = {}
        advice = {
            "status": "off_track",
            "category": "irreversible_risk",
            "action": "Stash changes",
            "confidence": 0.90,
        }
        # max_emissions=2 -> effective_max = 4 for irreversible_risk
        for i in range(4):
            res = classify_advice(advice, seen_advice=seen, max_emissions=2)
            self.assertEqual(res["decision"], "steer", f"Failed on iteration {i}")
            seen = res["seen"]

        # 5th attempt is deduplicated
        res5 = classify_advice(advice, seen_advice=seen, max_emissions=2)
        self.assertEqual(res5["decision"], "hold_dedup")

    def test_standard_category_single_emission_dedup(self):
        seen = {}
        advice = {
            "status": "watchout",
            "category": "missing_deliverable",
            "action": "Create notes.md",
            "confidence": 0.80,
        }
        # First emission allowed
        res1 = classify_advice(advice, seen_advice=seen)
        self.assertEqual(res1["decision"], "watchout")

        # Second emission deduplicated
        res2 = classify_advice(advice, seen_advice=res1["seen"])
        self.assertEqual(res2["decision"], "hold_dedup")

    def test_escalated_advice_allows_multiple_emissions(self):
        seen = {}
        advice = {
            "status": "watchout",
            "category": "missing_deliverable",
            "action": "Create notes.md",
            "confidence": 0.80,
            "escalation": "ignored_advice",
        }
        res1 = classify_advice(advice, seen_advice=seen, max_emissions=2)
        self.assertEqual(res1["decision"], "watchout")

        res2 = classify_advice(advice, seen_advice=res1["seen"], max_emissions=2)
        self.assertEqual(res2["decision"], "watchout")

    def test_seen_ledger_eviction_at_cap_50(self):
        seen = {f"key_{i}": i for i in range(55)}
        advice = {
            "status": "watchout",
            "category": "loop_detection",
            "action": "Check file.py",
        }
        res = classify_advice(advice, seen_advice=seen)
        self.assertLessEqual(len(res["seen"]), 50)

    def test_compute_advice_key_normalization(self):
        k1 = compute_advice_key("loop_detection", "Run pytest tests/", "Fix assertion error")
        k2 = compute_advice_key("LOOP-DETECTION", "  run pytest   tests/  ", "fix assertion error.")
        self.assertEqual(k1, k2)

    def test_triage_text_length_and_formatting_invariants(self):
        advice = {
            "status": "off_track",
            "category": "architectural_trap",
            "action": "Refactor locking mechanism in " + "x" * 200,
            "evidence": "Evidence string " * 20,
            "guidance": "Guidance string " * 20,
            "confidence": 0.95,
        }
        res = classify_advice(advice)
        self.assertLessEqual(len(res["text"]), 2000)
        self.assertTrue(res["text"].startswith("[STEER·architectural_trap]"))


class TestTaskStructureAdversarial(unittest.TestCase):
    """Adversarial testing of task structure heuristics."""

    def test_extract_file_path_all_parameter_variations(self):
        cases = [
            ({"TargetFile": "/path/to/file1.py"}, "/path/to/file1.py"),
            ({"AbsolutePath": "/path/to/file2.py"}, "/path/to/file2.py"),
            ({"TargetFiles": ["/path/to/file3.py", "/path/to/file4.py"]}, "/path/to/file3.py"),
            ({"path": "/path/to/file5.py"}, "/path/to/file5.py"),
            ({"file": "/path/to/file6.py"}, "/path/to/file6.py"),
            ({"target_file": "/path/to/file7.py"}, "/path/to/file7.py"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(_extract_file_path(args), expected)

    def test_extract_research_target_all_variations(self):
        cases = [
            ("search_web", {"query": "python ast tutorial"}, "search_web:python ast tutorial"),
            ("read_url_content", {"Url": "https://docs.python.org"}, "read_url_content:https://docs.python.org"),
            ("grep_search", {"Pattern": "def visit_Call"}, "grep_search:def visit_Call"),
        ]
        for tool, args, expected in cases:
            with self.subTest(tool=tool, args=args):
                self.assertEqual(_extract_research_target(tool, args), expected)

    def test_extract_test_target_all_runners(self):
        runners = [
            ("pytest -v tests/test_a.py", "pytest"),
            ("python3 -m unittest discover", "unittest"),
            ("cargo test --all", "cargo test"),
            ("npm test -- --coverage", "npm test"),
            ("go test ./...", "go test"),
            ("vitest run", "vitest"),
            ("jest --runInBand", "jest"),
        ]
        for cmd, runner_name in runners:
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(_extract_test_target({"CommandLine": cmd}))

    def test_composite_parallel_signals_all_categories(self):
        steps = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {"name": "write_to_file", "args": {"TargetFile": "/app/module_a/code.py"}},
                    {"name": "write_to_file", "args": {"TargetFile": "/app/module_b/code.py"}},
                    {"name": "search_web", "args": {"query": "topic alpha"}},
                    {"name": "search_web", "args": {"query": "topic beta"}},
                    {"name": "run_command", "args": {"CommandLine": "pytest tests/test_a.py"}},
                    {"name": "run_command", "args": {"CommandLine": "pytest tests/test_b.py"}},
                ],
            }
        ]
        signals = get_parallelizable_signals(steps)
        self.assertTrue(signals["parallelizable"])
        self.assertIn("disjoint_files", signals["categories"])
        self.assertIn("isolated_research", signals["categories"])
        self.assertIn("independent_verification", signals["categories"])
        self.assertIn("Implementer", signals["suggested_roles"])
        self.assertIn("Scout", signals["suggested_roles"])
        self.assertIn("QA", signals["suggested_roles"])
        self.assertIn("PARALLELIZABLE", signals["signal_text"])



class TestTranscriptAdversarial(unittest.TestCase):
    """Adversarial testing of transcript parsing, encodings, corruptions, and heuristics."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_corrupt_transcript_with_null_bytes_and_invalid_utf8(self):
        tpath = os.path.join(self.temp_dir, "corrupt_transcript.jsonl")
        with open(tpath, "wb") as f:
            f.write(b'{"type": "USER_INPUT", "content": "Initial prompt"}\n')
            f.write(b'\xff\xfe\xfd\n')  # Invalid utf-8 byte sequence
            f.write(b'{"type": "PLANNER_RESPONSE", "content": "Working", "tool_calls": [{"name": "view_file"}]}\n')

        steps = _read_transcript_steps(tpath)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["type"], "USER_INPUT")
        self.assertEqual(steps[1]["type"], "PLANNER_RESPONSE")

    def test_corrupt_transcript_with_malformed_json_lines(self):
        tpath = os.path.join(self.temp_dir, "broken_lines.jsonl")
        with open(tpath, "w", encoding="utf-8") as f:
            f.write('{"type": "USER_INPUT", "content": "User request"}\n')
            f.write('\n   \n')
            f.write('{"incomplete json object\n')
            f.write('{"type": "PLANNER_RESPONSE", "content": "Done"}\n')

        steps = _read_transcript_steps(tpath)
        self.assertEqual(len(steps), 2)

    def test_multi_turn_session_history_formatting(self):
        tpath = os.path.join(self.temp_dir, "multi_turn.jsonl")
        with open(tpath, "w", encoding="utf-8") as f:
            f.write('{"type": "USER_INPUT", "content": "First turn request"}\n')
            f.write('{"type": "PLANNER_RESPONSE", "content": "Response 1"}\n')
            f.write('{"type": "USER_INPUT", "content": "Second turn request"}\n')
            f.write('{"type": "PLANNER_RESPONSE", "content": "Response 2"}\n')

        user_prompt, raw_prompt, agent_steps, total_tools, _, _, _, _ = extract_session_and_turn_data(tpath)
        self.assertIn("SESSION HISTORY:\n- Prior request 1: First turn request", user_prompt)
        self.assertIn("[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\nSecond turn request", user_prompt)
        self.assertEqual(raw_prompt, "Second turn request")

    def test_turn_identity_resolution_precedence(self):
        tpath = os.path.join(self.temp_dir, "turn_id.jsonl")

        # 1. Step index precedence
        with open(tpath, "w", encoding="utf-8") as f:
            f.write('{"type": "USER_INPUT", "source": "USER", "content": "Hi", "step_index": 42, "created_at": "2026-08-24T00:00:00Z"}\n')
        self.assertEqual(get_active_turn_identity(tpath), "step:42")

        # 2. Timestamp precedence when no step_index
        with open(tpath, "w", encoding="utf-8") as f:
            f.write('{"type": "USER_INPUT", "source": "USER", "content": "Hi", "created_at": "2026-08-24T00:00:00Z"}\n')
        self.assertEqual(get_active_turn_identity(tpath), "created:2026-08-24T00:00:00Z")

        # 3. Line number precedence when no timestamp or step_index
        with open(tpath, "w", encoding="utf-8") as f:
            f.write('{"type": "USER_INPUT", "source": "USER", "content": "Hi"}\n')
        self.assertEqual(get_active_turn_identity(tpath), "line:1")

    def test_repeated_tool_calls_with_ignored_tools_and_thresholds(self):
        tpath = os.path.join(self.temp_dir, "repeated_tools.jsonl")

        # Polling tools ignored
        with open(tpath, "w", encoding="utf-8") as f:
            f.write('{"type": "USER_INPUT", "content": "Start"}\n')
            for _ in range(5):
                f.write('{"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "manage_task", "args": {"Action": "status"}}]}\n')
        self.assertFalse(has_repeated_tool_calls(tpath))

        # Repeated real tool detected
        with open(tpath, "w", encoding="utf-8") as f:
            f.write('{"type": "USER_INPUT", "content": "Start"}\n')
            for _ in range(4):
                f.write('{"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "python test.py"}}]}\n')
        self.assertTrue(has_repeated_tool_calls(tpath))

    def test_recent_tool_errors_all_error_signatures(self):
        signatures = [
            "error: could not resolve target",
            "Command exited with exit code 1",
            "Process failed with exit code 2",
            "sh: exit code 127",
            "bash: command not found: pytest",
            "Traceback (most recent call last):\n  File 'main.py', line 10",
        ]
        for sig in signatures:
            with self.subTest(sig=sig):
                tpath = os.path.join(self.temp_dir, "err_test.jsonl")
                with open(tpath, "w", encoding="utf-8") as f:
                    f.write('{"type": "USER_INPUT", "content": "Start"}\n')
                    f.write(json.dumps({"type": "GENERIC", "content": sig}) + "\n")
                    f.write(json.dumps({"type": "GENERIC", "content": sig}) + "\n")
                self.assertTrue(has_recent_tool_errors(tpath))

    def test_completion_candidate_blocked_by_active_subagents_or_tasks(self):
        tpath = os.path.join(self.temp_dir, "completion.jsonl")
        with open(tpath, "w", encoding="utf-8") as f:
            f.write('{"type": "USER_INPUT", "content": "Start"}\n')
            f.write('{"type": "PLANNER_RESPONSE", "content": "Subagent spawned", "tool_calls": [{"name": "invoke_subagent", "args": {"Goal": "Task"}}]}\n')
            f.write('{"type": "PLANNER_RESPONSE", "content": "Finished all work and verified."}\n')

        # Blocked because invoke_subagent has not completed
        self.assertFalse(is_post_invocation_completion_candidate(tpath))

    def test_has_new_user_activity_truncation_and_turn_detection(self):
        tpath = os.path.join(self.temp_dir, "activity.jsonl")
        with open(tpath, "w", encoding="utf-8") as f:
            f.write('{"type": "USER_INPUT", "content": "Turn 1"}\n')
            f.write('{"type": "PLANNER_RESPONSE", "content": "Resp 1"}\n')

        # No new activity with same line count and prompt
        self.assertFalse(has_new_user_activity(tpath, "Turn 1", original_line_count=2))

        # Truncation detected as new activity
        self.assertTrue(has_new_user_activity(tpath, "Turn 1", original_line_count=5))

        # Prompt change detected
        self.assertTrue(has_new_user_activity(tpath, "Turn 0", original_line_count=2))


class TestModelsAdversarial(unittest.TestCase):
    """Adversarial testing of model discovery, parsing, and candidate resolution."""

    def test_parse_model_version_complex_and_custom_names(self):
        cases = [
            ("gemini-3.7-flash-high", ((3, 7), 2, 3)),
            ("gemini-2.5-pro-medium", ((2, 5), 1, 2)),
            ("claude-3-opus-low", ((3, 0), 1, 1)),
            ("custom-agent-v1.2", ((1, 2), 0, 0)),
            ("", ((0, 0), 0, 0)),
            (None, ((0, 0), 0, 0)),
        ]
        for name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(parse_model_version(name), expected)

    def test_expand_alias_all_variants(self):
        available = ["gemini-3.7-flash-high", "gemini-3.7-flash-medium", "gemini-2.5-pro-high"]
        expanded_auto = _expand_alias("auto", available, "high")
        self.assertEqual(expanded_auto[0], "gemini-3.7-flash-high")

        expanded_med = _expand_alias("flash-medium", available, "medium")
        self.assertIn("gemini-3.7-flash-medium", expanded_med)

        expanded_pro = _expand_alias("pro", available, "high")
        self.assertIn("gemini-2.5-pro-high", expanded_pro)

    def test_resolve_model_candidates_capping_and_cached_model(self):
        with patch("sage.models.get_cached_working_model", return_value="Gemini 3.7 Flash (High)"):
            candidates = resolve_model_candidates("auto", max_candidates=3)
            self.assertEqual(len(candidates), 3)
            self.assertEqual(candidates[0], "Gemini 3.7 Flash (High)")

    def test_working_model_cache_file_and_memory(self):
        cache_working_model("gemini-3.7-flash-high")
        self.assertEqual(get_cached_working_model(), "gemini-3.7-flash-high")
        cache_working_model(None)
        self.assertIsNone(get_cached_working_model())


class TestStatuslineAdversarial(unittest.TestCase):
    """Adversarial testing of statusline formatter calculations and ANSI escaping."""

    def test_format_tokens_boundary_values(self):
        self.assertEqual(format_tokens(0), "0")
        self.assertEqual(format_tokens(None), "0")
        self.assertEqual(format_tokens(500), "500")
        self.assertEqual(format_tokens(1_500), "2k")
        self.assertEqual(format_tokens(1_200_000), "1M")

    def test_format_countdown_boundary_values(self):
        self.assertEqual(format_countdown(0), "")
        self.assertEqual(format_countdown(-100), "")
        self.assertEqual(format_countdown(None), "")
        self.assertEqual(format_countdown(59), "[1m]")
        self.assertEqual(format_countdown(3600), "[1h]")
        self.assertEqual(format_countdown(90000), "[2d]")

    def test_is_agent_active_all_states(self):
        inactive_states = ["completed", "done", "finished", "stopped", "killed", "dead", "failed", "idle"]
        for st in inactive_states:
            with self.subTest(state=st):
                self.assertFalse(is_agent_active({"status": st}))

        active_states = ["running", "active", "working", "busy", "pending", "waiting_for_input", "in_progress"]
        for st in active_states:
            with self.subTest(state=st):
                self.assertTrue(is_agent_active({"status": st}))

    def test_statusline_advisor_error_streak_badge(self):
        temp_state = {"session_mid_turn_steers": 2, "advisor_holds": 5, "advisor_error_streak": 3}
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            json.dump(temp_state, f)
            f_path = f.name
        try:
            with patch("statusline.statusline.safe_id", return_value="test_conv"):
                with patch("os.path.exists", return_value=True):
                    with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(temp_state))):
                        badges = get_advisor_steer_badges({"conversation_id": "test_conv"})
                        self.assertIn("/err[3]", badges[0])
        finally:
            if os.path.exists(f_path):
                os.remove(f_path)

    def test_statusline_render_padding_and_visible_len(self):
        sample_ansi = "\033[1;34m3.7 flash [h]\033[0m | \033[1;35magents:2\033[0m"
        self.assertEqual(visible_len(sample_ansi), len("3.7 flash [h] | agents:2"))

        rendered = render_statusline({
            "model": "gemini-3.7-flash-high",
            "terminal_width": 120,
            "context_window": {"input_tokens": 50000, "output_tokens": 1000},
        })
        self.assertLessEqual(visible_len(rendered), 120)


class TestStaticAnalysisASTEdgeCases(unittest.TestCase):
    """Adversarial AST analysis asserting engine behavior on complex Python constructs."""

    def test_ast_statement_packing_ignores_lambdas(self):
        code_with_lambdas = '''
f = lambda x: x + 1
items = sorted([1, 2, 3], key=lambda k: -k)
func = lambda a, b=lambda z: z * 2: a + b(1)
'''
        tree = ast.parse(code_with_lambdas)
        line_stmts = defaultdict(list)
        for node in ast.walk(tree):
            if isinstance(node, ast.stmt):
                line_stmts[node.lineno].append(node)
        packed_lines = {l: s for l, s in line_stmts.items() if len(s) > 1}
        self.assertEqual(len(packed_lines), 0, "Lambdas should not be treated as separate statements")

    def test_ast_statement_packing_with_stacked_decorators(self):
        code_with_decorators = '''
@decorator_one
@decorator_two(arg="value")
def complex_function(x: int) -> int:
    return x * 2
'''
        tree = ast.parse(code_with_decorators)
        line_stmts = defaultdict(list)
        for node in ast.walk(tree):
            if isinstance(node, ast.stmt):
                line_stmts[node.lineno].append(node)
        packed_lines = {l: s for l, s in line_stmts.items() if len(s) > 1}
        self.assertEqual(len(packed_lines), 0, "Decorators should not trigger statement packing")

    def test_ast_statement_packing_with_async_constructs(self):
        code_async = '''
async def fetch_data(session, url: str):
    async with session.get(url) as response:
        async for chunk in response.content.iter_chunked(1024):
            await process(chunk)
'''
        tree = ast.parse(code_async)
        line_stmts = defaultdict(list)
        for node in ast.walk(tree):
            if isinstance(node, ast.stmt):
                line_stmts[node.lineno].append(node)
        packed_lines = {l: s for l, s in line_stmts.items() if len(s) > 1}
        self.assertEqual(len(packed_lines), 0, "Valid async constructs should not trigger statement packing")

    def test_ast_statement_packing_with_match_case(self):
        code_match = '''
def handle_command(cmd):
    match cmd:
        case ["load", filename]:
            load(filename)
        case ["save", filename]:
            save(filename)
        case _:
            raise ValueError("Unknown")
'''
        tree = ast.parse(code_match)
        line_stmts = defaultdict(list)
        for node in ast.walk(tree):
            if isinstance(node, ast.stmt):
                line_stmts[node.lineno].append(node)
        packed_lines = {l: s for l, s in line_stmts.items() if len(s) > 1}
        self.assertEqual(len(packed_lines), 0, "Valid match/case should not trigger statement packing")

    def test_ast_statement_packing_with_nested_classes_and_functions(self):
        code_nested = '''
class Outer:
    class Inner:
        def method(self):
            def helper():
                return 42
            return helper()
'''
        tree = ast.parse(code_nested)
        line_stmts = defaultdict(list)
        for node in ast.walk(tree):
            if isinstance(node, ast.stmt):
                line_stmts[node.lineno].append(node)
        packed_lines = {l: s for l, s in line_stmts.items() if len(s) > 1}
        self.assertEqual(len(packed_lines), 0, "Properly indented nested structures are single statement per line")

    def test_semicolon_tokenizer_ignores_semicolons_in_strings_and_comments(self):
        sample = '''
def format_data():
    """Docstring containing semicolon; safely."""
    # Comment with semicolon; too
    msg = "value1;value2;value3"
    formatted = f"val:{123:0.2f};suffix"
    return msg + formatted
'''
        tokens = list(tokenize.tokenize(io.BytesIO(sample.encode("utf-8")).readline))
        semicolons = [
            tok for tok in tokens
            if tok.exact_type == tokenize.SEMI
            or (tok.type == token.OP and tok.string == ";")
        ]
        self.assertEqual(len(semicolons), 0, "Semicolons inside strings/comments must be ignored")

    def test_print_visitor_detects_nested_calls_ignores_strings(self):
        code_nested_prints = '''
def outer():
    class LocalClass:
        def local_method(self):
            helper = lambda: print("nested call in lambda")
            helper()
'''
        tree = ast.parse(code_nested_prints)
        visitor = PrintCallVisitor()
        visitor.visit(tree)
        self.assertEqual(len(visitor.print_lines), 1)
        self.assertEqual(visitor.print_lines[0], 5)

        code_print_strings_only = '''
def logger():
    msg = "Call print() only via authorized logger"
    info = 'print("do not run this")'
    return msg + info
'''
        tree_clean = ast.parse(code_print_strings_only)
        visitor_clean = PrintCallVisitor()
        visitor_clean.visit(tree_clean)
        self.assertEqual(len(visitor_clean.print_lines), 0)


if __name__ == "__main__":
    unittest.main()
