#!/usr/bin/env python3
"""
tests.test_sage_integration - End-to-end integration tests for Mid-Turn Verifier & Stop Gate Coordination.
"""

import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from sage.locking import safe_id
from sage.runner import main


class TestAdvisorIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.transcript_path = os.path.join(self.test_dir, "transcript.jsonl")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _write_transcript(self, user_text, tool_calls_count=0, is_final=False):
        steps = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "step_index": 1, "content": user_text},
        ]
        for i in range(tool_calls_count):
            steps.append({
                "type": "PLANNER_RESPONSE",
                "content": f"Step {i+1}",
                "tool_calls": [{"name": "write_to_file", "args": {"file": f"file_{i}.txt"}}],
            })
            steps.append({"type": "GENERIC", "content": "File saved"})
        if is_final:
            steps.append({
                "type": "PLANNER_RESPONSE",
                "content": "All requested changes are complete.",
                "tool_calls": [],
            })
        with open(self.transcript_path, "w", encoding="utf-8") as f:
            for s in steps:
                f.write(json.dumps(s) + "\n")

    def test_mid_turn_disabled_by_default_fast_exits(self):
        conv_id = f"test_mid_disabled_{int(time.time() * 1000)}"
        self._write_transcript("Build feature", tool_calls_count=6, is_final=False)
        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}

        with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch("sage.policies.MID_TURN_SAGE_ENABLED", 0), \
             patch("sage.sage.run_sage_model") as mock_ver, \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()

        mock_ver.assert_not_called()
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        data = json.loads(written.strip())
        self.assertEqual(data.get("injectSteps"), [])

    def test_mid_turn_below_tool_interval_fast_exits(self):
        conv_id = f"test_mid_below_int_{int(time.time() * 1000)}"
        self._write_transcript("Build feature", tool_calls_count=3, is_final=False)
        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}

        with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
             patch("sys.stdin.read", return_value=json.dumps(payload)), \
             patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
             patch("sage.sage.run_sage_model") as mock_ver, \
             patch("sys.stdout") as mock_stdout, \
             self.assertRaises(SystemExit):
            main()

        mock_ver.assert_not_called()
        written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
        data = json.loads(written.strip())
        self.assertEqual(data.get("injectSteps"), [])

    def test_mid_turn_healthy_trajectory_persists_state_and_exits_clean(self):
        conv_id = f"test_mid_healthy_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        self._write_transcript("Build feature", tool_calls_count=12, is_final=False)
        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}

        mock_output = {"healthy": True, "blind_spots": [], "guidance": ""}
        try:
            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.sage.run_sage_model", return_value=mock_output) as mock_ver, \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()

            mock_ver.assert_called_once()
            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            data = json.loads(written.strip())
            self.assertEqual(data.get("injectSteps"), [])

            self.assertTrue(os.path.exists(state_file))
            with open(state_file, "r") as sf:
                state_data = json.load(sf)
            self.assertEqual(state_data.get("last_verified_tools"), 12)
            self.assertEqual(state_data.get("mid_turn_steers"), 0)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_mid_turn_unhealthy_trajectory_injects_adviser_and_force_continue(self):
        conv_id = f"test_mid_unhealthy_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        self._write_transcript("Fix compiler errors", tool_calls_count=12, is_final=False)
        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}

        mock_output = {
            "healthy": False,
            "blind_spots": ["Repeated syntax error on line 42"],
            "guidance": "Fix missing closing bracket",
        }
        try:
            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.sage.run_sage_model", return_value=mock_output) as mock_ver, \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()

            mock_ver.assert_called_once()
            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            data = json.loads(written.strip())
            self.assertEqual(data.get("terminationBehavior"), "force_continue")
            self.assertIn("injectSteps", data)
            msg = data["injectSteps"][0]["userMessage"]
            self.assertTrue(msg.startswith("※ sage: "))
            self.assertIn("repeated syntax error on line 42", msg)

            with open(state_file, "r") as sf:
                state_data = json.load(sf)
            self.assertEqual(state_data.get("mid_turn_steers"), 1)
            self.assertEqual(state_data.get("last_verified_tools"), 12)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_mid_turn_watchout_counts_session_steers_not_budget(self):
        """Watchout injects advice and bumps session_mid_turn_steers (statusline f[]),
        but does NOT consume the mid_turn_steers budget (regression: f[0] while watchouts fired)."""
        conv_id = f"test_mid_watchout_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        self._write_transcript("Fix compiler errors", tool_calls_count=12, is_final=False)
        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}

        mock_output = {"healthy": True, "status": "watchout", "guidance": "Deliverable still missing"}
        try:
            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.sage.run_sage_model", return_value=mock_output), \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()

            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            data = json.loads(written.strip())
            self.assertEqual(data.get("terminationBehavior"), "force_continue")
            self.assertTrue(data["injectSteps"][0]["userMessage"].startswith("※ sage: "))

            with open(state_file, "r") as sf:
                state_data = json.load(sf)
            self.assertEqual(state_data.get("advisor_status"), "watchout")
            self.assertEqual(state_data.get("session_mid_turn_steers"), 1)
            self.assertEqual(state_data.get("mid_turn_steers"), 0)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_mid_turn_max_steers_ceiling_stops_further_injections(self):
        conv_id = f"test_mid_max_steers_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        self._write_transcript("Fix compiler errors", tool_calls_count=12, is_final=False)

        from sage.transcript import get_active_turn_identity
        turn_identity = get_active_turn_identity(self.transcript_path)
        turn_key = hashlib.sha256(f"{turn_identity}\x00Fix compiler errors".encode("utf-8")).hexdigest()

        with open(state_file, "w") as sf:
            json.dump({
                "turn_key": turn_key,
                "prompt_hash": "hash123",
                "steering_count": 0,
                "mid_turn_steers": 2,
                "last_verified_tools": 0,
            }, sf)

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        try:
            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.policies.MAX_MID_TURN_STEERS", 2), \
                 patch("sage.sage.run_sage_model") as mock_ver, \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()

            mock_ver.assert_not_called()
            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            data = json.loads(written.strip())
            self.assertEqual(data.get("injectSteps"), [])
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_mid_turn_coordination_with_final_stop_gate(self):
        conv_id = f"test_mid_then_stop_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"

        self._write_transcript("Implement feature Z", tool_calls_count=12, is_final=False)
        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        mid_mock = {"healthy": False, "blind_spots": ["drift detected"], "guidance": "stay focused"}

        try:
            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.sage.run_sage_model", return_value=mid_mock), \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()

            with open(state_file, "r") as sf:
                s1 = json.load(sf)
            self.assertEqual(s1.get("mid_turn_steers"), 1)
            self.assertEqual(s1.get("last_verified_tools"), 12)

            self._write_transcript("Implement feature Z", tool_calls_count=15, is_final=True)

            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.sage.run_sage_model", return_value={"healthy": True, "blind_spots": [], "guidance": "Final check passed", "recap": "Feature Z fully implemented and verified."}), \
                 patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
                 patch("sys.stdout") as mock_stdout2, \
                 self.assertRaises(SystemExit):
                main()

            written = "".join([c.args[0] for c in mock_stdout2.write.mock_calls if c.args])
            data = json.loads(written.strip())
            self.assertEqual(data.get("terminationBehavior"), "terminate")
            self.assertIn("Feature Z fully implemented and verified.", data["injectSteps"][0]["userMessage"])
            self.assertIn("※ sage:", data["injectSteps"][0]["userMessage"])

            with open(state_file, "r") as sf:
                s2 = json.load(sf)
            self.assertTrue(s2.get("recap_emitted"))
            self.assertEqual(s2.get("advisor_status"), "recap")
            self.assertEqual(s2.get("mid_turn_steers"), 1)
            self.assertEqual(s2.get("last_verified_tools"), 15)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_recap_emitted_resets_when_agent_makes_new_progress(self):
        conv_id = f"test_recap_reset_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        self._write_transcript("Perform optimization", tool_calls_count=20, is_final=True)

        from sage.transcript import get_active_turn_identity
        turn_identity = get_active_turn_identity(self.transcript_path)
        turn_key = hashlib.sha256((turn_identity + "\x00" + "Perform optimization").encode("utf-8")).hexdigest()

        with open(state_file, "w") as sf:
            json.dump({
                "turn_key": turn_key,
                "prompt_hash": "hash_opt",
                "recap_emitted": True,
                "last_verified_tools": 10,
                "last_audited_line_count": 5,
                "sage_status": "recap",
            }, sf)

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        gate_mock = {"healthy": True, "blind_spots": [], "guidance": "Optimization complete", "recap": "Optimization verified and passed."}

        try:
            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.sage.run_sage_model", return_value=gate_mock), \
                 patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()

            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            data = json.loads(written.strip())
            self.assertEqual(data.get("terminationBehavior"), "terminate")
            self.assertIn("Optimization verified and passed.", data["injectSteps"][0]["userMessage"])

            with open(state_file, "r") as sf:
                s_after = json.load(sf)
            self.assertTrue(s_after.get("recap_emitted"))
            self.assertEqual(s_after.get("last_verified_tools"), 20)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)



    def test_mid_turn_unlimited_steers_allows_continuous_advising(self):
        conv_id = f"test_mid_unlimited_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        self._write_transcript("Fix compiler errors", tool_calls_count=25, is_final=False)

        from sage.transcript import get_active_turn_identity
        turn_identity = get_active_turn_identity(self.transcript_path)
        turn_key = hashlib.sha256((turn_identity + "\x00" + "Fix compiler errors").encode("utf-8")).hexdigest()

        with open(state_file, "w") as sf:
            json.dump({
                "turn_key": turn_key,
                "prompt_hash": "hash123",
                "steering_count": 0,
                "mid_turn_steers": 10,
                "last_verified_tools": 0,
            }, sf)

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        mock_output = {"healthy": False, "blind_spots": ["Still looping"], "guidance": "Change strategy"}

        try:
            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.policies.MAX_MID_TURN_STEERS", 0), \
                 patch("sage.sage.run_sage_model", return_value=mock_output) as mock_adv, \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()

            mock_adv.assert_called_once()
            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            data = json.loads(written.strip())
            self.assertEqual(data.get("terminationBehavior"), "force_continue")
            with open(state_file, "r") as sf:
                state_data = json.load(sf)
            self.assertEqual(state_data.get("mid_turn_steers"), 11)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_staleness_gate_detects_transcript_advance_without_tools(self):
        conv_id = f"test_mid_stale_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        self._write_transcript("Fix issue", tool_calls_count=12, is_final=False)

        from sage.transcript import get_active_turn_identity
        turn_identity = get_active_turn_identity(self.transcript_path)
        turn_key = hashlib.sha256((turn_identity + "\x00" + "Fix issue").encode("utf-8")).hexdigest()

        with open(state_file, "w") as sf:
            json.dump({
                "turn_key": turn_key,
                "prompt_hash": "hash123",
                "steering_count": 0,
                "mid_turn_steers": 0,
                "last_verified_tools": 0,
            }, sf)

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}

        def side_effect_advisor(*args, **kwargs):
            # Simulate agent writing response or new line to transcript during advisor evaluation
            with open(self.transcript_path, "a") as f:
                f.write(json.dumps({"type": "GENERIC", "content": "Tool output returned asynchronously"}) + "\n")
            return {"healthy": False, "blind_spots": ["Old error"], "guidance": "Stale advice"}

        try:
            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.sage.run_sage_model", side_effect=side_effect_advisor), \
                 patch("sys.stdout") as mock_stdout, \
                 self.assertRaises(SystemExit):
                main()

            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            # Stale advice must NOT be injected (fail_safe_exit emits empty injectSteps)
            self.assertIn('"injectSteps": []', written)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_final_advisor_gate_emits_advisor_recap(self):
        """Verify final advisor gate healthy assessment emits advisor recap directly as the terminal gate."""
        conv_id = f"test_final_healthy_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        self._write_transcript("Build feature X", tool_calls_count=15, is_final=True)
        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}

        mock_adv_output = {"healthy": True, "blind_spots": [], "guidance": "Trajectory is solid and verified.", "recap": "Feature X complete and verified."}
        try:
            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.sage.run_sage_model", return_value=mock_adv_output) as mock_adv, \
                 patch("sys.stdout") as mock_stdout:
                try: main()
                except SystemExit: pass

            mock_adv.assert_called_once()
            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            data = json.loads(written.strip())
            self.assertEqual(data.get("terminationBehavior"), "terminate")
            self.assertIn("Feature X complete and verified.", data["injectSteps"][0]["userMessage"])
            self.assertIn("※ sage:", data["injectSteps"][0]["userMessage"])
            with open(state_file, "r") as sf:
                st = json.load(sf)
            self.assertEqual(st.get("advisor_status"), "recap")
            self.assertTrue(st.get("recap_emitted"))
            self.assertGreaterEqual(st.get("advisor_holds", 0), 1)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_advisor_disabled_at_final_gate_allows_clean_stop(self):
        """Verify a skipped final advisor gate (advisor disabled) allows clean termination — no auditor fallback exists."""
        conv_id = f"test_advisor_disabled_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        self._write_transcript("Build feature X without advisor", tool_calls_count=15, is_final=True)
        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}

        try:
            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 0), \
                 patch("sage.sage.run_sage_model") as mock_adv, \
                 patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
                 patch("sys.stdout") as mock_stdout:
                try: main()
                except SystemExit: pass

            mock_adv.assert_not_called()
            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            data = json.loads(written.strip())
            self.assertEqual(data.get("injectSteps"), [])
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_final_advisor_error_allows_clean_termination(self):
        """Verify an advisor cascade failure at the final gate fails open: stop allowed, error streak recorded."""
        conv_id = f"test_advisor_error_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        self._write_transcript("Build feature X with failing advisor", tool_calls_count=15, is_final=True)
        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}

        try:
            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.sage.run_sage_model", return_value={"status": "error"}), \
                 patch.dict(os.environ, {"AGY_STOP_AUDIT_TEST": "1"}), \
                 patch("sys.stdout") as mock_stdout:
                try: main()
                except SystemExit: pass

            written = "".join([c.args[0] for c in mock_stdout.write.mock_calls if c.args])
            data = json.loads(written.strip())
            self.assertEqual(data.get("injectSteps"), [])
            with open(state_file, "r") as sf:
                st = json.load(sf)
            self.assertEqual(st.get("advisor_status"), "error")
            self.assertEqual(st.get("advisor_error_streak"), 1)
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_final_advisor_gate_forces_model_evaluation_below_interval(self):
        """Verify final advisor gate forces evaluation even when tool delta is below SAGE_TOOL_INTERVAL."""
        conv_id = f"test_final_forced_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        self._write_transcript("Build feature Y", tool_calls_count=14, is_final=True)

        from sage.transcript import get_active_turn_identity
        turn_identity = get_active_turn_identity(self.transcript_path)
        turn_key = hashlib.sha256((turn_identity + "\x00" + "Build feature Y").encode("utf-8")).hexdigest()

        with open(state_file, "w") as sf:
            json.dump({
                "turn_key": turn_key,
                "prompt_hash": "hash123",
                "steering_count": 0,
                "mid_turn_steers": 0,
                "last_verified_tools": 12,  # delta = 14 - 12 = 2 < 10
            }, sf)

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        mock_adv_output = {"healthy": True, "blind_spots": [], "guidance": "Final check passed"}

        try:
            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.sage.run_sage_model", return_value=mock_adv_output) as mock_adv, \
                 patch("sys.stdout"):
                try: main()
                except SystemExit: pass

            mock_adv.assert_called_once()
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)

    def test_new_turn_state_overlay_resets_advisor_inputs(self):
        """Verify leftover state from previous turn does not leak stale last_verified_tools or advice into sage_flow."""
        conv_id = f"test_new_turn_overlay_{int(time.time() * 1000)}"
        state_file = f"/tmp/agy_sage_{safe_id(conv_id)}.json"
        self._write_transcript("Brand new turn prompt", tool_calls_count=12, is_final=False)

        # Leftover state from different turn
        with open(state_file, "w") as sf:
            json.dump({
                "turn_key": "stale-old-turn-key",
                "prompt_hash": "oldhash",
                "steering_count": 5,
                "mid_turn_steers": 2,
                "last_verified_tools": 50,
                "advisor_advice_counts": {"stale_advice": 10},
                "advisor_error_streak": 3,
            }, sf)

        seen_state = {}
        def spy_advisor_flow(mode, **kw):
            seen_state["last_verified_tools"] = kw["state"].get("last_verified_tools")
            seen_state["advisor_advice_counts"] = kw["state"].get("advisor_advice_counts")
            from sage.policies import sage_flow as real_flow
            return real_flow(mode, **kw)

        payload = {"conversationId": conv_id, "transcriptPath": self.transcript_path, "workspacePaths": [self.test_dir]}
        mock_adv_output = {"healthy": True, "blind_spots": [], "guidance": "On track"}

        try:
            with patch("sys.argv", ["session-sage.py", "post_invocation"]), \
                 patch("sys.stdin.read", return_value=json.dumps(payload)), \
                 patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.runner.sage_flow", side_effect=spy_advisor_flow), \
                 patch("sage.sage.run_sage_model", return_value=mock_adv_output) as mock_adv, \
                 patch("sys.stdout"):
                try: main()
                except SystemExit: pass

            self.assertEqual(seen_state.get("last_verified_tools"), 0)
            self.assertEqual(seen_state.get("advisor_advice_counts"), {})
            mock_adv.assert_called_once()
        finally:
            if os.path.exists(state_file):
                os.remove(state_file)


if __name__ == "__main__":
    unittest.main()
