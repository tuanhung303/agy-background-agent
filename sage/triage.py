"""
sage.triage - Confidence-gated classification, deduplication, and message structuring for sage feedback.
"""
import hashlib
import math
import re

from sage.guards import is_destructive_action
from sage.ladder import next_rung_suffix


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
    ceiling every time the sage relabelled it (general -> missing_proof ->
    ...), so the same steer could fire unboundedly and block termination.
    """
    cat = re.sub(r"[^a-z0-9]+", "_", str(category or "general").strip().lower()).strip("_")
    if "parallel" in cat:
        return hashlib.sha1(b"parallelize_subagent").hexdigest()[:12]
    act = re.sub(r"[^a-z0-9]+", " ", str(action or "").strip().lower()).strip()
    gui = re.sub(r"[^a-z0-9]+", " ", str(guidance or "").strip().lower()).strip()
    raw = (f"act|{act}" if act else (f"gui|{gui}" if gui else f"cat|{cat}")).encode("utf-8", errors="replace")
    return hashlib.sha1(raw).hexdigest()[:12]


def _lc_first(s):
    t = str(s or "").strip()
    return t[0].lower() + t[1:] if t else ""


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
        category = "missing_proof"
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
    if category == "grill_me" and status == "on_track" and (action or guidance or ver_res.get("questions") or ver_res.get("blind_spots")):
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

    advice_key = f"deferral_{re.sub(r'[^a-z0-9]+', '_', str(deferral.get('snippet', 'banned')).lower())[:24]}" if (deferral and deferral.get("matched")) else compute_advice_key(category, action or pinned, guidance)
    count = seen.get(advice_key, 0)
    escalation = str(ver_res.get("escalation") or "").strip().lower()
    escalating = "ignored" in escalation or escalation in ("escalated", "repeat")
    repeatable = category in ("loop_detection", "irreversible_risk")
    effective_max = max_emissions * 2 if category == "irreversible_risk" else max_emissions
    is_deferral = bool(deferral and deferral.get("matched"))
    # One extra budget grant, ADDED not multiplied: `effective_max * 2` compounded with
    # the irreversible_risk doubling above and let the same steer fire 8x. `escalation`
    # is model-supplied, so the grant has to be a fixed increment, not a factor.
    hard_cap = effective_max + max_emissions if escalating else effective_max
    if category in ("confused_goal", "grill_me"):
        # Clarify-the-user and grill-me fire without dedup suppression
        pass
    elif count >= hard_cap or (count >= 1 and not escalating and not repeatable and not is_deferral and not is_steer and not is_pinned):
        return {"decision": "hold_dedup", "status": status, "category": category, "confidence": conf, "advice_key": advice_key, "seen": seen}

    seen[advice_key] = count + 1
    if len(seen) > 50:
        seen = dict(sorted(seen.items(), key=lambda kv: (kv[0] != advice_key, -kv[1]))[:50])

    if is_pinned:
        p_clean = _lc_first(pinned) if pinned else _lc_first(action or "establish baseline objective.")
        a_clean = _lc_first(action)
        head = f"{p_clean.rstrip('.')}. next: {a_clean}" if (action and action != pinned) else p_clean
        parts = [head]
        interp = _safe_emission_text(ver_res.get("interpretation"))
        if interp:
            parts.append(f"rd: {_lc_first(interp)}")
    else:
        blind_spots = [_lc_first(_safe_emission_text(item)) for item in (ver_res.get("blind_spots") or [])]
        questions = [_lc_first(_safe_emission_text(item)) for item in (ver_res.get("questions") or [])]
        q_text = "; ".join(q for q in questions if q)
        b_text = "; ".join(b for b in (blind_spots if is_steer else wouts) if b)
        a_clean = _lc_first(action)
        g_clean = _lc_first(guidance)
        e_clean = _lc_first(evidence)
        if a_clean and q_text:
            target = f"{a_clean}; {q_text}"
        elif a_clean:
            target = a_clean
        elif q_text:
            target = q_text
        elif b_text:
            target = b_text
        else:
            target = g_clean or "course correction required."
        if e_clean:
            body = f"{e_clean.rstrip('.')}. {target}"
        elif g_clean and g_clean != a_clean:
            body = f"{g_clean.rstrip('.')}. {target}"
        else:
            body = target
        parts = [body]
        if pinned and (category == "scope_drift" or "drift" in str(ver_res.get("goal_status") or "").lower()):
            parts.append(f"pinned: {_lc_first(pinned)}")

    # Verification-depth ladder: deep tasks must climb past the easiest rung.
    # Appended AFTER advice_key derivation so dedup keys stay stable.
    if mode == "midturn" and complexity in ("complex_code", "multi_file"):
        ladder_bits = [pinned, action, guidance]
        if len(" | ".join(parts)) < 1900 and (sfx := next_rung_suffix(*ladder_bits)):
            parts.append(sfx)
    text = " | ".join(parts)
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
