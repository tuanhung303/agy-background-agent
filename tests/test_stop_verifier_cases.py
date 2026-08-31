"""tests.test_stop_verifier_cases - Test suite for Stop Verifier adversarial cases and domain channels."""
import unittest

from sage.lite.gating import is_plan_or_qa_intent
from sage.lite.proof_validator import validate_empirical_proof
from sage.lite.prompt import build_lite_verifier_prompt
from sage.lite.schemas import LiteVerdict


class TestStopVerifierDomainCases(unittest.TestCase):
    """Adversarial domain verification cases targeting lite agent weaknesses."""

    def test_plan_and_qa_intent_detection(self):
        """Plan and QA requests should be recognized as planning intent."""
        self.assertTrue(is_plan_or_qa_intent("/plan make a new branch"))
        self.assertTrue(is_plan_or_qa_intent("/qa explain the architecture"))
        self.assertTrue(is_plan_or_qa_intent("make a plan first before doing any changes"))
        self.assertTrue(is_plan_or_qa_intent("brainstorm the test cases"))
        self.assertFalse(is_plan_or_qa_intent("implement the login endpoint and write tests"))

    def test_website_ui_without_visual_proof_is_disqualified(self):
        """Web UI delivery with build logs and unit tests but no visual screenshot must fail."""
        user_prompt = "Build a modern responsive landing page with responsive grid and navbar."
        agent_response = "I created index.html and style.css. Ran vite build and 15/15 unit tests pass."
        prompt = build_lite_verifier_prompt(user_prompt, agent_response)
        self.assertIn("Visual / Perceptual (UI, Websites, Charts, SVG, Slides, Layouts)", prompt)
        self.assertIn("STRICT DISQUALIFICATION", prompt)

        # Proof validator overrides pseudo-proof PASS to FAIL
        pseudo_proofs = ["vite build successful", "15/15 unit tests passed", "tsc passed with 0 errors"]
        is_valid, reason = validate_empirical_proof(pseudo_proofs)
        self.assertFalse(is_valid)
        self.assertIn("disqualified items", reason)

    def test_svg_rendering_without_visual_verification_is_disqualified(self):
        """SVG graphic delivery with valid XML syntax but no visual verification must fail."""
        user_prompt = "Draw a glitch-free architecture SVG diagram for microservices."
        agent_response = "Generated architecture.svg. XML syntax is valid, all tags match, and pre-push hook passed."
        prompt = build_lite_verifier_prompt(user_prompt, agent_response)
        self.assertIn("Visual / Perceptual", prompt)

        # Validating XML or pre-push hook alone is disqualified
        pseudo_proofs = ["pre-push hook passed", "XML syntax validated with xmllint"]
        is_valid, reason = validate_empirical_proof(pseudo_proofs)
        self.assertFalse(is_valid)

    def test_script_with_static_tests_alone_is_disqualified_without_integration(self):
        """Automation script with static linter but no live execution output must fail."""
        user_prompt = "Write a bash database backup synchronization script."
        agent_response = "Created backup_sync.sh. Ran shellcheck with 0 warnings and unit tests pass."
        prompt = build_lite_verifier_prompt(user_prompt, agent_response)
        self.assertIn("Functional / Runtime (Code, Scripts, APIs, Automations)", prompt)

        pseudo_proofs = ["shellcheck passed", "unit tests 10/10 passed", "git push origin main"]
        is_valid, reason = validate_empirical_proof(pseudo_proofs)
        self.assertFalse(is_valid)

    def test_outsourcing_commands_to_user_is_forbidden(self):
        """Telling the user to run commands or verify manually must be caught by verifier prompt."""
        user_prompt = "Set up database migrations."
        agent_response = "I created migration files. Please run 'alembic upgrade head' and verify your database."
        prompt = build_lite_verifier_prompt(user_prompt, agent_response)
        self.assertIn("AUTONOMY & NON-OUTSOURCING", prompt)
        self.assertIn("telling the user to run commands/verify manually", prompt)

    def test_complete_deliverable_with_empirical_proof_passes(self):
        """Complete deliverable with static checks AND empirical proof channels passes cleanly."""
        genuine_proofs = [
            "Executed live integration test out-of-process in /tmp/test_env with exit code 0",
            "Captured rendered visual preview screenshot at /tmp/diagram_render.png",
            "Queried SQLite database table: 3 rows returned with expected schema",
        ]
        is_valid, reason = validate_empirical_proof(genuine_proofs)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

        verdict = LiteVerdict(
            verdict="PASS",
            action="",
            comment="Website and SVG rendered cleanly, integration test verified.",
            proof=genuine_proofs,
        )
        self.assertEqual(verdict.verdict, "PASS")
        self.assertEqual(len(verdict.proof), 3)


    def test_multi_file_scope_narrowing_is_caught(self):
        """Refactoring across multiple modules while only testing a single leaf must trigger scope failure."""
        user_prompt = "Refactor database models and API serializers across auth, billing, and reports."
        agent_response = "Refactored models.py across all 3 modules. Ran pytest tests/test_auth.py (3 passed)."
        prompt = build_lite_verifier_prompt(user_prompt, agent_response)
        self.assertIn("COMPLETENESS & SCOPE INTEGRITY", prompt)
        self.assertIn("narrowing scope across multi-file changes without regression verification", prompt)

    def test_destructive_operation_without_safety_is_caught(self):
        """Unmitigated destructive state operations must trigger escalation and safety failure."""
        user_prompt = "Clean up stale database records and temporary tables."
        agent_response = "Ran DROP DATABASE and truncated production tables without safety backup."
        prompt = build_lite_verifier_prompt(user_prompt, agent_response)
        self.assertIn("ESCALATION & SAFETY FAILURE", prompt)
        self.assertIn("destructive state risks", prompt)

    def test_retry_turn_after_fail_does_not_bypass_mutation_gate(self):
        """When fail_count > 0, read-only verification turns must still be routed to verifier."""
        from sage.lite.gating import extract_turn_mutations_and_context
        steps = [
            {"step_index": 0, "type": "USER_INPUT", "content": "Build website landing page"},
            {"step_index": 1, "type": "PLANNER_RESPONSE", "content": "Modified files", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/src/index.html"}}]},
            {"step_index": 2, "type": "USER_INPUT", "content": "Go Signal rejected: Proof lacks empirical evidence."},
            {"step_index": 3, "type": "PLANNER_RESPONSE", "content": "Ran verification script", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "python3 verify.py"}}]},
        ]
        has_mutation, reason, true_prompt, last_output = extract_turn_mutations_and_context(steps)
        self.assertEqual(true_prompt, "Build website landing page")

    def test_research_and_file_search_intent_detection(self):
        """Document review, file search, and advisory requests should be recognized as research intent."""
        self.assertTrue(is_plan_or_qa_intent("check the slides and drop the obsolete"))
        self.assertTrue(is_plan_or_qa_intent("search for all SOW files in downloads"))
        self.assertTrue(is_plan_or_qa_intent("investigate differences across slide decks"))
        self.assertTrue(is_plan_or_qa_intent("audit the migration scope spreadsheet"))

    def test_research_deliverable_with_file_citations_passes(self):
        """Research deliverable citing inspected spreadsheets and slide files must be accepted as valid empirical proof."""
        research_proofs = [
            "Inspected sbc/ARK Initiative Scope of Work Data Engineering.xlsx rows 1-35",
            "Referenced sbc/SBC_AWS_Agenda_Executive_1Slide.pptx slide 1",
            "Audited document sbc/CDP_Optimization/assessment/source-inventory.md",
        ]
        is_valid, reason = validate_empirical_proof(research_proofs)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

        verdict = LiteVerdict(
            verdict="PASS",
            action="",
            comment="SOW spreadsheet and slide inventory analyzed with concrete file citations.",
            proof=research_proofs,
        )
        self.assertEqual(verdict.verdict, "PASS")
        self.assertEqual(len(verdict.proof), 3)


if __name__ == "__main__":
    unittest.main()
