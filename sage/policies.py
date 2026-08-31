"""sage.policies - Decision policies for background task watching and terminal sage gating."""
import re
from sage.config import (
    ADAPTIVE_CADENCE_ENABLED, DIFF_SPIKE_THRESHOLD, MAX_MID_TURN_STEERS, MAX_TOOL_SCORE_THRESHOLD,
    MID_TURN_SAGE_ENABLED, MIN_TOOL_SCORE_THRESHOLD, SAGE_ESCALATE_MIN_CONFIDENCE, SAGE_MAX_ERROR_STREAK,
    SAGE_STEER_MIN_CONFIDENCE, SAGE_TOOL_SCORE_THRESHOLD,
)
from sage.events import (
    DELEGATE_REVIEW_PAYLOAD, EVENT_FANOUT, EVENT_FINAL_STOP, EVENT_TOOL_THRESHOLD, format_summon_message,
    playbook_reminder,
)
from sage.sage import evaluate_mid_turn_progress
from sage.sanitizer import detect_transcript_deferral, detect_user_approval
from sage.task_structure import _classify_subagents, get_parallelizable_signals, is_assist_signal
from sage.transcript import (
    _read_transcript_steps, calculate_turn_tool_score, extract_session_and_turn_data, has_new_user_activity,
    has_repeated_tool_calls, is_post_invocation_completion_candidate,
)
from sage.triage import classify_advice
_playbook_reminder = playbook_reminder
BG_STALE_SECONDS = 300.0


def compute_dynamic_tool_threshold(state, base_thresh=10.0, diff_cnt=0, read_ratio=0.0):
    """Calculates adaptive score threshold based on on_track streak and risk signals."""
    if not ADAPTIVE_CADENCE_ENABLED:
        return base_thresh
    last_status = state.get("sage_status") or state.get("advisor_status", "hold")
    if last_status in ("fired", "watchout", "off_track"):
        return max(MIN_TOOL_SCORE_THRESHOLD, min(base_thresh * 0.7, MAX_TOOL_SCORE_THRESHOLD))
    if diff_cnt > DIFF_SPIKE_THRESHOLD:
        return base_thresh
    consecutive = int(state.get("consecutive_on_track", 0))
    mult = 1.0 if consecutive == 0 else (1.3 if consecutive == 1 else 1.6)
    comp = str(state.get("task_complexity") or "").strip().lower()
    mult += 0.4 if comp == "simple_qa" else (min(mult, 1.3) - mult if comp == "complex_code" else (min(mult, 1.2) - mult if comp == "multi_file" else 0.0))
    if read_ratio >= 0.8:
        mult *= 1.2
    return round(max(MIN_TOOL_SCORE_THRESHOLD, min(MAX_TOOL_SCORE_THRESHOLD, base_thresh * mult)), 2)


def background_watch(active_tasks, bg_steered):
    """Decide what to do about active background tasks."""
    if not active_tasks:
        return {"action": "none"}
    stale = sorted((t for t in active_tasks if t.get("age_seconds", 0.0) > BG_STALE_SECONDS and t.get("task_id") not in bg_steered), key=lambda t: t.get("age_seconds", 0.0), reverse=True)
    if stale:
        t = stale[0]
        return {"action": "steer", "task_id": t.get("task_id", "task"), "description": t.get("description", "background command"), "age_seconds": t.get("age_seconds", 0.0)}
    if any(t.get("age_seconds", 0.0) <= BG_STALE_SECONDS for t in active_tasks):
        return {"action": "grace"}
    return {"action": "already_steered"}


def _hammer_suppressed(state, category, turn_tools):
    """Same-category steer + fresh executor tools since last steer = wait."""
    if not category or category in ("loop_detection", "irreversible_risk", "confused_goal"):
        return False
    return state.get("last_steer_category") == category and turn_tools - state.get("last_steer_tools", 0) >= 2 and state.get("steer_suppress_count", 0) < 2


def _facilitation_signal(transcript_path, state):
    """Advisory-only signal: delegate execution to subagents after goal settle."""
    from sage.facilitation import facilitation_signal
    return facilitation_signal(transcript_path, state)


def _review_payload_text(state):
    """Review brief made self-contained: the DoD and diff base are named, not implied."""
    st = state or {}
    dod = str(st.get("pinned_goal") or st.get("anchor_goal") or "").strip()
    base_sha = str(st.get("review_base_sha") or "").strip()
    parts = [f"DoD: {dod[:300]}"] if dod else []
    if base_sha:
        # `{sha}..HEAD` is the two-commit form: it ignores the working tree. The base
        # is captured at pin time and nothing here ever commits, so HEAD has not moved
        # and that range renders EMPTY — the audit would read no diff at all. Bare
        # `git diff {sha}` compares base against the working tree; untracked files are
        # invisible to any diff form, hence the explicit intent-to-add step.
        parts.append(
            f"Diff scope: git diff {base_sha} (base vs working tree — do NOT append ..HEAD, "
            f"the work is uncommitted). Include new files: git add -N . && git diff {base_sha}"
        )
    if not parts:
        return DELEGATE_REVIEW_PAYLOAD
    return f"{DELEGATE_REVIEW_PAYLOAD}\n" + "\n".join(parts)


def sage_flow(mode, conv_id, transcript_path, clean_prompt, initial_line_count, total_tool_calls,
              turn_tool_names, user_prompt, agent_steps, git_diff, state, forced=False, signal_note="", workspace_root=None):
    """Unified policy flow for sage decisions (mid-turn or final)."""
    final = mode == "final"
    if not MID_TURN_SAGE_ENABLED:
        return {"action": "skip", "reason": "sage disabled"} if final else {"action": "exit", "reason": "sage disabled"}
    ms, es = int(state.get("mid_turn_steers", 0)), int(state.get("sage_error_streak", state.get("advisor_error_streak", 0)))
    if not final and MAX_MID_TURN_STEERS > 0 and ms >= MAX_MID_TURN_STEERS:
        return {"action": "exit", "reason": f"max mid-turn steers reached ({ms}/{MAX_MID_TURN_STEERS})"}
    if es >= SAGE_MAX_ERROR_STREAK:
        return {"action": "skip", "reason": f"sage circuit breaker open (streak={es})"} if final else {"action": "exit", "reason": f"sage circuit breaker open (streak={es})"}
    par_sig = get_parallelizable_signals(transcript_path, workspace_root) if not final else {}
    assist_active = is_assist_signal(par_sig)
    if par_sig.get("parallelizable"):
        stable_details = [d for d in par_sig.get("details", []) if not d.startswith("mid-task tool accumulation")]
        fp = [sorted(par_sig.get("categories", [])), sorted(stable_details)]
        if par_sig.get("categories") != ["context_fatigue_delegation"] and fp != state.get("last_par_fp"):
            forced = True
            state["last_par_fp"], state["last_par_cats"] = fp, list(par_sig.get("categories", []))
        stext = par_sig.get("signal_text", "")
        if signal_note and stext and stext not in signal_note:
            signal_note = f"{signal_note}\n{stext}".strip()
        if not assist_active and state is not None:
            state["delegate_roles"] = list(par_sig.get("suggested_roles") or [])
            state["delegate_legs"] = list(stable_details)
    if par_sig.get("shared_files") and state is not None:
        state["shared_files"] = par_sig["shared_files"]
    fac_sig = _facilitation_signal(transcript_path, state) if not assist_active else ""
    if fac_sig:
        forced = True
        state["cmd_ignored"] = int(state.get("cmd_ignored", state.get("facilitation_cmd_ignored", 0))) + 1
        state["facilitation_cmd_ignored"] = state["cmd_ignored"]
        signal_note = f"{signal_note}\n{fac_sig}".strip() if signal_note else fac_sig
    diff_cnt = sum(int(m) for m in re.findall(r"^Changed lines: (\d+)", git_diff or "", re.M)) or (sum(1 for ln in (git_diff or "").splitlines() if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))))
    read_tools = {"view_file", "grep_search", "find_by_name", "list_dir", "read_url_content", "search_web", "list_resources", "read_resource"}
    read_ratio = 1.0 if turn_tool_names and all(t in read_tools for t in turn_tool_names) else 0.0
    effective_thresh = compute_dynamic_tool_threshold(state, base_thresh=SAGE_TOOL_SCORE_THRESHOLD, diff_cnt=diff_cnt, read_ratio=read_ratio)
    delta_score, delta_count = calculate_turn_tool_score(transcript_path, int(state.get("last_verified_tools", 0))) if transcript_path else (0.0, 0)
    loop_override = False
    if not final and not forced and delta_score < effective_thresh:
        repeats = transcript_path and has_repeated_tool_calls(transcript_path)
        fresh = max(0, total_tool_calls - int(state.get("last_loop_eval_tools", 0)))
        if not (repeats and fresh >= 2):
            return {"action": "exit", "reason": f"Mid-turn tool delta below threshold (score={delta_score:.1f}<{effective_thresh:.1f}, count={delta_count})"}
        state["last_loop_eval_tools"] = total_tool_calls
        loop_override = True
    tsteps = _read_transcript_steps(transcript_path) if transcript_path else []
    deferral = detect_transcript_deferral(tsteps)
    approval = detect_user_approval(user_prompt or clean_prompt)
    if approval.get("approved"):
        note = playbook_reminder("new_prompt", "Momentum Doctrine", f"user granted explicit approval ('{approval.get('snippet')}') in current prompt")
        signal_note = f"{signal_note}\n{note}".strip() if signal_note else note
    if final:
        if tsteps:
            has_build, has_review = _classify_subagents(tsteps)
            if has_build and not has_review and not (state or {}).get("review_gate_fired"):
                if state is not None:
                    state["review_gate_fired"], state["last_steer_category"] = True, "missing_proof"
                return {"action": "emit", "decision": "watchout", "category": "missing_proof", "text": _review_payload_text(state)}
        is_plan_turn = bool(re.search(r"(?i)\b/plan\b", str(user_prompt or "")) or re.search(r"(?i)\bplan\b", str(clean_prompt or "")))
        active_signal = format_summon_message(
            EVENT_FINAL_STOP, total_tools=total_tool_calls, diff=diff_cnt or None, is_plan=is_plan_turn or None,
            deferral=deferral.get("snippet") if deferral.get("matched") else None,
            deferral_cat=deferral.get("category") if deferral.get("matched") else None,
            delegated_cmd=deferral.get("delegated_cmd") if deferral.get("matched") else None,
            tail_todo=deferral.get("tail_todo") if deferral.get("matched") else None,
        )
        if signal_note:
            active_signal = f"{active_signal}\n{signal_note}".strip()
    elif signal_note:
        active_signal = signal_note
    elif par_sig.get("parallelizable"):
        active_signal = format_summon_message(EVENT_FANOUT, signal_text=par_sig.get("signal_text", ""))
    else:
        active_signal = format_summon_message(
            EVENT_TOOL_THRESHOLD, total_tools=total_tool_calls, mix=list(turn_tool_names)[-5:] if turn_tool_names else None,
            deferral=deferral.get("snippet") if deferral.get("matched") else None,
            delegated_cmd=deferral.get("delegated_cmd") if deferral.get("matched") else None,
        )
    verdict = evaluate_mid_turn_progress(
        conv_id, transcript_path, total_tool_calls, turn_tool_names,
        user_prompt, agent_steps, git_diff, state, is_forced=(forced or final or deferral.get("matched", False) or loop_override),
        signals=active_signal, workspace_root=workspace_root)
    if has_new_user_activity(transcript_path, clean_prompt, initial_line_count):
        return {"action": "yield", "reason": ("Fresh user input detected during final sage; yielding" if final else "Fresh user input detected during sage; yielding")}
    if not verdict or verdict.get("status") == "error":
        return {"action": "error"}
    seen_adv = state.get("sage_advice_counts") or state.get("advisor_advice_counts", {})
    classified = classify_advice(
        verdict, seen_advice=seen_adv, steer_min_conf=SAGE_STEER_MIN_CONFIDENCE,
        escalate_min_conf=SAGE_ESCALATE_MIN_CONFIDENCE, anchor_emitted=bool(state.get("pinned_emitted", state.get("anchor_emitted", False))),
        mode="final" if final else "midturn", deferral=deferral)
    latest = extract_session_and_turn_data(transcript_path)
    progressed = (not final and is_post_invocation_completion_candidate(transcript_path, conv_id)) or latest[3] > total_tool_calls or latest[7] > initial_line_count
    if progressed and not classified.get("pinned_emitted") and classified.get("category") != "pinned_goal":
        return {"action": "progressed", "tools": latest[3], "lines": latest[7]}
    dec, text = classified.get("decision"), classified.get("text", "")
    res = {"seen": classified.get("seen")}
    for k in ("recap", "category", "confidence", "pinned_goal", "anchor_goal", "revised_goal", "derived_tasks", "goal_status", "task_complexity", "pinned_emitted", "anchor_emitted", "facilitation_override", "override_receipt"):
        if k in classified or k in verdict:
            res[k] = classified.get(k) if k in classified else verdict.get(k)
    if par_sig.get("shared_files"):
        res["shared_files"] = par_sig["shared_files"]
    res["assist_active"] = assist_active
    if not final and classified.get("category") == "confused_goal":
        return {"action": "exit", "reason": "Confused goal recorded for final gate", "pending_clarify": {"category": "confused_goal", "question": text}}
    prior_texts = state.get("sage_emitted_texts") or state.get("advisor_emitted_texts") or []
    if dec in ("steer", "watchout") and classified.get("category") and mode != "final":
        cat = classified["category"]
        write_tools = {"write_to_file", "replace_file_content", "multi_replace_file_content", "edit_file", "apply_diff"}
        if cat in ("parallelize_subagent", "parallelize"):
            if assist_active:
                return {**res, "action": "hold_dedup", "assist_suppressed": True, "category": cat}
            if turn_tool_names and any(t in write_tools for t in turn_tool_names) and (diff_cnt > 50 or total_tool_calls >= 15):
                return {**res, "action": "hold_dedup", "half_done_suppressed": True, "category": cat}
        if _hammer_suppressed(state, cat, latest[3]):
            return {**res, "action": "hold_dedup", "hammer_suppressed": True, "category": cat}
    if (dec in ("steer", "watchout") and text and text in prior_texts) or dec == "hold_dedup":
        return {**res, "action": "hold_dedup"}
    if dec in ("steer", "watchout"):
        return {**res, "action": "emit", "decision": dec, "text": text}
    return {**res, "action": "healthy", "text": text}


def final_sage_gate(conv_id, transcript_path, clean_prompt, initial_line_count, total_tool_calls,
                    turn_tool_names, user_prompt, agent_steps, git_diff, state, workspace_root=None):
    """Sage assessment at a finishing stop — the sole terminal gate."""
    act = sage_flow("final", conv_id=conv_id, transcript_path=transcript_path, clean_prompt=clean_prompt,
                    initial_line_count=initial_line_count, total_tool_calls=total_tool_calls,
                    turn_tool_names=turn_tool_names, user_prompt=user_prompt, agent_steps=agent_steps,
                    git_diff=git_diff, state=state, workspace_root=workspace_root)
    if act.get("action") == "healthy":
        act["recap"] = act.get("recap") or act.get("text") or "Work completed and verified successfully."
        act["note"] = f"Sage final assessment: hold (healthy). {act.get('text', '')}".strip()
    return act
