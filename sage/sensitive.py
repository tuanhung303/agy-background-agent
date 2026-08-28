"""
sage.sensitive - Regex word-boundary keyword detection and tool argument scanning.
"""

import json
import os
import re

from sage.config import DEFAULT_SENSITIVE_KEYWORDS, SENSITIVE_TRIGGER_ENABLED, _safe_bool_multi


def is_sensitive_trigger_enabled():
    """Returns True if sensitive keyword tool triggers are enabled in config/env."""
    return _safe_bool_multi(
        ("AGY_SAGE_SENSITIVE_TRIGGER", "AGY_ADVISOR_SENSITIVE_TRIGGER", "AGY_STOP_AUDIT_SENSITIVE_TRIGGER"),
        SENSITIVE_TRIGGER_ENABLED,
    )


def get_sensitive_keywords():
    """Retrieves the set of sensitive keywords from env or defaults."""
    env_val = (
        os.environ.get("AGY_SAGE_SENSITIVE_KEYWORDS")
        or os.environ.get("AGY_ADVISOR_SENSITIVE_KEYWORDS")
        or os.environ.get("AGY_STOP_AUDIT_SENSITIVE_KEYWORDS")
    )
    if env_val is not None:
        items = [k.strip().lower() for k in env_val.split(",") if k.strip()]
        return tuple(items)
    return tuple(k.lower() for k in DEFAULT_SENSITIVE_KEYWORDS)


def compile_sensitive_pattern(keywords=None):
    """Compiles a word-boundary regex pattern for the provided or configured keywords."""
    kws = keywords if keywords is not None else get_sensitive_keywords()
    if not kws:
        return None
    escaped = [re.escape(k.strip()) for k in kws if k.strip()]
    if not escaped:
        return None
    pattern_str = r"\b(?:" + "|".join(sorted(escaped, key=len, reverse=True)) + r")\b"
    return re.compile(pattern_str, re.IGNORECASE)


def _extract_text_fragments(val, out):
    """Recursively extracts string argument values from dicts, lists, json strings, and primitives."""
    if val is None or isinstance(val, (bool, int, float)):
        return
    if isinstance(val, str):
        out.append(val)
        stripped = val.strip()
        if (stripped.startswith("{") and stripped.endswith("}")) or (
            stripped.startswith("[") and stripped.endswith("]")
        ):
            try:
                _extract_text_fragments(json.loads(stripped), out)
            except Exception:
                pass
    elif isinstance(val, dict):
        for k, v in val.items():
            _extract_text_fragments(v, out)
    elif isinstance(val, (list, tuple, set)):
        for item in val:
            _extract_text_fragments(item, out)


def extract_tool_strings(tool_call):
    """Extracts all string fragments from a tool call dictionary or object."""
    fragments = []
    _extract_text_fragments(tool_call, fragments)
    return fragments


def scan_tool_call_for_sensitive(tool_call, keywords=None):
    """Scans a single tool call for sensitive keywords using word boundaries."""
    pattern = compile_sensitive_pattern(keywords)
    if not pattern:
        return set()
    matches = set()
    # Check tool name with underscore boundary support
    tool_name = tool_call.get("name") if isinstance(tool_call, dict) else None
    if isinstance(tool_name, str):
        found_name = pattern.findall(tool_name)
        if found_name:
            matches.update(m.lower() for m in found_name)
        elif "_" in tool_name:
            for part in tool_name.split("_"):
                if part and pattern.fullmatch(part):
                    matches.add(part.lower())
    fragments = extract_tool_strings(tool_call)
    for frag in fragments:
        found = pattern.findall(frag)
        if found:
            matches.update(m.lower() for m in found)
    return matches


def scan_turn_tools_for_sensitive(tool_calls, keywords=None):
    """Scans a collection of tool calls in a turn and returns all matched sensitive keywords."""
    if not tool_calls:
        return set()
    pattern = compile_sensitive_pattern(keywords)
    if not pattern:
        return set()
    matches = set()
    for tool in tool_calls:
        matches.update(scan_tool_call_for_sensitive(tool, keywords=keywords))
    return matches


# --- Explicit user approval detection -------------------------------------
# When the CURRENT user prompt contains an explicit approval, any deferral in
# the agent's turn violates a granted permission and must escalate to STEER.

APPROVAL_PATTERNS = (
    # English
    re.compile(r"\bgo (?:ahead|on)\b", re.I),
    re.compile(r"\b(?:yes|yeah|yep|ok(?:ay)?|sure|approved|proceed|continue|confirmed)\b[,!.]?\s*$", re.I),
    re.compile(r"\b(?:please )?(?:do it|implement it|run it|execute it|just do(?: it)?)\b", re.I),
    re.compile(r"\bfeel free to (?:proceed|implement|run)\b", re.I),
    re.compile(r"\bkeep going\b", re.I),
    # Vietnamese
    re.compile(r"\b(?:làm đi|chạy đi|triển khai đi|cứ làm|cứ triển khai|cứ chạy|đồng ý|chấp thuận|ok anh|oke anh|ừ làm đi|tiến hành)\b", re.I),
    re.compile(r"^\s*(?:ừ|uhm?|ok|oke|yes)\b", re.I),
)

# Shapes that look like approval but are questions or conditionals — NOT approval.
APPROVAL_NEGATIONS = (
    re.compile(r"\b(?:should|shall|can|could|may|might|would|do|does|did)\b[^?.!\n]*\?", re.I),
    re.compile(r"\b(?:không|chưa|nhỉ)\s*\?\s*$", re.I),
    re.compile(r"\bif\b[^?.!\n]*\b(?:then|,)\b", re.I),
    re.compile(r"\bnếu\b", re.I),
)


def detect_user_approval(user_prompt):
    """Detects explicit user approval/permission in the CURRENT user prompt.

    Returns {"approved": bool, "snippet": str}. Question-shaped or conditional
    prompts never count as approval even if they contain approval words.
    """
    from sage.sanitizer import _normalize_for_search, strip_code_blocks

    if not user_prompt:
        return {"approved": False, "snippet": ""}
    clean = _normalize_for_search(strip_code_blocks(user_prompt)).strip()
    if not clean:
        return {"approved": False, "snippet": ""}
    if any(n.search(clean) for n in APPROVAL_NEGATIONS):
        return {"approved": False, "snippet": ""}
    for pat in APPROVAL_PATTERNS:
        if m := pat.search(clean):
            return {"approved": True, "snippet": m.group(0).strip()}
    return {"approved": False, "snippet": ""}
