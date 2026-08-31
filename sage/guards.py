"""
sage.guards - Lifecycle guards, subagent detection, and fail-safe exit helpers.
"""
from datetime import datetime, timezone
import json
import os
import re
import sys
from sage.config import MIN_TOOLS_FOR_DURATION_TRIGGER, SENSITIVE_TRIGGER_ENABLED, TOOL_CALL_THRESHOLD, TURN_DURATION_THRESHOLD
from sage.locking import log_audit, release_lock

_PENDING_INBOX_STEPS = []


def is_post_invocation():
    return any(a.lower() in ("post_invocation", "postinvocation", "post-invocation", "post") for a in sys.argv[1:])


DESTRUCTIVE_ACTION_RE = re.compile(
    r"(?:^|\s|\b)(rm\s+(?=[^;&|\n]*(?:--recursive(?=\s|[;&|]|$)|-(?!-)[a-zA-Z0-9]*r[a-zA-Z0-9]*(?=\s|[;&|]|$)))"
    r"(?=[^;&|\n]*(?:--force(?=\s|[;&|]|$)|-(?!-)[a-zA-Z0-9]*f[a-zA-Z0-9]*(?=\s|[;&|]|$)))|"
    r"sudo\s+rm|mkfs|dd\s+if=|:\(\)\s*\{|git\s+reset\s+--hard|"
    r"git\s+push\s+[^;&|\n]*(?:--force(?=\s|[;&|]|$)|-(?!-)[a-zA-Z0-9]*f[a-zA-Z0-9]*(?=\s|[;&|]|$))|"
    r"drop\s+(?:table|database)|truncate\s+table|chmod\s+-R\s+777)",
    re.IGNORECASE,
)


def is_destructive_action(text):
    return bool(text and DESTRUCTIVE_ACTION_RE.search(str(text)))


def set_pending_inbox_steps(steps):
    global _PENDING_INBOX_STEPS
    _PENDING_INBOX_STEPS = list(steps or [])


def get_pending_inbox_steps():
    return list(_PENDING_INBOX_STEPS)


def fail_safe_exit(reason=""):
    if reason:
        log_audit(f"Exit (pass/skip): {reason}")
    release_lock()
    inbox_steps = get_pending_inbox_steps()
    if is_post_invocation():
        payload = {"injectSteps": inbox_steps}
        if inbox_steps:
            payload["terminationBehavior"] = "force_continue"
    else:
        payload = {"decision": "continue", "reason": "Drained sage messages"} if inbox_steps else {"decision": "stop"}
    print(json.dumps(payload))
    sys.exit(0)


def emit_continue_response(message, is_post=None):
    post = is_post if is_post is not None else is_post_invocation()
    release_lock()
    steps = [{"userMessage": message}] + get_pending_inbox_steps()
    payload = {"injectSteps": steps, "terminationBehavior": "force_continue"} if post else {"decision": "continue", "reason": message}
    print(json.dumps(payload))
    sys.exit(0)


def emit_recap_response(recap, is_post=None, kind="recap"):
    post = is_post if is_post is not None else is_post_invocation()
    release_lock()
    msg = format_hook_message(kind, recap)
    steps = [{"userMessage": msg}] + get_pending_inbox_steps()
    payload = {"injectSteps": steps, "terminationBehavior": "terminate"} if post else {"decision": "stop", "reason": msg}
    print(json.dumps(payload))
    sys.exit(0)


def check_payload_and_lifecycle():
    if os.environ.get("AGY_STOP_AUDIT_ACTIVE") == "1":
        fail_safe_exit("Child audit process recursion blocked")
    try:
        raw_stdin = sys.stdin.read()
        if not raw_stdin.strip():
            fail_safe_exit("Empty stdin")
        payload = json.loads(raw_stdin)
    except Exception as e:
        fail_safe_exit(f"JSON parse error: {e}")
    if not isinstance(payload, dict):
        fail_safe_exit("Payload not a dict")
    if payload.get("error"):
        fail_safe_exit(f"Session stopped with error: {payload.get('error')}")
    term_reason = str(payload.get("terminationReason") or payload.get("termination_reason") or "").strip().lower()
    if term_reason in {"max_steps_exceeded", "error", "canceled", "cancelled", "user_abort", "user_interrupt", "abort", "system_error"}:
        fail_safe_exit(f"Skipping on termination reason: {term_reason}")
    return payload


def is_steering_message(content):
    if not content:
        return False
    s = content.strip().lower()
    prefixes = (
        "※", "steering:", "steerer:", "recap:", "steering -", "steerer -", "recap -",
        "sage:", "sage -", "adviser:", "advisor:", "adviser -", "advisor -",
        "*steering:", "*steerer:", "*recap:", "**steering", "**steerer", "**recap",
        "*sage:", "**sage", "*adviser:", "*advisor:", "**adviser", "**advisor",
        "[steering", "[steerer", "[recap", "[sage", "[adviser", "[advisor", "[verifier",
    )
    if any(s.startswith(p) for p in prefixes):
        return True
    markers = ("stop hook blocked termination", "you are a strict, objective", "[reviewer agent", "[reviewer steering", "[reviewer steer", "[verifier", "[advisor", "[sage")
    return any(m in s for m in markers)


def format_hook_message(kind, content):
    label = kind.strip().lower()
    text = str(content or "").strip()
    if label in ("comment", "raw"):
        text = re.sub(r"^※\s*", "", text).strip()
        return f"※ {text}"
    if label not in ("steering", "steerer", "recap", "sage", "adviser", "advisor"):
        raise ValueError(f"Unsupported hook message kind: {kind}")
    text = re.sub(r"^(?:※\s*)?(?:steering|steerer|recap|sage|adviser|advisor)(?::\w+)?\s*[:\-–—]?\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^\[(?:steering|steerer|recap|sage|adviser|advisor)(?::\w+)?\]\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^\*\*(?:steering|steerer|recap|sage|adviser|advisor)(?::\w+)?:?\*\*\s*", "", text, flags=re.IGNORECASE).strip()
    return f"※ {label}: {text}"


_KNOWN_SUBAGENTS_FILE = "/tmp/agy_known_subagents.json"


def register_known_subagent(conv_id):
    if not conv_id:
        return
    try:
        known = set()
        if os.path.exists(_KNOWN_SUBAGENTS_FILE):
            with open(_KNOWN_SUBAGENTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    known.update(data)
        known.add(str(conv_id))
        with open(_KNOWN_SUBAGENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(known), f)
    except Exception:
        pass


def is_known_subagent(conv_id):
    if not conv_id:
        return False
    try:
        if os.path.exists(_KNOWN_SUBAGENTS_FILE):
            with open(_KNOWN_SUBAGENTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and str(conv_id) in data:
                    return True
    except Exception:
        pass
    return False


def is_subagent_payload(payload):
    if not isinstance(payload, dict):
        return False
    conv_id = payload.get("conversationId") or payload.get("conversation_id")
    if conv_id and is_known_subagent(conv_id):
        return True
    if payload.get("isSubagent") or payload.get("is_subagent") or payload.get("parentConversationId") or payload.get("parent_conversation_id"):
        return True
    role = str(payload.get("agentRole") or payload.get("role") or "").lower()
    if role and ("subagent" in role or "implementer" in role or "research" in role or "auditor" in role or "worker" in role or "reviewer" in role or role in ("self", "scout", "qa")):
        return True
    return False


def is_subagent_session(payload, transcript_path, user_prompt, raw_user_prompt=""):
    if is_subagent_payload(payload):
        return True
    markers = ("<subagent_reminder>", "</subagent_reminder>", "you are running as a subagent", "invoked by a caller agent", "caller agent (name:", "caller agent (id:", "[subagent_role:", "caller_agent_id", "caller_agent_name")
    subagent_pattern = r"\b(?:branch implementer|module implementer|research subagent|codebase researcher|implementer subagent)\b"
    delegation_payload_re = re.compile(r"(?:^|\n)\s*(?:#+\s*)?(?:goal|scope|context_files|required_tests|dod|required\s+tests)(?:\s*:|\n|$)", re.I)
    forwarded_re = re.compile(r"not actually sent by the user|\[message\][^\n]*sender=", re.I)
    for text in (user_prompt, raw_user_prompt):
        if not text:
            continue
        low = text.lower()
        if any(m in low for m in markers) or re.search(subagent_pattern, low):
            return True
        matches = len(delegation_payload_re.findall(text))
        if matches >= 2 and ("dod" in low or "required_tests" in low or "required tests" in low or "context_files" in low or "scope" in low):
            return True
    if transcript_path and os.path.exists(transcript_path):
        try:
            with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        step = json.loads(line)
                        stype = str(step.get("type") or "").upper()
                        c = str(step.get("content") or "").strip().lower()
                        if stype not in ("CHECKPOINT", "PLANNER_RESPONSE"):
                            if forwarded_re.search(c):
                                continue
                            if any(c.startswith(m) for m in markers):
                                return True
                            if stype in ("USER_INPUT", "SYSTEM_MESSAGE") and (any(m in c for m in markers) or re.search(subagent_pattern, c)):
                                return True
                    except Exception:
                        pass
        except Exception:
            pass
    return False


def evaluate_turn_triggers(total_tool_calls, user_ts, sensitive_matches=None):
    now_dt = datetime.now(timezone.utc)
    turn_duration = max(0.0, (now_dt - (user_ts if user_ts and user_ts.tzinfo else user_ts.replace(tzinfo=timezone.utc))).total_seconds()) if user_ts else 0.0
    is_test = os.environ.get("AGY_STOP_AUDIT_TEST") == "1" or os.environ.get("AGY_STOP_AUDIT_MIN_SECONDS") == "0"
    is_heavy = total_tool_calls >= TOOL_CALL_THRESHOLD
    is_long = turn_duration >= TURN_DURATION_THRESHOLD and total_tool_calls >= MIN_TOOLS_FOR_DURATION_TRIGGER
    is_sens = bool(sensitive_matches) and total_tool_calls >= 1 and SENSITIVE_TRIGGER_ENABLED
    if not (is_test or is_heavy or is_long or is_sens):
        fail_safe_exit(f"Conditions not met: turn_dur={turn_duration:.1f}s (<{TURN_DURATION_THRESHOLD}s), tool_calls={total_tool_calls} (<{TOOL_CALL_THRESHOLD})")
    return turn_duration


def handle_background_watch_action(bgp, state, state_file, initial_lines, record_steer_fn, record_grace_fn=None):
    act = bgp["action"]
    if act == "steer":
        tid, desc, age = bgp["task_id"], bgp["description"], bgp["age_seconds"]
        msg = format_hook_message("steering", f"Background task '{desc}' ({tid}) has run for {int(age)}s (>300s). Check status and keep watching it; terminate only if confirmed hung.")
        record_steer_fn(state_file, state, tid, initial_lines)
        log_audit(f"Active background task: {tid} ({desc}, age={age:.1f}s) -> Steering agent to watch")
        emit_continue_response(msg)
    elif act == "grace":
        fail_safe_exit("Background task in 300s grace period; waiting")
    elif act == "already_steered":
        if not is_post_invocation():
            log_audit("All stale background tasks already steered; proceeding to stop audit")
        else:
            fail_safe_exit("All stale background tasks already received steering")
