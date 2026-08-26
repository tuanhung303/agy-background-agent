"""
sage.policies - Decision policies for background task watching and terminal sage gating.
"""
import re
from sage.sage import evaluate_mid_turn_progress
from sage.config import (
    MAX_MID_TURN_STEERS, MID_TURN_SAGE_ENABLED, SAGE_ESCALATE_MIN_CONFIDENCE,
    SAGE_MAX_ERROR_STREAK, SAGE_STEER_MIN_CONFIDENCE, SAGE_TOOL_INTERVAL,
    SAGE_TOOL_SCORE_THRESHOLD, ADVISOR_TOOL_SCORE_THRESHOLD,
)
MID_TURN_ADVISOR_ENABLED, ADVISOR_TOOL_INTERVAL = MID_TURN_SAGE_ENABLED, SAGE_TOOL_INTERVAL
ADVISOR_STEER_MIN_CONFIDENCE, ADVISOR_ESCALATE_MIN_CONFIDENCE, ADVISOR_MAX_ERROR_STREAK = SAGE_STEER_MIN_CONFIDENCE, SAGE_ESCALATE_MIN_CONFIDENCE, SAGE_MAX_ERROR_STREAK
from sage.events import EVENT_FINAL_STOP, EVENT_PARALLEL_OPP, EVENT_TOOL_THRESHOLD, format_summon_message
from sage.sanitizer import detect_transcript_deferral
from sage.task_structure import get_parallelizable_signals
from sage.transcript import (
    _read_transcript_steps, calculate_turn_tool_score, extract_session_and_turn_data,
    has_new_user_activity, is_post_invocation_completion_candidate,
)
from sage.triage import classify_advice

BG_STALE_SECONDS = 300.0


def background_watch(active_tasks, bg_steered):
    """Decide what to do about active background tasks.

    Returns one of:
      {"action": "none"}
      {"action": "steer", "task_id", "description", "age_seconds"}
      {"action": "grace"}
      {"action": "already_steered"}
    """
    if not active_tasks:
        return {"action": "none"}
    stale = sorted(
        (t for t in active_tasks if t.get("age_seconds", 0.0) > BG_STALE_SECONDS and t.get("task_id") not in bg_steered),
        key=lambda t: t.get("age_seconds", 0.0), reverse=True,
    )
    if stale:
        t = stale[0]
        return {"action": "steer", "task_id": t.get("task_id", "task"),
                "description": t.get("description", "background command"),
                "age_seconds": t.get("age_seconds", 0.0)}
    if any(t.get("age_seconds", 0.0) <= BG_STALE_SECONDS for t in active_tasks):
        return {"action": "grace"}
    return {"action": "already_steered"}


def _hammer_suppressed(state, category, turn_tools):
    """Same-category steer + fresh executor tools since the last steer = wait.

    The reworded-guidance hole: identical advice with new wording produces a
    fresh advice_key, so key-based dedup never counts it. If the executor
    already acted (new tools) since our last steer of this category, let that
    evidence land before hammering again.
    """
    if not category:
        return False
    prev_cat = state.get("last_steer_category")
    prev_tools = state.get("last_steer_tools", 0)
    return prev_cat == category and turn_tools > prev_tools


def sage_flow(mode, conv_id, transcript_path, clean_prompt, initial_line_count,
              total_tool_calls, turn_tool_names, user_prompt, agent_steps,
              git_diff, state, forced=False, signal_note=""):
    """Unified policy flow for sage decisions (mid-turn or final).

    Returns an action dict:
      {"action": "exit", "reason": str}         (midturn only; +pending_clarify)
      {"action": "skip"}                        (final only)
      {"action": "yield", "reason": str}
      {"action": "progressed", "tools": int, "lines": int}
      {"action": "error"}
      {"action": "hold_dedup", "seen": dict}
      {"action": "emit", "decision": "steer"|"watchout", "text": str, "seen": dict}
      {"action": "healthy", "text": str}
    """
    final = mode == "final"

    def _skip_or_exit(reason):
        return {"action": "skip", "reason": reason} if final else {"action": "exit", "reason": reason}
    is_enabled = bool(MID_TURN_SAGE_ENABLED and MID_TURN_ADVISOR_ENABLED)
    if not is_enabled:
        return _skip_or_exit("sage disabled")
    ms = int(state.get("mid_turn_steers", 0))
    es = int(state.get("sage_error_streak", state.get("advisor_error_streak", 0)))
    if not final and MAX_MID_TURN_STEERS > 0 and ms >= MAX_MID_TURN_STEERS:
        return _skip_or_exit(f"max mid-turn steers reached ({ms}/{MAX_MID_TURN_STEERS})")
    effective_max_streak = min(SAGE_MAX_ERROR_STREAK, ADVISOR_MAX_ERROR_STREAK)
    if es >= effective_max_streak:
        return _skip_or_exit(f"sage circuit breaker open (streak={es})")
    lv = int(state.get("last_verified_tools", 0))
    par_sig = get_parallelizable_signals(transcript_path) if not final else {}
    if par_sig.get("parallelizable"):
        stable_details = [d for d in par_sig.get("details", []) if not d.startswith("mid-task tool accumulation")]
        fp = [sorted(par_sig.get("categories", [])), sorted(stable_details)]
        if par_sig.get("categories") != ["context_fatigue_delegation"] and fp != state.get("last_par_fp"):
            forced = True
            state["last_par_fp"] = fp
            state["last_par_cats"] = list(par_sig.get("categories", []))
        stext = par_sig.get("signal_text", "")
        if signal_note and stext and stext not in signal_note:
            signal_note = f"{signal_note}\n{stext}".strip()
    effective_thresh = min(SAGE_TOOL_SCORE_THRESHOLD, ADVISOR_TOOL_SCORE_THRESHOLD)
    effective_interval = min(SAGE_TOOL_INTERVAL, ADVISOR_TOOL_INTERVAL)
    delta_score, delta_count = calculate_turn_tool_score(transcript_path, lv) if transcript_path else (0.0, 0)
    # Score is the sole cadence gate: weights (read-light, edit-heavy) decide how
    # soon the sage fires; raw tool counts no longer trigger it.
    if not final and not forced and delta_score < effective_thresh:
        return {"action": "exit", "reason": f"Mid-turn tool delta below threshold (score={delta_score:.1f}<{effective_thresh:.1f}, count={delta_count})"}

    tsteps = _read_transcript_steps(transcript_path) if transcript_path else []
    deferral = detect_transcript_deferral(tsteps)
    if final:
        diff_cnt = sum(int(m) for m in re.findall(r"^Changed lines: (\d+)", git_diff or "", re.M))
        if not diff_cnt and git_diff:
            diff_cnt = sum(1 for ln in git_diff.splitlines() if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---")))
        active_signal = format_summon_message(
            EVENT_FINAL_STOP, total_tools=total_tool_calls, diff=diff_cnt if diff_cnt else None,
            deferral=deferral.get("snippet") if deferral.get("matched") else None,
            deferral_cat=deferral.get("category") if deferral.get("matched") else None,
            delegated_cmd=deferral.get("delegated_cmd") if deferral.get("matched") else None,
            tail_todo=deferral.get("tail_todo") if deferral.get("matched") else None,
        )
    elif signal_note:
        active_signal = signal_note
    elif par_sig.get("parallelizable"):
        active_signal = format_summon_message(EVENT_PARALLEL_OPP, signal_text=par_sig.get("signal_text", ""))
    else:
        active_signal = format_summon_message(
            EVENT_TOOL_THRESHOLD,
            total_tools=total_tool_calls,
            mix=list(turn_tool_names)[-5:] if turn_tool_names else None,
            deferral=deferral.get("snippet") if deferral.get("matched") else None,
            delegated_cmd=deferral.get("delegated_cmd") if deferral.get("matched") else None,
        )
    verdict = evaluate_mid_turn_progress(
        conv_id, transcript_path, total_tool_calls, turn_tool_names,
        user_prompt, agent_steps, git_diff, state, is_forced=(forced or final or deferral.get("matched", False)),
        signals=active_signal)
    if has_new_user_activity(transcript_path, clean_prompt, initial_line_count):
        return {"action": "yield", "reason": ("Fresh user input detected during final sage; yielding" if final else "Fresh user input detected during sage; yielding")}
    if not verdict or verdict.get("status") == "error":
        return {"action": "error"}
    seen_adv = state.get("sage_advice_counts") or state.get("advisor_advice_counts", {})
    classified = classify_advice(
        verdict, seen_advice=seen_adv,
        steer_min_conf=SAGE_STEER_MIN_CONFIDENCE,
        escalate_min_conf=SAGE_ESCALATE_MIN_CONFIDENCE,
        anchor_emitted=bool(state.get("pinned_emitted", state.get("anchor_emitted", False))),
        mode="final" if final else "midturn",
        deferral=deferral)
    latest = extract_session_and_turn_data(transcript_path)
    progressed = (not final and is_post_invocation_completion_candidate(transcript_path, conv_id)) \
        or latest[3] > total_tool_calls or latest[7] > initial_line_count
    if progressed and not classified.get("pinned_emitted") and classified.get("category") != "pinned_goal":
        return {"action": "progressed", "tools": latest[3], "lines": latest[7]}
    dec, text = classified.get("decision"), classified.get("text", "")
    res = {"seen": classified.get("seen")}
    if "recap" in classified and classified["recap"]:
        res["recap"] = classified["recap"]
    if "category" in classified and classified["category"]:
        res["category"] = classified["category"]
    if "confidence" in classified and classified["confidence"] is not None:
        res["confidence"] = classified["confidence"]
    for k in ("pinned_goal", "anchor_goal", "revised_goal", "derived_tasks", "goal_status", "task_complexity", "pinned_emitted", "anchor_emitted"):
        if k in classified:
            res[k] = classified[k]
    if not final and classified.get("category") == "confused_goal":
        # Never interrupt mid-turn (user may be elsewhere). The FINAL gate of
        # this turn asks the user once and ends the turn.
        return {"action": "exit", "reason": "Confused goal recorded for final gate",
                "pending_clarify": {"category": "confused_goal", "question": text}}
    if dec == "hold_dedup":
        res["action"] = "hold_dedup"
        return res
    prior_texts = state.get("sage_emitted_texts") or state.get("advisor_emitted_texts") or []
    if dec in ("steer", "watchout") and classified.get("category"):
        if _hammer_suppressed(state, classified["category"], latest[3]):
            res["action"] = "hold_dedup"
            res["hammer_suppressed"] = True
            res["category"] = classified["category"]
            return res
    if dec in ("steer", "watchout") and text and text in prior_texts:
        # Backstop: an identical re-emission (key aged out of the counts table).
        res["action"] = "hold_dedup"
        return res
    if dec in ("steer", "watchout"):
        res.update({"action": "emit", "decision": dec, "text": text})
        return res
    res.update({"action": "healthy", "text": text})
    return res


def final_sage_gate(conv_id, transcript_path, clean_prompt, initial_line_count,
                    total_tool_calls, turn_tool_names, user_prompt,
                    agent_steps, git_diff, state):
    """Sage assessment at a finishing stop — the sole terminal gate.

    Thin wrapper over sage_flow(mode="final"); a healthy hold carries a
    sage recap that terminates the session cleanly.
    """
    _flow = advisor_flow if advisor_flow is not _BASE_SAGE_FLOW else sage_flow
    act = _flow("final", conv_id=conv_id, transcript_path=transcript_path,
                clean_prompt=clean_prompt, initial_line_count=initial_line_count,
                total_tool_calls=total_tool_calls, turn_tool_names=turn_tool_names,
                user_prompt=user_prompt, agent_steps=agent_steps,
                git_diff=git_diff, state=state)
    if act.get("action") == "healthy":
        recap_txt = act.get("recap") or act.get("text") or "Work completed and verified successfully."
        act["recap"] = recap_txt
        act["note"] = f"Sage final assessment: hold (healthy). {act.get('text', '')}".strip()
    return act


# Backward-compatibility aliases
_BASE_SAGE_FLOW = sage_flow
advisor_flow = sage_flow
final_advisor_gate = final_sage_gate
