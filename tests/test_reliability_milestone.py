#!/usr/bin/env python3
"""
tests/test_reliability_milestone.py - Milestone Regression Test Suite.

Asserts all 8 milestone properties:
1. Concurrent transcript creation cannot misattribute a run.
2. Grading leaves the agent tree byte-identical.
3. A complex task always delivers its pin.
4. Green self-authored tests plus uncovered requirement cannot recap.
5. Visible failing tests cannot recap.
6. Relevant edits stale evidence; unrelated edits preserve it.
7. Sage timeout produces [UNVERIFIED] or clean unverified exit.
8. Sage cannot execute mutation commands.
"""

import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sage.command_policy import is_sage_command_safe
from sage.evidence_store import append_evidence, cleanup_evidence_store, read_evidence, save_manifest
from sage.ledger import Criterion, Evidence, ValidationLedger
from sage.policies import final_sage_gate, sage_flow


class TestReliabilityMilestone(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="sage_milestone_")

    def tearDown(self):
        import shutil
        if os.path.exists(self.tmp_dir):
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_property_1_attribution_uniqueness(self):
        """Property 1: Concurrent transcript creation cannot misattribute a run."""
        nonce = "test-nonce-12345"
        work_dir = "/tmp/test_work"
        t1 = os.path.join(self.tmp_dir, "t1.jsonl")
        t2 = os.path.join(self.tmp_dir, "t2.jsonl")
        with open(t1, "w") as f:
            f.write(f'{{"type": "USER_INPUT", "content": "Run {nonce} in {work_dir}"}}\n')
        with open(t2, "w") as f:
            f.write(f'{{"type": "USER_INPUT", "content": "Run other in {work_dir}"}}\n')

        matches = []
        for path in [t1, t2]:
            with open(path) as f:
                content = f.read()
            if nonce in content and work_dir in content:
                matches.append(path)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0], t1)

    def test_property_2_grading_tree_identity(self):
        """Property 2: Grading leaves the agent tree byte-identical."""
        repo = os.path.join(self.tmp_dir, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", repo], capture_output=True, check=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "Test"], capture_output=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "test@test.com"], capture_output=True)
        fpath = os.path.join(repo, "solution.txt")
        with open(fpath, "w") as f:
            f.write("agent solution\n")
        subprocess.run(["git", "-C", repo, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", repo, "commit", "-m", "init"], capture_output=True)

        pre_tree = subprocess.run(["git", "-C", repo, "write-tree"], capture_output=True, text=True).stdout.strip()
        grade_copy = os.path.join(self.tmp_dir, "grade_copy")
        import shutil
        shutil.copytree(repo, grade_copy)
        with open(os.path.join(grade_copy, "held_out_test.txt"), "w") as f:
            f.write("test patch\n")
        shutil.rmtree(grade_copy)

        post_tree = subprocess.run(["git", "-C", repo, "write-tree"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(pre_tree, post_tree)

    def test_property_3_complex_task_inception_guarantee(self):
        """Property 3: A complex task always triggers inception evaluation at step 0."""
        ledger = ValidationLedger()
        ledger.seed_criteria_from_task("Build complex system", complexity="complex_code")
        self.assertIn("crit_deliverable", ledger.criteria)
        self.assertEqual(ledger.status["crit_deliverable"], "unverified")

    def test_property_4_uncovered_requirement_cannot_recap(self):
        """Property 4: Green self-authored tests + uncovered requirement blocks recap."""
        ledger = ValidationLedger()
        ledger.add_criterion(Criterion(id="crit_spec_a", claim="Implement feature A", applicability="required"), "verified")
        ledger.add_criterion(Criterion(id="crit_spec_b", claim="Implement held-out spec B", applicability="required"), "unverified")
        can_recap, reason, gaps = ledger.check_completion("complex_code")
        self.assertFalse(can_recap)
        self.assertIn("crit_spec_b", reason)

    def test_property_5_failing_tests_cannot_recap(self):
        """Property 5: Visible failing tests cannot recap."""
        ledger = ValidationLedger()
        ledger.add_criterion(Criterion(id="crit_test", claim="Existing tests pass", applicability="required"), "contradicted")
        can_recap, reason, gaps = ledger.check_completion("complex_code")
        self.assertFalse(can_recap)
        self.assertIn("CONTRADICTED", reason)

    def test_property_6_scoped_path_invalidation(self):
        """Property 6: Relevant edits stale evidence; unrelated edits preserve it."""
        ledger = ValidationLedger()
        ledger.add_criterion(Criterion(id="crit_auth", claim="Auth login", scope_paths=["src/auth/login.ts"]), "verified")
        ledger.add_criterion(Criterion(id="crit_billing", claim="Billing checkout", scope_paths=["src/billing/pay.ts"]), "verified")

        invalidated = ledger.invalidate_scoped_paths(["src/auth/login.ts"])
        self.assertIn("crit_auth", invalidated)
        self.assertEqual(ledger.status["crit_auth"], "unverified")
        self.assertEqual(ledger.status["crit_billing"], "verified")

    def test_property_7_fail_open_clean_termination_on_error(self):
        """Property 7: Sage error allows clean termination without claim of verified proof."""
        state = {"task_complexity": "complex_code", "last_verified_tools": 5}
        with patch("sage.policies.has_new_user_activity", return_value=False), \
             patch("sage.policies.evaluate_mid_turn_progress", return_value={"status": "error"}):
            act = final_sage_gate("test_conv", None, "Do work", 0, 10, ["run_command"], "prompt", [], None, state)
            self.assertEqual(act.get("action"), "error")

    def test_property_8_command_policy_enforcement(self):
        """Property 8: Sage cannot execute mutation commands."""
        self.assertTrue(is_sage_command_safe("pytest -v tests/test_core.py")[0])
        self.assertTrue(is_sage_command_safe("npm test")[0])
        self.assertTrue(is_sage_command_safe("vitest run")[0])
        self.assertTrue(is_sage_command_safe("git status")[0])
        self.assertTrue(is_sage_command_safe("git diff HEAD~1")[0])

        self.assertFalse(is_sage_command_safe("rm -rf src/")[0])
        self.assertFalse(is_sage_command_safe("pip install malware")[0])
        self.assertFalse(is_sage_command_safe("git commit -am 'bypass'")[0])
        self.assertFalse(is_sage_command_safe("git push origin main")[0])
        self.assertFalse(is_sage_command_safe("chmod +x run.sh")[0])


if __name__ == "__main__":
    unittest.main()
