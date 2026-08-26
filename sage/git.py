"""
sage.git - Workspace change shape. Pointers only; Sage reads the files itself.
"""

import os
import subprocess

from sage.config import FILE_EDITING_TOOLS
from sage.sanitizer import redact_secrets


MAX_STATUS_LINES = 12

def _parse_numstat(stdout):
    """Sums added and deleted lines from git diff --numstat output."""
    tot = 0
    for nl in (stdout or "").splitlines():
        cols = nl.split("\t")
        if len(cols) >= 2:
            tot += sum(int(c) for c in cols[:2] if c.isdigit())
    return tot


def resolve_workspace_root(workspace_paths):
    """First real directory among the payload's workspace paths, absolute.

    Absolute matters: Sage is spawned with HOME rebound to its isolated home, so
    a `~`-relative or cwd-relative path would resolve away from the real repo.
    """
    if not workspace_paths:
        return ""
    if isinstance(workspace_paths, str):
        workspace_paths = [workspace_paths]
    for ws in workspace_paths:
        if ws and os.path.isdir(ws):
            return os.path.abspath(ws)
    return ""


def get_git_diff(workspace_paths, turn_tool_names=None):
    """
    Summarizes change shape per workspace: status entries, changed-line count,
    untracked count. Emits no patch bodies and reads no file contents.
    Bypasses git execution if no file-editing tools were invoked in the turn.
    """
    if turn_tool_names is not None and not (turn_tool_names & FILE_EDITING_TOOLS):
        return "None (no file-editing tools invoked in turn)"

    if not workspace_paths:
        return ""

    if isinstance(workspace_paths, str):
        workspace_paths = [workspace_paths]

    summaries = []
    for ws in workspace_paths:
        if not ws or not os.path.isdir(ws):
            continue
        try:
            status_res = subprocess.run(
                ["git", "-C", ws, "-c", "core.quotePath=false", "status", "--porcelain"],
                capture_output=True, text=True, timeout=2,
            )
            numstat_res = subprocess.run(
                ["git", "-C", ws, "diff", "HEAD", "--numstat"],
                capture_output=True, text=True, timeout=3,
            )
            if numstat_res.returncode != 0:
                numstat_res = subprocess.run(
                    ["git", "-C", ws, "diff", "--cached", "--numstat"],
                    capture_output=True, text=True, timeout=3,
                )
            changed = _parse_numstat(numstat_res.stdout) if numstat_res.returncode == 0 else 0

            entries = [ln.strip() for ln in status_res.stdout.splitlines() if ln.strip()]
            if not entries and not changed:
                continue
            untracked = sum(1 for ln in entries if ln.startswith("??"))
            shown, overflow = entries[:MAX_STATUS_LINES], max(0, len(entries) - MAX_STATUS_LINES)
            status_summary = redact_secrets(", ".join(shown))
            if overflow:
                status_summary += f", +{overflow} more entries"
            summaries.append(
                f"Workspace ({os.path.abspath(ws)}):\nStatus:\n{status_summary}\n"
                f"Changed lines: {changed}\nUntracked files: {untracked}"
            )
        except Exception:
            continue
    return "\n\n".join(summaries)
