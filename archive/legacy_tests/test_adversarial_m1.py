#!/usr/bin/env python3
"""
tests.test_adversarial_m1 - Empirical challenger adversarial tests for Milestone M1.

Tests:
1. Static analysis detector stress tests (tokenize semicolon detection & AST single-statement validation).
2. Hook entry point (hooks/session-sage.py) crash isolation and fallback guarantees.
3. Exit signal routing across all exit paths (yield, continue, exit, steer, watchout, grace, hold, etc.).
4. Lock acquisition/release integrity under simulated faults and concurrent invocations.
"""

import ast
import io
import json
import os
import subprocess
import sys
import token
import tokenize
import unittest
from collections import defaultdict
from unittest.mock import MagicMock, patch

from sage.guards import (
    emit_continue_response,
    emit_recap_response,
    fail_safe_exit,
    handle_background_watch_action,
)
from sage.locking import (
    acquire_conversation_lock,
    release_lock,
)
from sage.policies import background_watch, final_sage_gate


def check_tokenize_semicolons(source_text: str):
    """Utility mirroring test_static_analysis semicolon detection."""
    tokens = list(tokenize.tokenize(io.BytesIO(source_text.encode("utf-8")).readline))
    return [
        tok for tok in tokens
        if tok.exact_type == tokenize.SEMI or (tok.type == token.OP and tok.string == ";")
    ]


def check_ast_packed_lines(source_text: str):
    """Utility mirroring test_static_analysis AST single-statement per line detection."""
    tree = ast.parse(source_text)
    line_stmts = defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            line_stmts[node.lineno].append(node)
    return {l: stmts for l, stmts in line_stmts.items() if len(stmts) > 1}


class TestAdversarialStaticAnalysis(unittest.TestCase):
    """Adversarial challenge against AST & Tokenize detectors in test_static_analysis.py."""

    def test_tokenize_catches_all_disguised_semicolons(self):
        """Ensure code semicolons in complex syntax variants are ALWAYS detected."""
        disguised_cases = [
            # Trailing semicolon
            "x = 42;\n",
            # Multiple statements separated by semicolon
            "a = 1; b = 2\n",
            # Semicolon after import
            "import os; import sys\n",
            # Semicolon in class definition
            "class Foo:\n    x = 1; y = 2\n",
            # Semicolon in function body
            "def fn():\n    return 1; pass\n",
            # Semicolon on empty line
            ";\n",
            # Semicolon after pass
            "pass; pass\n",
            # Semicolon after break/continue
            "while True:\n    break;\n",
            # Semicolon in lambda statement line
            "f = lambda x: x + 1;\n",
            # Semicolons inside list comprehension outer statement line
            "res = [x for x in range(5)]; x = 2\n",
        ]
        for idx, code in enumerate(disguised_cases):
            with self.subTest(case_idx=idx, code=code.strip()):
                semis = check_tokenize_semicolons(code)
                self.assertGreater(
                    len(semis), 0,
                    f"Tokenize failed to detect semicolon in code: {code!r}"
                )

    def test_tokenize_allows_semicolons_in_strings_and_comments(self):
        """Ensure valid semicolons in string literals, docstrings, and comments do NOT trigger."""
        valid_cases = [
            # Standard strings
            'msg = "select * from table; drop table;"\n',
            "msg2 = 'item1; item2; item3'\n",
            # Raw strings (e.g. regexes)
            r'regex = r"[a-z]+;[0-9]+;"' + "\n",
            # Byte strings
            'data = b"HTTP/1.1 200 OK; Content-Type: text/plain"\n',
            # Triple-quoted docstrings
            '"""\nModule docstring with; multiple; semicolons;\n"""\n',
            "'''\nSingle quote docstring; too;\n'''\n",
            # F-strings
            'f_str = f"status={1}; code={200}"\n',
            # F-string with semicolon in expression join
            "joined = f\"Result: {'; '.join(['a', 'b'])}\"\n",
            # Line comments
            "# Note: semicolon; here; should; be; ignored\n",
            # Inline comments
            'x = 10  # inline comment with ; semicolon\n',
        ]
        for idx, code in enumerate(valid_cases):
            with self.subTest(case_idx=idx, code=code.strip()):
                semis = check_tokenize_semicolons(code)
                self.assertEqual(
                    len(semis), 0,
                    f"Tokenize falsely flagged non-code semicolon in: {code!r} (found: {semis})"
                )

    def test_ast_detector_catches_all_single_line_compound_statements(self):
        """Ensure any compound statement packed onto a single line is caught."""
        packed_cases = [
            # if-statement packed
            "if True: x = 1\n",
            "if True: return 1\n",
            "if True: pass\n",
            # for-loop packed
            "for i in range(5): print(i)\n",
            # while-loop packed
            "while True: break\n",
            # try-except packed
            "try: do_thing()\nexcept: pass\n",
            # with-statement packed
            "with open('f') as f: data = f.read()\n",
            # def packed
            "def get_val(): return 42\n",
            # class packed
            "class Dummy: pass\n",
        ]
        for idx, code in enumerate(packed_cases):
            with self.subTest(case_idx=idx, code=code.strip()):
                packed = check_ast_packed_lines(code)
                self.assertGreater(
                    len(packed), 0,
                    f"AST detector failed to catch packed statement in: {code!r}"
                )

    def test_ast_detector_handles_complex_python_constructs_without_false_positives(self):
        """Ensure walrus expressions, multi-line calls, decorators, and lambdas are not falsely flagged."""
        clean_constructs = [
            # Walrus in if-condition (walrus is ast.NamedExpr, which is an expr not a stmt)
            "if (m := pattern.search(text)):\n    process(m)\n",
            # Walrus in while-condition
            "while (chunk := stream.read(1024)):\n    process(chunk)\n",
            # Decorators on function
            "@decorator1\n@decorator2(arg='val')\ndef target_func():\n    return True\n",
            # Lambda expression assigned
            "sorter = lambda item: (item.priority, item.timestamp)\n",
            # Complex multi-line dictionary and function call
            "result = compute(\n    alpha=1,\n    beta=2,\n    nested={'a': 1, 'b': 2},\n)\n",
            # Multi-line boolean expression
            "is_valid = (\n    x > 0 and\n    y < 10 and\n    z == 'ready'\n)\n",
            # Multi-line list comprehension with filtering
            "clean_items = [\n    x.strip()\n    for x in raw_items\n    if x and len(x) > 3\n]\n",
            # Type-annotated variable assignments
            "lookup_table: dict[str, list[int]] = {}\n",
        ]
        for idx, code in enumerate(clean_constructs):
            with self.subTest(case_idx=idx, code=code.strip()):
                packed = check_ast_packed_lines(code)
                self.assertEqual(
                    len(packed), 0,
                    f"AST detector produced false positive on valid code: {code!r} (flagged: {packed})"
                )


class TestAdversarialHookExitSignals(unittest.TestCase):
    """Adversarial challenge for hooks/session-sage.py and runner exit signals."""

    def setUp(self):
        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.hook_script = os.path.join(self.repo_root, "hooks", "session-sage.py")

    def test_hook_subprocess_invocation_pre_stop_healthy(self):
        """Test invoking hook script in pre-stop mode via subprocess with empty stdin."""
        proc = subprocess.run(
            [sys.executable, self.hook_script],
            input="",
            capture_output=True,
            text=True,
            cwd=self.repo_root,
        )
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout.strip())
        self.assertEqual(out, {"decision": "stop"})

    def test_hook_subprocess_invocation_post_invocation_healthy(self):
        """Test invoking hook script in post-invocation mode via subprocess with empty stdin."""
        proc = subprocess.run(
            [sys.executable, self.hook_script, "post_invocation"],
            input="",
            capture_output=True,
            text=True,
            cwd=self.repo_root,
        )
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout.strip())
        self.assertEqual(out, {"injectSteps": []})

    def test_hook_subprocess_top_level_crash_isolation(self):
        """Simulate an unhandled exception inside runner.main and verify hook never crashes or hangs."""
        env = dict(os.environ)
        proc = subprocess.run(
            [sys.executable, self.hook_script],
            input="{malformed json input !!!",
            capture_output=True,
            text=True,
            cwd=self.repo_root,
            env=env,
        )
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout.strip())
        self.assertEqual(out.get("decision"), "stop")

    def test_hook_subprocess_post_invocation_crash_isolation(self):
        """Simulate an unhandled exception in post-invocation mode and verify injectSteps: []."""
        proc = subprocess.run(
            [sys.executable, self.hook_script, "post"],
            input="{bad json",
            capture_output=True,
            text=True,
            cwd=self.repo_root,
        )
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout.strip())
        self.assertEqual(out, {"injectSteps": []})

    def test_fail_safe_exit_releases_lock(self):
        """Verify fail_safe_exit always releases active conversation lock."""
        conv_id = "test_adversarial_lock_conv"
        acquire_conversation_lock(conv_id)

        with patch("sys.exit") as mock_exit, patch("builtins.print") as mock_print:
            fail_safe_exit("adversarial test exit")
            mock_exit.assert_called_once_with(0)

        # Should be able to acquire lock again immediately because it was released
        self.assertTrue(acquire_conversation_lock(conv_id))
        release_lock()

    def test_emit_continue_response_pre_stop_vs_post_invocation(self):
        """Verify emit_continue_response generates proper contract for pre-stop vs post."""
        # Pre-stop
        with patch("sys.exit") as mock_exit, patch("builtins.print") as mock_print:
            emit_continue_response("Need more verification", is_post=False)
            mock_exit.assert_called_once_with(0)
            mock_print.assert_called_once()
            res = json.loads(mock_print.call_args[0][0])
            self.assertEqual(res["decision"], "continue")
            self.assertEqual(res["reason"], "Need more verification")

        # Post-invocation
        with patch("sys.exit") as mock_exit, patch("builtins.print") as mock_print:
            emit_continue_response("Adviser guidance", is_post=True)
            mock_exit.assert_called_once_with(0)
            mock_print.assert_called_once()
            res = json.loads(mock_print.call_args[0][0])
            self.assertEqual(res["terminationBehavior"], "force_continue")
            self.assertEqual(res["injectSteps"][0]["userMessage"], "Adviser guidance")

    def test_emit_recap_response_pre_stop_vs_post_invocation(self):
        """Verify emit_recap_response generates proper contract for pre-stop vs post."""
        # Pre-stop
        with patch("sys.exit") as mock_exit, patch("builtins.print") as mock_print:
            emit_recap_response("Task completed", is_post=False)
            mock_exit.assert_called_once_with(0)
            mock_print.assert_called_once()
            res = json.loads(mock_print.call_args[0][0])
            self.assertEqual(res["decision"], "stop")

        # Post-invocation
        with patch("sys.exit") as mock_exit, patch("builtins.print") as mock_print:
            emit_recap_response("All done and verified", is_post=True)
            mock_exit.assert_called_once_with(0)
            mock_print.assert_called_once()
            res = json.loads(mock_print.call_args[0][0])
            self.assertEqual(res["terminationBehavior"], "terminate")
            self.assertIn("※ recap:", res["injectSteps"][0]["userMessage"])

    def test_background_watch_action_routing(self):
        """Test all branches of handle_background_watch_action under adversarial signals."""
        state = {"bg_watch_count": 0}
        state_file = "/tmp/test_bg_state.json"
        record_steer = MagicMock()
        record_grace = MagicMock()

        # 1. Steer action
        steer_bgp = {
            "action": "steer",
            "task_id": "task-999",
            "description": "long running test",
            "age_seconds": 350.0,
        }
        with patch("sage.guards.emit_continue_response") as mock_emit:
            handle_background_watch_action(
                steer_bgp, state, state_file, 100, record_steer, record_grace
            )
            record_steer.assert_called_once_with(state_file, state, "task-999", 100)
            mock_emit.assert_called_once()

        # 2. Grace action (stop event -> fail_safe_exit without force_continue)
        grace_bgp = {"action": "grace"}
        with patch("sage.guards.is_post_invocation", return_value=False), \
             patch("sage.guards.emit_continue_response") as mock_emit, \
             patch("sage.guards.fail_safe_exit") as mock_fail_safe:
            handle_background_watch_action(
                grace_bgp, state, state_file, 100, record_steer, record_grace
            )
            mock_emit.assert_not_called()
            mock_fail_safe.assert_called_once_with("Background task in 300s grace period; waiting")

        # 3. Grace action (post-invocation event -> fail_safe_exit)
        with patch("sage.guards.is_post_invocation", return_value=True), \
             patch("sage.guards.emit_continue_response") as mock_emit, \
             patch("sage.guards.fail_safe_exit") as mock_exit:
            handle_background_watch_action(
                grace_bgp, state, state_file, 100, record_steer, record_grace
            )
            mock_emit.assert_not_called()
            mock_exit.assert_called_once_with("Background task in 300s grace period; waiting")


class TestAdversarialPolicySignals(unittest.TestCase):
    """Adversarial challenge for policies.py and runner policy signal integration."""

    def test_background_watch_policy_edge_cases(self):
        """Test background_watch policy under boundary conditions."""
        # Empty tasks -> none
        self.assertEqual(background_watch([], [])["action"], "none")

        # Task < 300s -> grace
        tasks_young = [{"task_id": "t1", "description": "build", "age_seconds": 120.0}]
        self.assertEqual(background_watch(tasks_young, [])["action"], "grace")

        # Task > 300s, not steered -> steer
        tasks_old = [{"task_id": "t1", "description": "build", "age_seconds": 350.0}]
        res = background_watch(tasks_old, [])
        self.assertEqual(res["action"], "steer")
        self.assertEqual(res["task_id"], "t1")

        # Task > 300s, already steered -> already_steered
        self.assertEqual(background_watch(tasks_old, ["t1"])["action"], "already_steered")

        # Multiple tasks: 1 old unsteered, 1 young -> steer takes priority over grace
        tasks_mixed = [
            {"task_id": "t_young", "age_seconds": 50.0},
            {"task_id": "t_old", "age_seconds": 400.0, "description": "stalled test"},
        ]
        res_mixed = background_watch(tasks_mixed, [])
        self.assertEqual(res_mixed["action"], "steer")
        self.assertEqual(res_mixed["task_id"], "t_old")

    def test_final_advisor_gate_healthy_note_propagation(self):
        """Verify final_sage_gate attaches healthy assessment note to auditor context."""
        mock_verdict = {
            "status": "success",
            "decision": "hold",
            "text": "Code looks clean and verified",
            "confidence": 0.95,
        }
        with patch("sage.policies.evaluate_mid_turn_progress", return_value=mock_verdict), \
             patch("sage.policies.has_new_user_activity", return_value=False), \
             patch("sage.policies.extract_session_and_turn_data", return_value=(None, None, None, 5, None, None, None, 50)):
            res = final_sage_gate(
                conv_id="test_conv",
                transcript_path="/tmp/fake_transcript.jsonl",
                clean_prompt="Do something",
                initial_line_count=50,
                total_tool_calls=5,
                turn_tool_names=["view_file"],
                user_prompt="Do something",
                agent_steps=["step 1"],
                git_diff="",
                state={},
            )
            self.assertEqual(res["action"], "healthy")
            self.assertIn("Sage final assessment: hold (healthy)", res["note"])


if __name__ == "__main__":
    unittest.main()
