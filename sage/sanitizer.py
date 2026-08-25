"""
sage.sanitizer - String cleaning, tool output sanitization, and output budget truncation.
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
    for i, line in enumerate(lines):
        l_str = line.strip()
        if not l_str:
            continue
        if l_str in ("Output:", "Stdout:", "Stderr:"):
            start_idx = i + 1
            break
        if re.match(
            r"^(?:Created At:|Completed At:|The command exited with code 0|"
            r"Task logs are available at:|Tool is running as a background task|"
            r"File Path:|Total Lines:|Total Bytes:|Showing lines)",
            l_str,
        ):
            start_idx = i + 1
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


_SECRET_RE = re.compile(
    r"(?i)("
    r"[a-z0-9_.-]*(?:token|secret|passwd|password|api[_-]?key|apikey|bearer|private[_-]?key"
    r"|access[_-]?key|auth[_-]?token|client[_-]?secret)[a-z0-9_.-]*"
    r"[^\S\r\n]*[:=][^\S\r\n]*(?:'[^'\r\n]*'|\"[^\"\r\n]*\"|[^\s'\"\r\n]+)"
    r"|Authorization[^\S\r\n]*:[^\S\r\n]*(?:Bearer|Basic|Digest|Negotiate|Token)[^\S\r\n]+[^\s\r\n]+"
    r"|-----BEGIN[ A-Z]*PRIVATE KEY-----(?:[\s\S]{0,4000}?-----END[ A-Z]*PRIVATE KEY-----|(?:[\r\n]+[+ \t]*[A-Za-z0-9+/=]{10,}[^\r\n]*)*)"
    r")"
)


def redact_secrets(text: Optional[str]) -> str:
    """Redacts secret patterns, tokens, passwords, and private keys."""
    if not text:
        return ""
    return _SECRET_RE.sub("[redacted]", str(text))


def clamp_diff(git_diff: Optional[str], budget: int = 4000) -> str:
    """Clamps git diff output to budget with head/tail preservation and secret redaction."""
    if not git_diff or not git_diff.strip():
        return "No file modifications detected."
    sanitized = redact_secrets(git_diff)
    if len(sanitized) <= budget:
        return sanitized
    head_budget = max(20, min(1200, budget // 3))
    tail_budget = max(20, budget - head_budget - 40)
    return f"{sanitized[:head_budget]}\n... [diff truncated] ...\n{sanitized[-tail_budget:]}"


BANNED_DEFERRAL_PATTERNS = (
    re.compile(r"\bout of scope\b", re.I),
    re.compile(r"\bkhông thuộc scope\b", re.I),
    re.compile(r"\bngoài scope\b", re.I),
    re.compile(r"\bngoài phạm vi\b", re.I),
    re.compile(r"\bfuture change\b", re.I),
    re.compile(r"\bđể sau làm\b", re.I),
    re.compile(r"\blater we can\b", re.I),
    re.compile(r"\bwe will need to\b", re.I),
    re.compile(r"\bdeliberate accepted cost\b", re.I),
    re.compile(r"\baccept the (?:gap|slight|small|aspirational|cosmetic)\b", re.I),
    re.compile(r"\bleft for user judgment\b", re.I),
    re.compile(r"\bfor now we(?:'ll| will) just\b", re.I),
    re.compile(r"\bgood enough for (?:v1|now)\b", re.I),
    re.compile(r"\btạm thời như vậy\b", re.I),
    re.compile(r"\bwe can revisit this\b", re.I),
    re.compile(r"\btactical fix only\b", re.I),
    re.compile(r"\bminimum viable fix\b", re.I),
    re.compile(r"\bnon-blocking\b", re.I),
    re.compile(r"\bnot blocking\b", re.I),
    re.compile(r"\bcost of fixing isn't worth\b", re.I),
    re.compile(r"\bmvp mindset\b", re.I),
    re.compile(r"\bship now, fix later\b", re.I),
    re.compile(r"\bnot worth the dance\b", re.I),
    re.compile(r"\btrivial enough to skip\b", re.I),
    re.compile(r"\bfile follow-up if load-bearing\b", re.I),
    re.compile(r"\bproves insufficient post-merge\b", re.I),
    re.compile(r"\bstructural and pre-existing\b", re.I),
    re.compile(r"\b(?:would you like me to|do you want me to|should (?:i|we)|shall (?:i|we)|let me know if you would like)\b", re.I),
    re.compile(r"\b(?:bạn|anh)\s*(?:có muốn|có cần|muốn)\b.*(?:\?|không\??)", re.I),
    re.compile(r"\b(?:có muốn|muốn làm tiếp|muốn address|có cần|muốn triển khai)\s*.*(?:\?|không\??)", re.I),
    re.compile(r"\bcòn\s+.*\s+(?:có muốn|có cần|muốn)\s+.*(?:\?|không\??)", re.I),
    re.compile(r"\badvise đã sync có muốn làm không\b", re.I),
)


def strip_code_blocks(text: Optional[str]) -> str:
    """Strips fenced code blocks (```...```) to only inspect conversational text."""
    return re.sub(r"```[\s\S]*?```", "", str(text or ""))


def detect_deferral_in_text(text: Optional[str]) -> List[str]:
    """Returns a list of matched deferral phrases in text, or empty list."""
    if not text:
        return []
    clean = strip_code_blocks(text)
    return [m.group(0).strip() for pat in BANNED_DEFERRAL_PATTERNS if (m := pat.search(clean))]


def detect_transcript_deferral(steps: Any) -> dict:
    """Inspects the last assistant response step in transcript steps for deferrals."""
    if not steps or not isinstance(steps, list):
        return {"matched": False, "phrases": [], "snippet": ""}
    latest_resp = ""
    for s in reversed(steps):
        if isinstance(s, dict) and s.get("type") == "PLANNER_RESPONSE":
            content = str(s.get("content") or "").strip()
            if content:
                latest_resp = content
                break
    if not latest_resp:
        return {"matched": False, "phrases": [], "snippet": ""}
    phrases = detect_deferral_in_text(latest_resp)
    return {"matched": bool(phrases), "phrases": phrases, "snippet": phrases[0] if phrases else "", "raw_preview": latest_resp[:200]}

