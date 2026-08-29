#!/usr/bin/env python3
"""
tests.test_delegation_taxonomy - Tests for typed delegation taxonomy, shared-file detection, terminal review gate, and worktree lifecycle.
"""

import json
import os
import subprocess
import tempfile
import unittest

from sage.events import (
    ASK,
    EVENT_DELEGATE,
    EVENT_FACILITATION,
    format_summon_message,
)
from sage.policies import final_sage_gate, sage_flow
from sage.task_structure import get_parallelizable_signals


class TestDelegationTaxonomyTemplates(unittest.TestCase):
    """Tests payload templates for typed delegation taxonomy."""

    def test_delegate_and_facilitation_ask_are_empty_to_avoid_duplication(self):
        self.assertEqual(ASK[EVENT_DELEGATE], "")
        self.assertEqual(ASK[EVENT_FACILITATION], "")

    def test_sage_prompt_contains_typed_delegation_taxonomy(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        prompt_path = os.path.join(repo_root, "sage", "sage_prompt.md")
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("delegate:leaf", content)
        self.assertIn("delegate:parallel", content)
        self.assertIn("delegate:sequence", content)
        self.assertIn("delegate:research", content)
        self.assertIn("delegate:race", content)
        self.assertIn("delegate:review", content)
        self.assertIn("Scope: NEW files only", content)
        self.assertIn("Write .sage-scope.<leg>.", content)
        self.assertIn("Max 2 review cycles.", content)
        self.assertNotIn("Should we", content)

    def test_format_summon_message_delegate_and_facilitation_no_ask_duplication(self):
        msg_del = format_summon_message(EVENT_DELEGATE, signal_text="delegate work")
        self.assertIn("[CMD·delegate s2]", msg_del)
        self.assertNotIn("ASK", msg_del)

        msg_fac = format_summon_message(EVENT_FACILITATION, signal_text="parallel work")
        self.assertIn("[CMD·facilitation s2]", msg_fac)
        self.assertNotIn("ASK", msg_fac)


class TestTaskStructureSharedFiles(unittest.TestCase):
    """Tests shared-file detection for the {shared} parameter."""

    def test_shared_file_detection_by_name_and_frequency(self):
        synthetic_steps = [
            {
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "content": "Refactor codebase",
            },
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {"name": "write_to_file", "args": {"TargetFile": "src/index.ts"}},
                    {"name": "replace_file_content", "args": {"TargetFile": "src/transformer.py"}},
                    {"name": "replace_file_content", "args": {"TargetFile": "src/visitor.py"}},
                    {"name": "edit_file", "args": {"TargetFile": "src/compiler.py"}},
                    {"name": "write_to_file", "args": {"TargetFile": "src/utils.py"}},
                    {"name": "replace_file_content", "args": {"TargetFile": "src/utils.py"}},
                    {"name": "replace_file_content", "args": {"TargetFile": "src/utils.py"}},
                    {"name": "write_to_file", "args": {"TargetFile": "pkg1/a.py"}},
                    {"name": "write_to_file", "args": {"TargetFile": "pkg2/b.py"}},
                ],
            },
        ]
        result = get_parallelizable_signals(synthetic_steps)
        shared = result.get("shared_files", [])
        self.assertIsInstance(shared, list)
        self.assertLessEqual(len(shared), 4)

        # src/utils.py had 3 edits -> should be at top
        self.assertEqual(shared[0], "src/utils.py")
        # Other candidate files should be matched
        base_names = [os.path.basename(f).lower() for f in shared]
        self.assertTrue(any("index.ts" in b for b in base_names))

    def test_shared_files_empty_when_subagents_already_dispatched(self):
        synthetic_steps = [
            {
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "content": "Refactor codebase",
            },
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {"name": "write_to_file", "args": {"TargetFile": "src/index.ts"}},
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Implementer"}]}},
                ],
            },
        ]
        result = get_parallelizable_signals(synthetic_steps)
        self.assertFalse(result["parallelizable"])
        self.assertEqual(result.get("shared_files"), [])


class TestPoliciesReviewLegGate(unittest.TestCase):
    """Tests terminal review-leg gate enforcement in sage/policies.py."""

    def test_review_gate_blocks_when_build_delegation_present_and_no_review(self):
        with tempfile.TemporaryDirectory() as td:
            tr_path = os.path.join(td, "transcript.jsonl")
            with open(tr_path, "w") as f:
                f.write(
                    json.dumps({
                        "type": "USER_INPUT",
                        "source": "USER_EXPLICIT",
                        "content": "Add feature",
                    }) + "\n"
                )
                f.write(
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [
                            {
                                "name": "invoke_subagent",
                                "args": {
                                    "Subagents": [
                                        {"Role": "Implementer", "Prompt": "Write feature in core.py"}
                                    ]
                                },
                            }
                        ],
                    }) + "\n"
                )

            state = {"mid_turn_steers": 0, "sage_error_streak": 0, "last_verified_tools": 0}
            act = final_sage_gate(
                conv_id="c_test",
                transcript_path=tr_path,
                clean_prompt="Add feature",
                initial_line_count=1,
                total_tool_calls=5,
                turn_tool_names=["invoke_subagent"],
                user_prompt="Add feature",
                agent_steps=[],
                git_diff="",
                state=state,
            )
            self.assertEqual(act.get("action"), "emit")
            self.assertEqual(act.get("decision"), "watchout")
            self.assertEqual(act.get("category"), "missing_proof")
            self.assertIn("[CMD·delegate:review]", act.get("text", ""))
            self.assertTrue(state.get("review_gate_fired"))

            # Fires ONCE: second run with state['review_gate_fired']=True does not block on this gate
            act2 = sage_flow(
                mode="final",
                conv_id="c_test",
                transcript_path=tr_path,
                clean_prompt="Add feature",
                initial_line_count=1,
                total_tool_calls=5,
                turn_tool_names=["invoke_subagent"],
                user_prompt="Add feature",
                agent_steps=[],
                git_diff="",
                state=state,
            )
            self.assertNotEqual(act2.get("text"), act.get("text"))

    def test_review_gate_does_not_block_when_review_leg_present(self):
        with tempfile.TemporaryDirectory() as td:
            tr_path = os.path.join(td, "transcript.jsonl")
            with open(tr_path, "w") as f:
                f.write(
                    json.dumps({
                        "type": "USER_INPUT",
                        "source": "USER_EXPLICIT",
                        "content": "Add feature",
                    }) + "\n"
                )
                f.write(
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [
                            {
                                "name": "invoke_subagent",
                                "args": {
                                    "Subagents": [
                                        {"Role": "Implementer", "Prompt": "Write feature"},
                                        {"Role": "Reviewer", "Prompt": "Blind read-only audit of diff"},
                                    ]
                                },
                            }
                        ],
                    }) + "\n"
                )

            state = {"mid_turn_steers": 0, "sage_error_streak": 0, "last_verified_tools": 0}
            act = sage_flow(
                mode="final",
                conv_id="c_test",
                transcript_path=tr_path,
                clean_prompt="Add feature",
                initial_line_count=1,
                total_tool_calls=5,
                turn_tool_names=["invoke_subagent"],
                user_prompt="Add feature",
                agent_steps=[],
                git_diff="",
                state=state,
            )
            # Should not be blocked by the review gate
            self.assertFalse(state.get("review_gate_fired", False))

    def test_review_gate_does_not_block_when_no_delegations(self):
        with tempfile.TemporaryDirectory() as td:
            tr_path = os.path.join(td, "transcript.jsonl")
            with open(tr_path, "w") as f:
                f.write(
                    json.dumps({
                        "type": "USER_INPUT",
                        "source": "USER_EXPLICIT",
                        "content": "Simple task",
                    }) + "\n"
                )
                f.write(
                    json.dumps({
                        "type": "PLANNER_RESPONSE",
                        "tool_calls": [
                            {"name": "write_to_file", "args": {"TargetFile": "a.py"}}
                        ],
                    }) + "\n"
                )

            state = {"mid_turn_steers": 0, "sage_error_streak": 0, "last_verified_tools": 0}
            act = sage_flow(
                mode="final",
                conv_id="c_test",
                transcript_path=tr_path,
                clean_prompt="Simple task",
                initial_line_count=1,
                total_tool_calls=5,
                turn_tool_names=["write_to_file"],
                user_prompt="Simple task",
                agent_steps=[],
                git_diff="",
                state=state,
            )
            self.assertFalse(state.get("review_gate_fired", False))


class TestSageWorktreeScript(unittest.TestCase):
    """Tests scripts/sage_worktree.sh spawn and prune in a temporary git repository."""

    def test_spawn_and_prune_dry_run_and_force(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        script_path = os.path.join(repo_root, "scripts", "sage_worktree.sh")

        with tempfile.TemporaryDirectory() as td:
            # Initialize temporary git repo
            subprocess.run(["git", "init"], cwd=td, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=td,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=td,
                check=True,
                capture_output=True,
            )
            dummy_file = os.path.join(td, "README.md")
            with open(dummy_file, "w") as f:
                f.write("# Temp Repo\n")
            subprocess.run(["git", "add", "README.md"], cwd=td, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "initial commit"],
                cwd=td,
                check=True,
                capture_output=True,
            )

            # 1. Spawn worktree
            spawn_res = subprocess.run(
                [script_path, "spawn", "sess-test", "leg-1"],
                cwd=td,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(spawn_res.returncode, 0)

            wt_path = os.path.join(td, ".worktrees", "sage", "sess-test-leg-1")
            self.assertTrue(os.path.isdir(wt_path))
            marker_path = os.path.join(wt_path, ".sage-ephemeral")
            self.assertTrue(os.path.isfile(marker_path))

            with open(marker_path, "r") as f:
                marker_data = json.load(f)
            self.assertEqual(marker_data.get("session"), "sess-test")
            self.assertEqual(marker_data.get("leg"), "leg-1")
            self.assertIn("created_at", marker_data)

            # 2. Prune dry-run (default and explicit)
            prune_dry = subprocess.run(
                [script_path, "prune"],
                cwd=td,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("[dry-run] would remove", prune_dry.stdout)
            self.assertIn("sess-test-leg-1", prune_dry.stdout)
            self.assertTrue(os.path.isdir(wt_path))

            prune_explicit_dry = subprocess.run(
                [script_path, "prune", "--dry-run"],
                cwd=td,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("[dry-run] would remove", prune_explicit_dry.stdout)
            self.assertTrue(os.path.isdir(wt_path))

            # 3. Prune force / remove
            prune_force = subprocess.run(
                [script_path, "prune", "--force"],
                cwd=td,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(prune_force.returncode, 0)
            self.assertFalse(os.path.isdir(wt_path))

            # Check branch was deleted
            branch_check = subprocess.run(
                ["git", "branch", "--list", "sage/sess-test/leg-1"],
                cwd=td,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(branch_check.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
