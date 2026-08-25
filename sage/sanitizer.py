"""
sage.sanitizer - String cleaning, tool output sanitization, and output budget truncation.
"""

import re
from typing import Any, Dict, List, Optional


def clean_user_prompt(text: Optional[str]) -> str:
    """Strips AGY XML envelope tags (<USER_REQUEST>, <ADDITIONAL_METADATA>, etc.)."""
    if not text:
        return ""
    for p in (r"<USER_REQUEST>(.*?)</USER_REQUEST>", r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>"):
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
    if not content or not str(content).strip():
        return ""
    clean_lines = _strip_boilerplate_headers(str(content).strip().splitlines())
    if not clean_lines:
        return ""
    clamped = _clamp_lines(clean_lines, max_line_len)
    total_lines = len(clamped)
    full_text = "\n".join(clamped)
    if len(full_text) <= max_chars:
        return full_text
    head_count = tail_count = min(6, total_lines // 2)
    if head_count == 0 or total_lines <= head_count + tail_count:
        h_half = max(1, max_chars // 2 - 15)
        return full_text[:max_chars] if max_chars < 60 else f"{full_text[:h_half]}\n... [truncated] ...\n{full_text[-h_half:]}"
    hdr = f"[Output: {total_lines} lines total]\n[Lines 1-{head_count}]:\n"
    mid = f"\n... [{total_lines - head_count - tail_count} lines truncated] ...\n[Lines {total_lines - tail_count + 1}-{total_lines}]:\n"
    res = hdr + "\n".join(clamped[:head_count]) + mid + "\n".join(clamped[-tail_count:])
    if len(res) <= max_chars:
        return res
    avail = max_chars - len(hdr) - len(mid)
    if avail < 40:
        return full_text[:max_chars] if max_chars > 0 else ""
    return hdr + "\n".join(clamped[:head_count])[:avail // 2] + mid + "\n".join(clamped[-tail_count:])[-(avail - avail // 2):]


_SECRET_RE = re.compile(
    r"(?i)([a-z0-9_.-]*(?:token|secret|passwd|password|api[_-]?key|apikey|bearer|private[_-]?key|access[_-]?key|auth[_-]?token|client[_-]?secret)[a-z0-9_.-]*[^\S\r\n]*[:=][^\S\r\n]*(?:'[^'\r\n]*'|\"[^\"\r\n]*\"|[^\s'\"\r\n]+)|Authorization[^\S\r\n]*:[^\S\r\n]*(?:Bearer|Basic|Digest|Negotiate|Token)[^\S\r\n]+[^\s\r\n]+|-----BEGIN[ A-Z]*PRIVATE KEY-----(?:[\s\S]{0,4000}?-----END[ A-Z]*PRIVATE KEY-----|(?:[\r\n]+[+ \t]*[A-Za-z0-9+/=]{10,}[^\r\n]*)*))"
)


def redact_secrets(text: Optional[str]) -> str:
    """Redacts secret patterns, tokens, passwords, and private keys."""
    return _SECRET_RE.sub("[redacted]", str(text)) if text else ""


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


DEFERRAL_TAXONOMY = (
    ("question_dumping", (
        re.compile(r"\b(?:would you like me to|do you want me to|should (?:i|we)|shall (?:i|we)|let me know if you would like|how would you like to proceed|what would you like to do next)\b", re.I),
        re.compile(r"\b(?:bạn|anh|chị|senpai)\s*(?:có\s+)?(?:muốn|cần|muốn em|muốn mình)\b.*(?:\?|không\??)", re.I),
        re.compile(r"\b(?:có\s+)?(?:muốn|cần)\s+(?:mình|em|tôi|tiếp tục|làm tiếp|triển khai|chạy|fix|sửa|test|address)\b.*(?:\?|không\??)", re.I),
        re.compile(r"\bcòn\s+.*\s+(?:có\s+)?(?:muốn|cần|muốn em)\s+.*(?:\?|không\??)", re.I),
        re.compile(r"\badvise đã sync có muốn làm không\b", re.I),
    )),
    ("scope_evasion", (
        re.compile(r"\b(?:out[\s_-]?of[\s_-]?scope|outside(?: of)? scope|không thuộc scope|ngoài scope|ngoài phạm vi|pre-existing|not in (?:the )?(?:original )?prompt|beyond the scope)\b", re.I),
    )),
    ("aspirational_gap", (
        re.compile(r"\b(?:good enough for (?:v1|now)|future (?:change|work|enhancement|iteration)|(?:we can|we will) revisit|we will need to|left for user judgment|for now we(?:'ll| will) just|tạm thời (?:như vậy|chấp nhận|thế)|để sau (?:làm|xử lý|tính)|gác lại phần này|mvp mindset|non-blocking|not blocking|deliberate accepted cost|accept the (?:gap|slight|small|aspirational))\b", re.I),
    )),
    ("delegated_execution", (
        re.compile(r"\b(?:(?:bạn|anh|chị|senpai)\s+(?:có thể\s+)?(?:tự\s+)?(?:chạy|thử|test|run|execute)|(?:you can|please|feel free to)\s+(?:now\s+)?(?:run|test|execute|verify|try)|hãy\s+(?:tự\s+)?(?:chạy|chạy lệnh|run)|to\s+(?:test|verify|run),?\s+(?:you can\s+)?run)\b", re.I),
    )),
    ("tail_todo", (
        re.compile(r"(?:^|\n)#{1,4}\s*(?:Next Steps|Remaining Work|Remaining|TODO|Caveats|Limitations|Lưu ý|Việc cần làm|Bước tiếp theo|Known issues?)\b", re.I),
        re.compile(r"(?:^|\n)-\s*\[\s*\]\s*TODO:", re.I),
    )),
)

BANNED_DEFERRAL_PATTERNS = tuple(pat for _, pats in DEFERRAL_TAXONOMY for pat in pats)


def strip_code_blocks(text: Optional[str]) -> str:
    """Strips fenced code blocks (```...```) to only inspect conversational text."""
    return re.sub(r"```[\s\S]*?```", "", str(text or ""))


def _normalize_for_search(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", str(text)).replace("\u00a0", " ")
    collapsed = re.sub(r"[^\S\n]+", " ", cleaned)
    return re.sub(r"\b(out|ngoài|không\s+thuộc)[-_]scope\b", r"\1 scope", collapsed, flags=re.I)


def detect_deferral_in_text(text: Optional[str]) -> List[str]:
    """Returns a list of matched deferral phrases in text, or empty list."""
    if not text:
        return []
    clean = _normalize_for_search(strip_code_blocks(text))
    return [m.group(0).strip() for pat in BANNED_DEFERRAL_PATTERNS if (m := pat.search(clean))]


def extract_delegated_command(text: str) -> str:
    clean = strip_code_blocks(text)
    m = re.search(r"(?:chạy|run|execute|lệnh|test(?:ing)?)\s*(?:bằng|with|command)?:?\s*`([^`]+)`", clean, re.I)
    return m.group(1).strip() if m else ""


def extract_tail_todo(text: str) -> str:
    clean = strip_code_blocks(text)
    m = re.search(r"(?:^|\n)(#{1,4}\s*(?:Next Steps|Remaining Work|Remaining|TODO|Việc cần làm|Bước tiếp theo)[^\n]*)", clean, re.I)
    return m.group(1).strip() if m else ""


def detect_transcript_deferral(steps: Any) -> Dict[str, Any]:
    """Inspects all assistant response steps in current turn for deferrals."""
    if not steps or not isinstance(steps, list):
        return {"matched": False, "category": "general", "phrases": [], "snippet": "", "delegated_cmd": "", "tail_todo": ""}
    turn_responses = []
    for s in reversed(steps):
        if isinstance(s, dict):
            stype = s.get("type")
            if stype == "USER_INPUT":
                break
            if stype == "PLANNER_RESPONSE":
                content = str(s.get("content") or "").strip()
                if content:
                    turn_responses.append(content)
    if not turn_responses:
        return {"matched": False, "category": "general", "phrases": [], "snippet": "", "delegated_cmd": "", "tail_todo": ""}
    full_turn_text = "\n".join(reversed(turn_responses))
    clean = _normalize_for_search(strip_code_blocks(full_turn_text))
    matched_cat, matched_phrases = "general", []
    for cat, pats in DEFERRAL_TAXONOMY:
        for p in pats:
            if m := p.search(clean):
                matched_phrases.append(m.group(0).strip())
                if matched_cat == "general":
                    matched_cat = cat
    del_cmd = extract_delegated_command(full_turn_text) if matched_cat == "delegated_execution" or "`" in full_turn_text else ""
    tail_td = extract_tail_todo(turn_responses[0])
    return {
        "matched": bool(matched_phrases),
        "category": matched_cat,
        "phrases": matched_phrases,
        "snippet": matched_phrases[0] if matched_phrases else "",
        "delegated_cmd": del_cmd,
        "tail_todo": tail_td,
        "raw_preview": turn_responses[0][:200],
    }

