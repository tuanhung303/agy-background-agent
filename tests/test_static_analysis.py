#!/usr/bin/env python3
"""
tests.test_static_analysis - Static validation, AST integrity, line limits, and formatting gates.
"""

import ast
import glob
import io
import os
import token
import tokenize
import unittest
from collections import defaultdict


class PrintCallVisitor(ast.NodeVisitor):
    """AST visitor targeting direct and builtins print() function calls."""

    def __init__(self):
        self.print_lines = []

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            self.print_lines.append(node.lineno)
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "print":
            if isinstance(node.func.value, ast.Name) and node.func.value.id in ("builtins", "__builtins__"):
                self.print_lines.append(node.lineno)
        self.generic_visit(node)


class TestStaticAnalysis(unittest.TestCase):
    def setUp(self):
        self.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.pkg_dir = os.path.join(self.repo_root, "sage")

    def test_all_python_files_are_valid_syntax(self):
        py_files = glob.glob(f"{self.pkg_dir}/**/*.py", recursive=True)
        py_files.extend(glob.glob(f"{self.repo_root}/hooks/**/*.py", recursive=True))
        py_files.extend(glob.glob(f"{self.repo_root}/statusline/**/*.py", recursive=True))
        py_files.extend(glob.glob(f"{self.repo_root}/tests/**/*.py", recursive=True))
        py_files.extend(glob.glob(f"{self.repo_root}/scripts/**/*.py", recursive=True))

        self.assertGreater(len(py_files), 0, "No python files found to test")
        for filepath in py_files:
            rel_path = os.path.relpath(filepath, self.repo_root)
            with self.subTest(filepath=rel_path):
                with open(filepath, "r", encoding="utf-8") as f:
                    source = f.read()
                try:
                    tree = ast.parse(source, filename=filepath)
                    self.assertIsInstance(tree, ast.AST)
                except SyntaxError as e:
                    self.fail(f"Syntax error in {rel_path}: {e}")

    def test_all_sage_modules_are_strictly_under_300_lines(self):
        pkg_files = glob.glob(f"{self.pkg_dir}/**/*.py", recursive=True)
        self.assertGreater(len(pkg_files), 0, "No python files found in sage/")
        self.assertEqual(len(pkg_files), 43, "Expected exactly 43 modules in sage/")
        bridge_modules = {"mcp_bridge.py", "mcp_bridge_helpers.py", "mcp_bridge_wait.py"}
        for filepath in sorted(pkg_files):
            rel_path = os.path.relpath(filepath, self.repo_root)
            fname = os.path.basename(filepath)
            with self.subTest(filepath=rel_path):
                with open(filepath, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                line_count = len(lines)
                limit = 199 if fname in bridge_modules else 300
                self.assertLessEqual(
                    line_count,
                    limit,
                    f"File {rel_path} has {line_count} lines (must be <= {limit} lines)",
                )

    def test_all_modules_have_docstrings(self):
        """Assert every module in sage, hooks, statusline, and scripts contains a non-empty docstring."""
        target_dirs = ["sage", "hooks", "statusline", "scripts"]
        pkg_files = []
        for d in target_dirs:
            pkg_files.extend(glob.glob(f"{self.repo_root}/{d}/**/*.py", recursive=True))
        self.assertGreater(len(pkg_files), 0, "No python files found in target directories")
        for filepath in sorted(pkg_files):
            rel_path = os.path.relpath(filepath, self.repo_root)
            with self.subTest(filepath=rel_path):
                with open(filepath, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=filepath)
                doc = ast.get_docstring(tree)
                self.assertIsNotNone(doc, f"Module {rel_path} is missing a module docstring")
                self.assertTrue(len(doc.strip()) > 0, f"Module {rel_path} has an empty docstring")

    def test_no_wildcard_imports_in_core_modules(self):
        """Assert zero 'from module import *' statements in sage, hooks, statusline, and scripts."""
        target_dirs = ["sage", "hooks", "statusline", "scripts"]
        pkg_files = []
        for d in target_dirs:
            pkg_files.extend(glob.glob(f"{self.repo_root}/{d}/**/*.py", recursive=True))
        self.assertGreater(len(pkg_files), 0, "No python files found in target directories")
        for filepath in sorted(pkg_files):
            rel_path = os.path.relpath(filepath, self.repo_root)
            with self.subTest(filepath=rel_path):
                with open(filepath, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=filepath)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            self.assertNotEqual(
                                alias.name,
                                "*",
                                f"Wildcard import forbidden in {rel_path}: from {node.module} import *",
                            )

    def test_no_semicolons_in_sage_modules(self):
        """Assert zero semicolon characters outside docstrings/strings in sage, hooks, statusline, and scripts."""
        target_files = glob.glob(f"{self.pkg_dir}/**/*.py", recursive=True)
        target_files.extend(glob.glob(f"{self.repo_root}/hooks/**/*.py", recursive=True))
        target_files.extend(glob.glob(f"{self.repo_root}/statusline/**/*.py", recursive=True))
        target_files.extend(glob.glob(f"{self.repo_root}/scripts/**/*.py", recursive=True))
        self.assertGreater(len(target_files), 0, "No python files found in target directories")

        for filepath in sorted(target_files):
            rel_path = os.path.relpath(filepath, self.repo_root)
            with self.subTest(filepath=rel_path):
                with open(filepath, "rb") as f:
                    try:
                        tokens = list(tokenize.tokenize(f.readline))
                    except tokenize.TokenError as e:
                        self.fail(f"Tokenization failed for {rel_path}: {e}")

                semicolons = [
                    tok
                    for tok in tokens
                    if tok.exact_type == tokenize.SEMI
                    or (tok.type == token.OP and tok.string == ";")
                ]
                if semicolons:
                    violations = [
                        f"  Line {tok.start[0]}:{tok.start[1]} -> {tok.line.strip()}"
                        for tok in semicolons
                    ]
                    self.fail(
                        f"Found {len(semicolons)} semicolon(s) outside docstrings/comments in {rel_path}:\n"
                        + "\n".join(violations)
                    )

    def test_ast_single_statement_per_line_in_sage_modules(self):
        """Assert AST single-statement per line in sage, hooks, statusline, and scripts."""
        target_files = glob.glob(f"{self.pkg_dir}/**/*.py", recursive=True)
        target_files.extend(glob.glob(f"{self.repo_root}/hooks/**/*.py", recursive=True))
        target_files.extend(glob.glob(f"{self.repo_root}/statusline/**/*.py", recursive=True))
        target_files.extend(glob.glob(f"{self.repo_root}/scripts/**/*.py", recursive=True))
        self.assertGreater(len(target_files), 0, "No python files found in target directories")

        for filepath in sorted(target_files):
            rel_path = os.path.relpath(filepath, self.repo_root)
            with self.subTest(filepath=rel_path):
                with open(filepath, "r", encoding="utf-8") as f:
                    source = f.read()
                    lines = source.splitlines()

                try:
                    tree = ast.parse(source, filename=filepath)
                except SyntaxError as e:
                    self.fail(f"Syntax error in {rel_path}: {e}")

                line_stmts = defaultdict(list)
                for node in ast.walk(tree):
                    if isinstance(node, ast.stmt):
                        line_stmts[node.lineno].append(node)

                packed_lines = {l: stmts for l, stmts in line_stmts.items() if len(stmts) > 1}
                if packed_lines:
                    violations = []
                    for l in sorted(packed_lines.keys()):
                        stmts = packed_lines[l]
                        types = [type(s).__name__ for s in stmts]
                        line_text = lines[l - 1].strip() if l <= len(lines) else ""
                        violations.append(
                            f"  Line {l} ({len(stmts)} stmts: {', '.join(types)}) -> {line_text}"
                        )
                    self.fail(
                        f"Found {len(packed_lines)} line(s) with statement packing in {rel_path}:\n"
                        + "\n".join(violations)
                    )

    def test_no_forbidden_debugging_prints_in_library_modules(self):
        """Assert no direct or builtins print() AST calls in library modules outside allowed entrypoints."""
        pkg_files = [
            f for f in glob.glob(f"{self.pkg_dir}/**/*.py", recursive=True)
            if not f.endswith("__init__.py")
        ]
        for filepath in pkg_files:
            rel_path = os.path.relpath(filepath, self.repo_root)
            if os.path.basename(filepath) in ("guards.py", "runner.py", "journal.py"):
                continue
            with self.subTest(filepath=rel_path):
                with open(filepath, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=filepath)
                visitor = PrintCallVisitor()
                visitor.visit(tree)
                self.assertEqual(
                    len(visitor.print_lines),
                    0,
                    f"Forbidden print() call(s) at line(s) {visitor.print_lines} in {rel_path}",
                )

    def test_static_analysis_engine_verification(self):
        """Verify the static analysis engine itself on synthetic positive and negative cases."""
        # 1. Clean code with valid constructs
        clean_sample = '''
import os
import sys

@decorator
def helper(x: int, y: str = "val;with;semi") -> bool:
    """Docstring; contains; semicolons; safely."""
    # Comment with semicolon; too
    msg = f"format;{x};{y}"
    if x > 0:
        return True
    return False
'''
        clean_tokens = list(tokenize.tokenize(io.BytesIO(clean_sample.encode("utf-8")).readline))
        clean_semis = [t for t in clean_tokens if t.exact_type == tokenize.SEMI or (t.type == token.OP and t.string == ";")]
        self.assertEqual(len(clean_semis), 0)

        clean_tree = ast.parse(clean_sample)
        clean_line_stmts = defaultdict(list)
        for node in ast.walk(clean_tree):
            if isinstance(node, ast.stmt):
                clean_line_stmts[node.lineno].append(node)
        clean_packed = {l: s for l, s in clean_line_stmts.items() if len(s) > 1}
        self.assertEqual(len(clean_packed), 0)

        # 2. Defect detection: semicolons
        bad_semi = "x = 1; y = 2\n"
        bad_tokens = list(tokenize.tokenize(io.BytesIO(bad_semi.encode("utf-8")).readline))
        bad_semis = [t for t in bad_tokens if t.exact_type == tokenize.SEMI or (t.type == token.OP and t.string == ";")]
        self.assertEqual(len(bad_semis), 1)

        # 3. Defect detection: statement packing across multiple compound AST patterns
        for bad_packed in [
            "if True: return 1\n",
            "while cond: do_something()\n",
            "for x in xs: pass\n",
            "def f(): return 42\n",
            "try: x = 1\nexcept: pass\n",
            "async def f(): return 1\n",
        ]:
            bad_tree = ast.parse(bad_packed)
            bad_line_stmts = defaultdict(list)
            for node in ast.walk(bad_tree):
                if isinstance(node, ast.stmt):
                    bad_line_stmts[node.lineno].append(node)
            bad_packed_lines = {l: s for l, s in bad_line_stmts.items() if len(s) > 1}
            self.assertEqual(len(bad_packed_lines), 1, f"Failed to detect packing in: {bad_packed}")

        # 4. AST print visitor detection across nested structures and builtins
        cases = [
            ('def test():\n    print("hello world")\n', [2]),
            ('def test():\n    builtins.print("hello")\n', [2]),
            ('def test():\n    __builtins__.print("hello")\n', [2]),
            ('fn = lambda x: print(x)\n', [1]),
            ('def outer():\n    def inner():\n        print("nested")\n', [3]),
            ('async def f():\n    print("async")\n', [2]),
            ('results = [print(x) for x in range(3)]\n', [1]),
            ('class C:\n    def m(self):\n        print("method")\n', [3]),
        ]
        for code, expected_lines in cases:
            visitor = PrintCallVisitor()
            visitor.visit(ast.parse(code))
            self.assertEqual(visitor.print_lines, expected_lines, f"Failed for code:\n{code}")

        # 5. AST print visitor tolerance for non-print calls and strings
        benign_cases = [
            'def test():\n    msg = "do not call print()"\n',
            'def test():\n    custom_printer.print("ok")\n',
            'def test():\n    logger.info("print this")\n',
        ]
        for benign_code in benign_cases:
            visitor = PrintCallVisitor()
            visitor.visit(ast.parse(benign_code))
            self.assertEqual(len(visitor.print_lines), 0, f"False positive for benign code:\n{benign_code}")

        # 6. Wildcard import detection
        tree_wildcard = ast.parse("from os.path import *\n")
        wildcards = [
            alias.name
            for node in ast.walk(tree_wildcard)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name == "*"
        ]
        self.assertEqual(wildcards, ["*"])


if __name__ == "__main__":
    unittest.main()
