"""
sage.sensitive - Regex word-boundary keyword detection and tool argument scanning.
"""

import json
import os
import re

from sage.config import DEFAULT_SENSITIVE_KEYWORDS, SENSITIVE_TRIGGER_ENABLED, _safe_bool


def is_sensitive_trigger_enabled():
    """Returns True if sensitive keyword tool triggers are enabled in config/env."""
    return _safe_bool(
        "AGY_SAGE_SENSITIVE_TRIGGER",
        _safe_bool(
            "AGY_ADVISOR_SENSITIVE_TRIGGER",
            _safe_bool("AGY_STOP_AUDIT_SENSITIVE_TRIGGER", SENSITIVE_TRIGGER_ENABLED),
        ),
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
        matches.update(scan_tool_call_for_sensitive(tool, keywords=kws_override(keywords)))
    return matches


def kws_override(keywords):
    """Helper to pass through keywords list."""
    return keywords if keywords is not None else get_sensitive_keywords()
