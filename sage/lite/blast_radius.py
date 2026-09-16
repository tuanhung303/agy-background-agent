"""sage.lite.blast_radius - Deterministic bounded AST blast radius detection for modified modules."""
import ast
import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional, Set


def find_direct_workspace_consumers(
    target_file: str,
    workspace_root: str = "",
) -> List[str]:
    """Finds direct importing Python files in workspace for a given target source file."""
    if not target_file or not str(target_file).endswith(".py"):
        return []
    ws_root = workspace_root or os.getcwd()
    abs_target = os.path.abspath(os.path.join(ws_root, target_file)) if not os.path.isabs(target_file) else os.path.abspath(target_file)
    if not os.path.isfile(abs_target):
        return []

    try:
        rel_target = os.path.relpath(abs_target, ws_root)
    except ValueError:
        return []

    parts = os.path.splitext(rel_target)[0].split(os.sep)
    stem = parts[-1]
    dotted_module = ".".join(parts)

    rg_bin = shutil.which("rg")
    candidates: List[str] = []
    if rg_bin:
        pattern = rf"\b({re.escape(dotted_module)}|{re.escape(stem)})\b"
        cmd = [
            rg_bin, "-l", pattern,
            "--glob", "*.py",
            "--glob", "!*.venv/*",
            "--glob", "!__pycache__/*",
            "--glob", "!.git/*",
        ]
        try:
            res = subprocess.run(cmd, cwd=ws_root, capture_output=True, text=True, timeout=2.0)
            if res.returncode == 0:
                candidates = [os.path.abspath(os.path.join(ws_root, p.strip())) for p in res.stdout.splitlines() if p.strip()]
        except Exception:
            candidates = []

    if not candidates and not rg_bin:
        for root, _, files in os.walk(ws_root):
            if any(x in root for x in (".git", ".venv", "__pycache__", "node_modules", ".gemini")):
                continue
            for f in files:
                if f.endswith(".py"):
                    candidates.append(os.path.abspath(os.path.join(root, f)))

    consumers: List[str] = []
    for cand in candidates:
        if cand == abs_target:
            continue
        try:
            with open(cand, "rb") as f:
                tree = ast.parse(f.read())
            is_consumer = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == dotted_module or alias.name.startswith(dotted_module + "."):
                            is_consumer = True
                            break
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod == dotted_module or mod.endswith("." + stem) or (node.level > 0 and stem in [a.name for a in node.names]):
                        is_consumer = True
                        break
                if is_consumer:
                    break
            if is_consumer:
                consumers.append(os.path.relpath(cand, ws_root))
        except Exception:
            continue

    return sorted(consumers)


def detect_blast_radius_gap(
    written_files: Set[str],
    inspected_files: Set[str],
    executed_commands: List[str],
    workspace_root: str = "",
) -> Optional[str]:
    """Detects when shared source modules were modified with unverified downstream consumers."""
    if not written_files:
        return None

    source_files = set()
    for f in written_files:
        p_lower = str(f).lower()
        if not p_lower.endswith(".py"):
            continue
        if any(token in p_lower for token in (
            "/tests/", "test_", "_test.", "/test/", "scratch/", "tmp/", ".venv/", "docs/"
        )):
            continue
        source_files.add(f)

    if not source_files:
        return None

    broad_test_executed = any(
        re.search(r"\b(?:pytest|python\s+scripts/verify/all\.py|npm\s+test|cargo\s+test)\b", cmd)
        and not re.search(r"pytest\s+tests/test_[a-zA-Z0-9_-]+\.py", cmd)
        for cmd in executed_commands
    )
    if broad_test_executed:
        return None

    unverified_map: Dict[str, List[str]] = {}
    ws_root = workspace_root or os.getcwd()

    for sf in sorted(source_files):
        consumers = find_direct_workspace_consumers(sf, ws_root)
        if not consumers:
            continue

        code_consumers = [c for c in consumers if not any(t in c.lower() for t in ("/tests/", "test_", "_test."))]
        test_consumers = [c for c in consumers if any(t in c.lower() for t in ("/tests/", "test_", "_test."))]

        if not code_consumers:
            continue

        unverified_code = []
        for cc in code_consumers:
            cc_base = os.path.basename(cc)
            cc_stem = os.path.splitext(cc_base)[0]
            is_inspected = cc in inspected_files or cc_base in inspected_files
            is_executed = any(
                cc in cmd or cc_base in cmd or cc_stem in cmd for cmd in executed_commands
            )
            is_test_executed = any(
                any(os.path.basename(tc) in cmd for tc in test_consumers) for cmd in executed_commands
            )
            if not (is_inspected or is_executed or is_test_executed):
                unverified_code.append(cc)

        if unverified_code:
            unverified_map[sf] = unverified_code

    if not unverified_map:
        return None

    lines = []
    for mod, callers in sorted(unverified_map.items()):
        caller_list = ", ".join(f"`{c}`" for c in callers)
        lines.append(f"- Modified module `{mod}` has unverified downstream caller(s): {caller_list}")

    return (
        "Shared source code was modified with unverified downstream callers:\n"
        + "\n".join(lines)
        + "\nNo test suite or inspection was executed across these dependent call sites. "
        "Reject completion (return FAIL) and direct the agent to run tests for or inspect the unverified callers to confirm regression immunity."
    )
