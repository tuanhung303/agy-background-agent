import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sage.locking import safe_id
from sage.runner import main
from sage.transcript import clean_user_prompt, get_active_turn_identity


class TestIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.test_dir, "transcript.jsonl")
        self._adv_patch = patch("sage.policies.MID_TURN_SAGE_ENABLED", 0)
        self._adv_patch.start()

    def tearDown(self):
        self._adv_patch.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_race_condition_abort_in_main(self):
        conv_id = f"test_race_{int(time.time() * 1000)}"
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Implement feature X", "created_at": "2026-08-20T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Running task", "tool_calls": [{"name": "write_to_file"}]},
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

        payload = {
            "conversationId": conv_id,
            "transcriptPath": self.transcript_path,
            "workspacePaths": [self.test_dir],
        }

        def mock_gate(*args, **kwargs):
            # Simulate fresh user input arriving during final advisor evaluation
            with open(self.transcript_path, "a") as f:
                f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Nevermind do feature Y"}) + "\n")
            return {"action": "yield", "reason": "Fresh user input detected during final advisor; yielding"}

        with patch("sage.runner.final_sage_gate", side_effect=mock_gate), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit) as cm:
            main()

        self.assertEqual(cm.exception.code, 0)
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        self.assertIn('"decision": "stop"', written)
        self.assertNotIn('"decision": "continue"', written)

    def test_steering_when_no_race_condition(self):
        conv_id = f"test_steer_{int(time.time() * 1000)}"
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Implement feature Z", "created_at": "2026-08-20T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Running task", "tool_calls": [{"name": "write_to_file"}]},
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

        payload = {
            "conversationId": conv_id,
            "transcriptPath": self.transcript_path,
            "workspacePaths": [self.test_dir],
        }

        gate_result = {"action": "emit", "decision": "steer", "text": "Write tests", "seen": {}}
        with patch("sage.runner.final_sage_gate", return_value=gate_result), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit) as cm:
            main()

        self.assertEqual(cm.exception.code, 0)
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        data = json.loads(written.strip())
        self.assertEqual(data["terminationBehavior"], "force_continue")
        self.assertEqual(data["injectSteps"][0]["userMessage"], "※ sage: Write tests")

    def test_clean_exit_when_passed(self):
        conv_id = f"test_pass_{int(time.time() * 1000)}"
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Implement feature A", "created_at": "2026-08-20T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Running task", "tool_calls": [{"name": "write_to_file"}]},
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

        payload = {
            "conversationId": conv_id,
            "transcriptPath": self.transcript_path,
            "workspacePaths": [self.test_dir],
        }

        with patch("sage.runner.final_sage_gate", return_value={"action": "healthy", "recap": "All done"}), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit) as cm:
            main()

        self.assertEqual(cm.exception.code, 0)
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        self.assertIn('"decision": "stop"', written)

    def test_guards(self):
        payload_subagent = {"isSubagent": True, "conversationId": "sub1"}
        with patch("sys.stdin.read", return_value=json.dumps(payload_subagent)), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        self.assertIn('"decision": "stop"', written)

        payload_idle = {"fullyIdle": False, "conversationId": "idle1"}
        with patch("sys.stdin.read", return_value=json.dumps(payload_idle)), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        self.assertIn('"decision": "stop"', written)

        conv_id = f"test_zero_{int(time.time() * 1000)}"
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Hello"}) + "\n")
            f.write(json.dumps({"type": "PLANNER_RESPONSE", "content": "Hi there!"}) + "\n")

        payload_zero = {"conversationId": conv_id, "transcriptPath": self.transcript_path}
        with patch("sys.stdin.read", return_value=json.dumps(payload_zero)), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        self.assertIn('"decision": "stop"', written)

    def test_fully_idle_underscore_false(self):
        payload_idle = {"fully_idle": False, "conversationId": "idle2"}
        with patch("sys.stdin.read", return_value=json.dumps(payload_idle)), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        self.assertIn('"decision": "stop"', written)

    def test_direct_hook_subprocess(self):
        hook_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "hooks", "session-sage.py"))
        res = subprocess.run([hook_path], input="{}", text=True, capture_output=True)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(json.loads(res.stdout.strip()), {"decision": "stop"})

    def test_final_advisor_error_fails_open_and_records_error_streak(self):
        """Advisor cascade failure at the final gate allows clean termination and records the error streak."""
        conv_id = f"test_commit_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        if os.path.exists(state_file):
            os.remove(state_file)

        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Task commit test", "created_at": "2026-08-20T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Running task", "tool_calls": [{"name": "write_to_file"}]},
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}

        try:
            with patch("sage.runner.final_sage_gate", return_value={"action": "error"}), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
                 patch("sys.stdout") as mock_stdout, self.assertRaises(SystemExit) as cm:
                main()

            self.assertEqual(cm.exception.code, 0)
            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            self.assertIn('"decision": "stop"', written)

            self.assertTrue(os.path.exists(state_file))
            with open(state_file, "r") as sf:
                st = json.load(sf)
            self.assertEqual(st.get("advisor_status"), "error")
            self.assertEqual(st.get("advisor_error_streak"), 1)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_post_invocation_passed_with_plain_recap(self):
        conv_id = f"test_post_pass_{int(time.time() * 1000)}"
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Create file", "created_at": "2026-08-20T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Writing", "tool_calls": [{"name": "write_to_file"}]},
            {"type": "GENERIC", "content": "File written"},
            {"type": "PLANNER_RESPONSE", "content": "Done", "tool_calls": []},
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        gate_result = {"action": "healthy", "recap": "Everything is verified."}
        with patch("sage.runner.final_sage_gate", return_value=gate_result), \
             patch("sage.policies.MID_TURN_SAGE_ENABLED", 0), \
             patch("sys.argv", ["session-sage.py", "post_invocation"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()

        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        data = json.loads(written.strip())
        self.assertEqual(data.get("terminationBehavior"), "terminate")
        msg = data["injectSteps"][0]["userMessage"]
        self.assertTrue(msg.startswith("※ sage: [RECAP·on_track] Everything is verified."))
        self.assertIn("[CMD·facilitation", msg)

    def test_post_invocation_failed_with_plain_steering(self):
        conv_id = f"test_post_steer_{int(time.time() * 1000)}"
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run tests", "created_at": "2026-08-20T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Writing", "tool_calls": [{"name": "write_to_file"}]},
            {"type": "GENERIC", "content": "File written"},
            {"type": "PLANNER_RESPONSE", "content": "Not done", "tool_calls": []},
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        gate_result = {"action": "emit", "decision": "steer", "text": "Run pytest now.", "seen": {}}
        with patch("sage.runner.final_sage_gate", return_value=gate_result), \
             patch("sage.policies.MID_TURN_SAGE_ENABLED", 0), \
             patch("sys.argv", ["session-sage.py", "post_invocation"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()

        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        data = json.loads(written.strip())
        self.assertEqual(data.get("terminationBehavior"), "force_continue")
        self.assertEqual(data["injectSteps"][0]["userMessage"], "※ sage: Run pytest now.")

    def test_post_invocation_mid_turn_never_audits_or_injects(self):
        conv_id = f"test_mid_turn_{int(time.time() * 1000)}"
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Fix it"}) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "Reading the file",
                "tool_calls": [{"name": "view_file"}],
            }) + "\n")
            f.write(json.dumps({"type": "GENERIC", "content": "file contents"}) + "\n")

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path}
        with patch("sage.runner.final_sage_gate") as gate_mock, \
             patch("sys.argv", ["session-sage.py", "post_invocation"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()

        gate_mock.assert_not_called()
        written = "".join(c.args[0] for c in mock_stdout.write.mock_calls if c.args)
        self.assertEqual(json.loads(written.strip()), {"injectSteps": []})

    def test_subagent_running_fast_exits_without_audit(self):
        conv_id = f"test_subagent_running_{int(time.time() * 1000)}"
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Dispatch task"}) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "Dispatching subagent",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Implementer", "TypeName": "self"}]}}],
            }) + "\n")
            f.write(json.dumps({
                "type": "GENERIC",
                "content": "Created the following subagents:\n{\"conversationId\": \"active-sub-999\"}",
            }) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE",
                "content": "Subagent dispatched, waiting for result.",
                "tool_calls": [],
            }) + "\n")

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        with patch("sage.runner.final_sage_gate") as gate_mock, \
             patch("sys.argv", ["session-sage.py", "post_invocation"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()

        gate_mock.assert_not_called()
        written = "".join(c.args[0] for c in mock_stdout.write.mock_calls if c.args)
        self.assertEqual(json.loads(written.strip()), {"injectSteps": []})

    def test_background_task_waits_300_seconds_before_one_steering(self):
        conv_id = f"test_background_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"

        def write_transcript(started_at, second_started_at=None, user_prompt="Run the model"):
            lines = [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": user_prompt},
                {"type": "PLANNER_RESPONSE", "content": "Launching", "tool_calls": [{"name": "run_command"}]},
                {
                    "type": "GENERIC",
                    "created_at": started_at.isoformat(),
                    "content": (
                        f"Tool is running as a background task with task id: {conv_id}/task-9\n"
                        "Task Description: agy models"
                    ),
                },
            ]
            if second_started_at:
                lines.append({
                    "type": "GENERIC",
                    "created_at": second_started_at.isoformat(),
                    "content": (
                        f"Tool is running as a background task with task id: {conv_id}/task-10\n"
                        "Task Description: second model"
                    ),
                })
            lines.append({
                "type": "PLANNER_RESPONSE",
                "content": "I will keep watching it.",
                "tool_calls": [],
            })
            with open(self.transcript_path, "w") as f:
                for item in lines:
                    f.write(json.dumps(item) + "\n")

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path}
        write_transcript(datetime.now(timezone.utc) - timedelta(seconds=30))
        with patch("sage.runner.final_sage_gate") as gate_mock, \
             patch("sys.argv", ["session-sage.py", "post_invocation"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()
        gate_mock.assert_not_called()
        written = "".join(c.args[0] for c in mock_stdout.write.mock_calls if c.args)
        self.assertEqual(json.loads(written.strip()), {"injectSteps": []})

        write_transcript(
            datetime.now(timezone.utc) - timedelta(seconds=301),
            datetime.now(timezone.utc) - timedelta(seconds=302),
        )
        outputs = []
        for _ in range(3):
            with patch("sage.runner.final_sage_gate") as gate_mock, \
                 patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()
            gate_mock.assert_not_called()
            outputs.append(json.loads("".join(
                c.args[0] for c in mock_stdout.write.mock_calls if c.args
            ).strip()))

        self.assertEqual(outputs[0]["terminationBehavior"], "force_continue")
        self.assertIn("※ steering: Background task", outputs[0]["injectSteps"][0]["userMessage"])
        self.assertEqual(outputs[1]["terminationBehavior"], "force_continue")
        self.assertNotEqual(
            outputs[0]["injectSteps"][0]["userMessage"],
            outputs[1]["injectSteps"][0]["userMessage"],
        )
        self.assertEqual(outputs[2], {"injectSteps": []})

        write_transcript(
            datetime.now(timezone.utc) - timedelta(seconds=301),
            datetime.now(timezone.utc) - timedelta(seconds=302),
            user_prompt="A new request while the same tasks are running",
        )
        with patch("sage.runner.final_sage_gate") as gate_mock, \
             patch("sys.argv", ["session-sage.py", "post_invocation"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()
        gate_mock.assert_not_called()
        written = "".join(c.args[0] for c in mock_stdout.write.mock_calls if c.args)
        self.assertEqual(json.loads(written.strip()), {"injectSteps": []})
        if os.path.exists(state_file):
            os.remove(state_file)

    def test_stop_event_can_steer_stale_task_when_fully_idle_is_false(self):
        conv_id = f"test_stale_stop_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run the model"},
            {"type": "PLANNER_RESPONSE", "content": "Launching", "tool_calls": [{"name": "run_command"}]},
            {
                "type": "GENERIC",
                "created_at": (datetime.now(timezone.utc) - timedelta(seconds=301)).isoformat(),
                "content": (
                    f"Tool is running as a background task with task id: {conv_id}/task-11\n"
                    "Task Description: stale model"
                ),
            },
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

        payload = {
            "conversationId": conv_id,
            "transcriptPath": self.transcript_path,
            "fullyIdle": False,
        }
        with patch("sys.argv", ["session-sage.py"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()

        written = "".join(c.args[0] for c in mock_stdout.write.mock_calls if c.args)
        data = json.loads(written.strip())
        self.assertEqual(data["decision"], "continue")
        self.assertIn("※ steering: Background task", data["reason"])
        if os.path.exists(state_file):
            os.remove(state_file)

    def test_recap_emitted_idempotency(self):
        conv_id = f"test_idemp_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Done task", "created_at": "2026-08-20T10:00:00Z"},
            {"type": "PLANNER_RESPONSE", "content": "Writing", "tool_calls": [{"name": "write_to_file"}]},
            {"type": "GENERIC", "content": "File written"},
            {"type": "PLANNER_RESPONSE", "content": "Finished", "tool_calls": []},
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        gate_result = {"action": "healthy", "recap": "Task completed successfully."}

        with patch("sage.runner.final_sage_gate", return_value=gate_result), \
             patch("sage.policies.MID_TURN_SAGE_ENABLED", 0), \
             patch("sys.argv", ["session-sage.py", "post_invocation"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()

        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        data = json.loads(written.strip())
        self.assertEqual(data.get("terminationBehavior"), "terminate")
        msg = data["injectSteps"][0]["userMessage"]
        self.assertTrue(msg.startswith("※ sage: [RECAP·on_track] Task completed successfully."))
        self.assertIn("[CMD·facilitation", msg)

        with patch("sage.runner.final_sage_gate", return_value=gate_result), \
             patch("sys.argv", ["session-sage.py", "post_invocation"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
             patch("sys.stdout") as mock_stdout2, \
             self.assertRaises(SystemExit):
            main()

        written2 = "".join([c.args[0] for c in mock_stdout2.write.mock_calls if c.args])
        data2 = json.loads(written2.strip())
        self.assertEqual(data2.get("injectSteps"), [])

    def test_identical_prompt_in_new_turn_gets_a_fresh_gate_evaluation(self):
        conv_id = f"test_repeated_prompt_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path}
        gate_result = {"action": "healthy", "recap": "Verified."}

        def append_turn(step_index):
            with open(self.transcript_path, "a") as f:
                for item in (
                    {
                        "type": "USER_INPUT",
                        "source": "USER_EXPLICIT",
                        "step_index": step_index,
                        "content": "Run tests",
                    },
                    {
                        "type": "PLANNER_RESPONSE",
                        "content": "Running",
                        "tool_calls": [{"name": "run_command"}],
                    },
                    {"type": "GENERIC", "content": "tests passed"},
                    {
                        "type": "PLANNER_RESPONSE",
                        "content": "Done",
                        "tool_calls": [],
                    },
                ):
                    f.write(json.dumps(item) + "\n")

        try:
            append_turn(10)
            for step_index in (10, 20):
                if step_index == 20:
                    append_turn(step_index)
                with patch(
                    "sage.runner.final_sage_gate", return_value=gate_result
                ) as gate_mock, patch(
                    "sys.argv", ["session-sage.py", "post_invocation"]
                ), patch(
                    "sys.stdin.read", return_value=json.dumps(payload)
                ), patch(
                    "sage.policies.MID_TURN_SAGE_ENABLED", 0
                ), patch.dict(
                    os.environ, {"AGY_STOP_AUDIT_TEST": "1"}
                ), patch("sys.stdout") as mock_stdout, self.assertRaises(SystemExit):
                    main()

                gate_mock.assert_called_once()
                written = "".join(
                    call.args[0] for call in mock_stdout.write.mock_calls if call.args
                )
                msg = json.loads(written.strip())["injectSteps"][0]["userMessage"]
                self.assertTrue(msg.startswith("※ sage: [RECAP·on_track] Verified."))
                self.assertIn("[CMD·facilitation", msg)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_concurrent_lock_collision_exits_cleanly_without_duplicate_steering(self):
        conv_id = f"test_lock_col_{int(time.time() * 1000)}"
        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path}

        with patch("sage.runner.acquire_conversation_lock", return_value=None), \
             patch("sage.runner.final_sage_gate") as gate_mock, \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit) as cm:
            main()

        self.assertEqual(cm.exception.code, 0)
        gate_mock.assert_not_called()
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        data = json.loads(written.strip())
        self.assertEqual(data, {"decision": "stop"})
        if os.path.exists(f"/tmp/agy_sage_{safe_id(conv_id)}.json"):
            os.remove(f"/tmp/agy_sage_{safe_id(conv_id)}.json")

    def test_child_audit_process_recursion_blocked_by_env(self):
        with patch.dict(os.environ, {"AGY_STOP_AUDIT_ACTIVE": "1"}), \
             patch("sage.runner.final_sage_gate") as gate_mock, \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit) as cm:
            main()

        self.assertEqual(cm.exception.code, 0)
        gate_mock.assert_not_called()
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        self.assertIn('"decision": "stop"', written)

    def test_repeated_identical_final_advice_dedup_terminates_loop(self):
        """No steering iteration cap: repeated identical final advisor advice terminates via hold_dedup."""
        conv_id = f"test_max_steer_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({
                "type": "USER_INPUT", "source": "USER_EXPLICIT",
                "step_index": 1, "content": "Fix code",
            }) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE", "content": "Working",
                "tool_calls": [{"name": "write_to_file"}],
            }) + "\n")

        from sage.transcript import get_active_turn_identity
        turn_identity = get_active_turn_identity(self.transcript_path)
        turn_key = hashlib.sha256(f"{turn_identity}\0Fix code".encode("utf-8")).hexdigest()

        with open(state_file, "w") as sf:
            json.dump({
                "turn_key": turn_key,
                "prompt_hash": "hash123",
                "advisor_advice_counts": {"loop_detection|fix it again|": 2},
                "recap_emitted": False,
            }, sf)

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        gate_result = {"action": "hold_dedup", "seen": {"loop_detection|fix it again|": 2}}
        try:
            with patch("sage.runner.final_sage_gate", return_value=gate_result) as gate_mock, \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()

            gate_mock.assert_called_once()
            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            self.assertIn('"decision": "stop"', written)

            with open(state_file, "r") as sf:
                st = json.load(sf)
            self.assertEqual(st.get("advisor_status"), "hold")
            self.assertEqual(st.get("advisor_holds"), 1)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_final_advisor_recap_emitted_after_previous_steering(self):
        conv_id = f"test_steer_then_pass_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        lines = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "step_index": 1, "content": "Fix code"},
            {"type": "PLANNER_RESPONSE", "content": "Working", "tool_calls": [{"name": "write_to_file"}]},
            {"type": "GENERIC", "content": "File written"},
            {"type": "PLANNER_RESPONSE", "content": "Done with fixes.", "tool_calls": []},
        ]
        with open(self.transcript_path, "w") as f:
            for item in lines:
                f.write(json.dumps(item) + "\n")

        from sage.transcript import get_active_turn_identity
        turn_identity = get_active_turn_identity(self.transcript_path)
        turn_key = hashlib.sha256(f"{turn_identity}\0Fix code".encode("utf-8")).hexdigest()

        with open(state_file, "w") as sf:
            json.dump({
                "turn_key": turn_key,
                "prompt_hash": "hash123",
                "mid_turn_steers": 1,
                "recap_emitted": False,
            }, sf)

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        gate_result = {"action": "healthy", "recap": "Everything is verified."}
        try:
            with patch("sage.runner.final_sage_gate", return_value=gate_result) as gate_mock, \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 0), \
                 patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()

            gate_mock.assert_called_once()
            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            data = json.loads(written.strip())
            self.assertEqual(data.get("terminationBehavior"), "terminate")
            msg = data["injectSteps"][0]["userMessage"]
            self.assertTrue(msg.startswith("※ sage: [RECAP·on_track] Everything is verified."))
            self.assertIn("[CMD·facilitation", msg)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_runner_unchanged_transcript_fast_exit(self):
        conv_id = f"test_unchanged_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({
                "type": "USER_INPUT", "source": "USER_EXPLICIT",
                "step_index": 1, "content": "Fix code",
            }) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE", "content": "Done",
                "tool_calls": [{"name": "write_to_file"}],
            }) + "\n")

        from sage.transcript import get_active_turn_identity
        turn_identity = get_active_turn_identity(self.transcript_path)
        turn_key = hashlib.sha256(f"{turn_identity}\0Fix code".encode("utf-8")).hexdigest()

        with open(state_file, "w") as sf:
            json.dump({
                "turn_key": turn_key,
                "prompt_hash": "hash123",
                "recap_emitted": False,
                "last_audited_line_count": 2,
                "last_final_gate_lines": 2,
            }, sf)

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        try:
            with patch("sage.runner.final_sage_gate") as gate_mock, \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()

            gate_mock.assert_not_called()
            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            self.assertIn('"decision": "stop"', written)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_stop_event_blocks_termination_when_subagents_active(self):
        conv_id = f"test_stop_subagent_{int(time.time() * 1000)}"
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run subagents"}) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE", "content": "Spawning subagent",
                "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Worker"}]}}],
            }) + "\n")
            f.write(json.dumps({
                "type": "GENERIC", "content": 'Spawned subagent with conversationId: "sub_123"',
            }) + "\n")

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        gate_result = {"action": "healthy", "recap": "Subagent work completed"}
        with patch("sage.runner.final_sage_gate", return_value=gate_result) as gate_mock, \
             patch("sys.argv", ["session-sage.py"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()

        gate_mock.assert_called_once()
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        data = json.loads(written.strip())
        self.assertEqual(data.get("decision"), "stop")

    def test_stop_event_blocks_termination_when_background_tasks_active(self):
        conv_id = f"test_stop_bg_task_{int(time.time() * 1000)}"
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run background job"}) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE", "content": "Starting job",
                "tool_calls": [{"name": "run_command", "args": {"CommandLine": "sleep 10"}}],
            }) + "\n")
            f.write(json.dumps({
                "type": "GENERIC",
                "content": 'Tool is running as a background task with task id: task-999\nTask Description: sleep 10',
                "created_at": datetime.now(timezone.utc).isoformat(),
            }) + "\n")

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        with patch("sys.argv", ["session-sage.py"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()

        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        data = json.loads(written.strip())
        self.assertEqual(data.get("decision"), "continue")
        self.assertIn("Background tasks in progress", data.get("reason", ""))

    def test_stop_event_background_task_livelock_escape_hatch_when_steered(self):
        conv_id = f"test_stop_escape_{int(time.time() * 1000)}"
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Long task"}) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE", "content": "Running",
                "tool_calls": [{"name": "run_command", "args": {"CommandLine": "watch"}}],
            }) + "\n")
            f.write(json.dumps({
                "type": "GENERIC",
                "content": f'Tool is running as a background task with task id: {conv_id}/task-999\nTask Description: watch',
                "created_at": old_time,
            }) + "\n")

        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        clean_prompt = clean_user_prompt("Long task")
        turn_key = hashlib.sha256(f"{get_active_turn_identity(self.transcript_path)}\x00{clean_prompt}".encode("utf-8")).hexdigest()
        with open(state_file, "w") as sf:
            json.dump({
                "turn_key": turn_key,
                "prompt_hash": "hash1",
                "background_steered_tasks": [f"{conv_id}/task-999"],
            }, sf)

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        try:
            with patch("sage.runner.final_sage_gate", return_value={"action": "healthy", "recap": "All done"}), \
                 patch("sys.argv", ["session-sage.py"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()

            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            data = json.loads(written.strip())
            # Must allow stop rather than blocking on continue
            self.assertEqual(data.get("decision"), "stop")
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_stop_event_grace_period_watch_limit_allows_termination(self):
        conv_id = f"test_stop_grace_limit_{int(time.time() * 1000)}"
        with open(self.transcript_path, "w") as f:
            f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Quick task"}) + "\n")
            f.write(json.dumps({
                "type": "PLANNER_RESPONSE", "content": "Running",
                "tool_calls": [{"name": "run_command", "args": {"CommandLine": "build"}}],
            }) + "\n")
            f.write(json.dumps({
                "type": "GENERIC",
                "content": f'Tool is running as a background task with task id: {conv_id}/task-123\nTask Description: build',
                "created_at": datetime.now(timezone.utc).isoformat(),
            }) + "\n")

        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        clean_prompt = clean_user_prompt("Quick task")
        turn_key = hashlib.sha256(f"{get_active_turn_identity(self.transcript_path)}\x00{clean_prompt}".encode("utf-8")).hexdigest()
        with open(state_file, "w") as sf:
            json.dump({
                "turn_key": turn_key,
                "prompt_hash": "hash2",
                "bg_watch_count": 3,  # Already reached watch limit
            }, sf)

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        try:
            with patch("sage.runner.final_sage_gate") as gate_mock, \
                 patch("sys.argv", ["session-sage.py"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()

            gate_mock.assert_not_called()

            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            data = json.loads(written.strip())
            self.assertEqual(data.get("decision"), "stop")
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)


if __name__ == "__main__":
    unittest.main()
