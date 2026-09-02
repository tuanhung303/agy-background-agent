"""tests.test_knowledge_maintenance - Staged verification test suite for Knowledge Base Maintenance.

Stages:
Stage 1: Contract, Prompt & Schema Invariants
Stage 2: Fork Session Lifecycle & Hermetic Isolation
Stage 3: KB Maintainer Execution & Environment Configuration
Stage 4: End-to-End Stop Hook Lifecycle & Statusline States
Stage 5: Adversarial Scenarios & Fault Tolerance
"""
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

from sage.config import (
    KB_MAINTENANCE_TIMEOUT,
    LITE_MODE_TIMEOUT,
    get_real_user_home,
)
from sage.lite.fork import (
    FAILED_FORKS_DIR,
    cleanup_fork_session,
    fork_conversation_session,
    prune_failed_forks_dir,
)
from sage.lite.prompt import (
    build_kb_maintainer_prompt,
    build_lite_verifier_prompt,
)
from sage.lite.runner import run_lite_stop_audit
from sage.lite.schemas import LiteVerdict
from sage.lite.verifier import (
    LITE_MODEL_CANDIDATES,
    run_kb_maintenance,
    run_lite_verification,
)
from statusline.statusline import get_sage_steer_badges, render_statusline


class TestStage1ContractAndPromptInvariants(unittest.TestCase):
    """Stage 1: Contract, Prompt & Schema Invariants."""

    def test_s1_01_verifier_prompt_includes_knowledge_criteria(self):
        prompt = build_lite_verifier_prompt("Implement feature", "Code done")
        self.assertIn("[KNOWLEDGE UPDATE CRITERIA]", prompt)
        self.assertIn("Set \"update_knowledge\": true ONLY if", prompt)
        self.assertIn("Set \"update_knowledge\": false for standard feature work", prompt)

    def test_s1_02_verifier_prompt_schema_allows_boolean_options(self):
        prompt = build_lite_verifier_prompt("Implement feature", "Code done")
        self.assertIn('"update_knowledge": false | true', prompt)
        self.assertIn('"verdict": "PASS" | "FAIL"', prompt)

    def test_s1_03_kb_maintainer_prompt_uses_absolute_paths(self):
        prompt = build_kb_maintainer_prompt()
        self.assertIn("Knowledge Base & Skill Registry Maintainer", prompt)
        self.assertIn("/Documents/GitHub/agentic/skills", prompt)
        self.assertIn("/.hermes/skills/validate/scripts/okf_validate.py", prompt)
        self.assertNotIn(" ~/Documents", prompt)
        self.assertNotIn(" ~/.hermes", prompt)

    def test_s1_04_schema_casting_all_types(self):
        # Boolean True / False
        v1 = LiteVerdict.from_dict({"verdict": "PASS", "update_knowledge": True})
        self.assertTrue(v1.update_knowledge)
        v2 = LiteVerdict.from_dict({"verdict": "PASS", "update_knowledge": False})
        self.assertFalse(v2.update_knowledge)

        # Truthy strings
        for val in ("true", "True", "1", "yes", "YES", "on", "enable", "enabled"):
            v = LiteVerdict.from_dict({"verdict": "PASS", "update_knowledge": val})
            self.assertTrue(v.update_knowledge, f"Failed on truthy string: {val}")

        # Falsy strings
        for val in ("false", "False", "0", "no", "off", "disable", "other"):
            v = LiteVerdict.from_dict({"verdict": "PASS", "update_knowledge": val})
            self.assertFalse(v.update_knowledge, f"Failed on falsy string: {val}")

        # Numeric values
        self.assertTrue(LiteVerdict.from_dict({"verdict": "PASS", "update_knowledge": 1}).update_knowledge)
        self.assertFalse(LiteVerdict.from_dict({"verdict": "PASS", "update_knowledge": 0}).update_knowledge)

        # Aliases
        v_alias1 = LiteVerdict.from_dict({"verdict": "PASS", "requires_knowledge_update": True})
        self.assertTrue(v_alias1.update_knowledge)
        v_alias2 = LiteVerdict.from_dict({"verdict": "PASS", "knowledge_update": "yes"})
        self.assertTrue(v_alias2.update_knowledge)

    def test_s1_05_get_real_user_home_escapes_isolated_home(self):
        with patch.dict(os.environ, {"HOME": "/Users/testuser/.gemini/antigravity-cli/sage_isolated_home"}):
            real = get_real_user_home()
            self.assertEqual(real, "/Users/testuser")

        with patch.dict(os.environ, {"AGY_REAL_HOME": "/custom/real/home", "HOME": "/tmp/isolated"}):
            real = get_real_user_home()
            self.assertEqual(real, "/custom/real/home")


class TestStage2ForkSessionLifecycle(unittest.TestCase):
    """Stage 2: Fork Session Lifecycle & Hermetic Isolation."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.real_cli = os.path.join(self.test_dir, "real_cli")
        self.iso_home = os.path.join(self.test_dir, "iso_home")
        os.makedirs(os.path.join(self.real_cli, "conversations"), exist_ok=True)
        os.makedirs(self.iso_home, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_s2_01_fork_creates_isolated_db_with_cascade_id(self):
        parent_id = "parent_test_conv"
        parent_db_path = os.path.join(self.real_cli, "conversations", f"{parent_id}.db")
        with sqlite3.connect(parent_db_path) as conn:
            conn.execute("CREATE TABLE trajectory_meta (cascade_id TEXT)")
            conn.execute("INSERT INTO trajectory_meta VALUES (?)", (parent_id,))
            conn.commit()

        with patch("sage.lite.fork.ensure_isolated_home", return_value=self.iso_home), \
             patch("sage.lite.fork.SAGE_CLI_DIR", os.path.join(self.iso_home, ".gemini", "antigravity-cli")), \
             patch("os.path.expanduser", side_effect=lambda p: self.real_cli if "antigravity-cli" in p else p):
            fork_id = fork_conversation_session(parent_id)
            self.assertIsNotNone(fork_id)
            self.assertTrue(fork_id.startswith(parent_id[:24]))

            fork_db = os.path.join(self.iso_home, ".gemini", "antigravity-cli", "conversations", f"{fork_id}.db")
            self.assertTrue(os.path.isfile(fork_db))

            with sqlite3.connect(fork_db) as conn:
                row = conn.execute("SELECT cascade_id FROM trajectory_meta").fetchone()
                self.assertEqual(row[0], fork_id)

    def test_s2_02_cleanup_fork_session_removes_dbs(self):
        fork_id = "fork_clean_test_123"
        conv_dir = os.path.join(self.iso_home, ".gemini", "antigravity-cli", "conversations")
        os.makedirs(conv_dir, exist_ok=True)
        db_file = os.path.join(conv_dir, f"{fork_id}.db")
        with open(db_file, "w") as f:
            f.write("mock db")

        with patch("sage.executor.SAGE_CLI_DIR", os.path.join(self.iso_home, ".gemini", "antigravity-cli")), \
             patch("sage.executor.CONV_DB_DIR", conv_dir):
            cleanup_fork_session(fork_id)
            self.assertFalse(os.path.exists(db_file))

    def test_s2_03_prune_failed_forks_dir(self):
        os.makedirs(FAILED_FORKS_DIR, exist_ok=True)
        for i in range(25):
            fp = os.path.join(FAILED_FORKS_DIR, f"test_failed_fork_{i}.jsonl")
            with open(fp, "w") as f:
                f.write(f"log {i}")

        prune_failed_forks_dir()
        remaining = [f for f in os.listdir(FAILED_FORKS_DIR) if f.startswith("test_failed_fork_")]
        self.assertLessEqual(len(remaining), 20)

        for f in os.listdir(FAILED_FORKS_DIR):
            if f.startswith("test_failed_fork_"):
                try:
                    os.remove(os.path.join(FAILED_FORKS_DIR, f))
                except OSError:
                    pass


class TestStage3KbMaintainerExecution(unittest.TestCase):
    """Stage 3: KB Maintainer Execution & Environment Configuration."""

    @patch("sage.lite.verifier.subprocess.run")
    @patch("sage.lite.verifier.ensure_isolated_home", return_value="/tmp/test_iso_home")
    def test_s3_01_run_kb_maintenance_environment_and_args(self, mock_iso, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="No knowledge base maintenance required.", stderr="")
        summary = run_kb_maintenance(
            parent_conv_id="parent_conv_abc",
            fork_conv_id="fork_conv_xyz",
            cwd="/test/workspace",
        )
        self.assertEqual(summary, "No knowledge base maintenance required.")
        self.assertTrue(mock_run.called)

        cmd = mock_run.call_args[0][0]
        self.assertIn("--conversation", cmd)
        self.assertIn("fork_conv_xyz", cmd)
        self.assertIn("-p", cmd)
        self.assertIn("--disable-slash-commands", cmd)

        env = mock_run.call_args[1]["env"]
        self.assertEqual(env["AGY_STOP_AUDIT_ACTIVE"], "1")
        self.assertEqual(env["HOME"], "/tmp/test_iso_home")
        self.assertIn("AGY_REAL_HOME", env)
        self.assertIn(".local/bin", env["PATH"])

    @patch("sage.lite.verifier.subprocess.run")
    @patch("sage.lite.verifier.ensure_isolated_home", return_value="/tmp/test_iso_home")
    def test_s3_02_run_kb_maintenance_model_fallback_on_failure(self, mock_iso, mock_run):
        # First model candidate fails with code 1, second succeeds
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="Candidate 1 failed"),
            MagicMock(returncode=0, stdout="Updated skills/.okf catalog successfully.", stderr=""),
        ]
        summary = run_kb_maintenance(
            parent_conv_id="parent_conv_abc",
            fork_conv_id="fork_conv_xyz",
        )
        self.assertEqual(summary, "Updated skills/.okf catalog successfully.")
        self.assertEqual(mock_run.call_count, 2)

    @patch("sage.lite.verifier.subprocess.run")
    @patch("sage.lite.verifier.ensure_isolated_home", return_value="/tmp/test_iso_home")
    def test_s3_03_run_kb_maintenance_timeout_configured(self, mock_iso, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Done", stderr="")
        run_kb_maintenance(
            parent_conv_id="parent_conv_abc",
            fork_conv_id="fork_conv_xyz",
            timeout=50.0,
        )
        cand_timeout = mock_run.call_args[1]["timeout"]
        self.assertLessEqual(cand_timeout, 50.0)
        self.assertGreaterEqual(cand_timeout, 2.0)


class TestStage4EndToEndStopHookLifecycle(unittest.TestCase):
    """Stage 4: End-to-End Stop Hook Lifecycle & Statusline States."""

    def setUp(self):
        self.tmp_files = []

    def tearDown(self):
        for f in self.tmp_files:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass

    @patch("sage.lite.runner.fail_safe_exit")
    @patch("sage.lite.runner.fork_conversation_session", side_effect=["fork_ver_s4_1", "fork_kb_s4_2"])
    @patch("sage.lite.runner.cleanup_fork_session")
    @patch("sage.lite.runner.run_lite_verification")
    @patch("sage.lite.runner.run_kb_maintenance", return_value="Knowledge update complete: created skills/new-tool/SKILL.md")
    @patch("sage.lite.runner.load_and_sync_session_state")
    def test_s4_01_full_lifecycle_with_knowledge_update(self, mock_load, mock_kb, mock_ver, mock_clean, mock_fork, mock_exit):
        mock_exit.side_effect = SystemExit(0)
        mock_load.return_value = ("prompt", "/tmp/mock_state.json", {}, False)
        mock_ver.return_value = LiteVerdict(
            verdict="PASS",
            action="",
            comment="All tests verified.",
            proof=["Live output validated HTTP 200"],
            update_knowledge=True,
        )
        payload = {
            "conversationId": "test_e2e_kb_unique_1",
            "transcript_path": "/tmp/mock_transcript.jsonl",
        }
        with patch("sage.lite.runner._read_transcript_steps", return_value=[
            {"type": "USER_INPUT", "content": "Build new agent tool"},
            {"type": "PLANNER_RESPONSE", "content": "Done", "tool_calls": [
                {"name": "write_to_file", "args": {"TargetFile": "/src/tool.py"}},
            ]},
        ]):
            try:
                run_lite_stop_audit(json.dumps(payload))
            except SystemExit:
                pass

            mock_kb.assert_called_once_with(
                parent_conv_id="test_e2e_kb_unique_1",
                fork_conv_id="fork_kb_s4_2",
                cwd=mock_kb.call_args[1]["cwd"],
            )
            self.assertEqual(mock_clean.call_count, 2)
            mock_exit.assert_called_once_with("Work verified cleanly by Lite Mode.")

    @patch("sage.lite.runner.fail_safe_exit")
    @patch("sage.lite.runner.fork_conversation_session", return_value="fork_ver_s4_only")
    @patch("sage.lite.runner.cleanup_fork_session")
    @patch("sage.lite.runner.run_lite_verification")
    @patch("sage.lite.runner.run_kb_maintenance")
    @patch("sage.lite.runner.load_and_sync_session_state")
    def test_s4_02_lifecycle_bypasses_kb_when_not_requested(self, mock_load, mock_kb, mock_ver, mock_clean, mock_fork, mock_exit):
        mock_exit.side_effect = SystemExit(0)
        mock_load.return_value = ("prompt", "/tmp/mock_state.json", {}, False)
        mock_ver.return_value = LiteVerdict(
            verdict="PASS",
            action="",
            comment="Local fix verified.",
            proof=["Live output validated HTTP 200"],
            update_knowledge=False,
        )
        payload = {
            "conversationId": "test_e2e_no_kb_unique_2",
            "transcript_path": "/tmp/mock_transcript.jsonl",
        }
        with patch("sage.lite.runner._read_transcript_steps", return_value=[
            {"type": "USER_INPUT", "content": "Fix typo"},
            {"type": "PLANNER_RESPONSE", "content": "Done", "tool_calls": [
                {"name": "write_to_file", "args": {"TargetFile": "/src/typo.py"}},
            ]},
        ]):
            try:
                run_lite_stop_audit(json.dumps(payload))
            except SystemExit:
                pass

            mock_kb.assert_not_called()
            self.assertEqual(mock_clean.call_count, 1)
            mock_exit.assert_called_once_with("Work verified cleanly by Lite Mode.")

    def test_s4_03_statusline_updating_renders_correctly(self):
        cid = "conv_stat_kb_test_s4"
        sf = f"/tmp/agy_sage_{cid}_staged.json"
        self.tmp_files.append(sf)
        with patch("statusline.statusline.safe_id", return_value=f"{cid}_staged"):
            with open(sf, "w", encoding="utf-8") as f:
                json.dump({"lite_status": "updating knowledge/memory", "sage_status": "updating"}, f)
            data = {"conversation_id": cid, "model": "Gemini 3.7 Flash (High)"}
            self.assertEqual(get_sage_steer_badges(data), [])
            rendered = render_statusline(data)
            self.assertIn("updating knowledge/memory...", rendered)


class TestStage5AdversarialAndFaultTolerance(unittest.TestCase):
    """Stage 5: Adversarial Scenarios & Fault Tolerance."""

    @patch("sage.lite.runner.fail_safe_exit")
    @patch("sage.lite.runner.fork_conversation_session", side_effect=["fork_ver_adv", "fork_kb_adv"])
    @patch("sage.lite.runner.cleanup_fork_session")
    @patch("sage.lite.runner.run_lite_verification")
    @patch("sage.lite.runner.run_kb_maintenance", side_effect=Exception("Subprocess crashed unexpected"))
    @patch("sage.lite.runner.load_and_sync_session_state")
    def test_s5_01_maintainer_exception_fails_safe_without_crashing(self, mock_load, mock_kb, mock_ver, mock_clean, mock_fork, mock_exit):
        mock_exit.side_effect = SystemExit(0)
        mock_load.return_value = ("prompt", "/tmp/mock_state.json", {}, False)
        mock_ver.return_value = LiteVerdict(
            verdict="PASS",
            action="",
            proof=["Live output exit code 0"],
            update_knowledge=True,
        )
        payload = {"conversationId": "test_adv_exc_unique", "transcript_path": "/tmp/a.jsonl"}
        with patch("sage.lite.runner._read_transcript_steps", return_value=[
            {"type": "USER_INPUT", "content": "Update code"},
            {"type": "PLANNER_RESPONSE", "content": "Done", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/a.py"}}]},
        ]):
            try:
                run_lite_stop_audit(json.dumps(payload))
            except SystemExit:
                pass
            mock_exit.assert_called_once_with("Work verified cleanly by Lite Mode.")

    @patch("sage.lite.runner.fail_safe_exit")
    @patch("sage.lite.runner.fork_conversation_session", side_effect=["fork_ver_adv", None])
    @patch("sage.lite.runner.cleanup_fork_session")
    @patch("sage.lite.runner.run_lite_verification")
    @patch("sage.lite.runner.run_kb_maintenance")
    @patch("sage.lite.runner.load_and_sync_session_state")
    def test_s5_02_fork_failure_bypasses_maintainer_safely(self, mock_load, mock_kb, mock_ver, mock_clean, mock_fork, mock_exit):
        mock_exit.side_effect = SystemExit(0)
        mock_load.return_value = ("prompt", "/tmp/mock_state.json", {}, False)
        mock_ver.return_value = LiteVerdict(
            verdict="PASS",
            action="",
            proof=["Live output exit code 0"],
            update_knowledge=True,
        )
        payload = {"conversationId": "test_adv_fork_fail_unique", "transcript_path": "/tmp/a.jsonl"}
        with patch("sage.lite.runner._read_transcript_steps", return_value=[
            {"type": "USER_INPUT", "content": "Update code"},
            {"type": "PLANNER_RESPONSE", "content": "Done", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/a.py"}}]},
        ]):
            try:
                run_lite_stop_audit(json.dumps(payload))
            except SystemExit:
                pass
            mock_kb.assert_not_called()
            mock_exit.assert_called_once_with("Work verified cleanly by Lite Mode.")

    @patch("sage.lite.runner.emit_continue_response")
    @patch("sage.lite.runner.fork_conversation_session", return_value="fork_ver_disq")
    @patch("sage.lite.runner.cleanup_fork_session")
    @patch("sage.lite.runner.run_lite_verification")
    @patch("sage.lite.runner.run_kb_maintenance")
    @patch("sage.lite.runner.generate_contextual_reject_action", return_value="Run live pytest test execution with stdout before stopping.")
    @patch("sage.lite.runner.load_and_sync_session_state")
    def test_s5_03_disqualified_proof_prevents_kb_maintenance(self, mock_load, mock_gen, mock_kb, mock_ver, mock_clean, mock_fork, mock_cont):
        mock_cont.side_effect = SystemExit(0)
        mock_load.return_value = ("prompt", "/tmp/mock_state.json", {}, False)
        mock_ver.return_value = LiteVerdict(
            verdict="PASS",
            action="",
            proof=["unit tests passed: 10/10", "tsc build passed"],
            update_knowledge=True,
        )
        payload = {"conversationId": "test_adv_disq_unique", "transcript_path": "/tmp/a.jsonl"}
        with patch("sage.lite.runner._read_transcript_steps", return_value=[
            {"type": "USER_INPUT", "content": "Update code"},
            {"type": "PLANNER_RESPONSE", "content": "Done", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/a.py"}}]},
        ]):
            try:
                run_lite_stop_audit(json.dumps(payload))
            except SystemExit:
                pass
            mock_kb.assert_not_called()
            mock_cont.assert_called_once()



if __name__ == "__main__":
    unittest.main()
