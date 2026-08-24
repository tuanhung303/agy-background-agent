"""
advisor.policies - Decision policies extracted from the stop runner.

Two policies live here:

1. background_watch: decides whether active background tasks require a
   watch-steer, a grace-period block, or free passage. Encapsulated so the
   mechanical watcher cannot fire before richer context (the advisor) has
   spoken, and so the policy stays unit-testable in isolation.

2. final_advisor_gate: on a finishing stop, the advisor is the sole final
   gate — it either emits a steer/watchout (agent continues) or approves
   with an on_track recap (session terminates). There is no separate
   steerer/auditor role.

Both return plain action dicts; the runner owns persistence and I/O.
"""

from advisor.advisor import evaluate_mid_turn_progress
from advisor.config import (
    ADVISOR_ESCALATE_MIN_CONFIDENCE,
    ADVISOR_MAX_ERROR_STREAK,
    ADVISOR_STEER_MIN_CONFIDENCE,
    ADVISOR_TOOL_INTERVAL,
    MAX_MID_TURN_STEERS,
    MID_TURN_ADVISOR_ENABLED,
)
from advisor.events import (
    EVENT_FINAL_STOP,
    EVENT_PARALLEL_OPP,
    EVENT_TOOL_THRESHOLD,
    format_summon_message,
)
from advisor.task_structure import get_parallelizable_signals
from advisor.triage import classify_advice
from advisor.transcript import (
    extract_session_and_turn_data,
    has_new_user_activity,
    is_post_invocation_completion_candidate,
)

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


def advisor_flow(mode, *, conv_id, transcript_path, clean_prompt, initial_line_count,
                 total_tool_calls, turn_tool_names, user_prompt,
                 agent_steps, git_diff, state, forced=False, signal_note=""):
    """Shared advisor evaluation for mid-turn and finishing stops.

    mode="midturn": legacy behavior — every skip condition is a hard exit with
    its historical reason string; healthy holds also exit.
    mode="final": the advisor is the terminal gate — skips return "skip" so the
    runner can allow a clean stop; a healthy hold carries the final recap.

    Returns an action dict:
      {"action": "exit", "reason": str}         (midturn only)
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
    if not MID_TURN_ADVISOR_ENABLED:
        return _skip_or_exit("advisor disabled")
    ms, es = int(state.get("mid_turn_steers", 0)), int(state.get("advisor_error_streak", 0))
    if MAX_MID_TURN_STEERS > 0 and ms >= MAX_MID_TURN_STEERS:
        return _skip_or_exit(f"max mid-turn steers reached ({ms}/{MAX_MID_TURN_STEERS})")
    if es >= ADVISOR_MAX_ERROR_STREAK:
        return _skip_or_exit(f"advisor circuit breaker open (streak={es})")
    lv = int(state.get("last_verified_tools", 0))
    par_sig = get_parallelizable_signals(transcript_path) if not final else {}
    if par_sig.get("parallelizable"):
        forced = True
        stext = par_sig.get("signal_text", "")
        if stext and stext not in signal_note:
            signal_note = f"{signal_note} {stext}".strip()
    if not final and not forced and (total_tool_calls - lv) < ADVISOR_TOOL_INTERVAL:
        return {"action": "exit", "reason": f"Mid-turn tool delta below interval ({total_tool_calls - lv} < {ADVISOR_TOOL_INTERVAL})"}

    if final:
        active_signal = format_summon_message(EVENT_FINAL_STOP)
    elif par_sig.get("parallelizable"):
        active_signal = format_summon_message(EVENT_PARALLEL_OPP, signal_text=par_sig.get("signal_text", ""))
    elif signal_note:
        active_signal = signal_note
    else:
        active_signal = format_summon_message(
            EVENT_TOOL_THRESHOLD,
            total_tools=total_tool_calls,
            delta_tools=total_tool_calls - lv,
            pinned_goal=state.get("pinned_goal") or state.get("anchor_goal"),
        )
    verdict = evaluate_mid_turn_progress(
        conv_id, transcript_path, total_tool_calls, turn_tool_names,
        user_prompt, agent_steps, git_diff, state, is_forced=(forced or final),
        signals=active_signal)
    if has_new_user_activity(transcript_path, clean_prompt, initial_line_count):
        return {"action": "yield", "reason": ("Fresh user input detected during final advisor; yielding" if final else "Fresh user input detected during advisor; yielding")}
    latest = extract_session_and_turn_data(transcript_path)
    progressed = (not final and is_post_invocation_completion_candidate(transcript_path, conv_id)) \
        or latest[3] > total_tool_calls or latest[7] > initial_line_count
    if progressed:
        return {"action": "progressed", "tools": latest[3], "lines": latest[7]}
    if not verdict or verdict.get("status") == "error":
        return {"action": "error"}

    classified = classify_advice(
        verdict, seen_advice=state.get("advisor_advice_counts", {}),
        steer_min_conf=ADVISOR_STEER_MIN_CONFIDENCE,
        escalate_min_conf=ADVISOR_ESCALATE_MIN_CONFIDENCE,
        anchor_emitted=bool(state.get("pinned_emitted", state.get("anchor_emitted", False))))
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
    if dec == "hold_dedup":
        res["action"] = "hold_dedup"
        return res
    if dec in ("steer", "watchout"):
        res.update({"action": "emit", "decision": dec, "text": text})
        return res
    res.update({"action": "healthy", "text": text})
    return res


def final_advisor_gate(conv_id, transcript_path, clean_prompt, initial_line_count,
                       total_tool_calls, turn_tool_names, user_prompt,
                       agent_steps, git_diff, state):
    """Advisor assessment at a finishing stop — the sole terminal gate.

    Thin wrapper over advisor_flow(mode="final"); a healthy hold carries an
    advisor recap that terminates the session cleanly.
    """
    act = advisor_flow("final", conv_id=conv_id, transcript_path=transcript_path,
                       clean_prompt=clean_prompt, initial_line_count=initial_line_count,
                       total_tool_calls=total_tool_calls, turn_tool_names=turn_tool_names,
                       user_prompt=user_prompt, agent_steps=agent_steps,
                       git_diff=git_diff, state=state)
    if act.get("action") == "healthy":
        recap_txt = act.get("recap") or act.get("text") or "Work completed and verified successfully."
        act["recap"] = recap_txt
        act["note"] = f"Advisor final assessment: hold (healthy). {act.get('text', '')}".strip()
    return act
