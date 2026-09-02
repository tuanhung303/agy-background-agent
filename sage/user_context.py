"""sage.user_context - Substantive multi-turn user context distillation and compaction handling."""
import os, re
from typing import Any, Dict, List, Optional, Set

from sage.config import MAX_PRIOR_REQUESTS
from sage.guards import is_steering_message
from sage.sanitizer import clean_user_prompt

_TRIVIAL_PHRASES: Set[str] = {
    "ok", "okay", "k", "kk", "oke", "okie", "okies", "okey", "yes", "y", "yeah", "yep", "yup",
    "sure", "proceed", "continue", "go ahead", "go", "go for it", "approved", "approve",
    "lgtm", "looks good", "looks good to me", "done", "thx", "thanks", "thank you", "ty", "next",
    "ship", "ship it", "ship code", "push", "push code", "push to remote", "commit and push",
    "run it", "execute", "do it", "perfect", "great", "nice", "sounds good", "alright", "all right", "fine",
}

_TRIVIAL_RE = re.compile(
    r"^(?:ok(?:ay|e|ie|ies|ey)?|y(?:es|eah|ep|up)?|sure|proceed|continue|go ahead|approved?|lgtm|looks good|"
    r"push(?: (?:code|to remote|it|changes))?|ship(?: (?:it|code))?|run it|do it|done|thanks?)[!.,? ]*$",
    re.I,
)
_INTER_AGENT_RE = re.compile(r"(?:^|\n)\s*(?:\[Message\]|sender=)|has gone idle", re.I)


def is_trivial_acknowledgment(text: Optional[str]) -> bool:
    """Returns True if message is a low-information conversational acknowledgment."""
    if not text:
        return True
    cleaned = clean_user_prompt(text).strip()
    if not cleaned or is_steering_message(cleaned):
        return True
    norm = re.sub(r"^[^\w]+|[^\w]+$", "", cleaned.lower()).strip()
    if not norm or norm in _TRIVIAL_PHRASES or _TRIVIAL_RE.match(cleaned):
        return True
    words = norm.split()
    if len(words) <= 3 and len(norm) <= 25:
        if all(w in _TRIVIAL_PHRASES or w in ("please", "now", "here", "too", "as well") for w in words):
            return True
    return False


def _parse_step_timestamp(ts_str: Any) -> float:
    if not ts_str:
        return 0.0
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        return float((dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp())
    except Exception:
        return 0.0


def _is_compaction_step(step: Dict[str, Any]) -> bool:
    stype = str(step.get("type") or "").upper()
    if stype in ("COMPACTION", "SUMMARY", "CONVERSATION_SUMMARY", "CHECKPOINT", "SYSTEM_SUMMARY"):
        return True
    return "<summary>" in str(step.get("content") or "").lower() or "summary_text" in step


def _extract_compaction_summary(step: Dict[str, Any]) -> str:
    content = str(step.get("summary") or step.get("summary_text") or step.get("compacted_summary") or step.get("content") or "").strip()
    m = re.search(r"<summary>(.*?)</summary>", content, re.DOTALL | re.I)
    return m.group(1).strip() if m else content


def _is_real_user_step(step: Dict[str, Any]) -> bool:
    if not isinstance(step, dict) or step.get("type") != "USER_INPUT":
        return False
    src, content = str(step.get("source") or "").upper(), str(step.get("content") or "")
    if _INTER_AGENT_RE.search(content) or (src and src not in ("USER_EXPLICIT", "USER")) or is_steering_message(content):
        return False
    return True


def extract_substantive_user_context(steps: List[Dict[str, Any]], conv_id: Optional[str] = None, max_chars: int = 3000) -> Dict[str, Any]:
    """Distills substantive user context across multiple turns, accounting for short acks and compaction."""
    empty_result = {
        "true_user_prompt": "", "latest_user_prompt": "", "primary_goal": "",
        "has_compaction": False, "compaction_summary": "", "is_latest_trivial": False,
        "user_turn_count": 0, "turn_start_time": 0.0,
    }
    if not steps or not isinstance(steps, list):
        return empty_result

    user_entries, compaction_summaries = [], []
    for idx, s in enumerate(steps):
        if not isinstance(s, dict):
            continue
        if _is_compaction_step(s):
            sum_text = _extract_compaction_summary(s)
            if sum_text:
                compaction_summaries.append(sum_text)
        elif _is_real_user_step(s):
            raw_c = str(s.get("content") or "")
            cleaned = clean_user_prompt(raw_c)
            if cleaned:
                user_entries.append({
                    "index": idx, "raw": raw_c, "clean": cleaned,
                    "is_trivial": is_trivial_acknowledgment(cleaned),
                    "timestamp": _parse_step_timestamp(s.get("created_at")),
                    "step_index": s.get("step_index"),
                })

    if not user_entries and not compaction_summaries:
        return empty_result

    latest_entry = user_entries[-1] if user_entries else None
    latest_clean = latest_entry["clean"] if latest_entry else ""
    latest_ts = latest_entry["timestamp"] if latest_entry else 0.0
    is_latest_trivial = latest_entry["is_trivial"] if latest_entry else False
    substantive_entries = [e for e in user_entries if not e["is_trivial"]]
    primary_goal = substantive_entries[-1]["clean"] if substantive_entries else ""
    latest_compaction = compaction_summaries[-1] if compaction_summaries else ""
    has_compaction = bool(latest_compaction)

    if not user_entries:
        formatted_prompt = f"[COMPACTED CONVERSATION SUMMARY]:\n{latest_compaction}"
    elif len(user_entries) == 1:
        formatted_prompt = f"[COMPACTED CONVERSATION SUMMARY]:\n{latest_compaction}\n\n[ACTIVE USER REQUEST]:\n{user_entries[0]['clean']}" if has_compaction else user_entries[0]["clean"]
    else:
        if not is_latest_trivial and substantive_entries and latest_entry == substantive_entries[-1] and not has_compaction:
            prior = [e["clean"] for e in user_entries[:-1]]
            max_priors = max(1, MAX_PRIOR_REQUESTS)
            hist_lines = [f"- Prior request {idx+1}: {p[:200]}" for idx, p in enumerate(prior)] if len(prior) <= max_priors else [f"- Prior request 1: {prior[0][:200]}", f"- (…{len(prior) - max_priors} earlier requests omitted)"] + [f"- Prior request {idx+1}: {prior[idx][:200]}" for idx in range(len(prior) - (max_priors - 1), len(prior))]
            formatted_prompt = f"SESSION HISTORY:\n{chr(10).join(hist_lines)}\n\n[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\n{latest_clean}"
        else:
            blocks = []
            if has_compaction:
                blocks.append(f"[COMPACTED CONVERSATION SUMMARY]:\n{latest_compaction}")
            if primary_goal:
                prim_idx = user_entries.index(substantive_entries[-1])
                blocks.append(f"[PRIMARY USER GOAL]:\n{primary_goal}")
                followups = user_entries[prim_idx + 1:]
                if followups:
                    blocks.append("[FOLLOW-UP INSTRUCTIONS & REFINEMENTS]:\n" + "\n".join(f"- User follow-up ({i+1}): {f['clean']}" for i, f in enumerate(followups)))
            else:
                blocks.append("[RECENT USER MESSAGES]:\n" + "\n".join(f"- Turn {i+1}: {e['clean']}" for i, e in enumerate(user_entries[-5:])))
            formatted_prompt = "\n\n".join(blocks)

    if len(formatted_prompt) > max_chars:
        h_half, t_half = max(50, int(max_chars * 0.6)), max(50, int(max_chars * 0.35))
        formatted_prompt = f"{formatted_prompt[:h_half]}\n\n... [intermediate context omitted] ...\n\n{formatted_prompt[-t_half:]}"

    return {
        "true_user_prompt": formatted_prompt.strip(), "latest_user_prompt": latest_clean,
        "primary_goal": primary_goal, "has_compaction": has_compaction,
        "compaction_summary": latest_compaction, "is_latest_trivial": is_latest_trivial,
        "user_turn_count": len(user_entries), "turn_start_time": latest_ts,
    }
