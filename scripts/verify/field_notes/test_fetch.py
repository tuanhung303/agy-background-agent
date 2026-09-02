#!/usr/bin/env python3
"""Verification suite for field-notes fetch, search, and sync scripts."""
import os
import subprocess
import unittest
from sage.config import get_real_user_home


class TestFieldNotesFetch(unittest.TestCase):
    """Verifies that field-notes scripts execute without permission faults and return valid output."""

    def setUp(self):
        self.real_home = get_real_user_home()
        self.central_script = os.path.join(
            self.real_home,
            "Documents",
            "GitHub",
            "field-notes",
            "scripts",
            "fetch.sh",
        )
        self.hermes_script = os.path.join(
            self.real_home,
            ".hermes",
            "skills",
            "field-notes",
            "scripts",
            "fetch.sh",
        )
        self.search_script = os.path.join(
            self.real_home,
            "Documents",
            "GitHub",
            "field-notes",
            "scripts",
            "search.sh",
        )
        self.sync_script = os.path.join(
            self.real_home,
            "Documents",
            "GitHub",
            "field-notes",
            "scripts",
            "sync.sh",
        )

    def test_01_fetch_central_script_permissions_and_output(self):
        """Test that central fetch.sh executes with exit code 0 and line numbers."""
        self.assertTrue(os.path.exists(self.central_script), f"Central script not found at {self.central_script}")
        res = subprocess.run(
            ["bash", self.central_script, "seeda", "shared", "-n"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"fetch.sh failed with stderr:\n{res.stderr}")
        self.assertNotIn("Operation not permitted", res.stderr)
        self.assertNotIn("Permission denied", res.stderr)
        self.assertIn("=== deploy (seeda / shared) ===", res.stdout)
        self.assertIn("company: seeda", res.stdout)
        self.assertIn("1: ", res.stdout)

    def test_02_fetch_hermes_symlink_script_permissions_and_output(self):
        """Test that hermes symlinked fetch.sh executes with exit code 0."""
        self.assertTrue(os.path.exists(self.hermes_script), f"Hermes script not found at {self.hermes_script}")
        res = subprocess.run(
            ["bash", self.hermes_script, "seeda", "shared", "-n"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Hermes fetch.sh failed with stderr:\n{res.stderr}")
        self.assertNotIn("Operation not permitted", res.stderr)
        self.assertNotIn("Permission denied", res.stderr)
        self.assertIn("=== deploy (seeda / shared) ===", res.stdout)
        self.assertIn("company: seeda", res.stdout)

    def test_03_fetch_tenant_specific_topic(self):
        """Test fetching a specific tenant topic (datum cbc azure)."""
        res = subprocess.run(
            ["bash", self.central_script, "datum", "cbc", "azure", "-n"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"fetch.sh failed with stderr:\n{res.stderr}")
        self.assertIn("Synapse Dedicated SQL Pool State Verification", res.stdout)
        self.assertIn("company: datum", res.stdout)
        self.assertIn("tenant: cbc", res.stdout)

    def test_04_search_quick_and_deep_modes(self):
        """Test quick and deep search execution."""
        self.assertTrue(os.path.exists(self.search_script), f"Search script not found at {self.search_script}")
        # Quick search
        res_quick = subprocess.run(
            ["bash", self.search_script, "partition"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_quick.returncode, 0, f"search.sh quick failed: {res_quick.stderr}")
        self.assertIn("Require Partition Filter Scans", res_quick.stdout)

        # Deep search
        res_deep = subprocess.run(
            ["bash", self.search_script, "proof_mapped", "--deep", "-n"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_deep.returncode, 0, f"search.sh deep failed: {res_deep.stderr}")
        self.assertIn("seeda/tcc/bq.yaml", res_deep.stdout)

    def test_05_sync_script_execution(self):
        """Test sync.sh execution and idempotency."""
        self.assertTrue(os.path.exists(self.sync_script), f"Sync script not found at {self.sync_script}")
        res = subprocess.run(
            ["bash", self.sync_script],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"sync.sh failed: {res.stderr}")
        self.assertTrue(
            "Field-notes already synced today" in res.stdout or "Field-notes committed" in res.stdout,
            f"Unexpected sync output: {res.stdout}",
        )


if __name__ == "__main__":
    unittest.main()
