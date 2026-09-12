"""Prompt/provenance wiring; semantic verdicts are tested by real-model fixtures."""
import unittest
from unittest.mock import MagicMock, patch

from sage.lite.prompt import build_lite_verifier_prompt
from sage.lite.proof_validator import validate_empirical_proof


class TestStopVerifierDomainCases(unittest.TestCase):
    def test_image_manifest_and_tool_output_extraction(self):
        """Turn provenance should extract referenced images and tool output snippets."""
        from sage.lite.gating import extract_turn_execution_provenance
        steps = [
            {"step_index": 0, "type": "USER_INPUT", "content": "Fix the chart bar scale"},
            {
                "step_index": 1,
                "type": "PLANNER_RESPONSE",
                "content": "Modified chart component",
                "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": "/src/Chart.tsx"}}],
            },
            {"step_index": 2, "type": "GENERIC", "content": "The following changes were made to /src/Chart.tsx"},
            {
                "step_index": 3,
                "type": "PLANNER_RESPONSE",
                "content": "Ran test script",
                "tool_calls": [{"name": "run_command", "args": {"CommandLine": "node verify.mjs"}}],
            },
            {"step_index": 4, "type": "GENERIC", "content": "Created At: ... Log output: screenshot saved to /tmp/chart_verified.png"},
            {
                "step_index": 5,
                "type": "PLANNER_RESPONSE",
                "content": "Viewed screenshot",
                "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": "/tmp/chart_verified.png"}}],
            },
            {"step_index": 6, "type": "GENERIC", "content": "Binary file rendered"},
            {
                "step_index": 7,
                "type": "PLANNER_RESPONSE",
                "content": "Verified screenshot: /tmp/chart_verified.png",
            },
        ]
        prov = extract_turn_execution_provenance(steps)
        self.assertTrue(prov["has_mutation"])
        self.assertIn("/tmp/chart_verified.png", prov["image_files"])
        self.assertIn("replace_file_content", prov["tool_executions_summary"])
        self.assertIn("run_command", prov["tool_executions_summary"])

    def test_verifier_prompt_includes_image_manifest_and_adversarial_inspection(self):
        """Verifier prompt must inject <current_turn_images_to_inspect> and adversarial audit rules."""
        user_prompt = "0.83x ROAS is longer than 1.17x ROAS, fix scale"
        agent_response = "Fixed scale. In /tmp/verified.png 0.83x < 1.17x."
        image_manifest = ["/tmp/verified.png"]
        turn_exec = "- run_command: `node test.mjs` -> [exit code 0]"

        prompt = build_lite_verifier_prompt(
            user_prompt,
            agent_response,
            turn_execution_summary=turn_exec,
            image_manifest=image_manifest,
        )
        self.assertIn("<current_turn_images_to_inspect>", prompt)
        self.assertIn("/tmp/verified.png", prompt)
        self.assertIn("not proof that the files exist or were inspected", prompt)
        self.assertIn("<current_turn_tool_executions>", prompt)
        self.assertIn("node test.mjs", prompt)



    def test_external_blocker_escalation_passes(self):
        """External blocker such as corporate MFA or RDP session conflict must pass validation."""
        blocker_proofs = [
            "Corporate MFA interactive login required for user operator@corp.internal. Redirected to https://login.example.com with 2FA prompt.",
            "Active RDP session conflict: Session ID 3 on jumpbox held by user admin_session. Disconnect requires human action.",
        ]
        is_valid, reason = validate_empirical_proof(blocker_proofs)
        self.assertTrue(is_valid, f"Expected blocker escalation to be valid proof, but failed with: {reason}")

        user_prompt = "Execute data extraction on analytics warehouse via RDP jumpbox"
        agent_response = "Encountered active RDP session lock. Disconnect required from user."
        prompt = build_lite_verifier_prompt(user_prompt, agent_response)
        self.assertIn("Respect actual access", prompt)
        self.assertIn("Finish independent authorized work", prompt)

    def test_recent_terminal_command_provenance_and_prompt_formatting(self):
        """Recent terminal command and output must be preserved in provenance and verifier prompt."""
        from sage.lite.gating import extract_turn_execution_provenance

        steps = [
            {"type": "USER_INPUT", "content": "can you check what columns are in data.xlsx?"},
            {
                "type": "PLANNER_RESPONSE",
                "content": "Let me inspect the columns with python.",
                "tool_calls": [
                    {
                        "name": "run_command",
                        "args": {
                            "CommandLine": "python3 -c \"import pandas as pd; df = pd.read_excel('data.xlsx'); print(df.columns); print(df.head(2))\""
                        }
                    }
                ]
            },
            {
                "type": "GENERIC",
                "content": "Index(['Order ID', 'Conversion Type', 'Order Amount', 'Date'], dtype='object')\nRow 0: JB45515332 475.00\nRow 1: CB79829170 139.00"
            },
            {
                "type": "PLANNER_RESPONSE",
                "content": "The Excel file contains Order ID, Conversion Type, Order Amount, and Date. Order Amount is revenue, not spend."
            }
        ]

        prov = extract_turn_execution_provenance(steps)
        recent_cmd = prov.get("most_recent_terminal_cmd")
        self.assertIsNotNone(recent_cmd)
        self.assertEqual(recent_cmd["tool"], "run_command")
        self.assertIn("import pandas as pd", recent_cmd["command"])
        self.assertIn("Order ID", recent_cmd["output"])
        self.assertIn("Order Amount", recent_cmd["output"])

        prompt = build_lite_verifier_prompt(
            user_prompt="can you check what columns are in data.xlsx?",
            last_agent_output=steps[-1]["content"],
            turn_provenance=prov,
        )
        self.assertIn("<most_recent_terminal_command>", prompt)
        self.assertIn("Command: `python3 -c \"import pandas as pd", prompt)
        self.assertIn("Order Amount", prompt)
        self.assertIn("cross-examine", prompt)



if __name__ == "__main__":
    unittest.main()
