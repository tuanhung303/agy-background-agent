"""
test_executor.py - Unit tests for sage.executor module.
"""

import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from sage.executor import (
    acquire_spawn_lock,
    clean_resume_history,
    clean_summary_only,
    clear_session_id,
    extract_json_from_llm_output,
    load_session_id,
    release_spawn_lock,
    run_model_cascade,
    save_session_id,
)


class TestExecutor(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.conv_id = f"test_exec_{int(time.time() * 1000)}"

    def tearDown(self):
        clear_session_id(self.conv_id, prefixes=("agy_stop_audit_session_", "agy_mid_advisor_session_"))
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_session_lifecycle(self):
        self.assertIsNone(load_session_id(self.conv_id))
        save_session_id(self.conv_id, "session_123", prefix="agy_stop_audit_session_")
        self.assertEqual(load_session_id(self.conv_id), "session_123")
        clear_session_id(self.conv_id, prefixes="agy_stop_audit_session_")
        self.assertIsNone(load_session_id(self.conv_id))

    def test_spawn_locking(self):
        fh = acquire_spawn_lock()
        self.assertIsNotNone(fh)
        release_spawn_lock(fh)

    def test_extract_json_from_markdown_blocks(self):
        raw = "Here is the response:\n```json\n{\"healthy\": false, \"guidance\": \"Stop loop\"}\n```\nDone."
        d = extract_json_from_llm_output(raw)
        self.assertIsNotNone(d)
        self.assertEqual(d.get("guidance"), "Stop loop")

    def test_extract_json_from_raw_braces(self):
        raw = "Prefix text {\"passed\": true, \"recap\": \"All green\"} suffix text"
        d = extract_json_from_llm_output(raw, schema_keys=("passed", "recap"))
        self.assertIsNotNone(d)
        self.assertTrue(d.get("passed"))
        self.assertEqual(d.get("recap"), "All green")

    def test_extract_json_returns_none_on_invalid(self):
        self.assertIsNone(extract_json_from_llm_output(""))
        self.assertIsNone(extract_json_from_llm_output("No json whatsoever here."))

    @patch("subprocess.run")
    def test_run_model_cascade_success(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"healthy": True})
        mock_run.return_value = mock_proc

        result = run_model_cascade(
            self.conv_id, "Test prompt", ("agy_stop_audit_session_",),
            lambda d: d, default_on_failure={"healthy": False}, label="TestRunner"
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.get("healthy"))

    def test_clean_resume_history_full_ephemeral_cleanup(self):
        import os
        import sqlite3
        cid = f"test_adv_{int(time.time() * 1000)}"
        fake_home = os.path.join(self.test_dir, "fake_home")
        gemini_dir = os.path.join(fake_home, ".gemini", "antigravity-cli")
        convs_dir = os.path.join(gemini_dir, "conversations")
        brain_dir = os.path.join(gemini_dir, "brain", cid)
        os.makedirs(convs_dir, exist_ok=True)
        os.makedirs(brain_dir, exist_ok=True)

        db_file = os.path.join(convs_dir, f"{cid}.db")
        with open(db_file, "w") as f:
            f.write("fake db")
        brain_sub = os.path.join(brain_dir, "transcript.jsonl")
        with open(brain_sub, "w") as f:
            f.write("{}")
        sum_db = os.path.join(gemini_dir, "conversation_summaries.db")
        with sqlite3.connect(sum_db) as conn:
            conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT)")
            conn.execute("INSERT INTO conversation_summaries VALUES (?)", (cid,))
            conn.commit()

        def custom_expanduser(path):
            if path.startswith("~"):
                return path.replace("~", fake_home)
            return path

        with patch("os.path.expanduser", side_effect=custom_expanduser):
            clean_resume_history(cid)

        self.assertFalse(os.path.exists(db_file))
        self.assertFalse(os.path.exists(brain_dir))
        with sqlite3.connect(sum_db) as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM conversation_summaries WHERE conversation_id = ?", (cid,)).fetchone()[0]
            self.assertEqual(cnt, 0)

    def test_clean_summary_only_preserves_runtime_db(self):
        import os
        import sqlite3
        cid = f"test_adv_{int(time.time() * 1000)}"
        fake_home = os.path.join(self.test_dir, "fake_home_summary")
        gemini_dir = os.path.join(fake_home, ".gemini", "antigravity-cli")
        convs_dir = os.path.join(gemini_dir, "conversations")
        brain_dir = os.path.join(gemini_dir, "brain", cid)
        os.makedirs(convs_dir, exist_ok=True)
        os.makedirs(brain_dir, exist_ok=True)

        db_file = os.path.join(convs_dir, f"{cid}.db")
        with open(db_file, "w") as f:
            f.write("fake db")
        brain_sub = os.path.join(brain_dir, "transcript.jsonl")
        with open(brain_sub, "w") as f:
            f.write("{}")
        sum_db = os.path.join(gemini_dir, "conversation_summaries.db")
        with sqlite3.connect(sum_db) as conn:
            conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT)")
            conn.execute("INSERT INTO conversation_summaries VALUES (?)", (cid,))
            conn.commit()

        def custom_expanduser(path):
            if path.startswith("~"):
                return path.replace("~", fake_home)
            return path

        with patch("os.path.expanduser", side_effect=custom_expanduser):
            clean_summary_only(cid)

        self.assertTrue(os.path.exists(db_file))
        self.assertTrue(os.path.exists(brain_dir))
        with sqlite3.connect(sum_db) as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM conversation_summaries WHERE conversation_id = ?", (cid,)).fetchone()[0]
            self.assertEqual(cnt, 0)

    @patch("subprocess.run")
    def test_run_model_cascade_session_reuse(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"healthy": True})
        mock_run.return_value = mock_proc

        # First run: initial mode, creates session
        fake_conv_dir = os.path.join(self.test_dir, "conversations")
        os.makedirs(fake_conv_dir, exist_ok=True)

        with patch("sage.executor.CONV_DB_DIR", fake_conv_dir), \
             patch("sage.executor._find_new_conv_id", return_value="sage_session_abc"):
            res1 = run_model_cascade(
                self.conv_id, "Prompt 1", ("agy_mid_sage_session_",),
                lambda d: d, default_on_failure={"healthy": False}, label="Sage"
            )
            self.assertTrue(res1.get("healthy"))
            self.assertEqual(load_session_id(self.conv_id, ("agy_mid_sage_session_",)), "sage_session_abc")

            # Second run: update mode, passes --conversation sage_session_abc
            res2 = run_model_cascade(
                self.conv_id, "Prompt 2", ("agy_mid_sage_session_",),
                lambda d: d, default_on_failure={"healthy": False}, label="Sage"
            )
            self.assertTrue(res2.get("healthy"))
            # Assert --conversation sage_session_abc was in cmd
            last_call_cmd = mock_run.call_args[0][0]
            self.assertIn("--conversation", last_call_cmd)
            self.assertIn("sage_session_abc", last_call_cmd)


if __name__ == "__main__":
    unittest.main()
