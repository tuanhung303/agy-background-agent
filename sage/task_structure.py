"""
sage.task_structure - Heuristics for analyzing task structure and parallelizable workstreams.
"""

import os
from sage.guards import is_steering_message

FILE_TOOLS = {
    "replace_file_content", "write_to_file", "write_file",
    "edit_file", "create_file", "notebook_edit", "patch", "apply_diff",
    "modify_file", "multi_replace_file_content",
}
RESEARCH_TOOLS = {"search_web", "read_url_content", "grep_search"}
EXEC_TOOLS = {"run_command", "bash", "exec", "terminal"}
TEST_RUNNERS = ("pytest", "unittest", "cargo test", "npm test", "go test", "vitest", "jest")


def _read_steps(steps_or_path):
    if isinstance(steps_or_path, list):
        return steps_or_path
    from sage.transcript import _read_transcript_steps
    return _read_transcript_steps(steps_or_path)


def _unwrap_quoted(val):
    """Strips the JSON re-encoding agy applies to tool-arg values.

    The transcript get_transcript_path resolves stores args re-encoded, so a path
    arrives as '"/repo/src/index.ts"' — quotes included. Left in place, basename()
    returns 'index.ts"' and every exact-name seam rule below silently never fires.
    """
    s = str(val).strip()
    for q in ('"', "'"):
        if len(s) >= 2 and s.startswith(q) and s.endswith(q):
            return s[1:-1].strip()
    return s


def _extract_file_path(args):
    if not isinstance(args, dict):
        return None
    for k in ("TargetFile", "AbsolutePath", "TargetFiles", "path", "file", "target_file"):
        val = args.get(k)
        if isinstance(val, str) and val.strip():
            return _unwrap_quoted(val) or None
        if isinstance(val, list) and val and isinstance(val[0], str):
            return _unwrap_quoted(val[0]) or None
    return None


def _normalize_repo_path(fpath, root=None):
    if not fpath:
        return ""
    repo = os.path.abspath(root or os.getcwd())
    abs_p = os.path.abspath(os.path.join(repo, fpath) if not os.path.isabs(fpath) else fpath)
    try:
        rel = os.path.relpath(abs_p, repo)
        return abs_p if rel.startswith("..") else rel
    except ValueError:
        return abs_p


INTEGRATION_NAMES = ("index.ts",)
INTEGRATION_SUBSTRINGS = ("transformer", "visitor", "compiler")


def _is_integration_name(fpath):
    """True for files whose NAME marks them as an integration seam, whatever the churn.

    Deliberately NOT widened to per-package barrels like __init__.py or mod.rs: those
    are one-per-directory, so N disjoint legs each editing their own would every one
    match and fabricate a seam out of a textbook fan-out.
    """
    base = os.path.basename(fpath).lower()
    return base in INTEGRATION_NAMES or any(k in base for k in INTEGRATION_SUBSTRINGS)


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


def _classify_subagents(tsteps):
    """Classifies subagent invocations in transcript steps into (has_build, has_review)."""
    has_build, has_review = False, False
    review_kw, research_kw = ("review", "read-only", "audit"), ("research", "scout")
    from sage.transcript import _safe_tool_calls
    for s in tsteps:
        for t in _safe_tool_calls(s):
            if str(t.get("name") or "") == "invoke_subagent":
                args = t.get("args") or t.get("arguments") or {}
                subs = args.get("Subagents") if isinstance(args, dict) else None
                for item in (subs if isinstance(subs, list) and subs else [args]):
                    txt = str(item).lower() if not isinstance(item, dict) else f"{item.get('Role', '')} {item.get('Prompt', '')} {item.get('TypeName', '')}".lower()
                    if any(kw in txt for kw in review_kw):
                        has_review = True
                    elif any(kw in txt for kw in research_kw):
                        pass
                    else:
                        has_build = True
    return has_build, has_review


def get_parallelizable_signals(steps_or_path, workspace_root=None):
    """Analyzes transcript tool calls and prompt context for parallelizable workstreams."""
    steps = _read_steps(steps_or_path)
    turn_idxs = [
        i for i, s in enumerate(steps)
        if isinstance(s, dict) and s.get("type") == "USER_INPUT"
        and str(s.get("source") or "").upper() in ("USER_EXPLICIT", "USER", "")
        and not is_steering_message(str(s.get("content") or ""))
    ]
    t_steps = steps[turn_idxs[-1] + 1:] if turn_idxs else steps
    from sage.transcript import _safe_tool_calls
    t_calls = [t for s in t_steps for t in _safe_tool_calls(s) if t.get("name")]

    if any(str(t.get("name") or "") == "invoke_subagent" for t in t_calls):
        return {"parallelizable": False, "categories": [], "details": [], "suggested_roles": [], "signal_text": "", "shared_files": []}

    categories, details, suggested_roles = [], [], []
    files_by_dir, file_write_counts = {}, {}
    write_seq = []
    research_queries, test_commands = set(), set()

    for t in t_calls:
        name = str(t.get("name") or "")
        args = t.get("args") or t.get("arguments") or {}
        if name in FILE_TOOLS:
            fpath = _extract_file_path(args)
            if fpath:
                norm = _normalize_repo_path(fpath, workspace_root)
                file_write_counts[norm] = file_write_counts.get(norm, 0) + 1
                files_by_dir.setdefault(os.path.dirname(norm), set()).add(norm)
                write_seq.append(norm)
        if name in RESEARCH_TOOLS:
            rtarget = _extract_research_target(name, args)
            if rtarget:
                research_queries.add(rtarget)
        if name in EXEC_TOOLS:
            ttarget = _extract_test_target(args)
            if ttarget:
                test_commands.add(ttarget)

    # Repetition is not sharing. A file written three times in one contiguous burst
    # is one leg iterating on its own file; a seam is a file work RETURNS to after
    # touching something else. Without leg attribution, revisiting is the only
    # observable that separates the two, and counting bursts as coupling routed
    # perfectly disjoint fan-outs (4..7 legs) into Assist Mode.
    runs = [f for i, f in enumerate(write_seq) if i == 0 or write_seq[i - 1] != f]
    run_counts = {}
    for f in runs:
        run_counts[f] = run_counts.get(f, 0) + 1
    revisited = {f for f, c in run_counts.items() if c >= 2}
    integration = {f for f in file_write_counts if _is_integration_name(f)}
    # A file written ONCE cannot be written by two legs, so it is never a seam whatever
    # its name — the doctrine's own definition. Fixing the quote bug above made the
    # name rules fire for the first time in production, and without this floor N legs
    # each touching their own barrel file would all qualify.
    candidates = [
        f for f, count in file_write_counts.items()
        if (count >= 2 and f in integration) or (count >= 3 and f in revisited)
    ]
    # Integration files first: a churn-ranked sort evicted the real seam (an index.ts
    # written once) in favour of high-churn leaf files, then reported those leaves to
    # the executor as `shared=`.
    candidates.sort(key=lambda f: (f not in integration, -file_write_counts[f], f))
    shared_files = candidates[:4]

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

    total_tools_in_turn = len(t_calls)
    if total_tools_in_turn >= 12 and (len(distinct_dirs) >= 2 or len(test_commands) >= 2):
        categories.append("context_fatigue_delegation")
        details.append(f"mid-task tool accumulation ({total_tools_in_turn} tools)")
        if "QA" not in suggested_roles:
            suggested_roles.append("QA")

    total_writes = sum(file_write_counts.values())
    # Seam = share of writes landing in shared integration files, not raw write concentration.
    # top-N concentration is 3/N for N uniformly-written files, so it exceeded the 0.3 threshold
    # for any 4..10 disjoint files and routed the ideal parallel case to Assist Mode.
    seam_writes = sum(file_write_counts[f] for f in shared_files)
    seam_ratio = (seam_writes / total_writes) if total_writes > 0 else 0.0

    parallelizable = len(categories) > 0
    signal_text = ""
    if len(file_write_counts) > 3 and seam_ratio > 0.3:
        if "assist_mode" not in categories:
            categories.append("assist_mode")
        signal_text = "ASSIST_MODE: High coupling: most work touches shared files. Use Assist Mode — no delegation orders."
        parallelizable = True
    elif parallelizable:
        roles_str = ", ".join(dict.fromkeys(suggested_roles))
        details_str = "; ".join(details)
        lead = "Delegation opportunity" if categories == ["context_fatigue_delegation"] else "Independent workstreams detected"
        signal_text = f"PARALLELIZABLE: {lead} ({details_str}). Suggest invoke_subagent with roles: {roles_str}."

    return {
        "parallelizable": parallelizable,
        "categories": categories,
        "details": details,
        "suggested_roles": list(dict.fromkeys(suggested_roles)),
        "signal_text": signal_text,
        "shared_files": shared_files,
    }


def is_assist_signal(par_sig):
    """True when a parallelizable signal routed the turn to Assist Mode."""
    sig = par_sig or {}
    return bool(
        str(sig.get("signal_text") or "").startswith("ASSIST_MODE")
        or "assist_mode" in (sig.get("categories") or [])
    )
