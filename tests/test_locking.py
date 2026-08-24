#!/usr/bin/env python3
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

from advisor.locking import (
    acquire_conversation_lock,
    atomic_write_json,
    cleanup_stale_tmp_files,
    log_audit,
    release_lock,
    safe_id,
)


class TestLocking(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        release_lock()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_safe_id(self):
        res1 = safe_id("conv-123_abc")
        self.assertTrue(res1.startswith("conv-123_abc_"))
        self.assertEqual(len(res1.split("_")[-1]), 8)

        res2 = safe_id("invalid/path.chars!")
        self.assertTrue(res2.startswith("invalid_path_chars__"))

        res3 = safe_id("")
        self.assertTrue(res3.startswith("_"))
        self.assertEqual(len(res3.split("_")[-1]), 8)

    def test_atomic_write_json_and_permissions(self):
        target_file = os.path.join(self.test_dir, "state.json")
        data = {"prompt_hash": "abc123", "iteration": 1}
        atomic_write_json(target_file, data)

        with open(target_file, "r") as f:
            loaded = json.load(f)
        self.assertEqual(loaded, data)

        # Mode should be 0600 (-rw-------)
        file_mode = stat.S_IMODE(os.stat(target_file).st_mode)
        self.assertEqual(file_mode, 0o600)

    def test_acquire_conversation_lock_and_permissions(self):
        conv_id = f"test_conv_{int(time.time() * 1000)}"
        fh1 = acquire_conversation_lock(conv_id)
        self.assertIsNotNone(fh1)

        lock_file = f"/tmp/agy_advisor_{safe_id(conv_id)}.lock"
        file_mode = stat.S_IMODE(os.stat(lock_file).st_mode)
        self.assertEqual(file_mode, 0o600)

        # Attempting second lock on same conversation in a subprocess to test fcntl conflict
        proc = subprocess.run([
            sys.executable, "-c",
            f"import fcntl, sys; fh = open('{lock_file}', 'w'); "
            f"fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB); sys.exit(0)"
        ], capture_output=True)
        self.assertNotEqual(proc.returncode, 0, "Second process should fail to acquire exclusive lock")

        release_lock()
        if os.path.exists(lock_file):
            os.remove(lock_file)

    def test_lock_fd_closed_on_conflict(self):
        conv_id = f"test_conflict_{int(time.time() * 1000)}"
        fh1 = acquire_conversation_lock(conv_id)
        self.assertIsNotNone(fh1)

        # In-process second acquire cleans up properly
        fh2 = acquire_conversation_lock(conv_id)
        release_lock()
        lock_file = f"/tmp/agy_advisor_{safe_id(conv_id)}.lock"
        if os.path.exists(lock_file):
            os.remove(lock_file)

    def test_cleanup_stale_tmp_files(self):
        stale_file = "/tmp/agy_advisor_stale_test_12345.json"
        fresh_file = "/tmp/agy_advisor_fresh_test_12345.json"
        try:
            with open(stale_file, "w") as f:
                f.write("{}")
            with open(fresh_file, "w") as f:
                f.write("{}")

            # Set stale_file mtime to 3 hours ago (> 7200s)
            three_hours_ago = time.time() - 10800
            os.utime(stale_file, (three_hours_ago, three_hours_ago))

            cleanup_stale_tmp_files(max_age_seconds=7200)

            self.assertFalse(os.path.exists(stale_file))
            self.assertTrue(os.path.exists(fresh_file))
        finally:
            for p in (stale_file, fresh_file):
                if os.path.exists(p):
                    os.remove(p)

    def test_log_audit(self):
        log_file = os.path.join(self.test_dir, "test.log")
        with unittest.mock.patch("advisor.locking.LOG_FILE", log_file):
            log_audit("Testing audit log entry")
        self.assertTrue(os.path.exists(log_file))
        with open(log_file, "r") as f:
            content = f.read()
        self.assertIn("Testing audit log entry", content)


if __name__ == "__main__":
    unittest.main()
