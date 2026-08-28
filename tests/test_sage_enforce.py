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


def _run_hook(payload, state=None, state_file=None, disabled=False, env_extra=None):
    """Runs hooks/sage-enforce.py as a subprocess with a staged state file."""
    import subprocess
    env = dict(os.environ)
    if disabled:
        env["AGY_SAGE_DISABLED"] = "1"
    else:
        env.pop("AGY_SAGE_DISABLED", None)
    if env_extra:
        env.update(env_extra)
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
        self.journal_file = f"/tmp/agy_journal_test_{self.conv_id}.jsonl"

    def tearDown(self):
        if os.path.exists(self.state_file):
            os.unlink(self.state_file)
        counter = f"/tmp/agy_sage_enforce_{self.conv_id}.json"
        if os.path.exists(counter):
            os.unlink(counter)
        if os.path.exists(self.journal_file):
            os.unlink(self.journal_file)
        if os.path.exists(self.journal_file + ".prev"):
            os.unlink(self.journal_file + ".prev")

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

    def test_subagent_payload_never_injects(self):
        # Regression (2026-08-28): subagents share the parent's conversationId,
        # so they matched the delegate state and got blocked → recursive
        # subagent spawning. Subagent tool calls must pass clean.
        for payload in (
            {"conversationId": self.conv_id, "isSubagent": True, "toolCall": {"name": "run_command", "args": {}}},
            {"conversationId": self.conv_id, "parentConversationId": "parent-1", "toolCall": {"name": "write_to_file", "args": {}}},
            {"conversationId": self.conv_id, "agentRole": "research", "toolCall": {"name": "run_command", "args": {}}},
        ):
            res = _run_hook(payload, state={"delegate_cmd_turn": 2}, state_file=self.state_file)
            self.assertNotIn("injectSteps", res, f"subagent blocked: {payload}")

    def test_violation_injected_only_once_per_conv(self):
        # Regression (2026-08-28): every inline tool call re-injected the
        # violation message, flooding the main agent's context during long
        # validate/verify stretches. Exactly ONE injection per conv, then quiet.
        payload = {"conversationId": self.conv_id, "toolCall": {"name": "run_command", "args": {}}}
        first = _run_hook(payload, state={"delegate_cmd_turn": 2}, state_file=self.state_file)
        self.assertIn("injectSteps", first)
        for _ in range(3):
            res = _run_hook(payload, state={"delegate_cmd_turn": 2}, state_file=self.state_file)
            self.assertNotIn("injectSteps", res, "flood: repeat injection after the first")

    def test_journal_logs_violation_inject_and_suppressed(self):
        from sage.journal import read
        payload = {"conversationId": self.conv_id, "toolCall": {"name": "run_command", "args": {}}}
        env_extra = {"AGY_SAGE_JOURNAL": self.journal_file}
        first = _run_hook(payload, state={"delegate_cmd_turn": 2}, state_file=self.state_file, env_extra=env_extra)
        self.assertIn("injectSteps", first)

        second = _run_hook(payload, state={"delegate_cmd_turn": 2}, state_file=self.state_file, env_extra=env_extra)
        self.assertNotIn("injectSteps", second)

        with patch.dict(os.environ, {"AGY_SAGE_JOURNAL": self.journal_file}):
            entries = read(conv_id=self.conv_id)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["event"], "violation_inject")
        self.assertEqual(entries[0]["tool"], "run_command")
        self.assertEqual(entries[0]["count"], 1)
        self.assertEqual(entries[1]["event"], "violation_suppressed")
        self.assertEqual(entries[1]["tool"], "run_command")
        self.assertEqual(entries[1]["count"], 1)


if __name__ == "__main__":
    unittest.main()
