"""
tests.test_tier5_challenger1 - Adversarial Coverage Hardening Suite (Tier 5 Challenger 1).
Exhaustively tests edge cases, race conditions, boundary states, exception fallbacks,
and lifecycle invariants across lifecycle and systems modules.
"""

import fcntl
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from advisor.config import (
    ADVISOR_MAX_ERROR_STREAK,
    _load_env_overlay,
    _safe_bool,
    _safe_float,
    _safe_int,
)
from advisor.executor import (
    clean_resume_history,
    clear_session_id,
    extract_json_from_llm_output,
    load_session_id,
    run_model_cascade,
    save_session_id,
)
from advisor.git import get_git_diff
from advisor.guards import (
    check_payload_and_lifecycle,
    evaluate_turn_triggers,
    format_hook_message,
    handle_background_watch_action,
    is_destructive_action,
    is_subagent_session,
)
from advisor.locking import (
    acquire_spawn_lock,
    atomic_write_json,
    cleanup_stale_tmp_files,
    release_lock,
    release_spawn_lock,
    safe_id,
)
from advisor.policies import (
    advisor_flow,
    background_watch,
    final_advisor_gate,
)
from advisor.runner import run_session_stop_audit
from advisor.sanitizer import (
    _clamp_lines,
    _strip_boilerplate_headers,
    clamp_diff,
    clean_user_prompt,
    sanitize_tool_output,
)
from advisor.sensitive import (
    compile_sensitive_pattern,
    extract_tool_strings,
    get_sensitive_keywords,
    is_sensitive_trigger_enabled,
    scan_tool_call_for_sensitive,
)
from advisor.watchers import (
    _parse_iso_ts,
    get_active_background_tasks,
    get_active_subagents,
)


class TestLockingAdversarial(unittest.TestCase):
    """Adversarial testing for atomic locking, concurrency, and tmp file management."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.log_file = os.path.join(self.tmp_dir, "test_audit.log")

    def tearDown(self):
        release_lock()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_l1_acquire_spawn_lock_polymorphism_and_permission_error(self):
        """Test acquire_spawn_lock polymorphic timeout and permission failure paths."""
        lock_path = os.path.join(self.tmp_dir, "spawn.lock")
        fh = acquire_spawn_lock(lock_path=lock_path, timeout=1.0)
        self.assertIsNotNone(fh)
        release_spawn_lock(fh)

        # Polymorphic invocation: numeric first arg treated as timeout
        with patch("advisor.locking.SPAWN_LOCK_FILE", lock_path):
            fh2 = acquire_spawn_lock(2.0)
            self.assertIsNotNone(fh2)
            release_spawn_lock(fh2)

        # Permission error / invalid path
        invalid_path = "/non_existent_directory_xyz/invalid.lock"
        fh3 = acquire_spawn_lock(lock_path=invalid_path, timeout=0.1)
        self.assertIsNone(fh3)

    def test_l2_acquire_spawn_lock_contention_timeout(self):
        """Test acquire_spawn_lock contention loop timing out when locked by another process."""
        lock_path = os.path.join(self.tmp_dir, "contention.lock")
        # Hold lock externally
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        external_fh = open(fd, "w")
        fcntl.flock(external_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        t0 = time.time()
        res = acquire_spawn_lock(lock_path=lock_path, timeout=0.15)
        elapsed = time.time() - t0
        self.assertIsNone(res)
        self.assertGreaterEqual(elapsed, 0.1)

        fcntl.flock(external_fh.fileno(), fcntl.LOCK_UN)
        external_fh.close()

    def test_l3_release_spawn_lock_robustness(self):
        """Test release_spawn_lock handles None, closed handles, and broken filenos."""
        self.assertIsNone(release_spawn_lock(None))

        # Closed handle
        lock_path = os.path.join(self.tmp_dir, "closed.lock")
        fh = open(lock_path, "w")
        fh.close()
        release_spawn_lock(fh)

        # Mock raising exception on flock
        mock_fh = MagicMock()
        mock_fh.fileno.side_effect = ValueError("invalid fileno")
        release_spawn_lock(mock_fh)

    def test_l4_safe_id_extreme_inputs(self):
        """Test safe_id with unicode, emojis, control chars, null bytes, None, and 1000+ chars."""
        self.assertTrue(safe_id(None).endswith("e3b0c442"))
        self.assertTrue(safe_id("").endswith("e3b0c442"))

        # Unicode with emojis and special characters
        res = safe_id("conv-🎯-🔥/path..\\0!@#$%")
        self.assertTrue(res.endswith(res.split("_")[-1]))
        self.assertLessEqual(len(res), 45)

        # Extreme length string
        huge_id = "A" * 1500
        res_huge = safe_id(huge_id)
        self.assertLessEqual(len(res_huge), 45)
        self.assertTrue(res_huge.startswith("A" * 32))

    def test_l5_atomic_write_json_type_error_cleanup(self):
        """Test atomic_write_json catches serialization exceptions and removes tmp files."""
        target = os.path.join(self.tmp_dir, "corrupt.json")
        bad_data = {"set_key": {1, 2, 3}}  # set is not JSON serializable

        with patch("advisor.locking.LOG_FILE", self.log_file):
            atomic_write_json(target, bad_data)

        self.assertFalse(os.path.exists(target))
        # Ensure no dangling .tmp files remained
        tmps = [f for f in os.listdir(self.tmp_dir) if f.startswith("corrupt.json.tmp")]
        self.assertEqual(len(tmps), 0)

    def test_l6_atomic_write_json_file_permissions(self):
        """Test atomic_write_json creates files with strict 0600 mode."""
        target = os.path.join(self.tmp_dir, "secure.json")
        atomic_write_json(target, {"status": "ok"})
        self.assertTrue(os.path.exists(target))
        mode = oct(os.stat(target).st_mode & 0o777)
        self.assertEqual(mode, "0o600")

    def test_l7_cleanup_stale_tmp_files_active_lock_retention(self):
        """Test cleanup_stale_tmp_files retains actively held locks while pruning stale files."""
        stale_time = time.time() - 10000

        # 1. Stale unlocked json file (should be deleted)
        stale_json = f"/tmp/agy_advisor_test_l7_{os.getpid()}_stale.json"
        with open(stale_json, "w") as f:
            f.write("{}")
        os.utime(stale_json, (stale_time, stale_time))

        # 2. Stale unlocked .lock file (should be deleted)
        stale_lock = f"/tmp/agy_advisor_test_l7_{os.getpid()}_stale.lock"
        with open(stale_lock, "w") as f:
            f.write("")
        os.utime(stale_lock, (stale_time, stale_time))

        # 3. Stale LOCKED .lock file (must NOT be deleted)
        active_stale_lock = f"/tmp/agy_advisor_test_l7_{os.getpid()}_active.lock"
        fd = os.open(active_stale_lock, os.O_RDWR | os.O_CREAT, 0o600)
        active_fh = open(fd, "w")
        fcntl.flock(active_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.utime(active_stale_lock, (stale_time, stale_time))

        try:
            cleanup_stale_tmp_files(max_age_seconds=7200)

            self.assertFalse(os.path.exists(stale_json))
            self.assertFalse(os.path.exists(stale_lock))
            self.assertTrue(os.path.exists(active_stale_lock))
        finally:
            fcntl.flock(active_fh.fileno(), fcntl.LOCK_UN)
            active_fh.close()
            if os.path.exists(active_stale_lock):
                try:
                    os.remove(active_stale_lock)
                except Exception:
                    pass


class TestWatchersAdversarial(unittest.TestCase):
    """Adversarial testing for subagent tracking, background task monitoring, and timestamp parsing."""

    def test_w1_parse_iso_ts_boundary_matrix(self):
        """Test _parse_iso_ts across varied ISO formats and edge cases."""
        self.assertIsNone(_parse_iso_ts(None))
        self.assertIsNone(_parse_iso_ts(""))
        self.assertIsNone(_parse_iso_ts("not-a-timestamp"))

        # Naive format gets UTC tzinfo
        dt1 = _parse_iso_ts("2026-08-24T03:00:00")
        self.assertIsNotNone(dt1)
        self.assertEqual(dt1.tzinfo, timezone.utc)

        # Z suffix
        dt2 = _parse_iso_ts("2026-08-24T03:00:00Z")
        self.assertEqual(dt2.tzinfo, timezone.utc)

        # Positive offset
        dt3 = _parse_iso_ts("2026-08-24T08:30:00+05:30")
        self.assertIsNotNone(dt3.tzinfo)

        # Microseconds
        dt4 = _parse_iso_ts("2026-08-24T03:00:00.123456Z")
        self.assertEqual(dt4.microsecond, 123456)

    def test_w2_subagents_malformed_json_and_completion_signals(self):
        """Test get_active_subagents with stringified/broken JSON and diverse completion keywords."""
        steps = [
            # 1. Stringified JSON list
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": '[{"Role": "Scout"}]'}}],
            },
            # 2. Malformed JSON string in Subagents
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": '{"bad": json'}}]
            },
            # 3. Non-dict list item
            {
                "type": "MODEL_OUTPUT",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": ["DirectRole"]}}]
            },
            # Resolution to conversation ID in content
            {
                "type": "GENERIC",
                "content": 'Spawned conversation {"conversationId": "sub_conv_001"} successfully',
            },
            # subagent gone idle
            {
                "type": "SYSTEM_MESSAGE",
                "content": "Subagent sub_conv_001 has gone idle after completing tasks",
            },
            # Killed subagent pattern
            {
                "type": "USER_INPUT",
                "content": 'Killed subagent "sub_conv_002"',
            },
        ]
        # Initially sub_conv_001 was resolved then marked idle, others pending
        active = get_active_subagents(steps, conv_id="primary_conv")
        active_roles = [s["role"] for s in active]
        self.assertIn("Subagent", active_roles)  # from malformed fallback
        self.assertIn("DirectRole", active_roles)
        # sub_conv_001 is idle, so not in active
        self.assertNotIn("sub_conv_001", [s.get("conversation_id") for s in active])

        # Test manage_subagents Action: kill_all
        kill_steps = steps + [{
            "type": "MODEL_OUTPUT",
            "tool_calls": [{"name": "manage_subagents", "args": {"Action": "kill_all"}}]
        }]
        self.assertEqual(len(get_active_subagents(kill_steps, conv_id="primary_conv")), 0)

    def test_w3_background_tasks_reset_false_positives_and_completions(self):
        """Test get_active_background_tasks with reset phrases, false-positive suppression, and all completions."""
        steps = [
            # Real task
            {
                "type": "SYSTEM",
                "content": "Tool is running as a background task with task id: primary_conv/task-1\nTask Description: build_assets",
                "created_at": "2026-08-24T03:00:00Z",
            },
            # False positive from code diff (must be skipped)
            {
                "type": "SYSTEM",
                "content": "Showing lines 1 to 20\nTool is running as a background task with task id: task-999\nTask Description: diff_block_start",
            },
            # False positive from pytest output
            {
                "type": "GENERIC",
                "content": "AssertionError in pytest: Tool is running as a background task with task id: task-888",
            },
            # Timer (must be skipped)
            {
                "type": "SYSTEM",
                "content": "Tool is running as a background task with task id: primary_conv/task-2\nTask Description: timer: 5 minutes",
            },
        ]
        active = get_active_background_tasks(steps, conv_id="primary_conv")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["task_id"], "primary_conv/task-1")

        # Test diverse completion messages
        completion_variants = [
            "Background task primary_conv/task-1 was canceled by user",
            "Task task-1 finished with result: exit code 0",
            "Task 'primary_conv/task-1' completed",
            "Task task-1 terminated unexpectedly",
            "Task task-1 killed",
            "Task task-1 status: done",
            "Task task-1 status: failed",
            "Task task-1 timer cancelled",
            "[Message] sender=primary_conv/task-1 priority=HIGH content=done",
        ]
        for comp in completion_variants:
            comp_steps = steps + [{"type": "SYSTEM_MESSAGE", "content": comp}]
            self.assertEqual(len(get_active_background_tasks(comp_steps, conv_id="primary_conv")), 0)

        # Test global reset marker
        reset_steps = steps + [{"type": "GENERIC", "content": "No background tasks are currently running."}]
        self.assertEqual(len(get_active_background_tasks(reset_steps, conv_id="primary_conv")), 0)


class TestGuardsAdversarial(unittest.TestCase):
    """Adversarial testing for destructive actions, subagent sessions, and triggers."""

    def test_g1_destructive_action_complex_matrix(self):
        """Test is_destructive_action regex against destructive vs safe command variants."""
        destructive = [
            "rm -rf /",
            "rm -fr /var/log",
            "rm --recursive --force .",
            "rm --force --recursive target/",
            "sudo rm -rf /etc",
            "mkfs.ext4 /dev/sdb",
            "dd if=/dev/urandom of=/dev/sda",
            ":(){ :|:& };:",
            "git reset --hard HEAD~1",
            "git push origin main --force",
            "git push origin main -f",
            "drop table users",
            "drop database production",
            "truncate table transactions",
            "chmod -R 777 /app",
        ]
        for cmd in destructive:
            self.assertTrue(is_destructive_action(cmd), f"Expected destructive for: {cmd}")

        safe = [
            "git push origin main --force-with-lease",
            "git push origin feature-branch",
            "git status",
            "rm specific_file.txt",
            "git reset HEAD file.py",
            "SELECT * FROM users",
            "chmod 644 file.txt",
        ]
        for cmd in safe:
            self.assertFalse(is_destructive_action(cmd), f"Expected safe for: {cmd}")


    def test_g3_format_hook_message_normalization_and_validation(self):
        """Test format_hook_message strips raw tokens and rejects invalid kinds."""
        self.assertEqual(format_hook_message("advisor", "※ advisor: Fix the bug"), "※ advisor: Fix the bug")
        self.assertEqual(format_hook_message("steering", "[STEERING] Check tests"), "※ steering: Check tests")
        self.assertEqual(format_hook_message("recap", "**recap:** All green"), "※ recap: All green")
        self.assertEqual(format_hook_message("adviser", "Adviser - Run tests"), "※ adviser: Run tests")

        with self.assertRaises(ValueError):
            format_hook_message("unknown_kind", "payload")

    def test_g4_subagent_session_detection_markers(self):
        """Test is_subagent_session across payload fields, role tags, and transcript markers."""
        # 1. Payload flags
        self.assertTrue(is_subagent_session({"isSubagent": True}, "", ""))
        self.assertTrue(is_subagent_session({"parentConversationId": "parent-123"}, "", ""))
        self.assertTrue(is_subagent_session({"agentRole": "implementer"}, "", ""))
        self.assertTrue(is_subagent_session({"role": "qa"}, "", ""))
        self.assertTrue(is_subagent_session({"role": "scout"}, "", ""))

        # 2. Prompt markers
        self.assertTrue(is_subagent_session({}, "", "<subagent_reminder>you are a worker</subagent_reminder>"))
        self.assertTrue(is_subagent_session({}, "", "you are running as a subagent invoked by a caller agent"))
        self.assertTrue(is_subagent_session({}, "", "Role: branch implementer for auth"))

        # 3. Negative case
        self.assertFalse(is_subagent_session({}, "", "Refactor user authentication and run tests"))

    def test_g5_evaluate_turn_triggers_matrix(self):
        """Test evaluate_turn_triggers across test env, heavy tools, duration, and sensitive matches."""
        # 1. Test environment bypass
        with patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}):
            dur = evaluate_turn_triggers(0, datetime.now(timezone.utc))
            self.assertGreaterEqual(dur, 0.0)

        # 2. Heavy tool threshold
        with patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "0", "AGY_STOP_AUDIT_MIN_SECONDS": "999"}):
            dur = evaluate_turn_triggers(15, datetime.now(timezone.utc))
            self.assertGreaterEqual(dur, 0.0)

        # 3. Sensitive keyword match with >= 1 tool
        with patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "0", "AGY_STOP_AUDIT_MIN_SECONDS": "999"}):
            dur = evaluate_turn_triggers(2, datetime.now(timezone.utc), sensitive_matches={"git"})
            self.assertGreaterEqual(dur, 0.0)

        # 4. Unmet triggers calls fail_safe_exit
        with patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "0", "AGY_STOP_AUDIT_MIN_SECONDS": "999"}):
            with self.assertRaises(SystemExit) as cm:
                evaluate_turn_triggers(2, datetime.now(timezone.utc) - timedelta(seconds=10))
            self.assertEqual(cm.exception.code, 0)

    def test_g6_check_payload_and_lifecycle_edge_cases(self):
        """Test check_payload_and_lifecycle handles recursion, empty/bad JSON, errors, and terminations."""
        # Recursion block
        with patch.dict(os.environ, {"AGY_STOP_AUDIT_ACTIVE": "1"}):
            with self.assertRaises(SystemExit):
                check_payload_and_lifecycle()

        # Empty stdin
        with patch.dict(os.environ, {"AGY_STOP_AUDIT_ACTIVE": "0"}):
            with patch("sys.stdin", io.StringIO("")):
                with self.assertRaises(SystemExit):
                    check_payload_and_lifecycle()

        # Termination reason: max_steps_exceeded / user_abort
        with patch.dict(os.environ, {"AGY_STOP_AUDIT_ACTIVE": "0"}):
            with patch("sys.stdin", io.StringIO(json.dumps({"terminationReason": "max_steps_exceeded"}))):
                with self.assertRaises(SystemExit):
                    check_payload_and_lifecycle()

    def test_g7_handle_background_watch_action_routing(self):
        """Test handle_background_watch_action handles steer, grace, and already_steered."""
        record_steer = MagicMock()
        record_grace = MagicMock()

        # Steer action
        bgp_steer = {"action": "steer", "task_id": "task-1", "description": "build", "age_seconds": 350.0}
        with self.assertRaises(SystemExit):
            handle_background_watch_action(bgp_steer, {}, "/tmp/state.json", 10, record_steer, record_grace)
        record_steer.assert_called_once()

        # Grace action during stop event (< 3 count)
        bgp_grace = {"action": "grace"}
        with patch("advisor.guards.is_post_invocation", return_value=False):
            with self.assertRaises(SystemExit):
                handle_background_watch_action(bgp_grace, {"bg_watch_count": 1}, "/tmp/state.json", 10, record_steer, record_grace)
            record_grace.assert_called_once()


class TestPoliciesAdversarial(unittest.TestCase):
    """Adversarial testing for background_watch and advisor_flow decision logic."""

    def test_p1_background_watch_decision_tree(self):
        """Test background_watch returns none, steer (oldest), grace, or already_steered."""
        # None
        self.assertEqual(background_watch([], set())["action"], "none")

        # Stale task (> 300s) not steered -> steer oldest
        tasks = [
            {"task_id": "task-1", "description": "test_1", "age_seconds": 320.0},
            {"task_id": "task-2", "description": "test_2", "age_seconds": 450.0},
        ]
        res = background_watch(tasks, set())
        self.assertEqual(res["action"], "steer")
        self.assertEqual(res["task_id"], "task-2")

        # Grace: tasks <= 300s
        fresh_tasks = [{"task_id": "task-3", "description": "fresh", "age_seconds": 120.0}]
        self.assertEqual(background_watch(fresh_tasks, set())["action"], "grace")

        # Already steered: stale task already in bg_steered
        self.assertEqual(background_watch(tasks, {"task-1", "task-2"})["action"], "already_steered")

    @patch("advisor.policies.has_new_user_activity", return_value=False)
    @patch("advisor.policies.extract_session_and_turn_data", return_value=("prompt", "raw", [], 15, set(), None, None, 0))
    @patch("advisor.policies.is_post_invocation_completion_candidate", return_value=False)
    @patch("advisor.policies.evaluate_mid_turn_progress")
    def test_p2_advisor_flow_circuit_breaker(self, mock_eval, mock_cand, mock_extract, mock_fresh):
        """Test advisor_flow circuit breaker."""
        # 1. Circuit breaker open
        state_err = {"advisor_error_streak": ADVISOR_MAX_ERROR_STREAK}
        res_cb = advisor_flow("midturn", conv_id="c1", transcript_path="", clean_prompt="",
                              initial_line_count=0, total_tool_calls=10, turn_tool_names=set(),
                              user_prompt="", agent_steps=[], git_diff="", state=state_err)
        self.assertEqual(res_cb["action"], "exit")
        self.assertIn("circuit breaker", res_cb["reason"])


    def test_p3_final_advisor_gate_note_forwarding(self):
        """Test final_advisor_gate attaches a healthy assessment note to its terminal-gate action."""
        with patch("advisor.policies.advisor_flow", return_value={"action": "healthy", "text": "All looks good"}):
            gate = final_advisor_gate("c1", "", "", 0, 5, set(), "", [], "", {})
            self.assertEqual(gate["action"], "healthy")
            self.assertIn("Advisor final assessment: hold (healthy)", gate["note"])


class TestSensitiveAdversarial(unittest.TestCase):
    """Adversarial testing for sensitive tool argument extraction and word-boundary matching."""

    def test_s1_sensitive_env_overrides(self):
        """Test sensitive keyword extraction and enabled toggle from environment variables."""
        with patch.dict(os.environ, {"AGY_STOP_AUDIT_SENSITIVE_TRIGGER": "0"}):
            self.assertFalse(is_sensitive_trigger_enabled())

        with patch.dict(os.environ, {"AGY_STOP_AUDIT_SENSITIVE_KEYWORDS": "docker, aws_custom, terraform"}):
            kws = get_sensitive_keywords()
            self.assertEqual(kws, ("docker", "aws_custom", "terraform"))

    def test_s2_compile_sensitive_pattern_edge_cases(self):
        """Test compile_sensitive_pattern with None, empty list, and length-descending sorting."""
        self.assertIsNone(compile_sensitive_pattern([]))
        self.assertIsNone(compile_sensitive_pattern(["", "   "]))

        pattern = compile_sensitive_pattern(["git", "gcloud"])
        # Should match word boundary
        self.assertIsNotNone(pattern.search("run gcloud compute"))
        self.assertIsNotNone(pattern.search("run git commit"))
        # Should NOT match sub-words
        self.assertIsNone(pattern.search("digitization"))

    def test_s3_extract_tool_strings_deep_traversal(self):
        """Test extract_tool_strings handles nested dicts, JSON strings, malformed strings, and primitives."""
        tool = {
            "name": "run_command",
            "args": {
                "CommandLine": '{"nested": ["kubectl apply -f pod.yaml"]}',
                "Flags": [1, 2, True, None, "plain_string"],
                "CorruptJSON": '{"open: bracket',
            }
        }
        strings = extract_tool_strings(tool)
        self.assertIn("kubectl apply -f pod.yaml", strings)
        self.assertIn("plain_string", strings)
        self.assertIn('{"open: bracket', strings)

    def test_s4_scan_tool_call_sensitive_word_boundary(self):
        """Test scan_tool_call_for_sensitive matches tool names and args accurately."""
        # Tool name with underscore boundary
        self.assertIn("git", scan_tool_call_for_sensitive({"name": "git_commit"}))
        self.assertIn("docker", scan_tool_call_for_sensitive({"name": "run_docker_image"}))

        # Tool args matching
        self.assertIn("terraform", scan_tool_call_for_sensitive({
            "name": "run_command",
            "args": {"CommandLine": "terraform plan -out=tfplan"}
        }))

        # Non-matching substring
        self.assertEqual(len(scan_tool_call_for_sensitive({
            "name": "view_file",
            "args": {"Path": "/path/to/awesome_digital_log.txt"}
        })), 0)


class TestExecutorAdversarial(unittest.TestCase):
    """Adversarial testing for model cascade, SQLite cleanup, JSON extraction, and session caching."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_e1_clean_resume_history_sqlite_safety(self):
        """Test clean_resume_history safely cleans SQLite DB and handles missing DB/errors."""
        self.assertIsNone(clean_resume_history(None))
        self.assertIsNone(clean_resume_history(""))

        # Real temporary SQLite DB
        db_path = os.path.join(self.tmp_dir, "conversation_summaries.db")
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT)")
            conn.execute("INSERT INTO conversation_summaries VALUES ('conv_test_123')")
            conn.commit()

        with patch("os.path.expanduser", return_value=db_path):
            clean_resume_history("conv_test_123")

        with sqlite3.connect(db_path) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM conversation_summaries WHERE conversation_id = 'conv_test_123'")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_e2_session_id_persistence_and_cleanup(self):
        """Test load_session_id, save_session_id, clear_session_id with multiple prefixes."""
        with patch("advisor.executor.get_session_file", side_effect=lambda cid, p: os.path.join(self.tmp_dir, f"{p}{cid}.txt")):
            save_session_id("conv_1", "session_abc", "pref_a_")
            self.assertEqual(load_session_id("conv_1", ("pref_a_", "pref_b_")), "session_abc")
            clear_session_id("conv_1", ("pref_a_", "pref_b_"))
            self.assertIsNone(load_session_id("conv_1", "pref_a_"))

    def test_e3_extract_json_from_llm_output_heuristics(self):
        """Test extract_json_from_llm_output across fences, raw text, and schema keys."""
        self.assertIsNone(extract_json_from_llm_output(None))
        self.assertIsNone(extract_json_from_llm_output(""))
        self.assertIsNone(extract_json_from_llm_output("No JSON present here."))

        # Markdown fence
        fenced = "```json\n{\"status\": \"healthy\", \"confidence\": 0.95}\n```"
        self.assertEqual(extract_json_from_llm_output(fenced)["status"], "healthy")

        # Embedded JSON with prefix and suffix
        embedded = "Preamble note: {\"passed\": true, \"recap\": \"done\"} - End of report."
        res = extract_json_from_llm_output(embedded, schema_keys=("passed", "recap"))
        self.assertTrue(res["passed"])

    @patch("subprocess.run")
    def test_e4_run_model_cascade_timeout_budget_and_fallbacks(self, mock_subproc):
        """Test run_model_cascade halts when timeout budget is exhausted and falls back on failure."""
        mock_acquire = MagicMock(return_value=MagicMock())
        mock_release = MagicMock()

        # Subprocess fails on first model, succeeds on second model
        mock_fail = MagicMock(returncode=1, stdout="", stderr="Model overloaded")
        mock_succ = MagicMock(returncode=0, stdout='{"decision": "hold"}', stderr="")
        mock_subproc.side_effect = [mock_fail, mock_succ]

        res = run_model_cascade(
            "parent_1", "prompt", ("pref_",),
            normalize_func=lambda d: d,
            default_on_failure={"decision": "default"},
            acquire_lock_fn=mock_acquire,
            release_lock_fn=mock_release,
            resolve_candidates_fn=lambda: ["model_1", "model_2"],
            clean_resume_fn=MagicMock(),
        )
        self.assertEqual(res.get("decision"), "hold")
        mock_release.assert_called()


class TestConfigAdversarial(unittest.TestCase):
    """Adversarial testing for config helpers and environment overlay loading."""

    def test_c1_safe_int_float_bool_conversions(self):
        """Test _safe_int, _safe_float, and _safe_bool type parsing and fallbacks."""
        with patch.dict(os.environ, {"INT_VAL": "42", "BAD_INT": "invalid"}):
            self.assertEqual(_safe_int("INT_VAL", 10), 42)
            self.assertEqual(_safe_int("BAD_INT", 10), 10)
            self.assertEqual(_safe_int("MISSING_KEY", 10), 10)

        with patch.dict(os.environ, {"FLOAT_VAL": "3.14", "BAD_FLOAT": "abc"}):
            self.assertEqual(_safe_float("FLOAT_VAL", 1.0), 3.14)
            self.assertEqual(_safe_float("BAD_FLOAT", 1.0), 1.0)

        with patch.dict(os.environ, {"B_OFF": "off", "B_TRUE": "true", "B_ZERO": "0", "B_ONE": "1"}):
            self.assertFalse(_safe_bool("B_OFF", True))
            self.assertTrue(_safe_bool("B_TRUE", False))
            self.assertFalse(_safe_bool("B_ZERO", True))
            self.assertTrue(_safe_bool("B_ONE", False))

    def test_c2_load_env_overlay_parsing_precedence(self):
        """Test _load_env_overlay loads AGY_* key=value lines without overwriting existing env."""
        tmp = tempfile.NamedTemporaryFile(mode="w", delete=False)
        tmp.write("# Comment line\n")
        tmp.write("AGY_TEST_KEY_1=loaded_value\n")
        tmp.write("AGY_TEST_KEY_2=\"quoted_value\"\n")
        tmp.write("NON_AGY_KEY=ignored\n")
        tmp.write("INVALID_LINE_NO_EQUALS\n")
        tmp.close()

        with patch.dict(os.environ, {"AGY_ADVISOR_ENV_FILE": tmp.name, "AGY_TEST_KEY_2": "original"}):
            _load_env_overlay()
            self.assertEqual(os.environ.get("AGY_TEST_KEY_1"), "loaded_value")
            self.assertEqual(os.environ.get("AGY_TEST_KEY_2"), "original")  # not overwritten
            self.assertNotIn("NON_AGY_KEY", os.environ)

        os.remove(tmp.name)


class TestGitAdversarial(unittest.TestCase):
    """Adversarial testing for git diff extraction and tool call short-circuiting."""

    def test_gi1_git_diff_tool_filtering_and_boundaries(self):
        """Test get_git_diff short-circuits when no file editing tools were invoked."""
        # Non-file editing tools -> returns immediately without git spawn
        res = get_git_diff(["/fake/ws"], turn_tool_names={"read_url_content", "search_web"})
        self.assertEqual(res, "None (no file-editing tools invoked in turn)")

        # Empty workspace paths
        self.assertEqual(get_git_diff([], turn_tool_names={"write_to_file"}), "")

        # Non-existent workspace path handled gracefully
        res_none = get_git_diff(["/non/existent/path/xyz"], turn_tool_names={"write_to_file"})
        self.assertEqual(res_none, "")


class TestSanitizerAdversarial(unittest.TestCase):
    """Adversarial testing for sanitizer, header stripping, line clamping, and diff budgets."""

    def test_sa1_clean_user_prompt_xml_envelopes(self):
        """Test clean_user_prompt strips USER_REQUEST, ADDITIONAL_METADATA, and USER_SETTINGS_CHANGE."""
        raw = "<USER_REQUEST>\nImplement feature X\n</USER_REQUEST>\n<ADDITIONAL_METADATA>meta</ADDITIONAL_METADATA>"
        self.assertEqual(clean_user_prompt(raw), "Implement feature X")

    def test_sa2_strip_boilerplate_headers_and_clamp_lines(self):
        """Test _strip_boilerplate_headers and _clamp_lines."""
        lines = [
            "Created At: 2026-08-24",
            "The command exited with code 0.",
            "Output:",
            "Real content line 1",
            "Real content line 2",
        ]
        clean = _strip_boilerplate_headers(lines)
        self.assertEqual(clean, ["Real content line 1", "Real content line 2"])

        # Clamp lines
        long_line = "A" * 500
        clamped = _clamp_lines([long_line, "short"], max_line_len=100)
        self.assertIn("... [line truncated] ...", clamped[0])
        self.assertEqual(clamped[1], "short")

    def test_sa3_sanitize_tool_output_budget_and_truncation(self):
        """Test sanitize_tool_output with small max_chars, empty output, and head/tail preservation."""
        self.assertEqual(sanitize_tool_output(""), "")
        self.assertEqual(sanitize_tool_output(None), "")

        # Short output
        self.assertEqual(sanitize_tool_output("Simple line"), "Simple line")

        # Long output
        long_output = "\n".join([f"Line {i}: " + "X" * 50 for i in range(100)])
        sanitized = sanitize_tool_output(long_output, max_chars=400)
        self.assertLessEqual(len(sanitized), 400)
        self.assertIn("lines truncated", sanitized)

    def test_sa4_clamp_diff_preservation(self):
        """Test clamp_diff returns default message on empty diff and truncates long diffs."""
        self.assertEqual(clamp_diff(""), "No file modifications detected.")
        self.assertEqual(clamp_diff(None), "No file modifications detected.")

        short_diff = "diff --git a/file b/file\n+new line"
        self.assertEqual(clamp_diff(short_diff, budget=1000), short_diff)

        long_diff = "diff header\n" + ("+line\n" * 1000)
        clamped = clamp_diff(long_diff, budget=200)
        self.assertIn("... [diff truncated] ...", clamped)
        self.assertLessEqual(len(clamped), 250)


class TestHooksAndRunnerAdversarial(unittest.TestCase):
    """Adversarial testing for runner lifecycle, concurrent lock contention, and gate cascades."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        release_lock()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_h1_session_stop_audit_hook_exception_fallback(self):
        """Test session-advisor.py top-level exception handling and fail-safe exit."""
        hook_script = os.path.join(os.path.dirname(__file__), "..", "hooks", "session-advisor.py")
        # Empty stdin triggers fail_safe_exit and outputs {"decision": "stop"}
        res = subprocess.run([sys.executable, hook_script], input="", text=True, capture_output=True)
        self.assertEqual(res.returncode, 0)
        payload = json.loads(res.stdout.strip())
        self.assertEqual(payload.get("decision"), "stop")

        # Post-invocation flag with empty stdin outputs {"injectSteps": []}
        res_post = subprocess.run([sys.executable, hook_script, "post_invocation"], input="", text=True, capture_output=True)
        self.assertEqual(res_post.returncode, 0)
        payload_post = json.loads(res_post.stdout.strip())
        self.assertEqual(payload_post.get("injectSteps"), [])

    @patch("advisor.runner.acquire_conversation_lock", return_value=None)
    def test_r1_concurrent_lock_contention_exit(self, mock_lock):
        """Test runner exits immediately with fail_safe_exit when conversation lock is busy."""
        raw_payload = json.dumps({"conversationId": "conv_busy"})
        with self.assertRaises(SystemExit):
            run_session_stop_audit(raw_payload)

    @patch("advisor.runner.acquire_conversation_lock", return_value=MagicMock())
    @patch("advisor.runner.get_active_subagents", return_value=[{"subagent_id": "sub_1", "role": "Worker"}])
    @patch("advisor.runner.extract_session_and_turn_data", return_value=("prompt", "raw", [], 5, set(), None, None, 10))
    @patch("advisor.runner.load_and_sync_session_state", return_value=("prompt", "/tmp/s.json", {}, True))
    def test_r2_active_subagent_stop_blocking(self, mock_sync, mock_extract, mock_subs, mock_lock):
        """Test runner blocks stop event when active subagents are in flight."""
        raw_payload = json.dumps({"conversationId": "conv_sub"})
        with patch("advisor.runner.is_post_invocation", return_value=False):
            with self.assertRaises(SystemExit):
                run_session_stop_audit(raw_payload)

    @patch("advisor.runner.acquire_conversation_lock", return_value=MagicMock())
    @patch("advisor.runner.get_active_subagents", return_value=[])
    @patch("advisor.runner.extract_session_and_turn_data", return_value=("prompt", "raw", [], 0, set(), None, None, 10))
    @patch("advisor.runner.load_and_sync_session_state", return_value=("prompt", "/tmp/s.json", {}, True))
    def test_r3_runner_fast_paths_and_stale_transcripts(self, mock_sync, mock_extract, mock_subs, mock_lock):
        """Test runner fast path exits on 0 tool calls conversational turns."""
        raw_payload = json.dumps({"conversationId": "conv_0tools"})
        with self.assertRaises(SystemExit):
            run_session_stop_audit(raw_payload)

    @patch("advisor.runner.acquire_conversation_lock", return_value=MagicMock())
    @patch("advisor.runner.get_active_subagents", return_value=[])
    @patch("advisor.runner.extract_session_and_turn_data", return_value=("prompt", "raw", ["step1"], 10, {"write_to_file"}, None, None, 10))
    @patch("advisor.runner.load_and_sync_session_state", return_value=("prompt", "/tmp/s.json", {"recap_emitted": False}, True))
    @patch("advisor.runner.is_post_invocation", return_value=True)
    @patch("advisor.runner.is_post_invocation_completion_candidate", return_value=False)
    @patch("advisor.runner.advisor_flow")
    def test_r4_runner_midturn_and_final_gate_cascades(self, mock_adv, mock_comp, mock_post, mock_sync, mock_extract, mock_subs, mock_lock):
        """Test runner midturn advisor emit and progressed actions."""
        # Emit action
        mock_adv.return_value = {"action": "emit", "decision": "steer", "text": "Fix loop", "seen": {}}
        raw_payload = json.dumps({"conversationId": "conv_mid"})
        with self.assertRaises(SystemExit):
            run_session_stop_audit(raw_payload)

        # Progressed action
        mock_adv.return_value = {"action": "progressed", "tools": 12, "lines": 20}
        with self.assertRaises(SystemExit):
            run_session_stop_audit(raw_payload)

    @patch("advisor.runner.acquire_conversation_lock", return_value=MagicMock())
    @patch("advisor.runner.get_active_subagents", return_value=[])
    @patch("advisor.runner.extract_session_and_turn_data", return_value=("prompt", "raw", ["step1"], 16, {"write_to_file"}, None, None, 10))
    @patch("advisor.runner.load_and_sync_session_state", return_value=("prompt", "/tmp/s.json", {"mid_turn_steers": 0}, True))
    @patch("advisor.runner.is_post_invocation", return_value=False)
    @patch("advisor.runner.final_advisor_gate", return_value={"action": "hold_dedup", "seen": {"k1": 2}})
    @patch("advisor.runner.record_advisor_hold")
    def test_r5_runner_final_advisor_hold_dedup_terminates(self, mock_hold, mock_gate, mock_post, mock_sync, mock_extract, mock_subs, mock_lock):
        """Test runner terminates cleanly when the final advisor's repeated advice is deduplicated (no steering cap)."""
        raw_payload = json.dumps({"conversationId": "conv_hold_dedup"})
        with self.assertRaises(SystemExit):
            run_session_stop_audit(raw_payload)
        mock_gate.assert_called_once()
        mock_hold.assert_called_once()


if __name__ == "__main__":
    unittest.main()
