"""
tests.test_eval_mutation - Systematic validation and mutation testing suite for eval scenarios.
"""
import subprocess
import sys
import unittest

from scripts.eval.eval_validator import (
    generate_scenario_mutations,
    run_policy_mutations,
    run_scenario_mutation_suite,
    validate_scenario_schema,
)
from scripts.eval.run_eval import dedup_probe, drive_turn, grade, load_scenarios


class TestEvalMutationSuite(unittest.TestCase):
    def setUp(self):
        self.scenarios = load_scenarios()
        self.scenarios_by_id = {sc["id"]: sc for sc in self.scenarios}

    def test_all_scenarios_schema_and_invariants(self):
        """Assert all scenario JSON files adhere to structural schema and invariant rules."""
        self.assertGreater(len(self.scenarios), 0, "No scenarios found in scripts/eval/scenarios")
        for sc in self.scenarios:
            with self.subTest(scenario=sc.get("id")):
                errors = validate_scenario_schema(sc)
                self.assertEqual(errors, [], f"Schema errors in scenario {sc.get('id')}: {errors}")

    def test_no_vacuous_expectations(self):
        """Assert every scenario has strict, non-vacuous expectation properties."""
        for sc in self.scenarios:
            with self.subTest(scenario=sc.get("id")):
                expect = sc.get("expect", {})
                self.assertIsInstance(expect, dict)
                self.assertGreater(len(expect), 0, f"Scenario {sc['id']} has empty expect block")
                # Anti-facade: ensure at least one substantive key is present
                keys = set(expect.keys())
                has_key = any(
                    k in keys
                    for k in (
                        "first_fire_cp",
                        "decision_category",
                        "fire_before_tool_index",
                        "max_parallel_emissions",
                        "midturn_interrupts",
                        "final_pending_clarify_ok",
                        "prompt_byte_ratio_lt",
                        "final_category",
                        "text_contains",
                        "decision_type",
                    )
                )
                self.assertTrue(has_key, f"Scenario {sc['id']} has no substantive assertions in expect")

    def test_all_scenario_mutations_killed(self):
        """Assert 100% mutation kill rate across all eval scenarios."""
        for sc in self.scenarios:
            with self.subTest(scenario=sc["id"]):
                report = run_scenario_mutation_suite(sc, drive_turn, grade, dedup_probe)
                self.assertTrue(report["base_pass"], f"Base scenario {sc['id']} failed baseline eval")
                self.assertGreater(
                    report["total_mutations"],
                    0,
                    f"Scenario {sc['id']} produced zero mutations",
                )
                self.assertEqual(
                    report["survived"],
                    [],
                    f"Scenario {sc['id']} had surviving mutations: {report['survived']}",
                )
                self.assertEqual(report["killed"], report["total_mutations"])

    def test_policy_fault_injections_killed(self):
        """Assert policy-level fault injections are detected and killed by scenario expectations."""
        results = run_policy_mutations(self.scenarios_by_id, drive_turn, grade)
        self.assertGreater(len(results), 0, "No policy mutation results produced")
        for mut_name, killed in results:
            with self.subTest(policy_mutation=mut_name):
                self.assertTrue(killed, f"Policy mutation {mut_name} survived without detection")

    def test_cli_execution_modes(self):
        """Assert run_eval.py CLI operates cleanly in default, --validate, and --mutate modes."""
        # 1. Default scenario run
        p1 = subprocess.run(
            [sys.executable, "scripts/eval/run_eval.py"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(p1.returncode, 0, f"run_eval.py failed:\n{p1.stderr}\n{p1.stdout}")
        n_scenarios = len(self.scenarios)
        self.assertIn(f"{n_scenarios}/{n_scenarios} passed", p1.stdout)

        # 2. Validate mode
        p2 = subprocess.run(
            [sys.executable, "scripts/eval/run_eval.py", "--validate"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(p2.returncode, 0, f"run_eval.py --validate failed:\n{p2.stderr}\n{p2.stdout}")
        self.assertIn("[SCHEMA_OK]", p2.stdout)

        # 3. Mutate mode
        p3 = subprocess.run(
            [sys.executable, "scripts/eval/run_eval.py", "--mutate"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(p3.returncode, 0, f"run_eval.py --mutate failed:\n{p3.stderr}\n{p3.stdout}")
        self.assertIn("100.0%", p3.stdout)


if __name__ == "__main__":
    unittest.main()
