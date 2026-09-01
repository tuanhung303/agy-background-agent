#!/usr/bin/env python3
"""
tests.test_m3_stress_challenge - Empirical adversarial stress testing for Milestone M3.
Tests edge cases, malicious inputs, boundary conditions, and concurrency for:
- Task structure extraction & parallelizable workstreams
- Subagent watcher lifecycle & role tracking
- Background task watchdog & age calculations
- Policy routing & advisor-first gate integration
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from sage.policies import (
    sage_flow,
    background_watch,
)
from sage.task_structure import (
    _extract_file_path,
    _extract_research_target,
    _extract_test_target,
    get_parallelizable_signals,
)
from sage.transcript import (
    is_post_invocation_completion_candidate,
)
from sage.watchers import (
    get_active_subagents as watchers_get_active_subagents,
)


class TestM3TaskStructureStress(unittest.TestCase):
    """Stress tests for task structure extraction and parallelizable signals."""

    def test_extract_file_path_edge_cases(self):
        # Empty and non-dict args
        self.assertIsNone(_extract_file_path(None))
        self.assertIsNone(_extract_file_path([]))
        self.assertIsNone(_extract_file_path("invalid"))
        self.assertIsNone(_extract_file_path({}))
        self.assertIsNone(_extract_file_path({"other": "foo.py"}))
        self.assertIsNone(_extract_file_path({"TargetFile": ""}))
        self.assertIsNone(_extract_file_path({"TargetFile": "   "}))
        self.assertIsNone(_extract_file_path({"TargetFiles": []}))
        self.assertIsNone(_extract_file_path({"TargetFiles": [123]}))

        # Varied key casings and list inputs
        self.assertEqual(_extract_file_path({"TargetFile": "src/a.py"}), "src/a.py")
        self.assertEqual(_extract_file_path({"AbsolutePath": " /abs/path.py "}), "/abs/path.py")
        self.assertEqual(_extract_file_path({"TargetFiles": ["src/b.py", "src/c.py"]}), "src/b.py")
        self.assertEqual(_extract_file_path({"target_file": "pkg/d.py"}), "pkg/d.py")
        self.assertEqual(_extract_file_path({"path": "e.py"}), "e.py")
        self.assertEqual(_extract_file_path({"file": "f.py"}), "f.py")

    def test_extract_research_target_edge_cases(self):
        self.assertIsNone(_extract_research_target("search_web", None))
        self.assertIsNone(_extract_research_target("search_web", "not_dict"))
        self.assertEqual(_extract_research_target("search_web", {}), "search_web")
        self.assertEqual(_extract_research_target("search_web", {"query": "python async"}), "search_web:python async")
        self.assertEqual(_extract_research_target("read_url_content", {"Url": " https://example.com "}), "read_url_content:https://example.com")
        self.assertEqual(_extract_research_target("grep_search", {"Pattern": "class Foo"}), "grep_search:class Foo")

    def test_extract_test_target_edge_cases(self):
        self.assertIsNone(_extract_test_target(None))
        self.assertIsNone(_extract_test_target({}))
        self.assertIsNone(_extract_test_target({"CommandLine": "ls -la"}))
        self.assertIsNone(_extract_test_target({"command": "git status"}))
        self.assertEqual(_extract_test_target({"command": "echo 'pytest not really'"}), "echo 'pytest not really'")
        self.assertEqual(_extract_test_target({"CommandLine": "pytest tests/test_a.py"}), "pytest tests/test_a.py")
        self.assertEqual(_extract_test_target({"command": "python3 -m unittest discover"}), "python3 -m unittest discover")
        self.assertEqual(_extract_test_target({"cmd": "cargo test --all"}), "cargo test --all")
        self.assertEqual(_extract_test_target({"CommandLine": "npm test -- --watch=false"}), "npm test -- --watch=false")

    def test_get_parallelizable_signals_disjoint_directories(self):
        # 1 directory -> not parallelizable
        steps = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [
                {"name": "write_to_file", "args": {"TargetFile": "src/module1/a.py"}},
                {"name": "write_to_file", "args": {"TargetFile": "src/module1/b.py"}},
            ]}
        ]
        res = get_parallelizable_signals(steps)
        self.assertFalse(res["parallelizable"])

        # 2 disjoint directories -> parallelizable Implementer
        steps_disjoint = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [
                {"name": "write_to_file", "args": {"TargetFile": "src/module1/a.py"}},
                {"name": "write_to_file", "args": {"TargetFile": "src/module2/b.py"}},
            ]}
        ]
        res = get_parallelizable_signals(steps_disjoint)
        self.assertTrue(res["parallelizable"])
        self.assertIn("disjoint_files", res["categories"])
        self.assertIn("Implementer", res["suggested_roles"])
        self.assertIn("PARALLELIZABLE", res["signal_text"])

    def test_get_parallelizable_signals_isolated_research(self):
        steps = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [
                {"name": "search_web", "args": {"query": "python ast tutorial"}},
                {"name": "read_url_content", "args": {"Url": "https://docs.python.org"}},
            ]}
        ]
        res = get_parallelizable_signals(steps)
        self.assertTrue(res["parallelizable"])
        self.assertIn("isolated_research", res["categories"])
        self.assertIn("Scout", res["suggested_roles"])

    def test_get_parallelizable_signals_independent_verification(self):
        steps = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [
                {"name": "run_command", "args": {"CommandLine": "pytest tests/unit"}},
                {"name": "run_command", "args": {"CommandLine": "pytest tests/integration"}},
            ]}
        ]
        res = get_parallelizable_signals(steps)
        self.assertTrue(res["parallelizable"])
        self.assertIn("independent_verification", res["categories"])
        self.assertIn("QA", res["suggested_roles"])

    def test_get_parallelizable_signals_all_categories_combined(self):
        steps = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [
                {"name": "write_to_file", "args": {"TargetFile": "pkg_a/a.py"}},
                {"name": "write_to_file", "args": {"TargetFile": "pkg_b/b.py"}},
                {"name": "search_web", "args": {"query": "api spec 1"}},
                {"name": "search_web", "args": {"query": "api spec 2"}},
                {"name": "run_command", "args": {"CommandLine": "cargo test --bin server"}},
                {"name": "run_command", "args": {"CommandLine": "cargo test --bin client"}},
            ]}
        ]
        res = get_parallelizable_signals(steps)
        self.assertTrue(res["parallelizable"])
        self.assertEqual(set(res["categories"]), {"disjoint_files", "isolated_research", "independent_verification"})
        self.assertEqual(set(res["suggested_roles"]), {"Implementer", "Scout", "QA"})

    def test_get_parallelizable_signals_corrupted_file_input(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("corrupted json {}}\n\n{\"type\": \"PLANNER_RESPONSE\"}\nINVALID\n")
            tpath = f.name
        try:
            res = get_parallelizable_signals(tpath)
            self.assertIsInstance(res, dict)
            self.assertFalse(res["parallelizable"])
        finally:
            os.remove(tpath)


class TestM3SubagentWatcherStress(unittest.TestCase):
    """Stress tests for subagent watcher tracking, role preservation, and lifecycle."""

    def test_invoke_subagent_varied_payload_formats(self):
        # Case 1: Subagents as JSON string
        steps1 = [{
            "type": "PLANNER_RESPONSE",
            "tool_calls": [{
                "name": "invoke_subagent",
                "args": {"Subagents": '[{"Role": "Scout", "Goal": "Explore repo"}]'},
            }],
        }]
        subs1 = watchers_get_active_subagents(steps1)
        self.assertEqual(len(subs1), 1)
        self.assertEqual(subs1[0]["role"], "Scout")
        self.assertTrue(subs1[0]["subagent_id"].startswith("pending_invoke_"))

        # Case 2: Subagents as single dict
        steps2 = [{
            "type": "PLANNER_RESPONSE",
            "tool_calls": [{
                "name": "invoke_subagent",
                "args": {"subagents": {"role": "Implementer", "goal": "Build module"}},
            }],
        }]
        subs2 = watchers_get_active_subagents(steps2)
        self.assertEqual(len(subs2), 1)
        self.assertEqual(subs2[0]["role"], "Implementer")

        # Case 3: Subagents as string role name
        steps3 = [{
            "type": "PLANNER_RESPONSE",
            "tool_calls": [{
                "name": "invoke_subagent",
                "args": {"Subagents": ["QA", "Worker"]},
            }],
        }]
        subs3 = watchers_get_active_subagents(steps3)
        self.assertEqual(len(subs3), 2)
        roles = [s["role"] for s in subs3]
        self.assertEqual(roles, ["QA", "Worker"])

    def test_conversation_id_resolution_and_metadata_retention(self):
        steps = [
            {
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-08-24T00:00:00Z",
                "tool_calls": [
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Implementer"}]}},
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "QA"}]}},
                ],
            },
            {
                "type": "GENERIC",
                "created_at": "2026-08-24T00:01:00Z",
                "content": 'Subagent spawned with "conversationId": "subagent_conv_alpha"',
            },
            {
                "type": "GENERIC",
                "created_at": "2026-08-24T00:01:05Z",
                "content": 'Subagent spawned with "conversationId": "subagent_conv_beta"',
            },
        ]
        subs = watchers_get_active_subagents(steps)
        self.assertEqual(len(subs), 2)
        sub_dict = {s["subagent_id"]: s for s in subs}
        self.assertIn("subagent_conv_alpha", sub_dict)
        self.assertIn("subagent_conv_beta", sub_dict)
        self.assertEqual(sub_dict["subagent_conv_alpha"]["role"], "Implementer")
        self.assertEqual(sub_dict["subagent_conv_beta"]["role"], "QA")

    def test_subagent_lifecycle_termination_signals(self):
        # 1. Idle notice
        steps_idle = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "QA"}]}}]},
            {"type": "GENERIC", "content": '"conversationId": "conv_qa_1"'},
            {"type": "GENERIC", "content": "Subagent conv_qa_1 has gone idle."},
        ]
        self.assertEqual(len(watchers_get_active_subagents(steps_idle)), 0)

        # 2. Sender message completion
        steps_sender = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Scout"}]}}]},
            {"type": "GENERIC", "content": '"conversationId": "conv_scout_1"'},
            {"type": "USER_INPUT", "content": "[Message] sender=conv_scout_1 priority=HIGH content=Found data"},
        ]
        self.assertEqual(len(watchers_get_active_subagents(steps_sender)), 0)

        # 3. manage_subagents kill_all
        steps_kill = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Worker"}]}}]},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "manage_subagents", "args": {"Action": "kill_all"}}]},
        ]
        self.assertEqual(len(watchers_get_active_subagents(steps_kill)), 0)


class TestM3PolicyAndAdvisorFlowStress(unittest.TestCase):
    """Stress tests for policy routing and parallelization forcing."""

    @patch("sage.policies.evaluate_mid_turn_progress")
    @patch("sage.policies.classify_advice")
    def test_advisor_flow_forces_evaluation_on_parallel_signals(self, mock_classify, mock_eval):
        mock_eval.return_value = {
            "status": "watchout",
            "category": "parallelize_subagent",
            "action": "invoke_subagent(Subagents=[{'Role': 'Implementer'}])",
            "confidence": 0.85,
        }
        mock_classify.return_value = {
            "decision": "watchout",
            "category": "parallelize_subagent",
            "text": "PARALLELIZABLE: Multi-directory edits detected",
            "seen": {},
        }

        # Create transcript with disjoint directories
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(json.dumps({"type": "USER_INPUT", "content": "Build multi-service app", "source": "USER_EXPLICIT"}) + "\n")
            f.write(json.dumps({"type": "PLANNER_RESPONSE", "tool_calls": [
                {"name": "write_to_file", "args": {"TargetFile": "srv1/app.py"}},
                {"name": "write_to_file", "args": {"TargetFile": "srv2/app.py"}},
            ]}) + "\n")
            tpath = f.name

        try:
            state = {"mid_turn_steers": 0, "advisor_error_streak": 0, "last_verified_tools": 10}
            # total_tool_calls = 10, meaning tool delta is 0 (< SAGE_TOOL_INTERVAL=3)
            # Normal flow would exit, but parallelizable signal MUST force evaluation
            act = sage_flow(
                "midturn",
                conv_id="test_conv",
                transcript_path=tpath,
                clean_prompt="Build multi-service app",
                initial_line_count=2,
                total_tool_calls=10,
                turn_tool_names={"write_to_file"},
                user_prompt="Build multi-service app",
                agent_steps=["write_to_file"],
                git_diff="",
                state=state,
            )
            self.assertEqual(act["action"], "emit")
            self.assertEqual(act["decision"], "watchout")
            self.assertTrue(mock_eval.called)
            # Verify is_forced was True in evaluate_mid_turn_progress call
            self.assertTrue(mock_eval.call_args[1].get("is_forced"))
        finally:
            os.remove(tpath)

class TestM3LifecycleComplexStress(unittest.TestCase):
    """Deep lifecycle stress tests for subagent watchers and background tasks."""

    def test_interleaved_multi_subagent_lifecycle(self):
        # 5 subagents spawned:
        # sub1: finishes via sender message
        # sub2: finishes via idle notice
        # sub3: killed via manage_subagents
        # sub4: killed via regex "Killed subagent sub4"
        # sub5: remains active
        steps = [
            # Spawn sub1, sub2
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Scout"}]}},
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Implementer"}]}},
                ],
            },
            {"type": "GENERIC", "content": 'Resolved "conversationId": "sub_1_alpha"'},
            {"type": "GENERIC", "content": 'Resolved "conversationId": "sub_2_beta"'},
            # Spawn sub3, sub4, sub5
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "QA"}]}},
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Worker"}]}},
                    {"name": "invoke_subagent", "args": {"Subagents": [{"Role": "Implementer"}]}},
                ],
            },
            {"type": "GENERIC", "content": 'Resolved "conversationId": "sub_3_gamma"'},
            {"type": "GENERIC", "content": 'Resolved "conversationId": "sub_4_delta"'},
            {"type": "GENERIC", "content": 'Resolved "conversationId": "sub_5_epsilon"'},
            # sub1 completes via sender
            {"type": "USER_INPUT", "content": "[Message] sender=sub_1_alpha content=Done"},
            # sub2 completes via idle notice
            {"type": "GENERIC", "content": "Subagent sub_2_beta has gone idle"},
            # sub3 killed via manage_subagents
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "manage_subagents", "args": {"ConversationIds": ["sub_3_gamma"]}}]},
            # sub4 killed via text notice
            {"type": "GENERIC", "content": "Killed subagent sub_4_delta"},
        ]

        active = watchers_get_active_subagents(steps)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["subagent_id"], "sub_5_epsilon")
        self.assertEqual(active[0]["role"], "Implementer")

    def test_background_task_ordering_and_grace(self):
        # 3 tasks: task_fresh (100s), task_stale (400s), task_staler (600s)
        tasks = [
            {"task_id": "t_fresh", "description": "fresh build", "age_seconds": 100.0},
            {"task_id": "t_stale", "description": "stale test", "age_seconds": 400.0},
            {"task_id": "t_staler", "description": "very stale search", "age_seconds": 600.0},
        ]
        # First check: steers the oldest unsteered stale task (t_staler)
        res1 = background_watch(tasks, bg_steered=set())
        self.assertEqual(res1["action"], "steer")
        self.assertEqual(res1["task_id"], "t_staler")

        # Second check: with t_staler steered, steers t_stale
        res2 = background_watch(tasks, bg_steered={"t_staler"})
        self.assertEqual(res2["action"], "steer")
        self.assertEqual(res2["task_id"], "t_stale")

        # Third check: with both stale steered, falls back to grace for fresh
        res3 = background_watch(tasks, bg_steered={"t_staler", "t_stale"})
        self.assertEqual(res3["action"], "grace")

        # Fourth check: with no fresh tasks remaining, returns already_steered
        tasks_no_fresh = [
            {"task_id": "t_stale", "description": "stale test", "age_seconds": 400.0},
            {"task_id": "t_staler", "description": "very stale search", "age_seconds": 600.0},
        ]
        res4 = background_watch(tasks_no_fresh, bg_steered={"t_staler", "t_stale"})
        self.assertEqual(res4["action"], "already_steered")

    def test_post_invocation_completion_candidate_with_subagents(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(json.dumps({"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "invoke_subagent", "args": {"Subagents": [{"Role": "QA"}]}}]}) + "\n")
            f.write(json.dumps({"type": "GENERIC", "content": '"conversationId": "sub_qa_active"'}) + "\n")
            f.write(json.dumps({"type": "PLANNER_RESPONSE", "content": "I am finished with the work."}) + "\n")
            tpath = f.name
        try:
            # Active subagent should prevent post-invocation completion candidate
            self.assertFalse(is_post_invocation_completion_candidate(tpath))
        finally:
            os.remove(tpath)

    def test_task_structure_path_normalization_robustness(self):
        # Check path normalization handles relative, leading dots, trailing slashes
        steps = [
            {"type": "PLANNER_RESPONSE", "tool_calls": [
                {"name": "write_to_file", "args": {"TargetFile": "./services/backend/main.py"}},
                {"name": "write_to_file", "args": {"TargetFile": "services/frontend/App.tsx"}},
            ]}
        ]
        res = get_parallelizable_signals(steps)
        self.assertTrue(res["parallelizable"])
        self.assertIn("disjoint_files", res["categories"])
        self.assertIn("Implementer", res["suggested_roles"])


if __name__ == "__main__":
    unittest.main()
