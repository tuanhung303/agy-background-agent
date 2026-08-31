"""
tests.test_mcp_bridge - Unit tests for Sage MCP Bridge, verification tools, and ACK steering channel.
"""
from datetime import datetime, timezone
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from sage.executor import ensure_isolated_home, SAGE_ISOLATED_HOME
from sage.guards import fail_safe_exit, set_pending_inbox_steps, get_pending_inbox_steps
from sage.mcp_bridge import dispatch_tool_call, handle_rpc_request, main as bridge_main, TOOLS
from sage.mcp_bridge_helpers import (
    drain_inbox, git_read, grep_search,
    run_command, sage_send, view_file,
)
from sage.mcp_bridge_wait import check_transcript_reaction, sage_wait
from sage.runner import run_session_stop_audit


class TestMCPBridge(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="sage_mcp_test_")
        self.inbox_dir = os.path.join(self.test_dir, "inbox")
        self.brain_dir = os.path.join(self.test_dir, "brain")
        os.makedirs(self.inbox_dir, exist_ok=True)
        os.makedirs(self.brain_dir, exist_ok=True)
        self.env_patch = patch.dict(os.environ, {
            "SAGE_INBOX_DIR": self.inbox_dir,
            "BRAIN_DIR": self.brain_dir,
        })
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        set_pending_inbox_steps([])
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_bridge_queue_ack_roundtrip(self):
        conv_id = "test_conv_001"
        res1 = sage_send(conv_id, "Check tests status")
        self.assertEqual(res1, {"ack": "queued", "seq": 1})
        res2 = sage_send(conv_id, "Run verify script")
        self.assertEqual(res2, {"ack": "queued", "seq": 2})

        inbox_file = os.path.join(self.inbox_dir, f"{conv_id}.jsonl")
        self.assertTrue(os.path.exists(inbox_file))
        with open(inbox_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["seq"], 1)
        self.assertEqual(lines[0]["message"], "Check tests status")
        self.assertEqual(lines[1]["seq"], 2)
        self.assertEqual(lines[1]["message"], "Run verify script")

    def test_drain_receipt_write(self):
        conv_id = "test_conv_002"
        sage_send(conv_id, "Msg 1")
        sage_send(conv_id, "Msg 2")

        drained = drain_inbox(conv_id)
        self.assertEqual(len(drained), 2)
        self.assertEqual(drained[0]["seq"], 1)
        self.assertEqual(drained[1]["seq"], 2)

        # Inbox file must be truncated
        inbox_file = os.path.join(self.inbox_dir, f"{conv_id}.jsonl")
        with open(inbox_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "")

        # Receipt file must exist with max seq and ts
        receipt_file = os.path.join(self.inbox_dir, f"{conv_id}.receipt")
        self.assertTrue(os.path.exists(receipt_file))
        with open(receipt_file, "r", encoding="utf-8") as rf:
            rec = json.load(rf)
        self.assertEqual(rec["seq"], 2)
        self.assertIn("ts", rec)
        self.assertEqual(rec["count"], 2)

        # Next send increments from receipt max seq
        res3 = sage_send(conv_id, "Msg 3")
        self.assertEqual(res3["seq"], 3)

    def test_hook_drain_fail_open_on_missing_dir(self):
        non_existent_inbox = os.path.join(self.test_dir, "does_not_exist")
        with patch.dict(os.environ, {"SAGE_INBOX_DIR": non_existent_inbox}):
            drained = drain_inbox("non_existent_conv")
            self.assertEqual(drained, [])

    def test_hook_drain_fail_open_on_corrupt_file(self):
        conv_id = "test_corrupt_conv"
        inbox_file = os.path.join(self.inbox_dir, f"{conv_id}.jsonl")
        with open(inbox_file, "w", encoding="utf-8") as f:
            f.write("invalid json\n{\"seq\": 5, \"message\": \"ok\"}\ncorrupt\n")
        drained = drain_inbox(conv_id)
        self.assertEqual(len(drained), 1)
        self.assertEqual(drained[0]["seq"], 5)

    def test_git_read_rejects_non_read_subcommands(self):
        forbidden = ["commit", "push", "checkout", "reset", "rebase", "rm", "branch -D", "tag -d"]
        for cmd in forbidden:
            res = git_read(cmd)
            self.assertIn("Forbidden git subcommand", res)

        empty_res = git_read("")
        self.assertIn("No git subcommand specified", empty_res)

        invalid_type = git_read(12345)
        self.assertIn("Invalid args", invalid_type)

        # Allowed subcommands pass filter (even if git fails due to test cwd)
        for allowed in ["status", "diff", "log", "show"]:
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "git ok"
                res = git_read(allowed)
                self.assertEqual(res, "git ok")
                mock_run.assert_called_once()

    def test_view_file_and_grep_search(self):
        test_file = os.path.join(self.test_dir, "sample.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("Line one\nLine two: target_pattern\nLine three\nLine four\n")

        # view_file tests
        view_all = view_file(test_file)
        self.assertIn("1: Line one", view_all)
        self.assertIn("4: Line four", view_all)

        view_slice = view_file(test_file, start=2, end=3)
        self.assertNotIn("1: Line one", view_slice)
        self.assertIn("2: Line two: target_pattern", view_slice)
        self.assertIn("3: Line three", view_slice)

        view_missing = view_file(os.path.join(self.test_dir, "missing.txt"))
        self.assertIn("Error: File not found", view_missing)

        view_dir = view_file(self.test_dir)
        self.assertIn("Error: Path is a directory", view_dir)

        # grep_search tests
        grep_res = grep_search("target_pattern", self.test_dir)
        self.assertIn("Line two: target_pattern", grep_res)

        grep_none = grep_search("non_existent_token_12345", self.test_dir)
        self.assertIn("No matches found", grep_none)

        grep_bad_regex = grep_search("[unclosed_regex", self.test_dir)
        self.assertIn("Invalid regex", grep_bad_regex)

    def test_run_command_execution_and_logging(self):
        # Default: disabled
        with patch.dict(os.environ, {"SAGE_MCP_EXEC": "0"}):
            res_disabled = run_command("echo hello")
            self.assertIn("disabled", res_disabled.get("error", ""))

        # Enabled: SAGE_MCP_EXEC=1
        with patch.dict(os.environ, {"SAGE_MCP_EXEC": "1"}):
            res_enabled = run_command("echo 'sage test run'")
            self.assertEqual(res_enabled["returncode"], 0)
            self.assertIn("sage test run", res_enabled["stdout"])

            exec_log = os.path.join(self.inbox_dir, "exec.log")
            self.assertTrue(os.path.exists(exec_log))
            with open(exec_log, "r", encoding="utf-8") as f:
                log_content = f.read()
            self.assertIn("START: echo 'sage test run'", log_content)
            self.assertIn("END (code 0)", log_content)

    def _setup_fake_transcript(self, conv_id, steps):
        t_dir = os.path.join(self.brain_dir, conv_id, ".system_generated", "logs")
        os.makedirs(t_dir, exist_ok=True)
        t_file = os.path.join(t_dir, "transcript.jsonl")
        with open(t_file, "w", encoding="utf-8") as f:
            for s in steps:
                f.write(json.dumps(s) + "\n")
        return t_file

    def test_sage_wait_status_replied(self):
        conv_id = "conv_replied"
        sage_send(conv_id, "Plan task")
        drain_inbox(conv_id)

        now_iso = datetime.now(timezone.utc).isoformat()
        self._setup_fake_transcript(conv_id, [
            {"type": "PLANNER_RESPONSE", "content": "I have verified the test suite.", "created_at": now_iso},
        ])

        res = sage_wait(conv_id, seq=1, timeout_s=1.0)
        self.assertEqual(res["status"], "replied")
        self.assertIn("verified the test suite", res["detail"])

    def test_sage_wait_status_tool_ran(self):
        conv_id = "conv_tool_ran"
        sage_send(conv_id, "Execute tests")
        drain_inbox(conv_id)

        now_iso = datetime.now(timezone.utc).isoformat()
        self._setup_fake_transcript(conv_id, [
            {
                "type": "GENERIC",
                "content": "",
                "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest"}}],
                "created_at": now_iso,
            },
        ])

        res = sage_wait(conv_id, seq=1, timeout_s=1.0)
        self.assertEqual(res["status"], "tool_ran")
        self.assertIn("run_command", res["detail"])

    def test_sage_wait_status_injected_only(self):
        conv_id = "conv_injected_only"
        sage_send(conv_id, "Wait on this")
        drain_inbox(conv_id)

        # No transcript activity; fake clock advances past timeout
        fake_times = [100.0, 100.2, 102.0]
        def fake_time():
            return fake_times.pop(0) if fake_times else 200.0

        res = sage_wait(conv_id, seq=1, timeout_s=1.0, sleep_fn=lambda _: None, time_fn=fake_time)
        self.assertEqual(res["status"], "injected_only")

    def test_sage_wait_status_timeout(self):
        conv_id = "conv_timeout"
        # Message not drained -> receipt missing
        fake_times = [100.0, 100.2, 102.0]
        def fake_time():
            return fake_times.pop(0) if fake_times else 200.0

        res = sage_wait(conv_id, seq=1, timeout_s=1.0, sleep_fn=lambda _: None, time_fn=fake_time)
        self.assertEqual(res["status"], "timeout")

    def test_executor_settings_snippet_isolated_and_unmodified_real_home(self):
        real_home_gemini = os.path.expanduser("~/.gemini/antigravity-cli/settings.json")
        real_before_mtime = os.path.getmtime(real_home_gemini) if os.path.exists(real_home_gemini) else None

        iso_home = ensure_isolated_home()
        self.assertTrue(os.path.isdir(iso_home))

        # Check isolated mcp_config.json
        iso_mcp_config = os.path.join(iso_home, ".gemini", "config", "mcp_config.json")
        self.assertTrue(os.path.exists(iso_mcp_config))
        with open(iso_mcp_config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertIn("mcpServers", cfg)
        self.assertIn("sage-mcp-bridge", cfg["mcpServers"])
        self.assertEqual(cfg["mcpServers"]["sage-mcp-bridge"]["args"], ["-m", "sage.mcp_bridge"])

        # Check isolated settings.json
        iso_settings = os.path.join(iso_home, ".gemini", "antigravity-cli", "settings.json")
        self.assertTrue(os.path.exists(iso_settings))
        self.assertFalse(os.path.islink(iso_settings))
        with open(iso_settings, "r", encoding="utf-8") as f:
            settings = json.load(f)
        self.assertIn("mcpServers", settings)
        self.assertIn("sage-mcp-bridge", settings["mcpServers"])

        # Check real settings file was NOT touched
        if real_before_mtime is not None and os.path.exists(real_home_gemini):
            self.assertEqual(os.path.getmtime(real_home_gemini), real_before_mtime)

    def test_mcp_rpc_server_protocol(self):
        init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        init_resp = handle_rpc_request(init_req)
        self.assertEqual(init_resp["id"], 1)
        self.assertEqual(init_resp["result"]["serverInfo"]["name"], "sage-mcp-bridge")

        list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        list_resp = handle_rpc_request(list_req)
        tools = list_resp["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        expected = {"view_file", "grep_search", "git_read", "sage_send", "sage_wait"}
        self.assertTrue(expected.issubset(tool_names))
        self.assertNotIn("run_command", tool_names)

        call_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "git_read", "arguments": {"args": "invalid_cmd"}},
        }
        call_resp = handle_rpc_request(call_req)
        self.assertEqual(call_resp["id"], 3)
        self.assertIn("Forbidden git subcommand", call_resp["result"]["content"][0]["text"])

    def test_selfcheck_cli(self):
        with patch("sys.argv", ["mcp_bridge.py", "--selfcheck"]):
            with self.assertRaises(SystemExit) as cm:
                bridge_main()
            self.assertEqual(cm.exception.code, 0)

    def test_hook_drains_inbox_into_inject_steps(self):
        conv_id = "test_hook_drain_conv"
        sage_send(conv_id, "Steer: verify tests")

        # When fail_safe_exit is called, drained inbox steps must be included in injectSteps
        drained = drain_inbox(conv_id)
        self.assertEqual(len(drained), 1)
        set_pending_inbox_steps([{"userMessage": drained[0]["message"]}])

        with patch("sage.guards.is_post_invocation", return_value=True):
            with patch("sys.stdout.write") as mock_stdout:
                with self.assertRaises(SystemExit) as cm:
                    fail_safe_exit("Mid-turn sage passed")
                self.assertEqual(cm.exception.code, 0)

        self.assertEqual(get_pending_inbox_steps(), [{"userMessage": "Steer: verify tests"}])


if __name__ == "__main__":
    unittest.main()
