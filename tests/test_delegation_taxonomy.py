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
from sage.session_state import load_and_sync_session_state, save_session_state
from sage.task_structure import _classify_subagents, get_parallelizable_signals


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
        self.assertIn("Hostile Execution Audit", content)
        self.assertIn(
            "A 'review passed' claim is rejected unless the transcript shows raw execution output (stdout/stderr/error traces) for the negative cases.",
            content,
        )
        self.assertIn("Scope: NEW files only", content)
        self.assertIn("Write .sage-scope.<leg>.", content)
        self.assertIn("Max 2 review cycles.", content)
        self.assertNotIn("Should we", content)

    def test_format_summon_message_delegate_and_facilitation_with_live_shared_and_legs(self):
        msg_del = format_summon_message(
            EVENT_DELEGATE,
            signal_text="delegate work",
            shared=["index.ts", "src/visitor.py"],
        )
        self.assertIn("[CMD·delegate s2]", msg_del)
        self.assertIn("shared=index.ts,src/visitor.py", msg_del)
        self.assertNotIn("ASK", msg_del)

        msg_fac = format_summon_message(
            EVENT_FACILITATION, signal_text="parallel work", legs=3
        )
        self.assertIn("[CMD·facilitation s2]", msg_fac)
        self.assertIn("legs=3", msg_fac)
        self.assertNotIn("ASK", msg_fac)

    def test_delegate_review_payload_contract_contains_spec_conformance(self):
        from sage.events import DELEGATE_REVIEW_PAYLOAD
        self.assertIn("[CMD·delegate:review]", DELEGATE_REVIEW_PAYLOAD)
        self.assertIn("Hostile Execution Audit", DELEGATE_REVIEW_PAYLOAD)
        self.assertIn("raw execution output", DELEGATE_REVIEW_PAYLOAD)
        self.assertIn("pass without pasted execution output is invalid", DELEGATE_REVIEW_PAYLOAD)
        self.assertIn("ORIGINAL user request", DELEGATE_REVIEW_PAYLOAD)
        self.assertIn("brief = DoD + the base-vs-working-tree diff ONLY", DELEGATE_REVIEW_PAYLOAD)
        # base..HEAD is the two-commit form and renders empty against uncommitted work;
        # it may only appear as the prohibition, never as the instruction.
        self.assertIn("never base..HEAD", DELEGATE_REVIEW_PAYLOAD)
        self.assertIn("Max 1 re-review", DELEGATE_REVIEW_PAYLOAD)
        self.assertIn("second fail: stop and report to user", DELEGATE_REVIEW_PAYLOAD)

    def test_fanout_emission_tag_matches_prompt_playbook_map(self):
        """A summon tag that drifts from the playbook map has no documented routing."""
        from sage.events import EVENT_FANOUT
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        with open(os.path.join(repo_root, "sage", "sage_prompt.md"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn(f"`[EVT·{EVENT_FANOUT}]`", content)
        rendered = format_summon_message(EVENT_FANOUT, signal_text="2 disjoint directories: core, tests")
        self.assertTrue(rendered.startswith(f"[EVT·{EVENT_FANOUT} s1]"))


class TestTaskStructureSharedFiles(unittest.TestCase):
    """Tests shared-file detection and relative/absolute path normalization."""

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
                    # Second touch each: one write cannot be shared by two legs, so a
                    # single-write file is never a seam whatever its name.
                    {"name": "replace_file_content", "args": {"TargetFile": "src/index.ts"}},
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

        # Integration files rank FIRST. Ranking by churn evicted the real seam (an
        # index.ts written once) in favour of whichever leaf file was edited most.
        base_names = [os.path.basename(f).lower() for f in shared]
        self.assertEqual(sorted(base_names), ["compiler.py", "index.ts", "transformer.py", "visitor.py"])
        # src/utils.py got 3 edits, but all three back to back: that is one leg
        # iterating on its own file, not two legs sharing one. Not a seam.
        self.assertNotIn("src/utils.py", shared)

    def test_relative_and_absolute_path_normalization_against_repo(self):
        with tempfile.TemporaryDirectory() as td:
            abs_index = os.path.join(td, "src", "index.ts")
            synthetic_steps = [
                {
                    "type": "USER_INPUT",
                    "source": "USER_EXPLICIT",
                    "content": "Refactor",
                },
                {
                    "type": "PLANNER_RESPONSE",
                    "tool_calls": [
                        {"name": "write_to_file", "args": {"TargetFile": abs_index}},
                        {"name": "replace_file_content", "args": {"TargetFile": "src/index.ts"}},
                        {"name": "replace_file_content", "args": {"TargetFile": "./src/index.ts"}},
                        {"name": "write_to_file", "args": {"TargetFile": "pkg/b.py"}},
                    ],
                },
            ]
            result = get_parallelizable_signals(synthetic_steps, workspace_root=td)
            shared = result.get("shared_files", [])
            # All three references to index.ts should normalize to src/index.ts
            self.assertIn("src/index.ts", shared)

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


class TestSubagentClassification(unittest.TestCase):
    """Tests _classify_subagents excludes research/scout and classifies build vs review."""

    def test_research_and_scout_subagents_excluded_from_build(self):
        steps = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "invoke_subagent",
                        "args": {
                            "Subagents": [
                                {"Role": "Research Subagent", "Prompt": "Search docs", "TypeName": "research"},
                                {"Role": "Codebase Scout", "Prompt": "Explore repository"},
                            ]
                        },
                    }
                ],
            }
        ]
        has_build, has_review = _classify_subagents(steps)
        self.assertFalse(has_build)
        self.assertFalse(has_review)

    def test_implementer_counts_as_build_and_reviewer_counts_as_review(self):
        build_steps = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "invoke_subagent",
                        "args": {"Subagents": [{"Role": "Implementer", "Prompt": "Write feature"}]},
                    }
                ],
            }
        ]
        has_build, has_review = _classify_subagents(build_steps)
        self.assertTrue(has_build)
        self.assertFalse(has_review)

        review_steps = [
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {
                        "name": "invoke_subagent",
                        "args": {"Subagents": [{"Role": "Code Reviewer", "Prompt": "Blind audit"}]},
                    }
                ],
            }
        ]
        has_build_rev, has_review_rev = _classify_subagents(review_steps)
        self.assertFalse(has_build_rev)
        self.assertTrue(has_review_rev)


def _write_build_only_transcript(dir_path):
    """Transcript with a build-leg delegation and no review leg."""
    path = os.path.join(dir_path, "transcript.jsonl")
    with open(path, "w") as f:
        f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Add feature"}) + "\n")
        f.write(json.dumps({
            "type": "PLANNER_RESPONSE",
            "tool_calls": [{
                "name": "invoke_subagent",
                "args": {"Subagents": [{"Role": "Implementer", "Prompt": "Write feature in core.py"}]},
            }],
        }) + "\n")
    return path


class TestPoliciesReviewLegGate(unittest.TestCase):
    """Tests terminal review-leg gate enforcement in sage/policies.py."""

    def test_review_gate_blocks_when_build_delegation_present_and_no_review(self):
        with tempfile.TemporaryDirectory() as td:
            tr_path = _write_build_only_transcript(td)
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
            self.assertIn("ORIGINAL user request", act.get("text", ""))
            self.assertIn("negative case", act.get("text", ""))
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

    def test_review_gate_payload_names_dod_and_diff_base_when_pinned(self):
        from sage.events import DELEGATE_REVIEW_PAYLOAD
        with tempfile.TemporaryDirectory() as td:
            tr_path = _write_build_only_transcript(td)
            state = {
                "mid_turn_steers": 0, "sage_error_streak": 0, "last_verified_tools": 0,
                "pinned_goal": "Ship the delegated refactor with a green suite",
                "review_base_sha": "1b48489a",
            }
            act = final_sage_gate(
                conv_id="c_test", transcript_path=tr_path, clean_prompt="Add feature",
                initial_line_count=1, total_tool_calls=5, turn_tool_names=["invoke_subagent"],
                user_prompt="Add feature", agent_steps=[], git_diff="", state=state,
            )
        text = act.get("text", "")
        self.assertEqual(act.get("action"), "emit")
        self.assertIn("[CMD·delegate:review]", text)
        self.assertIn("DoD: Ship the delegated refactor with a green suite", text)
        # base vs WORKING TREE, not base..HEAD: the pin captured HEAD before the work
        # existed and nothing commits, so the two-commit range renders empty.
        self.assertIn("Diff scope: git diff 1b48489a (base vs working tree", text)
        self.assertIn("git add -N . && git diff 1b48489a", text)
        self.assertNotIn("git diff 1b48489a..HEAD", text)
        self.assertTrue(text.startswith(DELEGATE_REVIEW_PAYLOAD))

    def test_review_gate_payload_stays_verbatim_without_goal_or_base(self):
        """Honest or silent: no invented DoD when the pin never recorded one."""
        from sage.events import DELEGATE_REVIEW_PAYLOAD
        with tempfile.TemporaryDirectory() as td:
            tr_path = _write_build_only_transcript(td)
            state = {"mid_turn_steers": 0, "sage_error_streak": 0, "last_verified_tools": 0}
            act = final_sage_gate(
                conv_id="c_test", transcript_path=tr_path, clean_prompt="Add feature",
                initial_line_count=1, total_tool_calls=5, turn_tool_names=["invoke_subagent"],
                user_prompt="Add feature", agent_steps=[], git_diff="", state=state,
            )
        self.assertEqual(act.get("text"), DELEGATE_REVIEW_PAYLOAD)

    def test_review_gate_does_not_block_research_scout_subagents(self):
        with tempfile.TemporaryDirectory() as td:
            tr_path = os.path.join(td, "transcript.jsonl")
            with open(tr_path, "w") as f:
                f.write(
                    json.dumps({
                        "type": "USER_INPUT",
                        "source": "USER_EXPLICIT",
                        "content": "Investigate bug",
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
                                        {"Role": "Research", "Prompt": "Search error logs", "TypeName": "research"}
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
                clean_prompt="Investigate bug",
                initial_line_count=1,
                total_tool_calls=5,
                turn_tool_names=["invoke_subagent"],
                user_prompt="Investigate bug",
                agent_steps=[],
                git_diff="",
                state=state,
            )
            self.assertFalse(state.get("review_gate_fired", False))

    def test_review_gate_fired_persisted_in_session_state(self):
        conv_id = f"c_persist_test_{os.getpid()}"
        with tempfile.TemporaryDirectory() as td:
            tr_path = os.path.join(td, "transcript.jsonl")
            with open(tr_path, "w") as f:
                f.write(
                    json.dumps({
                        "type": "USER_INPUT",
                        "source": "USER_EXPLICIT",
                        "content": "Build system",
                    }) + "\n"
                )

            clean_prompt, state_file, state, is_same = load_and_sync_session_state(
                conv_id, tr_path, "Build system"
            )
            self.assertFalse(state.get("review_gate_fired", False))

            # Simulate review gate firing and saving state
            save_session_state(state_file, state, review_gate_fired=True)

            # Reload session state and verify flag persisted
            clean_prompt2, state_file2, state2, is_same2 = load_and_sync_session_state(
                conv_id, tr_path, "Build system"
            )
            self.assertTrue(state2.get("review_gate_fired"))

            if os.path.exists(state_file):
                os.remove(state_file)

    def test_review_base_sha_survives_to_final_gate_same_turn(self):
        """Pin and final gate are separate hook processes; the sha rides the state file."""
        conv_id = f"c_base_sha_{os.getpid()}"
        with tempfile.TemporaryDirectory() as td:
            tr_path = os.path.join(td, "transcript.jsonl")
            with open(tr_path, "w") as f:
                f.write(json.dumps({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Build system"}) + "\n")
                f.write(json.dumps({"type": "PLANNER_RESPONSE", "tool_calls": []}) + "\n")

            _, state_file, state, _ = load_and_sync_session_state(conv_id, tr_path, "Build system")
            save_session_state(state_file, state, review_base_sha="1b48489a", delegate_cmd_turn=1)

            _, _, reloaded, is_same = load_and_sync_session_state(conv_id, tr_path, "Build system")
            self.assertTrue(is_same)
            self.assertEqual(reloaded.get("review_base_sha"), "1b48489a")

            # A fresh user prompt is a new turn: the previous turn's diff base is stale
            _, _, new_turn, _ = load_and_sync_session_state(conv_id, tr_path, "Totally different next task")
            self.assertIsNone(new_turn.get("review_base_sha"))

            if os.path.exists(state_file):
                os.remove(state_file)


class TestSageWorktreeScript(unittest.TestCase):
    """Tests scripts/sage_worktree.sh spawn and prune in a temporary git repository."""

    def test_spawn_and_prune_dry_run_and_force_from_subfolder(self):
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

            # Create a subfolder and spawn from inside it
            subfolder = os.path.join(td, "src", "nested")
            os.makedirs(subfolder, exist_ok=True)

            # 1. Spawn worktree from subfolder
            spawn_res = subprocess.run(
                [script_path, "spawn", "sess-test", "leg-1"],
                cwd=subfolder,
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
                cwd=subfolder,
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
                cwd=subfolder,
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


class TestAssistModeRouting(unittest.TestCase):
    """Tests for Assist Mode seam-ratio routing, signal emission, and delegation suppression."""

    def test_sage_prompt_contains_mode_routing_table_and_assist_mode_block(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        prompt_path = os.path.join(repo_root, "sage", "sage_prompt.md")
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Mode Routing Table", content)
        self.assertIn("Single Player (`simple_qa`)", content)
        self.assertIn("Teamplay (`multi_file`)", content)
        self.assertIn("Assist (`complex_code` / coupled `multi_file`)", content)
        self.assertIn(
            "[Mode: Assist] High coupling — shared state or monolithic integration files. "
            "Act as a staff-level reviewer; budget 3-5 steers, where one steer is one emitted verdict no matter how many items it carries. "
            "DISCOVER (Phase 1): read the raw request with verification tools; "
            "generate an acceptance checklist; hand it to the executor at goal pin. HINT: point to repo conventions or adjacent patterns (max 2 times). "
            "WATCH: track the checklist; batch every untouched item into a single steer rather than one reminder each. "
            "EXHAUSTION FALLBACK: if the budget is hit before completion, issue one final directive to finalize, "
            "then yield completely. VALIDATE (Phase 3): run the expectation-gap check and hostile audit yourself via verification tools; "
            "report findings in a single steer. Do NOT delegate review.",
            content,
        )

    def test_assist_mode_signal_fires_when_write_ratio_exceeds_threshold(self):
        # 5 files: core/main.py (5 writes), core/engine.py (3 writes), core/config.py (2 writes),
        # pkg1/a.py (1 write), pkg2/b.py (1 write). Total = 12; seam = main+engine = 8/12 = 0.667 > 0.3.
        # The writes INTERLEAVE: work keeps returning to main.py and engine.py after
        # touching other files, which is what a shared integration file looks like. A
        # contiguous burst on one file is self-iteration and deliberately does not count.
        order = [
            "core/main.py", "core/engine.py", "core/main.py", "core/config.py",
            "core/main.py", "core/engine.py", "core/main.py", "pkg1/a.py",
            "core/main.py", "core/engine.py", "core/config.py", "pkg2/b.py",
        ]
        steps = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Update core integration and leaf modules"}]
        steps += [
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": p}}]}
            for p in order
        ]
        res = get_parallelizable_signals(steps)
        self.assertTrue(res["parallelizable"])
        self.assertIn("assist_mode", res["categories"])
        self.assertEqual(
            res["signal_text"],
            "ASSIST_MODE: High coupling: most work touches shared files. Use Assist Mode — no delegation orders.",
        )

    def test_delegation_suppressed_when_assist_mode_active(self):
        from unittest.mock import patch
        import tempfile
        from sage.policies import sage_flow

        with tempfile.TemporaryDirectory() as td:
            tr_path = os.path.join(td, "transcript.jsonl")
            steps = [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Update coupled integration"},
                # core/a.py is the shared integration file: work RETURNS to it after each
                # other file, so 3 of this turn's 6 writes land in one file two legs would
                # share. Revisiting is the seam signal — three back-to-back writes to a.py
                # would just be one leg iterating on its own file.
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "core/a.py"}}]},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "core/b.py"}}]},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": "core/a.py"}}]},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "core/c.py"}}]},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "replace_file_content", "args": {"TargetFile": "core/a.py"}}]},
                {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "pkg/d.py"}}]},
            ]
            with open(tr_path, "w") as f:
                for s in steps:
                    f.write(json.dumps(s) + "\n")

            state = {"mid_turn_steers": 0, "sage_error_streak": 0, "last_verified_tools": 0}
            fake_verdict = {
                "status": "watchout",
                "task_complexity": "multi_file",
                "category": "parallelize_subagent",
                "action": "[CMD·delegate:leaf] Split work into subagents",
                "guidance": "Delegate now",
                "confidence": 0.9,
            }

            with patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.policies.evaluate_mid_turn_progress", return_value=fake_verdict), \
                 patch("sage.policies.has_new_user_activity", return_value=False), \
                 patch("sage.policies.extract_session_and_turn_data", return_value=(None, None, None, 10, None, None, None, 5)), \
                 patch("sage.policies.is_post_invocation_completion_candidate", return_value=False):
                res = sage_flow(
                    mode="midturn",
                    conv_id="c_test_assist",
                    transcript_path=tr_path,
                    clean_prompt="Update coupled integration",
                    initial_line_count=5,
                    total_tool_calls=10,
                    turn_tool_names=["write_to_file"],
                    user_prompt="Update coupled integration",
                    agent_steps=[],
                    git_diff="Changed lines: 10",
                    state=state,
                    forced=True,
                )
            self.assertEqual(res.get("action"), "hold_dedup")
            self.assertTrue(res.get("assist_suppressed"))
            self.assertTrue(res.get("assist_active"))

    def test_delegate_commands_still_emitted_for_disjoint_files(self):
        from unittest.mock import patch
        import tempfile
        from sage.policies import sage_flow

        # 10 disjoint files, 1 write each: top 3 = 3/10 = 0.3 <= 0.3 -> PARALLELIZABLE, not assist mode
        steps = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Update disjoint plugins"}]
        for i in range(10):
            steps.append({"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": f"plugin_{i}/mod_{i}.py"}}]})

        sig = get_parallelizable_signals(steps)
        self.assertTrue(sig["parallelizable"])
        self.assertNotIn("assist_mode", sig["categories"])
        self.assertTrue(sig["signal_text"].startswith("PARALLELIZABLE:"))
        self.assertIn("Implementer", sig["suggested_roles"])

        with tempfile.TemporaryDirectory() as td:
            tr_path = os.path.join(td, "transcript.jsonl")
            with open(tr_path, "w") as f:
                for s in steps:
                    f.write(json.dumps(s) + "\n")

            state = {"mid_turn_steers": 0, "sage_error_streak": 0, "last_verified_tools": 0}
            fake_verdict = {
                "status": "watchout",
                "task_complexity": "multi_file",
                "category": "parallelize_subagent",
                "action": "[CMD·delegate:leaf] Split work into subagents",
                "guidance": "Delegate now",
                "confidence": 0.9,
            }

            with patch("sage.policies.MID_TURN_SAGE_ENABLED", 1), \
                 patch("sage.policies.evaluate_mid_turn_progress", return_value=fake_verdict), \
                 patch("sage.policies.has_new_user_activity", return_value=False), \
                 patch("sage.policies.extract_session_and_turn_data", return_value=(None, None, None, 10, None, None, None, 10)), \
                 patch("sage.policies.is_post_invocation_completion_candidate", return_value=False):
                res = sage_flow(
                    mode="midturn",
                    conv_id="c_test_disjoint",
                    transcript_path=tr_path,
                    clean_prompt="Update disjoint plugins",
                    initial_line_count=10,
                    total_tool_calls=10,
                    turn_tool_names=["write_to_file"],
                    user_prompt="Update disjoint plugins",
                    agent_steps=[],
                    git_diff="Changed lines: 10",
                    state=state,
                    forced=True,
                )
            self.assertEqual(res.get("action"), "emit")
            self.assertEqual(res.get("decision"), "watchout")
            self.assertIn("[CMD·delegate:leaf]", res.get("text", ""))
            self.assertFalse(res.get("assist_active"))


if __name__ == "__main__":
    unittest.main()
