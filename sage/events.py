"""
sage.events - Dynamic context-aware event summon formatting for the strategic sage.
"""

import math
import re

EVENT_FINAL_STOP = "final_stop"
EVENT_HEARTBEAT = "heartbeat"
EVENT_TOOL_THRESHOLD = "tool_threshold"
EVENT_ERROR_LOOP = "error_loop"
EVENT_SENSITIVE_TOOL = "sensitive_tool"
EVENT_STALE_TASK = "stale_task"
EVENT_PARALLEL_OPP = "parallel_opportunity"

STYLE_FULL = "full"
STYLE_BALANCED = "balanced"
STYLE_VERBOSE = "verbose"

SEVERITY = {
    EVENT_TOOL_THRESHOLD: 1, EVENT_PARALLEL_OPP: 1,
    EVENT_HEARTBEAT: 2, EVENT_STALE_TASK: 2,
    EVENT_ERROR_LOOP: 3, EVENT_SENSITIVE_TOOL: 3, EVENT_FINAL_STOP: 3,
}

FACT_RANK = (
    "why", "sig", "cmd", "kw", "tool", "fails", "loop", "err", "bg", "age", "task",
    "sub", "plan", "deferral", "deferral_cat", "delegated_cmd", "tail_todo", "exec_after_edit",
    "test_cmd", "tools", "mix", "diff", "steers", "rep", "dur",
)

FILLER_RE = re.compile(
    r"\b(?:the|a|an|is|are|was|were|be|been|being|that|which|there|"
    r"please|kindly|simply|just|really|very|currently|also)\b",
    re.IGNORECASE,
)
POLARITY_TOKENS = ("NOT", "NO", "ONLY", "UNLESS", "BEFORE", "MUST")
_WS_RE = re.compile(r"\s{2,}")
_SECRET_RE = re.compile(r"(?i)\b(?:token|secret|password|api[_-]?key|bearer)\b\s*[:=]?\s*\S+")

FINAL_STOP_DIRECTIVE = (
    "Final stop: decide recap (terminate) or steer (continue). Enforce the Final Stop Gate, Prove-It-Works principle, and live empirical evidence: "
    "verify outputs directly against real artifacts (run feature, read actual values, inspect diff), "
    "reject proxies, self-reports, or 'it compiles' assumptions. Reject passive question-dumping ('Shall I...', 'có muốn... không') "
    "or banned deferral phrases ('out of scope', 'left for user judgment', 'future change', 'good enough for now', 'non-blocking'). "
    "Ask BEFORE permitting completion: 'Did the agent actually run and prove the real output, and can the user ship this to production right now without defects?' "
    "If unrun checks or fake proxy verification is detected, do NOT recap; steer agent to execute and verify directly."
)

PLAN_FINAL_STOP_DIRECTIVE = (
    "Final stop in /plan mode: perform adversarial grill-me audit on the proposed implementation plan. "
    "Reject premature stop if the plan contains unvalidated blind spots, unconfirmed design trade-offs, "
    "or critical choices the agent cannot unilaterally decide. "
    "Do NOT recap with 'on_track'. Emit 'watchout' with category='grill_me' and list the exact decision-critical "
    "questions with recommended options for the executing agent to ask the user via `ask_question`."
)

ASK = {
    EVENT_TOOL_THRESHOLD: "",
    EVENT_PARALLEL_OPP: "",
    EVENT_HEARTBEAT: "waiting on bg task, hung, or progressing? unblock cmd if hung.",
    EVENT_STALE_TASK: "producing output or hung? keep watch or kill.",
    EVENT_ERROR_LOOP: "root cause. exact fix cmd. NO blind retry.",
    EVENT_SENSITIVE_TOOL: "target env + preconditions + rollback verified BEFORE mutation.",
    EVENT_FINAL_STOP: FINAL_STOP_DIRECTIVE,
}

ESCALATED_ASK = {
    EVENT_ERROR_LOOP: "prior steer ignored. change approach, NOT retry count.",
    EVENT_HEARTBEAT: "prior steer ignored. kill or escalate, NOT wait longer.",
    EVENT_STALE_TASK: "prior steer ignored. kill task or report blocker.",
}


def _redact(text):
    return _SECRET_RE.sub("[redacted]", "" if text is None else str(text))


def caveman(text, style=STYLE_BALANCED):
    raw = str(text or "").strip()
    if not raw or style == STYLE_VERBOSE:
        return raw
    parts = raw.split("`")
    for i in range(0, len(parts), 2):
        parts[i] = FILLER_RE.sub(" ", parts[i])
    out = _WS_RE.sub(" ", "`".join(parts)).strip()
    return re.sub(r"\s+([,.;:])", r"\1", out)


def bucket_seconds(value):
    try:
        secs = float(value)
    except (TypeError, ValueError):
        return "?"
    for edge, label in ((60, "<1m"), (120, "~1m"), (300, "~2m"), (600, "~5m"),
                        (900, "~10m"), (1800, "~15m"), (3600, "~30m")):
        if secs < edge:
            return label
    return ">1h"


def bucket_lines(value):
    if value is None or isinstance(value, bool):
        return "?"
    try:
        val = float(value)
        if not math.isfinite(val) or val < 0 or val % 1 != 0:
            return ">1kL" if (math.isinf(val) and val > 0) else "?"
        num = int(val)
    except (TypeError, ValueError, OverflowError):
        return "?"
    for edge, label in ((1, "0L"), (11, "~10L"), (51, "~50L"),
                        (151, "~100L"), (501, "~500L"), (1001, "~1kL")):
        if num < edge:
            return label
    return ">1kL"


def _fmt_value(val):
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, float):
        return f"{val:.1f}" if val % 1 else f"{val:.0f}"
    if isinstance(val, (list, tuple, set)):
        return "/".join(sorted(str(v) for v in val))
    return _redact(val).strip()


def render_facts(facts, style=STYLE_BALANCED, max_facts=9):
    ranked = [k for k in FACT_RANK if facts.get(k) not in (None, "", [], ())]
    extra = sorted(k for k in facts if k not in FACT_RANK and facts.get(k) not in (None, "", [], ()))
    kept = (ranked + extra)[:max_facts]
    chunks = []
    for key in kept:
        val = _fmt_value(facts[key])
        if not val:
            continue
        chunks.append(val if key == "why" else f"{key}={val}")
    joined = " · ".join(chunks)
    return caveman(joined, style) if style == STYLE_FULL else joined


def _normalize_kwargs(kwargs):
    norm = dict(kwargs.get("facts") or {})
    mapping = {
        "total_tools": "tools",
        "error_streak": "fails", "tool_name": "tool", "error_sig": "sig",
        "command_snippet": "cmd", "keyword": "kw", "task_id": "task",
        "task_desc": "bg", "age_seconds": "age", "duration": "dur",
        "signal_text": "why", "is_plan": "plan",
    }
    for old_k, new_k in mapping.items():
        if old_k in kwargs and new_k not in norm:
            v = kwargs[old_k]
            if old_k in ("age_seconds", "duration") and isinstance(v, (int, float)):
                v = bucket_seconds(v)
            norm[new_k] = v
    ignored = ("facts", "style", "fallback_signal", "score", "delta", "delta_tools", "pinned_goal", "anchor_goal", "goal", "revised_goal")
    for k, v in kwargs.items():
        if k not in mapping and k not in ignored:
            norm.setdefault(k, v)
    if isinstance(norm.get("diff"), bool):
        norm.pop("diff", None)
    elif isinstance(norm.get("diff"), (int, float)):
        norm["diff"] = bucket_lines(norm["diff"])
    return norm


def format_summon_message(event_type, style=STYLE_BALANCED, **kwargs):
    """Formats dynamic, context-aware fact-ranked summon messages."""
    if event_type not in SEVERITY:
        return caveman(kwargs.get("fallback_signal") or "eval agent trajectory vs goal.", style)
    sev = SEVERITY[event_type]
    merged = _normalize_kwargs(kwargs)
    rep = merged.get("rep")
    try:
        rep_val = int(rep) if rep is not None else 0
    except (TypeError, ValueError):
        rep_val = 0
    ask = ""
    if event_type == EVENT_FINAL_STOP and (merged.get("is_plan") or merged.get("plan")):
        ask = PLAN_FINAL_STOP_DIRECTIVE
    elif sev >= 2:
        escalated = ESCALATED_ASK.get(event_type) if rep_val > 1 else ""
        ask = escalated or ASK.get(event_type, "")
    facts_str = render_facts(merged, style)
    head = f"[EVT·{event_type} s{sev}] {facts_str}".rstrip() if facts_str else f"[EVT·{event_type} s{sev}]"
    if not ask:
        return head
    sep = "\n\n" if event_type == EVENT_FINAL_STOP else "\nASK "
    return f"{head}{sep}{ask}"


def assert_polarity_intact(rendered):
    """Guards that critical negation and constraint operators remain explicit."""
    for line in str(rendered or "").splitlines():
        if not line.startswith("ASK ") and not line.startswith("Final stop"):
            continue
        lowered = line.lower()
        risky = any(word in lowered for word in ("not ", "no ", "only", "unless", "before"))
        if risky and not any(tok in line for tok in POLARITY_TOKENS):
            return False
    return True
