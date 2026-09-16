#!/usr/bin/env python3
"""
Comprehensive test suite for hooks/command-timer.py in agy-optimization
"""

import importlib.util
import json
import subprocess
import time
import unittest
from pathlib import Path

HOOK_SCRIPT = Path(__file__).parent.parent / "hooks" / "command-timer.py"

def _load_command_timer():
    spec = importlib.util.spec_from_file_location("command_timer", HOOK_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCommandTimer(unittest.TestCase):
    def setUp(self):
        self.conv_id = f"test-conv-{int(time.monotonic_ns())}"

    def run_hook(self, action: str, payload: dict) -> dict:
        proc = subprocess.run(
            ["python3", str(HOOK_SCRIPT), action],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(proc.stdout) if proc.stdout.strip() else {}

    def test_pre_tool_allow_decision(self):
        payload = {
            "conversationId": self.conv_id,
            "stepIdx": 1,
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": "echo 'safe command'"
                }
            }
        }
        res = self.run_hook("pre_tool", payload)
        self.assertEqual(res.get("decision"), "allow")

    def test_command_sanitization(self):
        command_timer = _load_command_timer()

        dirty_cmd = "\x1b[31m`cat password.txt`\x1b[0m token=secret123456789"
        clean = command_timer.sanitize_command_text(dirty_cmd)
        self.assertNotIn("\x1b", clean)
        self.assertNotIn("`", clean)
        self.assertIn("REDACTED", clean)

    def test_tier_ok_fast_execution(self):
        self.run_hook("pre_tool", {
            "conversationId": self.conv_id,
            "stepIdx": 2,
            "toolCall": {"name": "run_command", "args": {"CommandLine": "ls"}}
        })
        res = self.run_hook("post_tool", {
            "conversationId": self.conv_id,
            "stepIdx": 2
        })
        self.assertEqual(res, {})

        invoc = self.run_hook("pre_invocation", {
            "conversationId": self.conv_id
        })
        self.assertEqual(invoc.get("injectSteps", []), [])

    def test_duration_tiers_classification(self):
        command_timer = _load_command_timer()

        tier, guidance = command_timer.classify_duration(5.0)
        self.assertEqual(tier, "OK")
        self.assertIsNone(guidance)

        tier, guidance = command_timer.classify_duration(15.0)
        self.assertEqual(tier, "IMPROVE_NEXT_TIME")
        self.assertIn("10s - 30s", guidance)

        tier, guidance = command_timer.classify_duration(45.0)
        self.assertEqual(tier, "ADJUST_FILTER")
        self.assertIn("30s - 90s", guidance)

        tier, guidance = command_timer.classify_duration(200.0)
        self.assertEqual(tier, "HEAVY_RECOMMEND_BACKGROUND")
        self.assertIn("1.5m - 15m", guidance)

        tier, guidance = command_timer.classify_duration(950.0)
        self.assertEqual(tier, "FORBIDDEN_EXCEEDED_LIMIT")
        self.assertIn("forbidden", guidance.lower())

    def test_pre_invocation_injected_ephemeral_format(self):
        self.run_hook("pre_tool", {
            "conversationId": self.conv_id,
            "stepIdx": 10,
            "toolCall": {"name": "run_command", "args": {"CommandLine": "sleep 12"}}
        })
        command_timer = _load_command_timer()

        state_file = command_timer.get_state_file(self.conv_id, 10)
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["startMonoNs"] = time.monotonic_ns() - int(15.5 * 1_000_000_000)
        state_file.write_text(json.dumps(state), encoding="utf-8")

        self.run_hook("post_tool", {
            "conversationId": self.conv_id,
            "stepIdx": 10
        })

        invoc = self.run_hook("pre_invocation", {
            "conversationId": self.conv_id
        })
        steps = invoc.get("injectSteps", [])
        self.assertEqual(len(steps), 1)
        self.assertIn("ephemeralMessage", steps[0])
        self.assertIn("IMPROVE_NEXT_TIME", steps[0]["ephemeralMessage"])
        self.assertIn("10s - 30s", steps[0]["ephemeralMessage"])

    def test_intent_aware_classification_and_rewrites(self):
        command_timer = _load_command_timer()

        # 1. Search anti-patterns
        tier, note, tip = command_timer.classify_command_guidance("grep -r 'needle' .", 8.0)
        self.assertEqual(tier, "SEARCH_SLOW_UNINDEXED")
        self.assertIn("grep_search", tip)
        self.assertIn("--exclude-dir", tip)

        tier, note, tip = command_timer.classify_command_guidance("find . -name '*.py'", 12.0)
        self.assertEqual(tier, "SEARCH_SLOW_UNINDEXED")
        self.assertIn("find_by_name", tip)
        self.assertIn("-maxdepth", tip)

        # 2. Test runner unscoped vs OK
        tier, note, tip = command_timer.classify_command_guidance("pytest", 20.0)
        self.assertEqual(tier, "TEST_SUITE_UNSCOPED")
        self.assertIn("-k", tip)

        tier, note, tip = command_timer.classify_command_guidance("pytest tests/test_foo.py", 10.0)
        self.assertEqual(tier, "OK")
        self.assertIsNone(note)

        # 3. Build & Package install: <= 30s is OK, > 30s suggests background/uv
        tier, note, tip = command_timer.classify_command_guidance("npm install", 25.0)
        self.assertEqual(tier, "OK")
        self.assertIsNone(note)

        tier, note, tip = command_timer.classify_command_guidance("npm install", 45.0)
        self.assertEqual(tier, "BUILD_CONSIDER_BACKGROUND")
        self.assertIn("WaitMsBeforeAsync", tip)

        tier, note, tip = command_timer.classify_command_guidance("pip install torch", 35.0)
        self.assertEqual(tier, "BUILD_CONSIDER_BACKGROUND")
        self.assertIn("uv pip install", tip)

        # 4. Git unbounded operations
        tier, note, tip = command_timer.classify_command_guidance("git log", 8.0)
        self.assertEqual(tier, "GIT_UNPAGED_OR_UNSCOPED")
        self.assertIn("-n 20", tip)

        # 5. Network operations
        tier, note, tip = command_timer.classify_command_guidance("curl https://example.com", 15.0)
        self.assertEqual(tier, "NETWORK_TIMEOUT_RECOMMENDED")
        self.assertIn("--max-time", tip)

        # 6. Exceeded limit
        tier, note, tip = command_timer.classify_command_guidance("npm run long-build", 950.0)
        self.assertEqual(tier, "FORBIDDEN_EXCEEDED_LIMIT")

    def test_multi_command_grouped_pre_invocation(self):
        command_timer = _load_command_timer()
        feedback_file = command_timer.get_feedback_file(self.conv_id)

        feedback_items = [
            {
                "command": "grep -r 'todo' .",
                "duration": 12.5,
                "tier": "SEARCH_SLOW_UNINDEXED",
                "note": "Search took 12.5s (> 5s).",
                "tip": "Use Antigravity native tool grep_search",
                "guidance": "Search took 12.5s. Tip: Use Antigravity native tool grep_search",
            },
            {
                "command": "git log",
                "duration": 9.2,
                "tier": "GIT_UNPAGED_OR_UNSCOPED",
                "note": "Git operation took 9.2s (> 5s).",
                "tip": "Pass -n 20 or --oneline to limit git log",
                "guidance": "Git operation took 9.2s. Tip: Pass -n 20",
            },
        ]
        feedback_file.write_text(json.dumps(feedback_items), encoding="utf-8")

        invoc = self.run_hook("pre_invocation", {"conversationId": self.conv_id})
        steps = invoc.get("injectSteps", [])
        self.assertEqual(len(steps), 1)

        msg = steps[0]["ephemeralMessage"]
        self.assertIn("2 Slow Commands Detected", msg)
        self.assertIn("1. `grep -r 'todo' .` (12.5s) - SEARCH_SLOW_UNINDEXED", msg)
        self.assertIn("Tip: Use Antigravity native tool grep_search", msg)
        self.assertIn("2. `git log` (9.2s) - GIT_UNPAGED_OR_UNSCOPED", msg)
        self.assertIn("Tip: Pass -n 20", msg)


if __name__ == "__main__":
    unittest.main()
