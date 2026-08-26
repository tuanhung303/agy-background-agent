"""
sage.transcript - Parsing, sanitization, and turn extraction from transcript.jsonl.
"""

from datetime import datetime, timezone
import hashlib, itertools, json, os, re

from sage.config import get_tool_weight
from sage.guards import is_steering_message
from sage.locking import log_audit
from sage.sanitizer import clean_user_prompt, sanitize_tool_output
from sage.task_structure import get_parallelizable_signals
from sage.watchers import get_active_background_tasks as _get_tasks, get_active_subagents as _get_subs
_INTER_AGENT_RE = re.compile(r"(?:^|\n)\s*(?:\[Message\]|sender=)|has gone idle", re.I)


def get_transcript_path(payload, conv_id):
    tp = payload.get("transcriptPath") or payload.get("transcript_path")
    if tp and os.path.exists(tp):
        return tp
    for base in ("~/.gemini/antigravity-cli/brain", "~/.gemini/antigravity/brain"):
        fb = os.path.expanduser(f"{base}/{conv_id}/.system_generated/logs/transcript.jsonl")
        if os.path.exists(fb):
            return fb


def _parse_ts(ts_str):
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def is_explicit_user_input(step):
    """True when a step is a real user turn boundary (blank `source` included)."""
    if step.get("type") != "USER_INPUT":
        return False
    src = str(step.get("source") or "").upper()
    content = str(step.get("content") or "")
    if _INTER_AGENT_RE.search(content):
        return False
    return (not src or src in ("USER_EXPLICIT", "USER")) and not is_steering_message(content)


def _read_transcript_steps(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return []
    steps = []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for lno, line in enumerate(f, start=1):
                if line.strip():
                    try:
                        s = json.loads(line)
                        s["_line_no"] = lno
                        steps.append(s)
                    except Exception:
                        pass
    except Exception:
        pass
    return steps


def _safe_tool_calls(s):
    if not isinstance(s, dict):
        return []
    tc = s.get("tool_calls")
    return [t for t in tc if isinstance(t, dict)] if isinstance(tc, list) else []


def extract_session_and_turn_data(transcript_path):
    steps = _read_transcript_steps(transcript_path)
    if not steps:
        return "", "", [], 0, set(), None, None, 0
    user_prompt, raw_user_prompt, agent_steps, total_tools, tool_names = "", "", [], 0, set()
    first_ts, user_ts, all_prompts, last_user_idx = None, None, [], -1
    for i, s in enumerate(steps):
        ts = _parse_ts(s.get("created_at"))
        first_ts = first_ts or ts
        if is_explicit_user_input(s):
            last_user_idx, raw_user_prompt, user_ts = i, str(s.get("content") or ""), ts or user_ts
            cleaned = clean_user_prompt(raw_user_prompt)
            if cleaned:
                all_prompts.append(cleaned)
    if all_prompts:
        hist = "\n".join(f"- Prior request {idx+1}: {p[:200]}" for idx, p in enumerate(all_prompts[:-1]))
        user_prompt = f"[LATEST ACTIVE USER REQUEST]:\n{all_prompts[-1]}" if len(all_prompts) == 1 else f"SESSION HISTORY:\n{hist}\n\n[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\n{all_prompts[-1]}"
    if last_user_idx == -1:
        return user_prompt, raw_user_prompt, agent_steps, 0, tool_names, first_ts, user_ts, len(steps)
    for s in steps[last_user_idx + 1:]:
        stype, scontent = s.get("type"), str(s.get("content") or "")
        stools = _safe_tool_calls(s)
        total_tools += len(stools)
        tool_names.update(t.get("name") for t in stools if t.get("name"))
        if stype == "PLANNER_RESPONSE":
            snip = scontent if len(scontent) <= 1000 else f"{scontent[:500]}\n...\n{scontent[-500:]}"
            agent_steps.append(f"Response: {snip} | Tools: {[t.get('name') for t in stools]}")
        elif stype == "GENERIC":
            agent_steps.append(f"Tool output: {sanitize_tool_output(scontent)}")
        elif scontent and not is_steering_message(scontent):
            agent_steps.append(f"{stype}: {sanitize_tool_output(scontent, max_chars=400)}")
    return user_prompt, raw_user_prompt, agent_steps, total_tools, tool_names, first_ts, user_ts, len(steps)


def _step_to_text(s):
    """Render one transcript step the same way extract_session_and_turn_data does."""
    stype, scontent = s.get("type"), str(s.get("content") or "")
    stools = _safe_tool_calls(s)
    if stype == "PLANNER_RESPONSE":
        snip = scontent if len(scontent) <= 1000 else f"{scontent[:500]}\n...\n{scontent[-500:]}"
        return f"Response: {snip} | Tools: {[t.get('name') for t in stools]}"
    if stype == "GENERIC":
        return f"Tool output: {sanitize_tool_output(scontent)}"
    if scontent and not is_steering_message(scontent):
        return f"{stype}: {sanitize_tool_output(scontent, max_chars=400)}"
    return ""


def render_turn_steps_slice(transcript_path, since_tools):
    """Steps the sage has NOT seen yet (cumulative tool calls passed since_tools),
    rendered identically to extract_session_and_turn_data. Returns
    (step_strings, unchanged_count) or None when the whole window is new/stale."""
    if since_tools <= 0 or not transcript_path or not os.path.exists(transcript_path):
        return None
    steps = _read_transcript_steps(transcript_path)
    if not steps:
        return None
    start = 0
    for i, s in enumerate(steps):
        if is_explicit_user_input(s):
            start = i + 1
    cum, end = 0, len(steps)
    for j in range(start, len(steps)):
        cum += len(_safe_tool_calls(steps[j]))
        if cum >= since_tools:
            end = j + 1
            break
    tail = steps[end:]
    if not tail:
        return None
    return [_step_to_text(s) for s in tail if _step_to_text(s)], end


def has_new_user_activity(transcript_path, original_user_prompt, original_line_count=0):
    try:
        steps = _read_transcript_steps(transcript_path)
        if not steps or len(steps) < original_line_count:
            return True
        latest = [clean_user_prompt(str(s.get("content") or "")) for s in steps if is_explicit_user_input(s)]
        if latest and latest[-1] and latest[-1] != clean_user_prompt(original_user_prompt):
            return True
        return any(is_explicit_user_input(s) for s in steps[original_line_count:]) if len(steps) > original_line_count else False
    except Exception as e:
        log_audit(f"Error checking new user activity: {e}")
        return False


def get_active_turn_identity(transcript_path):
    for s in reversed(_read_transcript_steps(transcript_path)):
        if is_explicit_user_input(s):
            sid = s.get("step_index")
            return f"step:{sid}" if sid is not None else (f"created:{s.get('created_at')}" if s.get('created_at') else f"line:{s.get('_line_no', 1)}")
    return "missing"


def extract_turn_tool_calls(transcript_path):
    steps = _read_transcript_steps(transcript_path)
    turn_idxs = [i for i, s in enumerate(steps) if is_explicit_user_input(s)]
    t_steps = steps[turn_idxs[-1] + 1:] if turn_idxs else steps
    return [t for s in t_steps for t in _safe_tool_calls(s)]


def calculate_turn_tool_score(transcript_path, last_verified_tools=0):
    calls = extract_turn_tool_calls(transcript_path)
    new_calls = calls[last_verified_tools:] if len(calls) > last_verified_tools else []
    return sum(get_tool_weight(c.get("name")) for c in new_calls if isinstance(c, dict)), len(calls)


def _tool_sig(t):
    """Repeat-detection signature: strip churn tokens, hash full args (P2-D)."""
    name = str(t.get("name") or "")
    if any(m in name.lower() for m in ("manage_task", "status", "list_dir", "get_window")):
        return None
    args = t.get("args") or t.get("arguments") or {}
    try:
        raw = json.dumps(args, sort_keys=True, default=str)
    except Exception:
        return f"{name}|?"
    # Timestamps are anchored to the clock format (P2-C over-match), retry
    # counters (`i3`, attempt#) and bare seconds before `s` are stripped; two
    # different long commands must never collide on a truncated prefix.
    normed = re.sub(r"\b(?:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?Z?|i\d+\b|it #\d+|attempt[ _]?\d+|\b\d+(?:\.\d+)?(?=s\b))", "", raw)
    return f"{name}|{hashlib.sha1(normed.encode('utf-8', errors='replace')).hexdigest()[:16]}"


def has_repeated_tool_calls(transcript_path, lookback=12, min_repeats=3, min_dominance=0.6):
    calls = extract_turn_tool_calls(transcript_path)[-lookback:]
    if len(calls) < min_repeats:
        return False
    sigs = [s for t in calls if (s := _tool_sig(t)) is not None]
    if len(sigs) < min_repeats:
        return False
    counts = {s: sigs.count(s) for s in set(sigs)}
    for sig, cnt in counts.items():
        if cnt >= min_repeats:
            best = max(sum(1 for _ in group) for match, group in itertools.groupby(sigs) if match == sig) if sig in sigs else 0
            if best >= 2 or cnt / len(sigs) >= min_dominance:
                return True
    return False


def get_active_subagents(transcript_path, conv_id=None):
    return _get_subs(_read_transcript_steps(transcript_path), conv_id)


def get_active_external_panes(transcript_path):
    from sage.watchers import get_active_external_panes as _panes
    return _panes(_read_transcript_steps(transcript_path))


def has_active_subagents(transcript_path, conv_id=None):
    return bool(get_active_subagents(transcript_path, conv_id))


def get_active_background_tasks(transcript_path, conv_id=None):
    return _get_tasks(_read_transcript_steps(transcript_path), conv_id, _parse_ts)


def has_active_background_tasks(transcript_path, conv_id=None):
    return bool(get_active_background_tasks(transcript_path, conv_id))


def is_post_invocation_completion_candidate(transcript_path, conv_id=None):
    if has_active_subagents(transcript_path, conv_id) or has_active_background_tasks(transcript_path, conv_id):
        return False
    steps = _read_transcript_steps(transcript_path)
    latest_idx = next((i for i in range(len(steps) - 1, -1, -1) if steps[i].get("type") == "PLANNER_RESPONSE"), -1)
    if latest_idx == -1 or any(s.get("type") not in ("CHECKPOINT", "SYSTEM_MESSAGE", "EPHEMERAL_MESSAGE", "ERROR_MESSAGE") for s in steps[latest_idx + 1:]):
        return False
    latest = steps[latest_idx]
    return bool(str(latest.get("content") or "").strip() and not latest.get("tool_calls"))


def has_recent_tool_errors(transcript_path, max_lookback=6):
    steps = _read_transcript_steps(transcript_path)
    recent = [s for s in steps if s.get("type") in ("GENERIC", "PLANNER_RESPONSE")][-max_lookback:] if steps else []
    err_patterns = ("error:", "exit code 1", "exit code 2", "exit code 127", "command not found", "traceback (most recent call last)")
    return sum(1 for s in recent if any(p in str(s.get("content") or "").lower() for p in err_patterns)) >= 2
