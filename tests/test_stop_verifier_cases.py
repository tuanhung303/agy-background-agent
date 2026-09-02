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
        self.assertIn("Visual / Frontend (UI, Websites, Charts, SVG, Slides, Layouts)", prompt)
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
        self.assertIn("Visual / Frontend", prompt)

        # Validating XML or pre-push hook alone is disqualified
        pseudo_proofs = ["pre-push hook passed", "XML syntax validated with xmllint"]
        is_valid, reason = validate_empirical_proof(pseudo_proofs)
        self.assertFalse(is_valid)

    def test_script_with_static_tests_alone_is_disqualified_without_integration(self):
        """Automation script with static linter but no live execution output must fail."""
        user_prompt = "Write a bash database backup synchronization script."
        agent_response = "Created backup_sync.sh. Ran shellcheck with 0 warnings and unit tests pass."
        prompt = build_lite_verifier_prompt(user_prompt, agent_response)
        self.assertIn("Backend / API / Runtime (Code, Scripts, Services, Automations)", prompt)

        pseudo_proofs = ["shellcheck passed", "unit tests 10/10 passed", "git push origin main"]
        is_valid, reason = validate_empirical_proof(pseudo_proofs)
        self.assertFalse(is_valid)

    def test_outsourcing_commands_to_user_is_forbidden(self):
        """Telling the user to run commands or verify manually must be caught by verifier prompt."""
        user_prompt = "Set up database migrations."
        agent_response = "I created migration files. Please run 'alembic upgrade head' and verify your database."
        prompt = build_lite_verifier_prompt(user_prompt, agent_response)
        self.assertIn("AUTONOMY & ANTI-DEFERRAL", prompt)
        self.assertIn("telling the user to run commands/migrations/verification manually", prompt)

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
        self.assertIn("COMPLETENESS, BLAST RADIUS & REGRESSION IMMUNITY", prompt)
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

    def test_release_and_git_push_alone_is_disqualified(self):
        """Pushed branch to remote or staging without CI/CD or endpoint check must fail."""
        user_prompt = "good push to remote, merge to staging"
        agent_response = "Merged and pushed to origin staging. 37/37 pre-push tests passed."
        prompt = build_lite_verifier_prompt(user_prompt, agent_response)
        self.assertIn("Release, Remote Merge & Deployment (git push, staging/prod deploy, release branch)", prompt)
        self.assertIn("STRICT DISQUALIFICATION", prompt)

        pseudo_proofs = ["git push origin staging -> 658423aa..b1e599b1 staging -> staging", "37/37 pre-push tests passed"]
        is_valid, reason = validate_empirical_proof(pseudo_proofs)
        self.assertFalse(is_valid)
        self.assertIn("disqualified items", reason)

    def test_stale_recycled_artifact_is_disqualified_by_provenance(self):
        """Proof citing an old screenshot generated in a prior turn must be rejected."""
        import os
        import tempfile
        import time

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"PNG_MOCK")
            old_path = f.name

        try:
            # Set file mtime to 10 seconds ago
            old_time = time.time() - 20.0
            os.utime(old_path, (old_time, old_time))

            # Turn started 5 seconds ago (after the file was created)
            turn_start = time.time() - 5.0
            turn_prov = {
                "turn_start_time": turn_start,
                "written_files": ["/other/file.txt"],
                "generated_images": [],
            }

            proof = [f"Captured screenshot at {old_path}"]
            is_valid, reason = validate_empirical_proof(proof, turn_provenance=turn_prov)
            self.assertFalse(is_valid)
            self.assertIn("stale", reason.lower())
        finally:
            if os.path.exists(old_path):
                os.remove(old_path)

    def test_fresh_turn_artifact_passes_provenance(self):
        """Proof citing an artifact touched or created in the current turn passes cleanly."""
        import os
        import tempfile
        import time

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"PNG_MOCK")
            fresh_path = f.name

        try:
            turn_start = time.time() - 2.0
            turn_prov = {
                "turn_start_time": turn_start,
                "written_files": [fresh_path],
                "generated_images": [],
            }

            proof = [f"Captured screenshot at {fresh_path}"]
            is_valid, reason = validate_empirical_proof(proof, turn_provenance=turn_prov)
            self.assertTrue(is_valid)
            self.assertEqual(reason, "")
        finally:
            if os.path.exists(fresh_path):
                os.remove(fresh_path)

    def test_grill_me_and_planning_intent_requires_evidence(self):
        """Interactive /grill-me and planning prompts require cited evidence and cannot pass with empty proof."""
        prompts = [
            "/grill-me interview me about the architecture",
            "/grill-me /grill-me> what is the design",
            "<GRILL_ME> Clarify the requirements",
            "/plan outline the database migration steps",
            "brainstorm the test cases",
        ]
        for p in prompts:
            self.assertTrue(is_plan_or_qa_intent(p), f"Failed to detect plan/QA intent for: {p}")
            # Empty proof must be rejected
            is_valid_empty, reason_empty = validate_empirical_proof([], user_prompt=p)
            self.assertFalse(is_valid_empty)
            self.assertIn("empty", reason_empty.lower())

            # Cited questions / artifacts must pass cleanly
            valid_evidence = [f"Formulated architectural decision branch and questions for: {p}"]
            is_valid, reason = validate_empirical_proof(valid_evidence, user_prompt=p)
            self.assertTrue(is_valid, f"Rejected valid evidence for {p}: {reason}")
            self.assertEqual(reason, "")

    def test_it_devops_terraform_deferral_vs_valid_proof(self):
        """IT/DevOps task with deferred execution or unverified infra must be disqualified."""
        # Deferral pseudo-proof
        deferred_proofs = ["Wrote main.tf, user can run terraform apply in AWS account", "syntax checked"]
        is_valid, reason = validate_empirical_proof(deferred_proofs)
        self.assertFalse(is_valid)

        # Genuine validation proof
        valid_infra_proofs = [
            "Executed terraform validate in sandbox: Success! The configuration is valid with 0 errors.",
            "Executed docker build -t app:test . with exit code 0 and verified image inspection output",
        ]
        is_valid, reason = validate_empirical_proof(valid_infra_proofs)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_office_documents_formula_and_presentation_proof(self):
        """Office and presentation tasks require concrete formula audit and slide structure validation."""
        # Deferral / narrative claim
        narrative_proofs = ["Excel file created, XML is valid", "Slide looks good"]
        is_valid, reason = validate_empirical_proof(narrative_proofs)
        self.assertFalse(is_valid)

        # Genuine office proof
        valid_office_proofs = [
            "Audited quarterly_report.xlsx: 12/12 formulas verified with no #REF! or #VALUE! errors and verified calculated totals",
            "Inspected presentation deck pitch.pptx slide 1-10 layout structures with 0 text overflow defects",
        ]
        is_valid, reason = validate_empirical_proof(valid_office_proofs)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_backend_regression_and_scope_narrowing_disqualification(self):
        """Backend changes modifying shared models require multi-module regression and live runtime verification."""
        # Isolated unit test only without live execution
        isolated_proofs = ["1/1 unit test passed for UserModel"]
        is_valid, reason = validate_empirical_proof(isolated_proofs)
        self.assertFalse(is_valid)

        # Multi-module regression + live curl execution
        valid_backend_proofs = [
            "Executed pytest tests/: 48/48 passed across user_service, order_service, and auth_service with exit code 0",
            "Live curl POST http://127.0.0.1:8000/api/v1/users returned HTTP 201 with verified UUID response payload",
        ]
        is_valid, reason = validate_empirical_proof(valid_backend_proofs)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_web_visual_regression_vs_computed_dom_layout(self):
        """Web frontend changes require rendered visual proof or computed DOM layout validation."""
        # Vite build only
        build_only = ["vite build finished in 1.4s with 0 errors"]
        is_valid, reason = validate_empirical_proof(build_only)
        self.assertFalse(is_valid)

        # Visual preview + Playwright computed layout
        valid_web_proofs = [
            "Captured rendered screenshot at /tmp/sidebar_desktop.png",
            "Playwright browser evaluated DOM layout: #sidebar width is 240px with 0px overlap on #main-content",
        ]
        is_valid, reason = validate_empirical_proof(valid_web_proofs)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_data_sql_pipeline_rowcount_proof(self):
        """Data and SQL pipelines require table query results, row counts, and schema assertions."""
        # Query written but not run
        unrun_sql = ["Written query to models/marts/fct_sales.sql", "SQL syntax is valid"]
        is_valid, reason = validate_empirical_proof(unrun_sql)
        self.assertFalse(is_valid)

        # Live table query proof
        valid_sql_proofs = [
            "Executed query against test sqlite database: returned 1420 rows with non-null transaction_id and valid schema",
            "dbt test --select fct_sales returned 0 failures with exit code 0",
        ]
        is_valid, reason = validate_empirical_proof(valid_sql_proofs)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_prod_terraform_destructive_plan_disqualified_vs_safe_plan(self):
        """Destructive terraform plan with resources to destroy must be disqualified without escalation."""
        destructive_plan = ["plan: 1 to add, 0 to change, 1 to destroy in staging"]
        is_valid, reason = validate_empirical_proof(destructive_plan)
        self.assertFalse(is_valid)

        safe_plan = [
            "Executed terraform plan -out=tfplan with exit code 0: 2 to add, 1 to change, 0 to destroy",
        ]
        is_valid, reason = validate_empirical_proof(safe_plan)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_container_docker_build_only_disqualified_vs_container_healthcheck(self):
        """Docker build alone is disqualified without live container boot and healthcheck verification."""
        build_only = ["docker build successful for image app:latest"]
        is_valid, reason = validate_empirical_proof(build_only)
        self.assertFalse(is_valid)

        live_container = [
            "Executed docker run in sandbox and curl http://127.0.0.1:8080/healthz returned response code 200",
        ]
        is_valid, reason = validate_empirical_proof(live_container)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_svg_viewbox_and_bounding_geometry_proof(self):
        """SVG vector proof requires explicit viewBox and non-zero layout bounding geometry."""
        syntax_only = ["SVG XML is valid, all tags closed"]
        is_valid, reason = validate_empirical_proof(syntax_only)
        self.assertFalse(is_valid)

        geometry_proof = [
            "Evaluated SVG in browser: explicit viewBox='0 0 500 200' with DOM getBoundingClientRect width 500px",
            "Captured rendered visual screenshot at /tmp/chart_preview.png",
        ]
        is_valid, reason = validate_empirical_proof(geometry_proof)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_backend_mock_only_test_disqualified_vs_live_negative_curl(self):
        """Mocked unit test pass counts are disqualified; live execution with negative cases is accepted."""
        mock_only = ["4 passed on mock with @patch"]
        is_valid, reason = validate_empirical_proof(mock_only)
        self.assertFalse(is_valid)

        live_curl = [
            "Live curl -X POST http://127.0.0.1:8000/api/v1/webhook with empty payload returned response code 422",
            "Live curl http://127.0.0.1:8000/api/v1/health returned response code 200",
        ]
        is_valid, reason = validate_empirical_proof(live_curl)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")

    def test_migration_with_upgrade_and_downgrade_rollback_proof(self):
        """Database migration requires both upgrade and downgrade rollback validation without manual deferral."""
        deferred_migration = ["Generated migration, please apply in production database"]
        is_valid, reason = validate_empirical_proof(deferred_migration)
        self.assertFalse(is_valid)

        rollback_proof = [
            "Executed alembic upgrade head and alembic downgrade -1 against test database with exit code 0",
        ]
        is_valid, reason = validate_empirical_proof(rollback_proof)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "")


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
        self.assertIn("MANDATORY ACTION: You must inspect the image(s) above using `view_file`", prompt)
        self.assertIn("ADVERSARIAL EMPIRICAL PROOF & VISUAL DISCREPANCY AUDIT", prompt)
        self.assertIn("NEVER trust the agent's text claims about what an image or diagram shows", prompt)
        self.assertIn("<current_turn_tool_executions>", prompt)
        self.assertIn("node test.mjs", prompt)


    def test_aftershock_sibling_blast_radius_escalation(self):
        """Single-sighting failures on enumerable entities (channels/tenants/formulas) must enforce universe expansion."""
        user_prompt = "Fix Meta spend calculation discrepancy in marketing dashboard"
        agent_response = "Patched Meta spend formula in sheets/meta.py."
        prompt = build_lite_verifier_prompt(user_prompt, agent_response)
        self.assertIn("COMPLETENESS, BLAST RADIUS & REGRESSION IMMUNITY", prompt)
        self.assertIn("Treat an error in any enumerable entity (channel, tenant, route, formula, parser, model) as a sighting", prompt)
        self.assertIn("Prohibit single-sighting narrow patching", prompt)
        self.assertIn("Sibling Verification Contract: The agent must declare the active candidate universe U", prompt)


if __name__ == "__main__":
    unittest.main()
