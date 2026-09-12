"""tests.test_evidence_context - Tests for transcript provenance, attribution, and user context."""
import unittest

from sage.lite.evidence import (
    clean_tool_output_snippet,
    extract_command_from_args,
    extract_path_from_args,
    match_tool_call_outputs,
    normalize_tool_args,
)
from sage.lite.gating import (
    extract_turn_execution_provenance,
    extract_turn_mutations_and_context,
    is_mutating_tool_call,
)
from sage.user_context import (
    extract_substantive_user_context,
    is_compaction_step,
    is_real_user_step,
)


class TestRealUserBoundaryAndSubagents(unittest.TestCase):
    """Finding 1: Subagent inputs must not prematurely truncate turn provenance."""

    def test_subagent_messages_do_not_drop_prior_mutations(self):
        steps = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Refactor authentication layer"},
            {
                "type": "PLANNER_RESPONSE",
                "content": "Writing updated auth service",
                "tool_calls": [
                    {"name": "write_to_file", "args": {"TargetFile": "/workspace/auth.py"}}
                ],
            },
            {"type": "GENERIC", "content": "File written successfully"},
            {
                "type": "USER_INPUT",
                "source": "SUBAGENT",
                "content": "[Message] from researcher: auth specs confirmed",
            },
            {"type": "PLANNER_RESPONSE", "content": "Refactor finished."},
        ]
        prov = extract_turn_execution_provenance(steps)
        self.assertTrue(prov["has_mutation"])
        self.assertIn("/workspace/auth.py", prov["written_files"])
        self.assertEqual(prov["true_user_prompt"], "Refactor authentication layer")

    def test_inter_agent_prefix_filtered_from_user_boundary(self):
        step_sub = {"type": "USER_INPUT", "source": "USER", "content": "[Message] worker complete"}
        step_user = {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Please run the build"}
        step_idle = {"type": "USER_INPUT", "content": "subagent-1 has gone idle"}
        self.assertFalse(is_real_user_step(step_sub))
        self.assertFalse(is_real_user_step(step_idle))
        self.assertTrue(is_real_user_step(step_user))

    def test_contrasting_explicit_user_input_resets_turn_boundary(self):
        steps = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "First task: write helper"},
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"name": "write_to_file", "args": {"TargetFile": "/workspace/old.py"}}],
            },
            {"type": "GENERIC", "content": "ok"},
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Second task: read status only"},
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": "/workspace/old.py"}}],
            },
        ]
        prov = extract_turn_execution_provenance(steps)
        self.assertFalse(prov["has_mutation"])
        self.assertNotIn("/workspace/old.py", prov["written_files"])
        self.assertIn("/workspace/old.py", prov["inspected_files"])


class TestToolCallOutputAttribution(unittest.TestCase):
    """Finding 2: Multi-call tool attribution by ID, 1-to-1 positional, and ambiguity handling."""

    def test_unambiguous_one_to_one_positional_mapping(self):
        steps = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run two checks"},
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {"name": "run_command", "args": {"CommandLine": "echo FIRST"}},
                    {"name": "run_command", "args": {"CommandLine": "echo SECOND"}},
                ],
            },
            {"type": "GENERIC", "content": "FIRST\n"},
            {"type": "GENERIC", "content": "SECOND\n"},
            {"type": "PLANNER_RESPONSE", "content": "Done with checks"},
        ]
        prov = extract_turn_execution_provenance(steps)
        recent = prov["most_recent_terminal_cmd"]
        self.assertIsNotNone(recent)
        self.assertEqual(recent["command"], "echo SECOND")
        self.assertEqual(recent["output"], "SECOND")
        self.assertIn("- run_command: `echo FIRST` -> [FIRST]", prov["tool_executions_summary"])
        self.assertIn("- run_command: `echo SECOND` -> [SECOND]", prov["tool_executions_summary"])

    def test_tool_call_id_attribution(self):
        stools = [
            {"id": "call_1", "name": "run_command", "args": {"CommandLine": "cmd1"}},
            {"id": "call_2", "name": "run_command", "args": {"CommandLine": "cmd2"}},
        ]
        out_steps = [
            {"type": "GENERIC", "tool_call_id": "call_2", "content": "OUT2"},
        ]
        matched = match_tool_call_outputs(stools, out_steps)
        self.assertIsNone(matched[0][0])
        self.assertTrue(matched[0][1])
        self.assertEqual(matched[1][0], "OUT2")
        self.assertFalse(matched[1][1])

    def test_ambiguous_multi_call_marked_unknown(self):
        steps = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Run batch"},
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {"name": "run_command", "args": {"CommandLine": "make test1"}},
                    {"name": "run_command", "args": {"CommandLine": "make test2"}},
                ],
            },
            {"type": "GENERIC", "content": "Single combined output"},
            {"type": "PLANNER_RESPONSE", "content": "Done"},
        ]
        prov = extract_turn_execution_provenance(steps)
        recent = prov["most_recent_terminal_cmd"]
        self.assertIsNotNone(recent)
        self.assertEqual(recent["output"], "")
        self.assertIn("output unknown: ambiguous multi-call attribution", prov["tool_executions_summary"])


class TestCompactionVsSummaryTags(unittest.TestCase):
    """Finding 6: Literal summary tags in user/tool steps must not trigger false compaction."""

    def test_user_prompt_with_summary_tag_is_not_compaction(self):
        steps = [
            {
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "content": "Please update index.html to include a <summary>FAQ</summary> section",
            }
        ]
        self.assertFalse(is_compaction_step(steps[0]))
        ctx = extract_substantive_user_context(steps)
        self.assertFalse(ctx["has_compaction"])
        self.assertEqual(ctx["user_turn_count"], 1)
        self.assertIn("<summary>FAQ</summary>", ctx["true_user_prompt"])

    def test_tool_output_with_summary_tag_is_not_compaction(self):
        step = {"type": "GENERIC", "content": "diff --git: + <details><summary>Details</summary></details>"}
        self.assertFalse(is_compaction_step(step))

    def test_genuine_checkpoint_is_compaction(self):
        step = {
            "type": "CHECKPOINT",
            "summary": "<summary>Database migrations finished, Kafka consumer ready.</summary>",
        }
        self.assertTrue(is_compaction_step(step))
        steps = [
            step,
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "proceed"},
        ]
        ctx = extract_substantive_user_context(steps)
        self.assertTrue(ctx["has_compaction"])
        self.assertIn("Database migrations finished", ctx["compaction_summary"])


class TestHistoricalConstraintsAndFollowups(unittest.TestCase):
    """Finding 9: Multi-turn historical context and constraints preserved on followups."""

    def test_prior_constraints_preserved_on_trivial_ack(self):
        steps = [
            {
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "content": "Build order service with PostgreSQL; enforce sslmode=require and zero external npm deps",
            },
            {"type": "PLANNER_RESPONSE", "content": "Created schema"},
            {
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "content": "Add migration script for orders table",
            },
            {"type": "PLANNER_RESPONSE", "content": "Added migration"},
            {
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "content": "proceed",
            },
        ]
        ctx = extract_substantive_user_context(steps)
        self.assertTrue(ctx["is_latest_trivial"])
        self.assertEqual(ctx["primary_goal"], "Add migration script for orders table")
        prompt = ctx["true_user_prompt"]
        self.assertIn("[PRIOR USER REQUESTS & CONSTRAINTS]:", prompt)
        self.assertIn("enforce sslmode=require and zero external npm deps", prompt)
        self.assertIn("[PRIMARY USER GOAL]:", prompt)
        self.assertIn("Add migration script for orders table", prompt)
        self.assertIn("[FOLLOW-UP INSTRUCTIONS & REFINEMENTS]:", prompt)
        self.assertIn("proceed", prompt)


class TestArgumentAliasesAndSeparateFileTracking(unittest.TestCase):
    """Finding 10 & Path aliases: normalize argument keys, separate read vs write targets."""

    def test_path_aliases_normalized(self):
        cases = [
            ({"TargetFile": "/app/one.py"}, "/app/one.py"),
            ({"target_file": "/app/two.py"}, "/app/two.py"),
            ({"AbsolutePath": "/app/three.py"}, "/app/three.py"),
            ({"file_path": "/app/four.py"}, "/app/four.py"),
            ({"path": "/app/five.py"}, "/app/five.py"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(extract_path_from_args(args), expected)

    def test_json_string_tool_args_parsed(self):
        norm = normalize_tool_args('{"TargetFile": "/src/service.py", "Overwrite": true}')
        self.assertEqual(norm.get("TargetFile"), "/src/service.py")
        self.assertTrue(norm.get("Overwrite"))

    def test_command_string_tool_args_normalized(self):
        norm = normalize_tool_args("pytest tests/test_auth.py", "run_command")
        self.assertEqual(norm.get("CommandLine"), "pytest tests/test_auth.py")

    def test_brain_artifacts_with_aliases_not_mutating(self):
        self.assertFalse(is_mutating_tool_call("write_to_file", {"AbsolutePath": "/workspace/.gemini/scratch.py"}))
        self.assertFalse(is_mutating_tool_call("write_to_file", {"file_path": "/workspace/brain/notes.md"}))
        self.assertTrue(is_mutating_tool_call("write_to_file", {"AbsolutePath": "/workspace/src/app.py"}))

    def test_read_target_separated_from_written_files(self):
        steps = [
            {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "Check artifact"},
            {
                "type": "PLANNER_RESPONSE",
                "tool_calls": [
                    {"name": "view_file", "args": {"AbsolutePath": "/tmp/prior_turn_chart.png"}}
                ],
            },
            {"type": "GENERIC", "content": "Rendered 100 bytes"},
        ]
        prov = extract_turn_execution_provenance(steps)
        self.assertNotIn("/tmp/prior_turn_chart.png", prov["written_files"])
        self.assertIn("/tmp/prior_turn_chart.png", prov["inspected_files"])


class TestExitCodePreservation(unittest.TestCase):
    """Bounded summaries must retain command exit codes and trailing failures."""

    def test_long_output_preserves_exit_code_and_trailing_failure(self):
        long_log = "\n".join([f"Processing batch row {i}" for i in range(200)])
        long_log += "\nFAILED tests/test_db.py::test_connect - ConnectionRefusedError\nCommand failed with exit code 1"
        snippet = clean_tool_output_snippet(long_log, max_chars=250)
        self.assertLessEqual(len(snippet), 250)
        self.assertIn("exit code 1", snippet)
        self.assertIn("ConnectionRefusedError", snippet)

    def test_no_invented_zero_status(self):
        out = "Server listening on http://localhost:4000\nReady for incoming requests."
        snippet = clean_tool_output_snippet(out, max_chars=200)
        self.assertNotIn("exit code 0", snippet)
        self.assertNotIn("exit 0", snippet)
        self.assertEqual(snippet, "Server listening on http://localhost:4000 Ready for incoming requests.")


if __name__ == "__main__":
    unittest.main()
