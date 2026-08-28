"""
tests.test_sage_enforce - PreToolUse zero-delay delegate enforcement hook.
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sage.session_state import get_state_file_path  # noqa: E402

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "sage-enforce.py")


def _run_hook(payload, state=None, state_file=None, disabled=False):
    """Runs hooks/sage-enforce.py as a subprocess with a staged state file."""
    import subprocess
    env = dict(os.environ)
    if disabled:
        env["AGY_SAGE_DISABLED"] = "1"
    else:
        env.pop("AGY_SAGE_DISABLED", None)
    if state is not None and state_file:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f)
    proc = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=10,
    )
    return json.loads(proc.stdout)


class TestSageEnforceHook(unittest.TestCase):
    def setUp(self):
        self.conv_id = "test_enforce_conv"
        self.state_file = get_state_file_path(self.conv_id)

    def tearDown(self):
        if os.path.exists(self.state_file):
            os.unlink(self.state_file)

    def test_inline_exec_after_delegate_cmd_injects_violation(self):
        res = _run_hook(
            {"conversationId": self.conv_id, "toolCall": {"name": "run_command", "args": {}}},
            state={"delegate_cmd_turn": 2}, state_file=self.state_file,
        )
        self.assertEqual(res["decision"], "allow")
        self.assertIn("injectSteps", res)
        self.assertIn("CMD·delegate·violation", res["injectSteps"][0]["ephemeralMessage"])

    def test_file_write_after_goal_settled_injects_violation(self):
        res = _run_hook(
            {"conversationId": self.conv_id, "toolCall": {"name": "write_to_file", "args": {}}},
            state={"goal_settled": True}, state_file=self.state_file,
        )
        self.assertIn("injectSteps", res)

    def test_read_only_tool_passes_clean(self):
        res = _run_hook(
            {"conversationId": self.conv_id, "toolCall": {"name": "grep_search", "args": {}}},
            state={"delegate_cmd_turn": 2}, state_file=self.state_file,
        )
        self.assertEqual(res, {"decision": "allow"})
        self.assertNotIn("injectSteps", res)

    def test_no_delegate_cmd_passes_clean(self):
        res = _run_hook(
            {"conversationId": self.conv_id, "toolCall": {"name": "run_command", "args": {}}},
            state={"recap_count": 1}, state_file=self.state_file,
        )
        self.assertNotIn("injectSteps", res)

    def test_missing_state_file_passes_clean(self):
        if os.path.exists(self.state_file):
            os.unlink(self.state_file)
        res = _run_hook(
            {"conversationId": self.conv_id, "toolCall": {"name": "run_command", "args": {}}},
        )
        self.assertNotIn("injectSteps", res)

    def test_disabled_env_is_passthrough(self):
        res = _run_hook(
            {"conversationId": self.conv_id, "toolCall": {"name": "run_command", "args": {}}},
            state={"delegate_cmd_turn": 2}, state_file=self.state_file, disabled=True,
        )
        self.assertEqual(res, {"decision": "allow"})

    def test_malformed_payload_passes_clean(self):
        import subprocess
        env = dict(os.environ)
        env.pop("AGY_SAGE_DISABLED", None)
        proc = subprocess.run(
            [sys.executable, HOOK], input="not-json{{", capture_output=True, text=True, env=env, timeout=10,
        )
        self.assertEqual(json.loads(proc.stdout), {"decision": "allow"})


if __name__ == "__main__":
    unittest.main()
