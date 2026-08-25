"""
sage.triage - Confidence-gated classification, deduplication, and message structuring for sage feedback.
"""
import hashlib
import math
import re

from sage.guards import is_destructive_action


def _safe_emission_text(value):
    text = str(value or "").strip()
    if is_destructive_action(text):
        return "[Destructive command suppressed] Avoid destructive commands; verify first."
    return text


def _parse_confidence(val):
    if val is None or isinstance(val, bool):
        return None
    try:
        if isinstance(val, str):
            val = val.strip().rstrip("%")
        v = float(val)
        if math.isnan(v) or math.isinf(v):
            return None
        if 1.0 < v <= 100.0:
            v = v / 100.0
        return max(0.0, min(1.0, v))
    except Exception:
        return None


def compute_advice_key(category, action, guidance=None):
    """Keys advice by the intervention itself, deliberately category-independent.

    Mixing the category into the key let one repeated demand escape the emission
    ceiling every time the sage relabelled it (general -> missing_deliverable ->
    ...), so the same steer could fire unboundedly and block termination.
    """
    cat = re.sub(r"[^a-z0-9]+", "_", str(category or "general").strip().lower()).strip("_")
    if "parallel" in cat:
        return hashlib.sha1(b"parallelize_subagent").hexdigest()[:12]
    act = re.sub(r"[^a-z0-9]+", " ", str(action or "").strip().lower()).strip()
    gui = re.sub(r"[^a-z0-9]+", " ", str(guidance or "").strip().lower()).strip()
    raw = (f"act|{act}" if act else (f"gui|{gui}" if gui else f"cat|{cat}")).encode("utf-8", errors="replace")
    return hashlib.sha1(raw).hexdigest()[:12]


def classify_advice(ver_res, seen_advice=None, steer_min_conf=0.7, escalate_min_conf=0.85, max_emissions=2, anchor_emitted=False, mode="midturn", deferral=None):
    """
    Evaluates advisor output with confidence gating, keyed deduplication, and structured tags.
    Returns dict with decision ('steer', 'watchout', 'hold', 'hold_dedup'), status, and formatted text.
    """
    if not isinstance(ver_res, dict):
        return {"decision": "hold", "status": "on_track", "category": "general", "advice_key": "", "seen": seen_advice or {}}

    seen = dict(seen_advice or {})
    status = ver_res.get("status") or ("on_track" if ver_res.get("healthy", True) else "off_track")
    category = re.sub(r"[^a-z0-9]+", "_", str(ver_res.get("category") or "general").strip().lower()).strip("_")
    action = _safe_emission_text(ver_res.get("action"))
    guidance = _safe_emission_text(ver_res.get("guidance"))
    evidence = _safe_emission_text(ver_res.get("evidence"))
    conf = _parse_confidence(ver_res.get("confidence"))
    complexity = str(ver_res.get("task_complexity") or "").strip().lower()
    pinned = _safe_emission_text(ver_res.get("pinned_goal") or ver_res.get("anchor_goal") or "")
    is_pinned = mode == "midturn" and (category in ("pinned_goal", "anchor_goal") or (bool(pinned) and not anchor_emitted and complexity in ("complex_code", "multi_file")))

    if deferral and deferral.get("matched"):
        phrase = deferral.get("snippet") or "banned deferral"
        status = "watchout"
        category = "missing_deliverable"
        del_cmd = deferral.get("delegated_cmd")
        tail_td = deferral.get("tail_todo")
        if del_cmd:
            action = f"Run `{del_cmd}` directly and verify empirical output"
        elif tail_td:
            action = f"Execute remaining work directly: {tail_td}"
        else:
            action = action or "Execute required implementation and verification tests directly"
        evidence = evidence or f"Agent output contained banned deferral/question pattern: '{phrase}'"
        guidance = guidance or "Do not defer or stop on passive confirmation questions; execute the verified work directly."

    if is_pinned and status == "on_track" and not action and not guidance:
        status = "watchout"

    if status == "off_track" and conf is not None and conf < steer_min_conf:
        status = "watchout"
    elif status == "watchout" and category == "irreversible_risk" and action and conf is not None and conf >= escalate_min_conf:
        status = "off_track"

    wouts = [_safe_emission_text(item) for item in (ver_res.get("watchouts") or [])]
    is_steer = status == "off_track"
    is_watch = status == "watchout" or (not is_steer and bool(wouts)) or is_pinned
    if not is_steer and not is_watch:
        res = {"decision": "hold", "status": "on_track", "category": category, "confidence": conf, "advice_key": "", "seen": seen}
        if "recap" in ver_res and ver_res["recap"] is not None and str(ver_res["recap"]).strip():
            res["recap"] = ver_res["recap"]
        for k in ("pinned_goal", "anchor_goal", "revised_goal", "derived_tasks", "goal_status", "task_complexity"):
            if k in ver_res and ver_res[k] is not None:
                res[k] = ver_res[k]
        if pinned:
            res["pinned_goal"] = pinned
            res["anchor_goal"] = pinned
        return res

    advice_key = compute_advice_key(category, action or pinned, guidance)
    count = seen.get(advice_key, 0)
    escalation = str(ver_res.get("escalation") or "").strip().lower()
    escalating = "ignored" in escalation or escalation in ("escalated", "repeat") or bool(deferral and deferral.get("matched"))
    repeatable = category in ("loop_detection", "irreversible_risk")
    effective_max = max_emissions * 2 if category == "irreversible_risk" else max_emissions

    if count >= effective_max or (count >= 1 and not escalating and not repeatable and not is_steer and not is_pinned):
        return {"decision": "hold_dedup", "status": status, "category": category, "confidence": conf, "advice_key": advice_key, "seen": seen}

    seen[advice_key] = count + 1
    if len(seen) > 50:
        seen = dict(sorted(seen.items(), key=lambda kv: (kv[0] != advice_key, -kv[1]))[:50])

    if is_pinned:
        tag = "[Pinned Goal]"
        head = pinned if pinned else (action or "Establish baseline objective.")
        if action and action != pinned:
            head = f"{head} | Next: {action}"
        parts = [f"{tag} {head}"]
    else:
        tag_prefix = "STEER" if is_steer else "WATCH"
        tag = f"[{tag_prefix}·{category}]"
        blind_spots = [_safe_emission_text(item) for item in (ver_res.get("blind_spots") or [])]
        head = action or "; ".join(blind_spots if is_steer else wouts) or guidance or "Course correction required."
        parts = [f"{tag} {head}"]
        if evidence:
            parts.append(f"Ev: {evidence}")
        if guidance and action and guidance != action:
            parts.append(f"Why: {guidance}")
        if pinned and (category == "scope_drift" or "drift" in str(ver_res.get("goal_status") or "").lower()):
            parts.append(f"Pinned: {pinned}")

    text = " | ".join(parts)[:2000]
    res = {
        "decision": "steer" if is_steer else "watchout",
        "status": "off_track" if is_steer else "watchout",
        "category": "pinned_goal" if is_pinned else category,
        "confidence": conf,
        "advice_key": advice_key,
        "seen": seen,
        "text": text,
    }
    if is_pinned:
        res["pinned_emitted"] = True
        res["anchor_emitted"] = True
    for k in ("pinned_goal", "anchor_goal", "revised_goal", "derived_tasks", "goal_status", "task_complexity"):
        if k in ver_res and ver_res[k] is not None:
            res[k] = ver_res[k]
    if pinned:
        res["pinned_goal"] = pinned
        res["anchor_goal"] = pinned
    if "recap" in ver_res and ver_res["recap"] is not None and str(ver_res["recap"]).strip():
        res["recap"] = ver_res["recap"]
    return res
