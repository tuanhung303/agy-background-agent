#!/usr/bin/env python3
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from sage.transcript import (
    clean_user_prompt,
    extract_session_and_turn_data,
    get_active_background_tasks,
    get_active_subagents,
    get_active_turn_identity,
    get_transcript_path,
    has_active_background_tasks,
    has_active_subagents,
    has_new_user_activity,
    has_repeated_tool_calls,
    is_post_invocation_completion_candidate,
)


class TestTranscript(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.test_dir, "transcript.jsonl")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_clean_user_prompt(self):
        raw = "<USER_REQUEST>Fix the bug</USER_REQUEST><ADDITIONAL_METADATA>time: 123</ADDITIONAL_METADATA>"
        cleaned = clean_user_prompt(raw)
        self.assertEqual(cleaned, "Fix the bug")

        nested = "<USER_REQUEST>\nreview this:\n<USER_REQUEST>\ninner\n</USER_REQUEST>\n</USER_REQUEST>"
        c1 = clean_user_prompt(nested)
        c2 = clean_user_prompt(c1)
        self.assertEqual(c1, c2)
        self.assertEqual(c1, "review this:\n\ninner")

        raw_settings = "<USER_SETTINGS_CHANGE>debug=1</USER_SETTINGS_CHANGE>Hello"
        self.assertEqual(clean_user_prompt(raw_settings), "Hello")
        self.assertEqual(clean_user_prompt(None), "")

    def test_get_transcript_path(self):
        payload = {"transcriptPath": self.transcript_path}
        self.assertIsNone(get_transcript_path(payload, "nonexistent"))

        with open(self.transcript_path, "w") as f:
            f.write("{}\n")
        self.assertEqual(get_transcript_path(payload, "nonexistent"), self.transcript_path)

    def test_extract_session_and_turn_data(self):
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "<USER_REQUEST>Refactor code</USER_REQUEST>", "created_at": "2026-08-20T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Analyzing files", "tool_calls": [{"name": "view_file"}]},
            {"type": "GENERIC", "content": "File contents..."},
            {"type": "PLANNER_RESPONSE", "content": "Editing file", "tool_calls": [{"name": "replace_file_content"}]},
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

        user_prompt, raw_prompt, steps, tools_count, tool_names, first_ts, user_ts, line_cnt = (
            extract_session_and_turn_data(self.transcript_path)
        )
        self.assertIn("Refactor code", user_prompt)
        self.assertEqual(raw_prompt, "<USER_REQUEST>Refactor code</USER_REQUEST>")
        self.assertEqual(user_prompt, "[LATEST ACTIVE USER REQUEST]:\nRefactor code")
        self.assertEqual(tools_count, 2)
        self.assertEqual(tool_names, {"view_file", "replace_file_content"})
        self.assertEqual(line_cnt, 4)
        self.assertIsNotNone(first_ts)
        self.assertIsNotNone(user_ts)

    def test_extract_session_and_turn_data_malformed_tool_calls(self):
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "hello", "created_at": "2026-08-20T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "step 1", "tool_calls": None},
            {"type": "PLANNER_RESPONSE", "content": "step 2", "tool_calls": 123},
            {"type": "PLANNER_RESPONSE", "content": "step 3", "tool_calls": "invalid"},
            {"type": "PLANNER_RESPONSE", "content": "step 4", "tool_calls": [None, 456, "bad", {"name": "write_to_file"}]},
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")
        user_prompt, raw_prompt, steps, tools_count, tool_names, first_ts, user_ts, _ = extract_session_and_turn_data(self.transcript_path)
        self.assertEqual(tools_count, 1)
        self.assertIn("write_to_file", tool_names)
        self.assertIsNotNone(first_ts)
        self.assertIsNotNone(user_ts)

        from sage.transcript import extract_turn_tool_calls, calculate_turn_tool_score, has_repeated_tool_calls
        calls = extract_turn_tool_calls(self.transcript_path)
        self.assertEqual(len(calls), 1)
        score, count = calculate_turn_tool_score(self.transcript_path)
        self.assertGreater(score, 0.0)
        self.assertEqual(count, 1)
        self.assertFalse(has_repeated_tool_calls(self.transcript_path))

    def test_extract_empty_or_missing_transcript(self):
        res = extract_session_and_turn_data("/path/does/not/exist.jsonl")
        self.assertEqual(res[0], "")
        self.assertEqual(res[3], 0)

    def test_has_new_user_activity(self):
        initial_lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Initial prompt"},
            {"type": "PLANNER_RESPONSE", "content": "Working", "tool_calls": [{"name": "view_file"}]},
        ]
        with open(self.transcript_path, "w") as f:
            for item in initial_lines:
                f.write(json.dumps(item) + "\n")

        self.assertFalse(has_new_user_activity(self.transcript_path, "Initial prompt", original_line_count=2))

        # Appending identical prompt past original_line_count is detected as new user activity
        with open(self.transcript_path, "a") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Initial prompt"}) + "\n")
        self.assertTrue(has_new_user_activity(self.transcript_path, "Initial prompt", original_line_count=2))

        with open(self.transcript_path, "a") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Wait stop that"}) + "\n")

        self.assertTrue(has_new_user_activity(self.transcript_path, "Initial prompt", original_line_count=2))

        # Exception handling fails closed (returns False and logs audit)
        with patch("sage.transcript.log_audit") as mock_log, \
                patch("sage.transcript._read_transcript_steps", side_effect=RuntimeError("Disk failure")):
            self.assertFalse(has_new_user_activity(self.transcript_path, "Initial prompt", original_line_count=2))
            mock_log.assert_called_once()

    def test_has_new_user_activity_ignores_reviewer_steering(self):
        initial_lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Initial prompt"},
        ]
        with open(self.transcript_path, "w") as f:
            for item in initial_lines:
                f.write(json.dumps(item) + "\n")

        with open(self.transcript_path, "a") as f:
            f.write(json.dumps({
                "type": "USER_INPUT",
                "source": "USER",
                "content": "[Reviewer Steering - Action Required]\nCheck tests"
            }) + "\n")

        self.assertFalse(has_new_user_activity(self.transcript_path, "Initial prompt", original_line_count=1))

    def test_has_new_user_activity_missing_file(self):
        self.assertTrue(has_new_user_activity("/path/does/not/exist.jsonl", "prompt"))

    def test_has_new_user_activity_corrupted_file(self):
        corrupt_path = os.path.join(self.test_dir, "corrupt.jsonl")
        with open(corrupt_path, "wb") as f:
            f.write(b"\x00\xff\xfe\x00")
        res = has_new_user_activity(corrupt_path, "prompt")
        self.assertIsInstance(res, bool)

    def test_has_active_background_tasks(self):
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "PLANNER_RESPONSE", "content": "Running command synchronously"}) + "\n")
        self.assertFalse(has_active_background_tasks(self.transcript_path))

        with open(self.transcript_path, "a") as f:
            f.write(json.dumps({"type": "GENERIC", "content": "Tool is running as a background task with task id: conv1/task-123\nLogs: ..."}) + "\n")
        self.assertTrue(has_active_background_tasks(self.transcript_path))

        with open(self.transcript_path, "a") as f:
            f.write(json.dumps({"type": "USER_INPUT", "content": "[Message] timestamp=... sender=conv1/task-123 content=Done"}) + "\n")
        self.assertFalse(has_active_background_tasks(self.transcript_path))

        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "GENERIC", "content": "Tool is running as a background task with task id: conv1/task-456"}) + "\n")
            f.write(json.dumps({"type": "GENERIC", "content": "Task id \"conv1/task-456\" completed successfully."}) + "\n")
        self.assertFalse(has_active_background_tasks(self.transcript_path))

        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "GENERIC", "content": "Tool is running as a background task with task id: conv1/task-789"}) + "\n")
            f.write(json.dumps({"type": "GENERIC", "content": "Task \"conv1/task-789\" terminated by user."}) + "\n")
        self.assertFalse(has_active_background_tasks(self.transcript_path))

    def test_f8_planner_response_model_narration_does_not_complete_tasks(self):
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run job"}) + "\n")
            f.write(json.dumps({"type": "GENERIC", "content": "Tool is running as a background task with task id: conv1/task-999"}) + "\n")
            # Model narrating task completion in PLANNER_RESPONSE should NOT satisfy task completion
            f.write(json.dumps({"type": "PLANNER_RESPONSE", "content": "Task conv1/task-999 completed successfully."}) + "\n")
        self.assertTrue(has_active_background_tasks(self.transcript_path))

    def test_background_task_age_uses_earliest_task_id_observation(self):
        now = datetime.now(timezone.utc)
        lines = [
            {
                "type": "GENERIC",
                "created_at": (now - timedelta(seconds=301)).isoformat(),
                "content": (
                    "Tool is running as a background task with task id: conv1/task-7\n"
                    "Task Description: original launch"
                ),
            },
            {
                "type": "GENERIC",
                "created_at": (now - timedelta(seconds=10)).isoformat(),
                "content": (
                    "Status poll for task id: conv1/task-7\n"
                    "Task Description: repeated poll"
                ),
            },
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

        tasks = get_active_background_tasks(self.transcript_path)
        self.assertEqual(len(tasks), 1)
        self.assertGreater(tasks[0]["age_seconds"], 300)
        self.assertEqual(tasks[0]["description"], "original launch")

    def test_cross_conversation_background_task_isolation(self):
        lines = [
            {
                "type": "GENERIC",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "content": (
                    "Tool is running as a background task with task id: foreign_conv/task-9\n"
                    "Task Description: foreign task"
                ),
            },
            {
                "type": "GENERIC",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "content": (
                    "Tool is running as a background task with task id: target_conv/task-1\n"
                    "Task Description: my active task"
                ),
            },
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

        target_tasks = get_active_background_tasks(self.transcript_path, conv_id="target_conv")
        self.assertEqual(len(target_tasks), 1)
        self.assertEqual(target_tasks[0]["task_id"], "target_conv/task-1")

        foreign_tasks = get_active_background_tasks(self.transcript_path, conv_id="foreign_conv")
        self.assertEqual(len(foreign_tasks), 1)
        self.assertEqual(foreign_tasks[0]["task_id"], "foreign_conv/task-9")

    def test_unscoped_foreign_task_id_in_command_output_ignored(self):
        lines = [
            {
                "type": "GENERIC",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "content": (
                    "Command output from git diff:\n"
                    "+ Tool is running as a background task with task id: conv1/task-7\n"
                    "+ Task Description: original launch"
                ),
            },
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

        tasks = get_active_background_tasks(self.transcript_path, conv_id="active_conv_123")
        self.assertEqual(len(tasks), 0)

    def test_active_turn_identity_distinguishes_identical_user_prompts(self):
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "step_index": 10,
                "content": "Run tests",
            }) + "\n")
        first_identity = get_active_turn_identity(self.transcript_path)

        with open(self.transcript_path, "a") as f:
            f.write(json.dumps({
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "step_index": 20,
                "content": "Run tests",
            }) + "\n")
        second_identity = get_active_turn_identity(self.transcript_path)

        self.assertNotEqual(first_identity, second_identity)

    def test_subagent_activity_detection_and_completion(self):
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Dispatch work"}) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "Dispatching subagent",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Implementer", "TypeName": "self"}]}}],
            }) + "\n")
            f.write(json.dumps({
                "type": "GENERIC",
                "content": "Created the following subagents:\n{\"conversationId\": \"subagent-uuid-12345\"}",
            }) + "\n")

        self.assertTrue(has_active_subagents(self.transcript_path))
        subs = get_active_subagents(self.transcript_path)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["conversation_id"], "subagent-uuid-12345")
        self.assertFalse(is_post_invocation_completion_candidate(self.transcript_path))

        # Subagent delivers result via message
        with open(self.transcript_path, "a") as f:
            f.write(json.dumps({
                "type": "SYSTEM_MESSAGE",
                "content": "<SYSTEM_MESSAGE>\n[Message] timestamp=... sender=subagent-uuid-12345 content=Done\n</SYSTEM_MESSAGE>",
            }) + "\n")

        self.assertFalse(has_active_subagents(self.transcript_path))

    def test_multiple_subagents_partial_completion_remains_active(self):
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Spawn 2 subagents"}) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "Launching",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "A"}, {"Role": "B"}]}}],
            }) + "\n")
            f.write(json.dumps({
                "type": "GENERIC",
                "content": "Spawned:\n{\"conversationId\": \"sub-A\"}\n{\"conversationId\": \"sub-B\"}",
            }) + "\n")

        # Both subagents active
        self.assertEqual(len(get_active_subagents(self.transcript_path)), 2)
        self.assertTrue(has_active_subagents(self.transcript_path))

        # Subagent A delivers message -> Subagent B must remain active
        with open(self.transcript_path, "a") as f:
            f.write(json.dumps({
                "type": "SYSTEM_MESSAGE",
                "content": "[Message] sender=sub-A content=Task A finished",
            }) + "\n")

        active = get_active_subagents(self.transcript_path)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["subagent_id"], "sub-B")
        self.assertTrue(has_active_subagents(self.transcript_path))

    def test_subagent_activity_killed_via_manage_subagents(self):
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Dispatch subagents"}) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "Dispatching",
                "tool_calls": [{"name": "invoke_subagent"}],
            }) + "\n")
            f.write(json.dumps({
                "type": "GENERIC",
                "content": "Created the following subagents:\n{\"conversationId\": \"sub-to-kill\"}",
            }) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "Killing subagent",
                "tool_calls": [{"name": "manage_subagents", "args": {"Action": "kill", "ConversationIds": ["sub-to-kill"]}}],
            }) + "\n")

        self.assertFalse(has_active_subagents(self.transcript_path))

    def test_post_invocation_completion_candidate_requires_final_text_response(self):
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Fix it"},
            {"type": "PLANNER_RESPONSE", "content": "Reading", "tool_calls": [{"name": "view_file"}]},
            {"type": "GENERIC", "content": "file contents"},
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")
        self.assertFalse(is_post_invocation_completion_candidate(self.transcript_path))

        with open(self.transcript_path, "a") as f:
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "Implemented and verified the fix.",
                "tool_calls": [],
            }) + "\n")
            f.write(json.dumps({
                "type": "EPHEMERAL_MESSAGE",
                "content": "Command Timer - IMPROVE_NEXT_TIME: view_file took 12s",
            }) + "\n")
        self.assertTrue(is_post_invocation_completion_candidate(self.transcript_path))

        with open(self.transcript_path, "a") as f:
            f.write(json.dumps({
                "type": "USER_INPUT",
                "source": "USER",
                "content": "steering - please verify with subagent",
            }) + "\n")
        self.assertFalse(is_post_invocation_completion_candidate(self.transcript_path))

    def test_get_active_background_tasks_single_quotes_and_killed(self):
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "GENERIC", "content": "Tool is running as a background task with task id: conv1/task-111"}) + "\n")
            f.write(json.dumps({"type": "GENERIC", "content": "Task 'conv1/task-111' completed."}) + "\n")
        self.assertFalse(has_active_background_tasks(self.transcript_path))

    def test_subagent_malformed_arguments_robustness(self):
        # 1. Subagents as json string
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": '[{"Role": "Tester"}]'}}],
            }) + "\n")
        subs = get_active_subagents(self.transcript_path)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["role"], "Tester")

        # 2. Subagents as list of strings (raw strings)
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": ["raw_string_role"]}}],
            }) + "\n")
        subs = get_active_subagents(self.transcript_path)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["role"], "raw_string_role")

        # 3. Subagents as None / empty
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": None}}],
            }) + "\n")
        subs = get_active_subagents(self.transcript_path)
        self.assertEqual(len(subs), 1)

    def test_cross_turn_background_tasks_persisted(self):
        # Turn 1 starts background task
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Start server"}) + "\n")
            f.write(json.dumps({
                "type": "GENERIC",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "content": "Tool is running as a background task with task id: conv1/task-999\nTask Description: server",
            }) + "\n")
            # Turn 2 user asks something else
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Now run test"}) + "\n")
            f.write(json.dumps({"type": "PLANNER_RESPONSE", "content": "Running tests", "tool_calls": [{"name": "run_command"}]}) + "\n")

        # Task from Turn 1 must still be detected as active in Turn 2
        tasks = get_active_background_tasks(self.transcript_path, conv_id="conv1")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], "conv1/task-999")

    def test_timer_schedule_tasks_ignored_by_background_watchdog(self):
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({
                "type": "GENERIC",
                "content": "Tool is running as a background task with task id: conv1/task-1024\nTask Description: Timer: 8s, Prompt: Wait for task-1022",
                "created_at": (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat(),
            }) + "\n")
        tasks = get_active_background_tasks(self.transcript_path, conv_id="conv1")
        self.assertEqual(len(tasks), 0)
        self.assertFalse(has_active_background_tasks(self.transcript_path, conv_id="conv1"))

    def test_manage_task_list_empty_clears_active_tasks(self):
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({
                "type": "GENERIC",
                "content": "Tool is running as a background task with task id: conv1/task-555\nTask Description: compile assets",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }) + "\n")
            f.write(json.dumps({
                "type": "GENERIC",
                "content": "No background tasks are currently running.",
            }) + "\n")
        tasks = get_active_background_tasks(self.transcript_path, conv_id="conv1")
        self.assertEqual(len(tasks), 0)

    def test_task_status_done_and_timer_cancelled_recognized(self):
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({
                "type": "GENERIC",
                "content": "Tool is running as a background task with task id: conv1/task-666\nTask Description: run script",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }) + "\n")
            f.write(json.dumps({
                "type": "GENERIC",
                "content": "Task: conv1/task-666\nStatus: DONE\nLog: /tmp/log",
            }) + "\n")
        self.assertFalse(has_active_background_tasks(self.transcript_path, conv_id="conv1"))

    def test_sanitize_tool_output_strips_boilerplate(self):
        from sage.transcript import sanitize_tool_output
        raw = "Created At: 2026-08-23T02:00:00Z\nCompleted At: 2026-08-23T02:00:05Z\nThe command exited with code 0.\nOutput:\nHello World\nLine 2"
        res = sanitize_tool_output(raw)
        self.assertEqual(res, "Hello World\nLine 2")

    def test_sanitize_tool_output_clamps_long_lines(self):
        from sage.transcript import sanitize_tool_output
        long_line = "A" * 500
        res = sanitize_tool_output(long_line, max_line_len=100)
        self.assertIn("[line truncated]", res)
        self.assertLess(len(res), 300)

    def test_sanitize_tool_output_real_agy_blank_lines(self):
        from sage.transcript import sanitize_tool_output
        raw = "Created At: 2026-08-23T02:00:00Z\nCompleted At: 2026-08-23T02:00:05Z\n\nThe command exited with code 0.\nOutput:\nActual Output Here"
        self.assertEqual(sanitize_tool_output(raw), "Actual Output Here")

    def test_sanitize_tool_output_non_header_prefix_preserved(self):
        from sage.transcript import sanitize_tool_output
        raw = "build ok\nStep 2\nError:\ndetails here"
        res = sanitize_tool_output(raw)
        self.assertIn("build ok", res)
        self.assertIn("details here", res)

    def test_sanitize_tool_output_only_boilerplate_returns_empty(self):
        from sage.transcript import sanitize_tool_output
        raw = "Created At: 2026-08-23T02:00:00Z\nCompleted At: 2026-08-23T02:00:05Z\nThe command exited with code 0."
        self.assertEqual(sanitize_tool_output(raw), "")

    def test_sanitize_preserves_nonzero_exit_code_evidence(self):
        from sage.transcript import sanitize_tool_output
        raw = "Created At: X\nCompleted At: Y\nThe command exited with code 1.\nOutput:\nboom"
        res = sanitize_tool_output(raw)
        self.assertIn("exited with code 1", res)

    def test_sanitize_strips_zero_exit_code(self):
        from sage.transcript import sanitize_tool_output
        raw = "Created At: X\nCompleted At: Y\nThe command exited with code 0.\nOutput:\nfine"
        self.assertEqual(sanitize_tool_output(raw), "fine")

    def test_sanitize_tool_output_strictly_bounds_max_chars(self):
        from sage.transcript import sanitize_tool_output
        lines = [f"Line {i}: " + "x" * 200 for i in range(1, 50)]
        raw = "\n".join(lines)
        res = sanitize_tool_output(raw, max_chars=400)
        self.assertLessEqual(len(res), 400)


class TestLoopSignalDominance(unittest.TestCase):
    """has_repeated_tool_calls must fire on genuine stalls and stay silent on interleaved work."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.path = os.path.join(self.test_dir, "t.jsonl")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write(self, calls):
        lines = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "build"}]
        for name, args in calls:
            lines.append({"type": "PLANNER_RESPONSE", "tool_calls": [{"name": name, "args": args}]})
        with open(self.path, "w") as f:
            f.writelines(json.dumps(s) + "\n" for s in lines)

    def test_interleaved_edit_loop_is_silent(self):
        calls = []
        for _ in range(4):
            calls += [("view_file", {"path": "/src/a.py"}), ("replace_file_content", {"path": "/src/a.py"})]
        self._write(calls)
        self.assertFalse(has_repeated_tool_calls(self.path))

    def test_alternating_pair_small_window_is_silent(self):
        calls = [("grep_a", {"q": "x"}), ("grep_b", {"q": "x"})] * 3
        self._write(calls)
        self.assertFalse(has_repeated_tool_calls(self.path))

    def test_back_to_back_retry_fires(self):
        self._write([("run_command", {"command": "pytest -x"})] * 4)
        self.assertTrue(has_repeated_tool_calls(self.path))

    def test_dominant_signature_with_chatter_fires(self):
        calls = [("run_command", {"command": "pytest -x"}), ("echo", {"m": "hi"}),
                 ("run_command", {"command": "pytest -x"}), ("run_command", {"command": "pytest -x"}),
                 ("run_command", {"command": "pytest -x"})]
        self._write(calls)
        self.assertTrue(has_repeated_tool_calls(self.path))

    def test_polling_tools_exempt(self):
        self._write([("manage_task", {"id": f"task-{i}"}) for i in range(5)])
        self.assertFalse(has_repeated_tool_calls(self.path))

    def test_identical_shell_poll_is_a_stall_signal(self):
        self._write([("run_command", {"command": "tail /tmp/task-123.log"})] * 3)
        self.assertTrue(has_repeated_tool_calls(self.path))

    def test_short_window_is_silent(self):
        self._write([("run_command", {"command": "pytest -x"}), ("run_command", {"command": "pytest -x"})])
        self.assertFalse(has_repeated_tool_calls(self.path))


class TestSanitizerBudgets(unittest.TestCase):
    """Budget and boilerplate guarantees for sanitize_tool_output."""

    SHAPES = {
        "wide_lines": "\n".join("X" * 400 for _ in range(40)),
        "many_short": "\n".join(f"L{i}" for i in range(50)),
        "single_huge": "Z" * 20000,
        "few_long": "\n".join("Y" * 900 for _ in range(3)),
    }

    def test_max_chars_is_never_exceeded(self):
        from sage.transcript import sanitize_tool_output
        for name, text in self.SHAPES.items():
            for budget in (0, 1, 5, 29, 60, 150, 200, 400, 800, 2000):
                with self.subTest(shape=name, max_chars=budget):
                    self.assertLessEqual(len(sanitize_tool_output(text, max_chars=budget)), budget)

    def test_tail_survives_head_tail_slicing(self):
        from sage.transcript import sanitize_tool_output
        res = sanitize_tool_output(self.SHAPES["many_short"], max_chars=150)
        self.assertIn("[Lines 1-", res)
        self.assertIn("L49", res, "tail must survive the max_chars budget")
        self.assertLessEqual(len(res), 150)

    def test_strips_boilerplate_with_blank_line_separator(self):
        """Production AGY output has a blank line before the exit-code banner."""
        from sage.transcript import sanitize_tool_output
        raw = "Created At: X\nCompleted At: Y\n\nThe command exited with code 0.\nOutput:\ntotal 4\ndrwx 3 u s ."
        res = sanitize_tool_output(raw)
        self.assertEqual(res, "total 4\ndrwx 3 u s .")

    def test_pure_boilerplate_yields_empty_not_original(self):
        from sage.transcript import sanitize_tool_output
        self.assertEqual(sanitize_tool_output("Created At: X\nCompleted At: Y"), "")

    def test_max_line_len_controls_clamp_width(self):
        from sage.transcript import sanitize_tool_output
        narrow = sanitize_tool_output("Y" * 2000, max_chars=99999, max_line_len=300)
        wide = sanitize_tool_output("Y" * 2000, max_chars=99999, max_line_len=900)
        self.assertLess(len(narrow), len(wide))


    def test_extract_session_and_turn_data_sliding_window_compaction(self):
        lines = []
        for i in range(1, 16):
            lines.append({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": f"<USER_REQUEST>Request {i} detail text</USER_REQUEST>", "created_at": f"2026-08-20T10:{i:02d}:00Z"})
            lines.append({"type": "PLANNER_RESPONSE", "content": f"Step {i}", "tool_calls": [{"name": "view_file"}]})
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")
            path = f.name
        try:
            user_prompt, raw_prompt, steps, tools_count, _, _, _, _ = extract_session_and_turn_data(path)
            self.assertIn("SESSION HISTORY:", user_prompt)
            self.assertIn("Prior request 1: Request 1 detail text", user_prompt)
            self.assertIn("earlier requests omitted", user_prompt)
            self.assertIn("Prior request 14: Request 14 detail text", user_prompt)
            self.assertIn("[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\nRequest 15 detail text", user_prompt)
            self.assertEqual(raw_prompt, "<USER_REQUEST>Request 15 detail text</USER_REQUEST>")
            self.assertLess(len(user_prompt), 2000)
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()

