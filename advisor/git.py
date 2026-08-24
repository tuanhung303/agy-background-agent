"""
advisor.git - Git diff and workspace change extraction.
"""

import os
import subprocess

from advisor.config import FILE_EDITING_TOOLS


def get_git_diff(workspace_paths, turn_tool_names=None):
    """
    Extracts git status, staged diff, and unstaged diff across workspace paths.
    Bypasses git execution if no file-editing tools were invoked in the turn.
    """
    if turn_tool_names is not None and not (turn_tool_names & FILE_EDITING_TOOLS):
        return "None (no file-editing tools invoked in turn)"

    if not workspace_paths:
        return ""

    if isinstance(workspace_paths, str):
        workspace_paths = [workspace_paths]

    diffs = []
    for ws in workspace_paths:
        if not ws or not os.path.isdir(ws):
            continue
        try:
            status_res = subprocess.run(
                ["git", "-C", ws, "status", "--porcelain"],
                capture_output=True, text=True, timeout=2,
            )
            diff_unstaged_res = subprocess.run(
                ["git", "-C", ws, "diff", "-U1"],
                capture_output=True, text=True, timeout=3,
            )
            diff_staged_res = subprocess.run(
                ["git", "-C", ws, "diff", "--cached", "-U1"],
                capture_output=True, text=True, timeout=3,
            )

            status_lines = [l.strip() for l in status_res.stdout.splitlines() if l.strip()][:12]
            diff_unstaged = diff_unstaged_res.stdout.strip()
            diff_staged = diff_staged_res.stdout.strip()

            combined_diff_parts = []
            if diff_staged:
                combined_diff_parts.append(f"Staged changes:\n{diff_staged}")
            if diff_unstaged:
                combined_diff_parts.append(f"Unstaged changes:\n{diff_unstaged}")
            combined_diff = "\n".join(combined_diff_parts).strip()

            if status_lines or combined_diff:
                truncated_diff = combined_diff[:1500] if len(combined_diff) > 1500 else combined_diff
                status_summary = ", ".join(status_lines)
                diffs.append(
                    f"Workspace ({ws}):\nStatus:\n{status_summary}\nDiff:\n{truncated_diff}"
                )
        except Exception:
            continue
    return "\n\n".join(diffs)
