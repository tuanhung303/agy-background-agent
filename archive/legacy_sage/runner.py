"""
sage.runner - Main execution flow for the session stop audit hook.
"""

import json
from sage import journal
from sage.events import EVENT_ERROR_LOOP, format_summon_message
from sage.evidence_store import cleanup_evidence_store, save_manifest
from sage.facilitation import immediate_delegate_message, immediate_settle_message
from sage.git import get_git_diff, get_head_sha, resolve_workspace_root
from sage.goals import sync_goal_state
from sage.guards import (
    check_payload_and_lifecycle, emit_continue_response, emit_recap_response,
    fail_safe_exit, format_hook_message, handle_background_watch_action,
    is_post_invocation, is_subagent_session,
)
from sage.locking import acquire_conversation_lock, cleanup_stale_tmp_files, log_audit
from sage.policies import background_watch, final_sage_gate, sage_flow
from sage.sage import _clear_sage_session
from sage.session_state import (
    load_and_sync_session_state, record_background_grace, record_background_steer,
    record_sage_emit, record_sage_hold, record_sage_recap, save_session_state,
)
from sage.transcript import (
    extract_session_and_turn_data, get_active_background_tasks, get_active_external_panes,
    get_transcript_path, has_recent_tool_errors, has_repeated_tool_calls,
    is_post_invocation_completion_candidate,
)


def run_session_stop_audit(raw_payload=None):
    payload = json.loads(raw_payload) if raw_payload else check_payload_and_lifecycle()
    conv_id = payload.get("conversationId") or payload.get("conversation_id") or "default"
    cleanup_stale_tmp_files(state_max_age_seconds=604800)
    cleanup_evidence_store(max_age_seconds=604800)
    if not acquire_conversation_lock(conv_id):
        fail_safe_exit(f"Concurrent audit in progress for {conv_id}")

    transcript_path = get_transcript_path(payload, conv_id)
    (user_prompt, raw_user_prompt, agent_steps, total_tool_calls, turn_tool_names, _, _, initial_line_count) = extract_session_and_turn_data(transcript_path)

    clean_prompt, state_file, state, _ = load_and_sync_session_state(conv_id, transcript_path, raw_user_prompt)
    sync_goal_state(state, user_prompt, total_tool_calls, turn_tool_names)
    save_session_state(state_file, state)

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
                emit_continue_response(f"External worker pane(s) still streaming ({', '.join(unsteered)}); wait for idle prompt.", is_post=False)
            fail_safe_exit("External worker pane(s) in progress; waiting for idle prompt")

    bgp = background_watch(get_active_background_tasks(transcript_path, conv_id), state.get("background_steered_tasks", []))
    handle_background_watch_action(bgp, state, state_file, initial_line_count, record_background_steer, record_background_grace)

    if total_tool_calls == 0:
        fail_safe_exit("Conversational turn (0 tool calls)")
    if is_subagent_session(payload, transcript_path, user_prompt, raw_user_prompt):
        fail_safe_exit("Subagent session detected; skipping audit")
    if not user_prompt.strip() or payload.get("fullyIdle") is False or payload.get("fully_idle") is False:
        fail_safe_exit("No user prompt or runtime reports active background work")

    if state.get("recap_emitted"):
        if total_tool_calls <= int(state.get("last_verified_tools", 0)) and initial_line_count <= int(state.get("last_audited_line_count", 0)):
            fail_safe_exit("Recap already emitted")
        state["recap_emitted"] = False
        save_session_state(state_file, state, recap_emitted=False)

    last_lines = state.get("last_audited_line_count", 0)
    if is_post_invocation():
        if last_lines > 0 and last_lines == initial_line_count:
            fail_safe_exit("Mid-turn transcript unchanged")
    else:
        if state.get("last_final_gate_lines", 0) > 0 and state.get("last_final_gate_lines") == initial_line_count:
            fail_safe_exit("Final stop already audited at current line count")
        save_session_state(state_file, state, sage_status="evaluating", last_final_gate_lines=initial_line_count)

    ws_paths = payload.get("workspacePaths") or payload.get("workspace_paths") or []
    workspace_root = resolve_workspace_root(ws_paths)
    save_manifest(conv_id, {"conversation_id": conv_id, "workspace_root": workspace_root, "pinned_goal": state.get("pinned_goal"), "task_complexity": state.get("task_complexity"), "total_tool_calls": total_tool_calls})

    comp = str(state.get("task_complexity") or "").strip().lower()
    is_inception = state.get("last_verified_tools", 0) == 0 and not state.get("pinned_emitted") and comp in ("complex_code", "multi_file")

    if is_post_invocation() and not is_post_invocation_completion_candidate(transcript_path, conv_id):
        has_err, has_loop = has_recent_tool_errors(transcript_path), has_repeated_tool_calls(transcript_path)
        sig = format_summon_message(EVENT_ERROR_LOOP, err=has_err or None, loop=has_loop or None) if (has_err or has_loop) else ""
        save_session_state(state_file, state, sage_status="evaluating", last_audited_line_count=initial_line_count)
        act = sage_flow("midturn", conv_id=conv_id, transcript_path=transcript_path, clean_prompt=clean_prompt, initial_line_count=initial_line_count, total_tool_calls=total_tool_calls, turn_tool_names=turn_tool_names, user_prompt=user_prompt, agent_steps=agent_steps, git_diff=get_git_diff(ws_paths, turn_tool_names), state=state, forced=(has_err or has_loop or is_inception), signal_note=sig.strip(), workspace_root=workspace_root)
        aact = act.get("action")
        if aact in ("exit", "yield"):
            save_session_state(state_file, state, sage_status="hold", **({"pending_clarify": act["pending_clarify"]} if act.get("pending_clarify") else {}))
            fail_safe_exit(act["reason"])
        elif aact == "progressed":
            save_session_state(state_file, state, sage_status="hold", last_verified_tools=act["tools"], last_audited_line_count=act["lines"])
            fail_safe_exit("Agent progressed during sage evaluation; discarding stale advice")
        elif aact == "error":
            save_session_state(state_file, state, sage_status="error", sage_error_streak=state.get("sage_error_streak", state.get("advisor_error_streak", 0)) + 1, last_audited_line_count=initial_line_count)
            fail_safe_exit("Mid-turn sage unavailable (empty or model cascade failed); window preserved")
        elif aact == "hold_dedup":
            if act.get("hammer_suppressed"):
                save_session_state(state_file, state, steer_suppress_count=state.get("steer_suppress_count", 0) + 1)
            record_sage_hold(state_file, state, total_tool_calls, initial_line_count, act.get("seen"))
            fail_safe_exit("Sage advice deduplicated")
        elif aact == "emit":
            fdec, ftext = act["decision"], act["text"]
            gu = {k: act[k] for k in ("pinned_goal", "anchor_goal", "revised_goal", "derived_tasks", "task_complexity", "pinned_emitted", "anchor_emitted", "validation_ledger") if k in act and act[k] is not None}
            gu.update({"last_steer_category": act.get("category"), "last_steer_tools": total_tool_calls, "steer_suppress_count": 0})
            if act.get("pinned_emitted") and not state.get("delegate_cmd_turn") and comp not in ("simple_qa", "qa") and not act.get("assist_active"):
                turn_idx = state.get("recap_count", 0) + 1
                gu.update({"delegate_cmd_turn": turn_idx, "facilitation_cmd_turn": turn_idx, "review_base_sha": get_head_sha(workspace_root)})
                del_msg = immediate_delegate_message(state, pinned_goal=act.get("pinned_goal"), shared=act.get("shared_files") or state.get("shared_files"))
                if del_msg:
                    ftext = f"{ftext}\n\n{del_msg}"
                journal.write("delegate_cmd", conv_id=conv_id, detail=(act.get("pinned_goal") or "")[:120])
            journal.write("steer_emitted", conv_id=conv_id, detail=act.get("category") or fdec)
            record_sage_emit(state_file, state, total_tool_calls, initial_line_count, fdec, ftext, act.get("seen", state.get("sage_advice_counts", state.get("advisor_advice_counts", {}))), **gu)
            log_audit(f"Mid-turn sage {('triggered steer' if fdec == 'steer' else 'watchout emitted')}: {ftext}")
            emit_continue_response(format_hook_message("sage", ftext), is_post=True)
        else:
            record_sage_hold(state_file, state, total_tool_calls, initial_line_count)
            fail_safe_exit("Mid-turn sage passed (healthy)")

    git_diff = get_git_diff(ws_paths, turn_tool_names)
    gate = final_sage_gate(conv_id, transcript_path, clean_prompt, initial_line_count, total_tool_calls, turn_tool_names, user_prompt, agent_steps, git_diff, state, workspace_root=workspace_root)
    gact = gate.get("action")
    log_audit(f"Final sage gate: {gact}" + (f" ({gate.get('reason', '')})" if gate.get("reason") else ""))
    if state.get("pending_clarify") and not state.get("clarify_asked"):
        save_session_state(state_file, state, clarify_asked=True, pending_clarify=None)
        emit_recap_response(f"[CLARIFY] {(state.get('pending_clarify') or {}).get('question') or 'The goal is ambiguous; please clarify the objective.'}", kind="sage")
    if gact == "yield":
        save_session_state(state_file, state, sage_status="hold")
        fail_safe_exit(gate["reason"])
    elif gact == "progressed":
        save_session_state(state_file, state, sage_status="hold", last_verified_tools=gate["tools"], last_audited_line_count=gate["lines"])
        fail_safe_exit("Agent progressed during final sage; discarding stale advice")
    elif gact == "emit":
        fdec, ftext = gate["decision"], gate["text"]
        gu = {k: gate[k] for k in ("pinned_goal", "anchor_goal", "revised_goal", "derived_tasks", "task_complexity", "pinned_emitted", "anchor_emitted", "validation_ledger") if k in gate and gate[k] is not None}
        record_sage_emit(state_file, state, total_tool_calls, initial_line_count, fdec, ftext, gate.get("seen", state.get("sage_advice_counts", state.get("advisor_advice_counts", {}))), **gu)
        log_audit(f"Final sage-first {fdec}: {ftext}")
        journal.write("recap_rejected", conv_id=conv_id, detail=ftext)
        emit_continue_response(format_hook_message("sage", ftext), is_post=True)
    elif gact == "hold_dedup":
        record_sage_hold(state_file, state, total_tool_calls, initial_line_count, gate.get("seen"))
        fail_safe_exit("Final sage advice deduplicated")
    elif gact in ("hold", "healthy"):
        recap = gate.get("recap") or "Work completed and verified successfully."
        cat = gate.get("category", "on_track")
        sage_recap = f"[RECAP·{cat}] {recap}" if not recap.startswith("[RECAP") else recap
        gu = {k: gate[k] for k in ("pinned_goal", "anchor_goal", "revised_goal", "derived_tasks", "task_complexity", "pinned_emitted", "anchor_emitted", "validation_ledger") if k in gate and gate[k] is not None}
        record_sage_recap(state_file, state, total_tool_calls, initial_line_count, recap_text=sage_recap, goal_settled=True, **gu)
        log_audit(f"Sage passed cleanly. Sage recap recorded: {sage_recap}")
        journal.write("recap_pass", conv_id=conv_id)
        fail_safe_exit("Sage passed cleanly")
    elif gact == "error":
        err_streak = state.get("sage_error_streak", state.get("advisor_error_streak", 0)) + 1
        save_session_state(state_file, state, sage_status="error", sage_error_streak=err_streak, last_audited_line_count=initial_line_count)
        fail_safe_exit("Final sage unavailable (empty or model cascade failed); allowing clean termination")
    else:
        save_session_state(state_file, state, sage_status="hold")
        fail_safe_exit(f"Final sage gate skipped: {gate.get('reason', 'no reason')}")


main = run_session_stop_audit


