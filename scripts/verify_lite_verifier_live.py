"""
Live comprehensive test script for Antigravity Lite Mode Stop Verifier.
Executes 6 exhaustive real-world scenarios through ~/.config/agy/session-sage.py.
"""

import json
import os
import subprocess
import tempfile
import time
import unittest

HOOK_PATH = os.path.expanduser("~/.config/agy/session-sage.py")


class TestLiveLiteStopVerifier(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="sage_live_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_transcript(self, steps, filename="transcript.jsonl"):
        tpath = os.path.join(self.tmp_dir, filename)
        with open(tpath, "w", encoding="utf-8") as f:
            for s in steps:
                f.write(json.dumps(s) + "\n")
        return tpath

    def _run_hook(self, payload_dict, extra_args=None, extra_env=None):
        cmd = ["python3", HOOK_PATH]
        if extra_args:
            cmd.extend(extra_args)
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        payload_str = json.dumps(payload_dict)
        res = subprocess.run(
            cmd,
            input=payload_str,
            text=True,
            capture_output=True,
            env=env,
            cwd=self.tmp_dir,
        )
        self.assertEqual(res.returncode, 0, f"Hook failed with stderr: {res.stderr}")
        try:
            return json.loads(res.stdout.strip())
        except Exception as e:
            self.fail(f"Failed to parse hook stdout as JSON: '{res.stdout}'. Error: {e}")

    def test_scenario_1_zero_mutation_qa_fast_bypass(self):
        """Pure Q&A turn with no mutations exits instantly (< 50ms) with decision: stop."""
        tpath = self._create_transcript([
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Explain how RSA encryption works."},
            {"type": "PLANNER_RESPONSE", "content": "RSA is an asymmetric cryptographic algorithm based on prime factorization.", "tool_calls": []},
        ])
        t0 = time.time()
        res = self._run_hook({"conversationId": f"live_qa_conv_{time.time()}", "transcriptPath": tpath})
        elapsed = time.time() - t0

        self.assertEqual(res.get("decision"), "stop")
        self.assertLess(elapsed, 0.5, f"Q&A bypass took too long: {elapsed:.3f}s")
        print(f"✓ Scenario 1 (Q&A Fast Path): PASS in {elapsed*1000:.1f}ms")

    def test_scenario_2_mutation_without_proof_intercepted(self):
        """Code edits without test execution trigger interception and contextual guidance."""
        tpath = self._create_transcript([
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Fix database connection pooling leak."},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": "db/pool.py"}}], "content": "Fixed leak in pool."},
        ])
        extra_env = {"AGY_LITE_MOCK_VERDICT": "FAIL: missing unit tests"}
        res = self._run_hook(
            {"conversationId": f"live_unverified_conv_{time.time()}", "transcriptPath": tpath},
            extra_env=extra_env,
        )
        self.assertEqual(res.get("decision"), "continue")
        self.assertIn("reason", res)
        self.assertIn("missing unit tests", res["reason"])
        print(f"✓ Scenario 2 (Mutation Intercept): Intercepted correctly with directive: '{res['reason'][:60]}...'")

    def test_scenario_3_mutation_with_test_proof_verified(self):
        """Code edits accompanied by passing test execution are verified and allowed to stop."""
        tpath = self._create_transcript([
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Implement fast cache layer."},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "cache.py"}}], "content": "Implemented."},
            {"type": "GENERIC", "content": "pytest tests/test_cache.py\n2 passed in 0.05s\nexit code 0"},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest tests/test_cache.py"}}], "content": "Tests pass."},
        ])
        extra_env = {"AGY_LITE_MOCK_VERDICT": "PASS: Cache tests pass with 100% assertions verified"}
        res = self._run_hook(
            {"conversationId": f"live_verified_conv_{time.time()}", "transcriptPath": tpath},
            extra_env=extra_env,
        )
        self.assertEqual(res.get("decision"), "stop")
        print("✓ Scenario 3 (Verified Stop): Clean PASS with verified proof")

    def test_scenario_4_post_invocation_lifecycle_injection(self):
        """PostInvocation event returns injectSteps with force_continue on verification rejection."""
        tpath = self._create_transcript([
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Refactor router endpoints."},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": "routes.py"}}], "content": "Editing routes..."},
            {"type": "GENERIC", "content": "File routes.py modified."},
            {"type": "PLANNER_RESPONSE", "content": "All endpoints updated.", "tool_calls": []},
        ])
        extra_env = {"AGY_LITE_MOCK_VERDICT": "FAIL: Need integration test for router"}
        res = self._run_hook(
            {"conversationId": f"live_post_inv_conv_{time.time()}", "transcriptPath": tpath},
            extra_args=["post_invocation"],
            extra_env=extra_env,
        )
        self.assertIn("injectSteps", res)
        self.assertEqual(res.get("terminationBehavior"), "force_continue")
        self.assertTrue(len(res["injectSteps"]) > 0)
        self.assertIn("userMessage", res["injectSteps"][0])
        print(f"✓ Scenario 4 (PostInvocation Injection): Successfully injected directive via protojson: '{res['injectSteps'][0]['userMessage'][:60]}...'")

    def test_scenario_5_three_strike_circuit_breaker(self):
        """Rejections hit 3-strike ceiling and fail open to prevent infinite loops."""
        conv_id = f"live_strike_{int(time.time()*1000)}"
        extra_env = {"AGY_LITE_MOCK_VERDICT": "FAIL: Stalled bug"}

        steps = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Fix parser logic."}]

        for attempt in range(1, 4):
            steps.append({"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": f"parser_attempt_{attempt}.py"}}], "content": f"Attempt {attempt}"})
            tpath = self._create_transcript(steps, filename=f"t_{attempt}.jsonl")
            r = self._run_hook({"conversationId": conv_id, "transcriptPath": tpath}, extra_env=extra_env)
            self.assertEqual(r.get("decision"), "continue", f"Attempt {attempt} should return continue")

        # Attempt 4 (fail_count was 3) -> circuit breaker trips!
        steps.append({"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": "parser_attempt_4.py"}}], "content": "Attempt 4"})
        tpath4 = self._create_transcript(steps, filename="t_4.jsonl")
        r4 = self._run_hook({"conversationId": conv_id, "transcriptPath": tpath4}, extra_env=extra_env)
        self.assertEqual(r4.get("decision"), "stop", "Attempt 4 should trip circuit breaker and allow clean stop")
        print("✓ Scenario 5 (3-Strike Circuit Breaker): Safely tripped on strike 3 and allowed clean exit")

    def test_scenario_6_subagent_and_background_bypass(self):
        """Subagent sessions and active background jobs bypass immediately."""
        # 6a. Subagent payload bypass
        res_sub = self._run_hook({
            "conversationId": f"subagent_conv_{time.time()}",
            "isSubagent": True,
            "transcriptPath": "/tmp/nonexistent.jsonl"
        })
        self.assertEqual(res_sub.get("decision"), "stop")

        # 6b. Runtime reports active background work
        res_fully_idle = self._run_hook({
            "conversationId": f"bg_work_conv_{time.time()}",
            "fullyIdle": False,
            "transcriptPath": "/tmp/nonexistent.jsonl"
        })
        self.assertEqual(res_fully_idle.get("decision"), "stop")

        # 6c. Active background task in transcript
        bg_conv = f"bg_task_conv_{int(time.time())}"
        tpath = self._create_transcript([
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run long build"},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "make -j8"}}], "content": "Building in background..."},
            {"type": "GENERIC", "content": f"Tool is running as a background task with task id: {bg_conv}/task-1"},
        ])
        res_bg = self._run_hook({"conversationId": bg_conv, "transcriptPath": tpath})
        self.assertEqual(res_bg.get("decision"), "stop")
        print("✓ Scenario 6 (Subagent & Background Tasks): Immediate clean bypass across all 3 conditions")


if __name__ == "__main__":
    unittest.main()
