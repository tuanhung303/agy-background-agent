"""
sage.runner - Main execution flow for the session stop audit hook.
"""

import json

from sage.events import EVENT_ERROR_LOOP, format_summon_message
from sage.facilitation import immediate_settle_message
from sage.git import get_git_diff, resolve_workspace_root
from sage.goals import sync_goal_state
from sage.guards import (
    check_payload_and_lifecycle, emit_continue_response, emit_recap_response,
    fail_safe_exit, format_hook_message,
    handle_background_watch_action, is_post_invocation, is_subagent_session,
)
from sage.locking import acquire_conversation_lock, cleanup_stale_tmp_files, log_audit
from sage.policies import background_watch, final_sage_gate, sage_flow
from sage.sage import _clear_sage_session
from sage.session_state import (
    load_and_sync_session_state,
    record_background_grace, record_background_steer,
    record_sage_emit, record_sage_hold, record_sage_recap,
    save_session_state,
)
from sage.transcript import (
    extract_session_and_turn_data,
    get_active_background_tasks, get_active_subagents, get_active_external_panes,
    get_transcript_path,
    has_recent_tool_errors, has_repeated_tool_calls,
    is_post_invocation_completion_candidate,
)

# Backwards compatibility alias for external imports/patches
_save_state = save_session_state
final_advisor_gate = final_sage_gate
advisor_flow = sage_flow
record_advisor_hold, record_advisor_emit, record_advisor_recap = record_sage_hold, record_sage_emit, record_sage_recap


def run_session_stop_audit(raw_payload=None):
    payload = json.loads(raw_payload) if raw_payload else check_payload_and_lifecycle()
    conv_id = payload.get("conversationId") or payload.get("conversation_id") or "default"
    cleanup_stale_tmp_files(state_max_age_seconds=604800)
    if not acquire_conversation_lock(conv_id):
        fail_safe_exit(f"Concurrent audit in progress for {conv_id}")

    transcript_path = get_transcript_path(payload, conv_id)
    (
        user_prompt, raw_user_prompt, agent_steps, total_tool_calls,
        turn_tool_names, first_ts, user_ts, initial_line_count,
    ) = extract_session_and_turn_data(transcript_path)

    clean_prompt, state_file, state, is_same = load_and_sync_session_state(conv_id, transcript_path, raw_user_prompt)
    sync_goal_state(state, user_prompt, total_tool_calls, turn_tool_names)
    save_session_state(state_file, state)

    active_subagents = get_active_subagents(transcript_path, conv_id)
    if active_subagents:
        if not is_post_invocation():
            log_audit("Active subagents detected during Stop event -> Blocking stop")
            emit_continue_response("Subagent work in progress; waiting for subagents", is_post=False)
        fail_safe_exit("Subagent work in progress; waiting for subagents")

    active_panes = get_active_external_panes(transcript_path)
    if active_panes:
        pane_steers = state.get("external_pane_steers", {})
        unsteered = [p for p in active_panes if pane_steers.get(p, 0) < 2]
        if unsteered:
            for p in unsteered:
                pane_steers[p] = pane_steers.get(p, 0) + 1
            save_session_state(state_file, state, external_pane_steers=pane_steers)
            log_audit(f"Active external worker pane(s) detected: {unsteered}")
            if not is_post_invocation():
                emit_continue_response(
                    "External worker pane(s) still streaming (" + ", ".join(unsteered) + "); "
                    "wait for the idle prompt and read full output before concluding.",
                    is_post=False,
                )
            fail_safe_exit("External worker pane(s) in progress; waiting for idle prompt")

    active_tasks = get_active_background_tasks(transcript_path, conv_id)
    bgp = background_watch(active_tasks, state.get("background_steered_tasks", []))
    handle_background_watch_action(bgp, state, state_file, initial_line_count, record_background_steer, record_background_grace)

    if total_tool_calls == 0:
        fail_safe_exit("Conversational turn (0 tool calls)")
    if is_subagent_session(payload, transcript_path, user_prompt, raw_user_prompt):
        fail_safe_exit("Subagent session detected; skipping audit")
    if not user_prompt.strip() or payload.get("fullyIdle") is False or payload.get("fully_idle") is False:
        fail_safe_exit("No user prompt or runtime reports active background work")

    if state.get("recap_emitted"):
        last_verified = int(state.get("last_verified_tools", 0))
        last_lines = int(state.get("last_audited_line_count", 0))
        if total_tool_calls <= last_verified and initial_line_count <= last_lines:
            fail_safe_exit("Recap already emitted")
        else:
            state["recap_emitted"] = False
            save_session_state(state_file, state, recap_emitted=False)
    last_lines = state.get("last_audited_line_count", 0)
    if is_post_invocation():
        if last_lines > 0 and last_lines == initial_line_count:
            fail_safe_exit("Mid-turn transcript unchanged")
    else:
        last_final_lines = state.get("last_final_gate_lines", 0)
        if last_final_lines > 0 and last_final_lines == initial_line_count:
            fail_safe_exit("Final stop already audited at current line count")
        save_session_state(state_file, state, sage_status="evaluating", last_final_gate_lines=initial_line_count)

    ws_paths = payload.get("workspacePaths") or payload.get("workspace_paths") or []
    workspace_root = resolve_workspace_root(ws_paths)
    if is_post_invocation() and not is_post_invocation_completion_candidate(transcript_path, conv_id):
        has_err, has_loop = has_recent_tool_errors(transcript_path), has_repeated_tool_calls(transcript_path)
        sig_kwargs = {}
        if has_err:
            sig_kwargs["err"] = True
        if has_loop:
            sig_kwargs["loop"] = True
        sig = format_summon_message(EVENT_ERROR_LOOP, **sig_kwargs) if sig_kwargs else ""
        save_session_state(state_file, state, sage_status="evaluating", last_audited_line_count=initial_line_count)
        _flow_fn = advisor_flow if advisor_flow != sage_flow else sage_flow
        act = _flow_fn(
            "midturn", conv_id=conv_id, transcript_path=transcript_path,
            clean_prompt=clean_prompt, initial_line_count=initial_line_count,
            total_tool_calls=total_tool_calls, turn_tool_names=turn_tool_names,
            user_prompt=user_prompt, agent_steps=agent_steps, git_diff=get_git_diff(ws_paths, turn_tool_names),
            state=state, forced=(has_err or has_loop), signal_note=sig.strip(),
            workspace_root=workspace_root,
        )
        aact = act.get("action")
        if aact in ("exit", "yield"):
            if act.get("pending_clarify"):
                save_session_state(state_file, state, sage_status="hold", pending_clarify=act["pending_clarify"])
            else:
                save_session_state(state_file, state, sage_status="hold")
            fail_safe_exit(act["reason"])
        elif aact == "progressed":
            save_session_state(state_file, state, sage_status="hold", last_verified_tools=act["tools"], last_audited_line_count=act["lines"])
            fail_safe_exit("Agent progressed during sage evaluation; discarding stale advice")
        elif aact == "error":
            err_streak = state.get("sage_error_streak", state.get("advisor_error_streak", 0)) + 1
            save_session_state(state_file, state, sage_status="error", sage_error_streak=err_streak, last_audited_line_count=initial_line_count)
            fail_safe_exit("Mid-turn sage unavailable (empty or model cascade failed); window preserved")
        elif aact == "hold_dedup":
            if act.get("hammer_suppressed"):
                save_session_state(state_file, state, steer_suppress_count=state.get("steer_suppress_count", 0) + 1)
            (record_advisor_hold if record_advisor_hold != record_sage_hold else record_sage_hold)(state_file, state, total_tool_calls, initial_line_count, act.get("seen"))
            fail_safe_exit("Sage advice deduplicated")
        elif aact == "emit":
            fdec, ftext = act["decision"], act["text"]
            gu = {k: act[k] for k in ("pinned_goal", "anchor_goal", "revised_goal", "derived_tasks", "task_complexity", "pinned_emitted", "anchor_emitted") if k in act and act[k] is not None}
            gu["last_steer_category"] = act.get("category")
            gu["last_steer_tools"] = total_tool_calls
            gu["steer_suppress_count"] = 0  # budget: ≤2 suppressions per emitted steer
            (record_advisor_emit if record_advisor_emit != record_sage_emit else record_sage_emit)(state_file, state, total_tool_calls, initial_line_count, fdec, ftext, act.get("seen", state.get("sage_advice_counts", state.get("advisor_advice_counts", {}))), **gu)
            log_audit(f"Mid-turn sage {('triggered steer' if fdec == 'steer' else 'watchout emitted')}: {ftext}")
            emit_continue_response(format_hook_message("sage", ftext), is_post=True)
        else:
            (record_advisor_hold if record_advisor_hold != record_sage_hold else record_sage_hold)(state_file, state, total_tool_calls, initial_line_count)
            fail_safe_exit("Mid-turn sage passed (healthy)")

    git_diff = get_git_diff(ws_paths, turn_tool_names)
    _gate_fn = final_advisor_gate if final_advisor_gate != final_sage_gate else final_sage_gate
    gate = _gate_fn(conv_id, transcript_path, clean_prompt, initial_line_count, total_tool_calls, turn_tool_names, user_prompt, agent_steps, git_diff, state, workspace_root=workspace_root)
    gact = gate.get("action")
    log_audit(f"Final sage gate: {gact}" + (f" ({gate.get('reason', '')})" if gate.get("reason") else ""))
    if state.get("pending_clarify") and not state.get("clarify_asked"):
        # Confused goal surfaced earlier this turn: ask the user ONCE and end the
        # turn — never ask mid-turn, never loop on repeated asks.
        q = (state.get("pending_clarify") or {}).get("question") or "The goal is ambiguous; please clarify the objective."
        save_session_state(state_file, state, clarify_asked=True, pending_clarify=None)
        emit_recap_response(f"[CLARIFY] {q}", kind="sage")
    if gact == "yield":
        save_session_state(state_file, state, sage_status="hold")
        fail_safe_exit(gate["reason"])
    elif gact == "progressed":
        save_session_state(state_file, state, sage_status="hold", last_verified_tools=gate["tools"], last_audited_line_count=gate["lines"])
        fail_safe_exit("Agent progressed during final sage; discarding stale advice")
    elif gact == "emit":
        fdec, ftext = gate["decision"], gate["text"]
        gu = {k: gate[k] for k in ("pinned_goal", "anchor_goal", "revised_goal", "derived_tasks", "task_complexity", "pinned_emitted", "anchor_emitted") if k in gate and gate[k] is not None}
        (record_advisor_emit if record_advisor_emit != record_sage_emit else record_sage_emit)(state_file, state, total_tool_calls, initial_line_count, fdec, ftext, gate.get("seen", state.get("sage_advice_counts", state.get("advisor_advice_counts", {}))), **gu)
        log_audit(f"Final sage-first {fdec}: {ftext}")
        emit_continue_response(format_hook_message("sage", ftext), is_post=True)
    elif gact == "hold_dedup":
        (record_advisor_hold if record_advisor_hold != record_sage_hold else record_sage_hold)(state_file, state, total_tool_calls, initial_line_count, gate.get("seen"))
        fail_safe_exit("Final sage advice deduplicated")
    elif gact in ("hold", "healthy"):
        recap = gate.get("recap") or "Work completed and verified successfully."
        cat = gate.get("category", "on_track")
        sage_recap = f"[RECAP·{cat}] {recap}" if not recap.startswith("[RECAP") else recap
        gu = {k: gate[k] for k in ("pinned_goal", "anchor_goal", "revised_goal", "derived_tasks", "task_complexity", "pinned_emitted", "anchor_emitted") if k in gate and gate[k] is not None}
        (record_advisor_recap if record_advisor_recap != record_sage_recap else record_sage_recap)(state_file, state, total_tool_calls, initial_line_count, recap_text=sage_recap, goal_settled=True, **gu)
        log_audit(f"Sage passed cleanly. Sage recap recorded: {sage_recap}")
        _clear_sage_session(conv_id)
        fac_msg = immediate_settle_message(state)
        if fac_msg:
            sage_recap = f"{sage_recap}\n\n{fac_msg}"
        emit_recap_response(sage_recap, kind="sage")
    elif gact == "error":
        err_streak = state.get("sage_error_streak", state.get("advisor_error_streak", 0)) + 1
        save_session_state(state_file, state, sage_status="error", sage_error_streak=err_streak, last_audited_line_count=initial_line_count)
        fail_safe_exit("Final sage unavailable (empty or model cascade failed); allowing clean termination")
    else:  # skip — sage disabled, max steers reached, or circuit breaker open
        save_session_state(state_file, state, sage_status="hold")
        fail_safe_exit(f"Final sage gate skipped: {gate.get('reason', 'no reason')}")


main = run_session_stop_audit

