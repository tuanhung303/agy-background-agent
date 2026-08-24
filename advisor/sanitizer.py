"""
advisor.sanitizer - String cleaning, tool output sanitization, and output budget truncation.
"""

import re
from typing import Any, List, Optional


def clean_user_prompt(text: Optional[str]) -> str:
    """Strips AGY XML envelope tags (<USER_REQUEST>, <ADDITIONAL_METADATA>, etc.)."""
    if not text:
        return ""
    patterns = (
        r"<USER_REQUEST>(.*?)</USER_REQUEST>",
        r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>",
        r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>",
    )
    for p in patterns:
        text = re.sub(p, r"\1" if "(.*?)" in p else "", text, flags=re.DOTALL)
    return text.strip()


def _strip_boilerplate_headers(lines: List[str]) -> List[str]:
    """Strips execution metadata headers from tool output."""
    start_idx = 0
    in_header = False
    for i, line in enumerate(lines):
        l_str = line.strip()
        if not l_str:
            continue
        if l_str in ("Output:", "Stdout:", "Stderr:"):
            start_idx = i + 1
            break
        if re.match(
            r"^(?:Created At:|Completed At:|The command exited with code 0\b|"
            r"The command exited with code 0\.|Task logs are available at:|"
            r"Tool is running as a background task|File Path:|Total Lines:|"
            r"Total Bytes:|Showing lines)",
            l_str,
        ):
            in_header = True
            start_idx = i + 1
        elif in_header:
            break
        else:
            break
    return [l for l in lines[start_idx:] if l.strip()]


def _clamp_lines(lines: List[str], max_line_len: int) -> List[str]:
    """Clamps long lines with mid-line truncation indicators."""
    h_len = max(20, int(max_line_len * 0.6))
    t_len = max(10, int(max_line_len * 0.25))
    clamped = []
    for l in lines:
        if len(l) > max_line_len:
            clamped.append(f"{l[:h_len]} ... [line truncated] ... {l[-t_len:]}")
        else:
            clamped.append(l)
    return clamped


def sanitize_tool_output(content: Any, max_chars: int = 800, max_line_len: int = 300) -> str:
    """Sanitizes tool output by stripping boilerplates, clamping long lines, and head/tail truncation."""
    if not content:
        return ""
    text = str(content).strip()
    if not text:
        return ""

    clean_lines = _strip_boilerplate_headers(text.splitlines())
    if not clean_lines:
        return ""

    clamped = _clamp_lines(clean_lines, max_line_len)
    total_lines = len(clamped)
    full_text = "\n".join(clamped)
    if len(full_text) <= max_chars:
        return full_text

    head_count = min(6, total_lines // 2)
    tail_count = min(6, total_lines // 2)
    if head_count == 0 or total_lines <= head_count + tail_count:
        h_half = max(1, max_chars // 2 - 15)
        if max_chars < 60:
            return full_text[:max_chars]
        return f"{full_text[:h_half]}\n... [truncated] ...\n{full_text[-h_half:]}"

    head = clamped[:head_count]
    tail = clamped[-tail_count:]
    tr_count = total_lines - head_count - tail_count
    head_txt = "\n".join(head)
    tail_txt = "\n".join(tail)
    hdr = f"[Output: {total_lines} lines total]\n[Lines 1-{head_count}]:\n"
    mid = f"\n... [{tr_count} lines truncated] ...\n[Lines {total_lines - tail_count + 1}-{total_lines}]:\n"
    res = hdr + head_txt + mid + tail_txt
    if len(res) <= max_chars:
        return res

    avail = max_chars - len(hdr) - len(mid)
    if avail < 40:
        return full_text[:max_chars] if max_chars > 0 else ""
    return hdr + head_txt[: avail // 2] + mid + tail_txt[-(avail - avail // 2) :]


def clamp_diff(git_diff: Optional[str], budget: int = 4000) -> str:
    """Clamps git diff output to budget with head/tail preservation."""
    if not git_diff or not git_diff.strip():
        return "No file modifications detected."
    if len(git_diff) <= budget:
        return git_diff
    head_budget = max(20, min(1200, budget // 3))
    tail_budget = max(20, budget - head_budget - 40)
    return f"{git_diff[:head_budget]}\n... [diff truncated] ...\n{git_diff[-tail_budget:]}"
