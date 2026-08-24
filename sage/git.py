"""
sage.git - Git diff and workspace change extraction.
"""

import os
import subprocess

from sage.config import FILE_EDITING_TOOLS
from sage.sanitizer import redact_secrets


MAX_UNTRACKED_BYTES = 1 << 20  # 1 MiB
MAX_UNTRACKED_FILES = 50


def _parse_numstat(stdout):
    tot = 0
    for nl in (stdout or "").splitlines():
        cols = nl.split("\t")
        if len(cols) >= 2:
            tot += sum(int(c) for c in cols[:2] if c.isdigit())
    return tot


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

            numstat_res = subprocess.run(
                ["git", "-C", ws, "diff", "HEAD", "--numstat"],
                capture_output=True, text=True, timeout=3,
            )
            if numstat_res.returncode == 0:
                changed = _parse_numstat(numstat_res.stdout)
            else:
                numstat_res = subprocess.run(
                    ["git", "-C", ws, "diff", "--cached", "--numstat"],
                    capture_output=True, text=True, timeout=3,
                )
                changed = _parse_numstat(numstat_res.stdout)

            untracked_res = subprocess.run(
                ["git", "-C", ws, "-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard", "-z"],
                capture_output=True, text=True, timeout=2,
            )
            untracked_files = [f for f in untracked_res.stdout.split("\0") if f] if untracked_res.returncode == 0 else []
            capped = len(untracked_files) > MAX_UNTRACKED_FILES
            skipped = 0
            for uf in untracked_files[:MAX_UNTRACKED_FILES]:
                uf_path = os.path.join(ws, uf)
                if os.path.islink(uf_path):
                    skipped += 1
                    continue
                if os.path.isfile(uf_path):
                    try:
                        if os.path.getsize(uf_path) > MAX_UNTRACKED_BYTES:
                            skipped += 1
                            continue
                        with open(uf_path, "rb") as f:
                            blob = f.read(MAX_UNTRACKED_BYTES)
                        if b"\0" in blob[:8192]:
                            skipped += 1
                            continue
                        changed += blob.count(b"\n") + (1 if blob and not blob.endswith(b"\n") else 0)
                    except Exception:
                        skipped += 1

            status_lines = [l.strip() for l in status_res.stdout.splitlines() if l.strip()][:12]
            diff_unstaged = redact_secrets(diff_unstaged_res.stdout.strip())
            diff_staged = redact_secrets(diff_staged_res.stdout.strip())

            combined_diff_parts = []
            if diff_staged:
                combined_diff_parts.append(f"Staged changes:\n{diff_staged}")
            if diff_unstaged:
                combined_diff_parts.append(f"Unstaged changes:\n{diff_unstaged}")

            combined_diff = "\n".join(combined_diff_parts).strip()
            parts = []
            if capped:
                parts.append(f">{MAX_UNTRACKED_FILES} untracked files")
            if skipped:
                parts.append(f"{skipped} skipped")
            count_suffix = f" + (partial: {', '.join(parts)})" if parts else ""

            if status_lines or combined_diff:
                truncated_diff = combined_diff[:1500] if len(combined_diff) > 1500 else combined_diff
                status_summary = ", ".join(status_lines)
                diffs.append(
                    f"Workspace ({ws}):\nStatus:\n{status_summary}\nChanged lines: {changed}{count_suffix}\nDiff:\n{truncated_diff}"
                )
        except Exception:
            continue
    return "\n\n".join(diffs)
