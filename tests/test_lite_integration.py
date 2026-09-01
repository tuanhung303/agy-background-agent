"""tests.test_lite_integration - Out-of-process CLI & subprocess integration tests for Lite Mode."""
import glob
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid

from sage.locking import safe_id


class TestLiteCLIIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        self.conv_id = f"cli_test_{uuid.uuid4().hex[:12]}"
        # Write a mutating turn into transcript with completion step
        with open(self.transcript_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "USER_INPUT", "content": "Add unit test"}) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "Added test file",
                "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/tmp/test.py"}}],
            }) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "All unit tests were added and verified cleanly.",
            }) + "\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
        for p in glob.glob(f"/tmp/agy_sage_{self.conv_id}*") + glob.glob(f"/tmp/agy_sage_{safe_id(self.conv_id)}*"):
            try:
                os.remove(p)
            except OSError:
                pass

    def test_cli_post_invocation_fail_injection(self):
        """CLI out-of-process test: PostInvocation FAIL returns strict protojson injectSteps & force_continue."""
        hook_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "hooks", "session-sage.py"))
        payload = {
            "conversationId": self.conv_id,
            "transcript_path": self.transcript_path,
            "hook_event_name": "PostInvocation",
            "cwd": self.test_dir,
        }
        env = dict(
            os.environ,
            AGY_LITE_MOCK_VERDICT="FAIL:Write a regression test now.",
            AGY_STOP_AUDIT_TEST="1",
        )
        res = subprocess.run(
            [sys.executable, hook_script, "post_invocation"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(res.returncode, 0, f"Hook failed with stderr: {res.stderr}")
        data = json.loads(res.stdout.strip())
        self.assertIn("injectSteps", data)
        self.assertIn("terminationBehavior", data)
        self.assertEqual(data["terminationBehavior"], "force_continue")
        self.assertNotIn("decision", data, "PostInvocation protojson must not contain 'decision'")
        self.assertNotIn("reason", data, "PostInvocation protojson must not contain 'reason'")
        self.assertEqual(data["injectSteps"][0]["userMessage"], "Write a regression test now.")

    def test_cli_post_invocation_pass_recap(self):
        """CLI out-of-process test: PostInvocation PASS returns strict protojson terminate with natural comment."""
        hook_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "hooks", "session-sage.py"))
        payload = {
            "conversationId": self.conv_id,
            "transcript_path": self.transcript_path,
            "hook_event_name": "PostInvocation",
            "cwd": self.test_dir,
        }
        env = dict(
            os.environ,
            AGY_LITE_MOCK_VERDICT="PASS:verified browser screenshot at /tmp/test.png.",
            AGY_STOP_AUDIT_TEST="1",
        )
        res = subprocess.run(
            [sys.executable, hook_script, "post_invocation"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(res.returncode, 0, f"Hook failed with stderr: {res.stderr}")
        data = json.loads(res.stdout.strip())
        self.assertIn("injectSteps", data)
        self.assertIn("terminationBehavior", data)
        self.assertEqual(data["terminationBehavior"], "terminate")
        self.assertNotIn("decision", data, "PostInvocation protojson must not contain 'decision'")
        self.assertEqual(data["injectSteps"][0]["userMessage"], "※ verified browser screenshot at /tmp/test.png.")

    def test_cli_post_invocation_disqualified_proof_overridden_to_fail(self):
        """CLI out-of-process test: PostInvocation PASS with only unit tests is overridden to FAIL and forces continue."""
        hook_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "hooks", "session-sage.py"))
        cid = f"{self.conv_id}_disq"
        payload = {
            "conversationId": cid,
            "transcript_path": self.transcript_path,
            "hook_event_name": "PostInvocation",
            "cwd": self.test_dir,
        }
        env = dict(
            os.environ,
            AGY_LITE_MOCK_VERDICT="PASS:all unit tests passed with 37/37 pre-push tests.",
            AGY_STOP_AUDIT_TEST="1",
        )
        res = subprocess.run(
            [sys.executable, hook_script, "post_invocation"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(res.returncode, 0, f"Hook failed with stderr: {res.stderr}")
        data = json.loads(res.stdout.strip())
        self.assertIn("injectSteps", data)
        self.assertEqual(data["terminationBehavior"], "force_continue")
        injected_msg = data["injectSteps"][0]["userMessage"]
        self.assertTrue(len(injected_msg) > 10)
        self.assertTrue(any(word in injected_msg.lower() for word in ["run", "test", "verification", "execute", "proof"]))

    def test_cli_stop_hook_fail_decision(self):
        """CLI out-of-process test: Stop hook FAIL returns strict protojson continue decision & reason."""
        hook_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "hooks", "session-sage.py"))
        payload = {
            "conversationId": self.conv_id,
            "transcript_path": self.transcript_path,
            "hook_event_name": "Stop",
            "cwd": self.test_dir,
        }
        env = dict(
            os.environ,
            AGY_LITE_MOCK_VERDICT="FAIL:Please add failure injection test.",
            AGY_STOP_AUDIT_TEST="1",
        )
        res = subprocess.run(
            [sys.executable, hook_script],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(res.returncode, 0, f"Hook failed with stderr: {res.stderr}")
        data = json.loads(res.stdout.strip())
        self.assertIn("decision", data)
        self.assertIn("reason", data)
        self.assertEqual(data["decision"], "continue")
        self.assertEqual(data["reason"], "Please add failure injection test.")
        self.assertNotIn("injectSteps", data, "Stop hook protojson must not contain 'injectSteps'")
        self.assertNotIn("terminationBehavior", data, "Stop hook protojson must not contain 'terminationBehavior'")

    def test_cli_statusline_verified_rendering(self):
        """CLI out-of-process test: statusline.py renders gray verified badge."""
        statusline_script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "statusline", "statusline.py"))
        sf = f"/tmp/agy_sage_{safe_id(self.conv_id)}.json"
        with open(sf, "w", encoding="utf-8") as f:
            json.dump({"lite_status": "verified", "sage_status": "idle"}, f)
        try:
            payload = {"conversation_id": self.conv_id, "model": "Gemini 3.7 Flash (High)"}
            res = subprocess.run(
                [sys.executable, statusline_script],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
            )
            self.assertEqual(res.returncode, 0)
            self.assertIn("\033[90mverified\033[0m", res.stdout)
        finally:
            if os.path.exists(sf):
                os.remove(sf)


if __name__ == "__main__":
    unittest.main()
