"""
test_executor.py - Unit tests for advisor.executor module.
"""

import json
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from advisor.executor import (
    acquire_spawn_lock,
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


if __name__ == "__main__":
    unittest.main()
