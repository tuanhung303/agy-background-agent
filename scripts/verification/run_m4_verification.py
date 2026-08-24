#!/usr/bin/env python3
"""
scripts.run_m4_verification - Unified Verification Runner for Milestone M4 (R3).
Executes AST static analysis validation, line budget checking across all 19 modules,
the M2 empirical suite, M4 hardening suites, the 4-tier E2E suite, and full test discovery.
"""

import ast
from collections import defaultdict
import glob
import io
import os
import sys
import time
import token
import tokenize
import unittest


class PrintCallVisitor(ast.NodeVisitor):
    def __init__(self):
        self.print_lines = []

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            self.print_lines.append(node.lineno)
        self.generic_visit(node)


def verify_static_invariants(repo_root):
    print("=" * 70)
    print("STEP 1: Static Analysis & Architectural Invariant Gates")
    print("=" * 70)

    pkg_dir = os.path.join(repo_root, "advisor")
    pkg_files = sorted(glob.glob(f"{pkg_dir}/**/*.py", recursive=True))

    print(f"Found {len(pkg_files)} modules in advisor/ (expected: 19)")
    if len(pkg_files) != 19:
        print(f"[FAIL] Expected 19 modules, found {len(pkg_files)}")
        return False

    # 1. Line Budget Check
    print("\n--- 1. Module Line Budget Check (Limit: <= 199 lines) ---")
    line_violations = []
    for fpath in pkg_files:
        rel = os.path.relpath(fpath, repo_root)
        with open(fpath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        count = len(lines)
        headroom = 199 - count
        status = "OK" if count <= 199 else "EXCEEDED"
        print(f"  {rel:<35} : {count:>3} lines ({headroom:>2} headroom) -> [{status}]")
        if count > 199:
            line_violations.append((rel, count))

    if line_violations:
        print(f"[FAIL] {len(line_violations)} file(s) exceeded 199 lines: {line_violations}")
        return False
    print("[PASS] All 19 modules satisfy <= 199 lines constraint.")

    # 2. Docstrings Check
    print("\n--- 2. Module Docstrings AST Check ---")
    doc_violations = []
    for fpath in pkg_files:
        rel = os.path.relpath(fpath, repo_root)
        with open(fpath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=fpath)
        doc = ast.get_docstring(tree)
        if not doc or not doc.strip():
            doc_violations.append(rel)
        else:
            first_line = doc.strip().split("\n")[0]
            print(f"  {rel:<35} : {first_line[:50]}...")
    if doc_violations:
        print(f"[FAIL] Modules missing docstrings: {doc_violations}")
        return False
    print("[PASS] All 19 modules contain non-empty docstrings.")

    # 3. Semicolon & Statement Packing Gates
    print("\n--- 3. Semicolon & AST Statement Packing Gates ---")
    target_files = list(pkg_files)
    target_files.extend(glob.glob(f"{repo_root}/hooks/**/*.py", recursive=True))
    target_files.extend(glob.glob(f"{repo_root}/statusline/**/*.py", recursive=True))

    semi_violations = []
    pack_violations = []

    for fpath in sorted(target_files):
        rel = os.path.relpath(fpath, repo_root)
        # Semicolons
        with open(fpath, "rb") as f:
            tokens = list(tokenize.tokenize(f.readline))
        semis = [
            tok for tok in tokens
            if tok.exact_type == tokenize.SEMI
            or (tok.type == token.OP and tok.string == ";")
        ]
        if semis:
            semi_violations.append((rel, len(semis)))

        # Packing
        with open(fpath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=fpath)
        line_stmts = defaultdict(list)
        for node in ast.walk(tree):
            if isinstance(node, ast.stmt):
                line_stmts[node.lineno].append(node)
        packed = {l: s for l, s in line_stmts.items() if len(s) > 1}
        if packed:
            pack_violations.append((rel, list(packed.keys())))

    if semi_violations:
        print(f"[FAIL] Semicolons found: {semi_violations}")
        return False
    if pack_violations:
        print(f"[FAIL] Statement packing found: {pack_violations}")
        return False
    print(f"[PASS] 0 semicolons and 0 statement packing across {len(target_files)} production files.")

    # 4. Wildcard Imports Gate
    print("\n--- 4. Wildcard Imports Gate ---")
    wildcard_violations = []
    for fpath in pkg_files:
        rel = os.path.relpath(fpath, repo_root)
        if os.path.basename(fpath) == "mid_verifier.py":
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=fpath)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        wildcard_violations.append((rel, node.module))
    if wildcard_violations:
        print(f"[FAIL] Wildcard imports found: {wildcard_violations}")
        return False
    print("[PASS] 0 wildcard imports in core library modules (mid_verifier.py shim allowlisted).")

    # 5. Library Print Calls AST Gate
    print("\n--- 5. Library Print Calls AST Gate ---")
    print_violations = []
    for fpath in pkg_files:
        rel = os.path.relpath(fpath, repo_root)
        if os.path.basename(fpath) in ("guards.py", "runner.py", "__init__.py"):
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=fpath)
        visitor = PrintCallVisitor()
        visitor.visit(tree)
        if visitor.print_lines:
            print_violations.append((rel, visitor.print_lines))
    if print_violations:
        print(f"[FAIL] Direct print() calls found: {print_violations}")
        return False
    print("[PASS] 0 bare print() calls in non-entrypoint library modules.")

    return True


def run_suite(suite_name, test_module_name):
    print("\n" + "=" * 70)
    print(f"STEP: Executing {suite_name} ({test_module_name})")
    print("=" * 70)
    suite = unittest.defaultTestLoader.loadTestsFromName(test_module_name)
    runner = unittest.TextTestRunner(verbosity=2)
    start_t = time.perf_counter()
    result = runner.run(suite)
    elapsed = time.perf_counter() - start_t
    print(f"\n{suite_name} Results: Ran {result.testsRun} tests in {elapsed:.3f}s, Failures={len(result.failures)}, Errors={len(result.errors)}")
    return result.wasSuccessful(), result.testsRun, elapsed


def run_full_discovery(repo_root):
    print("\n" + "=" * 70)
    print("STEP: Full Discovery Test Matrix (tests/test_*.py)")
    print("=" * 70)
    tests_dir = os.path.join(repo_root, "tests")
    suite = unittest.defaultTestLoader.discover(tests_dir, pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=1)
    start_t = time.perf_counter()
    result = runner.run(suite)
    elapsed = time.perf_counter() - start_t
    print(f"\nFull Discovery Results: Ran {result.testsRun} tests in {elapsed:.3f}s, Failures={len(result.failures)}, Errors={len(result.errors)}")
    return result.wasSuccessful(), result.testsRun, elapsed


def main():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path.insert(0, repo_root)

    print("#" * 70)
    print("AGY BACKGROUND AGENT: Unified Verification Runner")
    print("#" * 70)

    t0 = time.perf_counter()

    # Step 1: Static invariants
    if not verify_static_invariants(repo_root):
        print("\n[VERIFICATION FAILED] Static invariant check failed.")
        sys.exit(1)

    # Step 2: Static analysis unit suite
    ok_static, count_static, dur_static = run_suite("Static Analysis Suite", "tests.test_static_analysis")
    if not ok_static:
        print("\n[VERIFICATION FAILED] Static analysis test suite failed.")
        sys.exit(1)

    # Step 3: M2 empirical stress suite (stored outside tests/, so discovery cannot find it)
    ok_m2, count_m2, dur_m2 = run_suite("M2 Empirical Stress Suite", "scripts.verification.test_m2_empirical_stress")
    if not ok_m2:
        print("\n[VERIFICATION FAILED] M2 empirical stress suite failed.")
        sys.exit(1)

    # Step 4: M4 Hardening suite
    ok_m4, count_m4, dur_m4 = run_suite("M4 Comprehensive Hardening Suite", "tests.test_m4_hardening")
    if not ok_m4:
        print("\n[VERIFICATION FAILED] M4 hardening test suite failed.")
        sys.exit(1)

    # Step 5: M4 Adversarial suite
    ok_adv, count_adv, dur_adv = run_suite("M4 Adversarial Hardening Suite", "tests.test_m4_adversarial_hardening")
    if not ok_adv:
        print("\n[VERIFICATION FAILED] M4 adversarial test suite failed.")
        sys.exit(1)

    # Step 6: E2E 4-Tier Suite
    ok_e2e, count_e2e, dur_e2e = run_suite("4-Tier E2E Test Suite", "tests.test_e2e_suite")
    if not ok_e2e:
        print("\n[VERIFICATION FAILED] E2E test suite failed.")
        sys.exit(1)

    # Step 7: Full discovery across all test modules under tests/
    ok_all, count_all, dur_all = run_full_discovery(repo_root)
    if not ok_all:
        print("\n[VERIFICATION FAILED] Full discovery test matrix failed.")
        sys.exit(1)

    total_time = time.perf_counter() - t0

    print("\n" + "#" * 70)
    print("MILESTONE M4 VERIFICATION SUMMARY: 100% PASS")
    print("#" * 70)
    print(f"  1. 19/19 stop_audit modules <= 199 lines (0 semicolons, 0 packing): PASS")
    print(f"  2. AST Docstrings, Wildcard Import, & Print Gates:                  PASS")
    print(f"  3. Static Analysis Suite (tests/test_static_analysis.py):           {count_static:>3} tests in {dur_static:.3f}s [PASS]")
    print(f"  4. M2 Empirical Suite (scripts/test_m2_empirical_stress.py):       {count_m2:>3} tests in {dur_m2:.3f}s [PASS]")
    print(f"  5. M4 Unit Hardening Suite (tests/test_m4_hardening.py):           {count_m4:>3} tests in {dur_m4:.3f}s [PASS]")
    print(f"  6. M4 Adversarial Suite (tests/test_m4_adversarial_hardening.py):   {count_adv:>3} tests in {dur_adv:.3f}s [PASS]")
    print(f"  7. 4-Tier E2E Test Suite (tests/test_e2e_suite.py):                 {count_e2e:>3} tests in {dur_e2e:.3f}s [PASS]")
    print(f"  8. Tests-directory Discovery Matrix (tests/test_*.py):             {count_all:>3} tests in {dur_all:.3f}s [PASS]")
    print(f"  Total Verification Time: {total_time:.3f}s")
    print("#" * 70)


if __name__ == "__main__":
    main()
