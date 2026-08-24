"""
sage.task_structure - Heuristics for analyzing task structure and parallelizable workstreams.
"""

import json
import os
import re

from sage.guards import is_steering_message

FILE_TOOLS = {
    "replace_file_content", "write_to_file",
    "edit_file", "create_file", "notebook_edit",
}
RESEARCH_TOOLS = {"search_web", "read_url_content", "grep_search"}
TEST_RUNNERS = ("pytest", "unittest", "cargo test", "npm test", "go test", "vitest", "jest")


def _read_steps(steps_or_path):
    if isinstance(steps_or_path, list):
        return steps_or_path
    if not isinstance(steps_or_path, str) or not os.path.exists(steps_or_path):
        return []
    steps = []
    try:
        with open(steps_or_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip():
                    try:
                        steps.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    return steps


def _extract_file_path(args):
    if not isinstance(args, dict):
        return None
    for k in ("TargetFile", "AbsolutePath", "TargetFiles", "path", "file", "target_file"):
        val = args.get(k)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, list) and val and isinstance(val[0], str):
            return val[0].strip()
    return None


def _extract_research_target(tool_name, args):
    if not isinstance(args, dict):
        return None
    for k in ("query", "Query", "Url", "url", "pattern", "Pattern"):
        val = args.get(k)
        if isinstance(val, str) and val.strip():
            return f"{tool_name}:{val.strip()}"
    return tool_name


def _extract_test_target(args):
    if not isinstance(args, dict):
        return None
    cmd = str(args.get("CommandLine") or args.get("command") or args.get("cmd") or "").strip()
    if any(runner in cmd.lower() for runner in TEST_RUNNERS):
        return cmd
    return None


def get_parallelizable_signals(steps_or_path):
    """Analyzes transcript tool calls and prompt context for parallelizable workstreams.

    Returns:
      {
        "parallelizable": bool,
        "categories": List[str],
        "details": List[str],
        "suggested_roles": List[str],
        "signal_text": str,
        "reason": str (optional)
      }
    """
    steps = _read_steps(steps_or_path)
    turn_idxs = [
        i for i, s in enumerate(steps)
        if s.get("type") == "USER_INPUT"
        and str(s.get("source") or "").upper() in ("USER_EXPLICIT", "USER", "")
        and not is_steering_message(str(s.get("content") or ""))
    ]
    t_steps = steps[turn_idxs[-1] + 1:] if turn_idxs else steps

    # If subagents were already dispatched in this turn, suppress parallelizable signal
    if any(str(t.get("name") or "") == "invoke_subagent" for s in t_steps for t in s.get("tool_calls", [])):
        return {"parallelizable": False, "categories": [], "details": [], "suggested_roles": [], "signal_text": ""}

    categories = []
    details = []
    suggested_roles = []

    files_by_dir = {}
    research_queries = set()
    test_commands = set()

    for s in t_steps:
        stools = [t for t in s.get("tool_calls", []) if isinstance(t, dict)]
        for t in stools:
            name = str(t.get("name") or "")
            args = t.get("args") or t.get("arguments") or {}

            if name in FILE_TOOLS:
                fpath = _extract_file_path(args)
                if fpath:
                    norm = os.path.normpath(fpath)
                    dname = os.path.dirname(norm)
                    files_by_dir.setdefault(dname, set()).add(norm)

            if name in RESEARCH_TOOLS:
                rtarget = _extract_research_target(name, args)
                if rtarget:
                    research_queries.add(rtarget)

            if name == "run_command":
                ttarget = _extract_test_target(args)
                if ttarget:
                    test_commands.add(ttarget)

    distinct_dirs = [d for d, fs in files_by_dir.items() if d]
    total_files = sum(len(fs) for fs in files_by_dir.values())
    if len(distinct_dirs) >= 2 and total_files >= 2:
        categories.append("disjoint_files")
        short_dirs = [os.path.basename(d) or d for d in distinct_dirs]
        details.append(f"{len(distinct_dirs)} disjoint directories: {', '.join(sorted(short_dirs)[:3])}")
        suggested_roles.append("Implementer")

    if len(research_queries) >= 2:
        categories.append("isolated_research")
        details.append(f"{len(research_queries)} distinct research queries")
        suggested_roles.append("Scout")

    if len(test_commands) >= 2:
        categories.append("independent_verification")
        details.append(f"{len(test_commands)} independent test suites")
        suggested_roles.append("QA")

    parallelizable = len(categories) > 0
    signal_text = ""
    if parallelizable:
        roles_str = ", ".join(suggested_roles)
        details_str = "; ".join(details)
        signal_text = f"PARALLELIZABLE: Independent workstreams detected ({details_str}). Suggest invoke_subagent with roles: {roles_str}."

    return {
        "parallelizable": parallelizable,
        "categories": categories,
        "details": details,
        "suggested_roles": suggested_roles,
        "signal_text": signal_text,
    }
