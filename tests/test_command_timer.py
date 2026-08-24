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


if __name__ == "__main__":
    unittest.main()
