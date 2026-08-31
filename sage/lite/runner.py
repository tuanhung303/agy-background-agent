"""sage.lite.runner - Main lifecycle runner for Stop Hook Lite Mode."""
import json
import os
from typing import Any, Dict, Optional

from sage.config import LITE_MAX_RETRIES, LITE_MODE_ENABLED
from sage.git import resolve_workspace_root
from sage.guards import (
    check_payload_and_lifecycle, emit_continue_response, emit_recap_response,
    fail_safe_exit, is_post_invocation, is_subagent_session, set_pending_inbox_steps,
)
from sage.lite.fork import cleanup_fork_session, fork_conversation_session
from sage.lite.gating import extract_turn_mutations_and_context
from sage.lite.schemas import LiteVerdict
from sage.lite.verifier import run_kb_maintenance, run_lite_verification
from sage.locking import acquire_conversation_lock, log_audit, release_lock
from sage.mcp_bridge_helpers import drain_inbox
from sage.session_state import load_and_sync_session_state, save_session_state
from sage.transcript import (
    _read_transcript_steps, get_active_background_tasks,
    get_active_external_panes, get_transcript_path,
)


def run_lite_stop_audit(raw_payload: Optional[str] = None) -> None:
    """Executes the Lite Mode Stop Hook verification gate."""
    payload = json.loads(raw_payload) if raw_payload else check_payload_and_lifecycle()
    conv_id = str(payload.get("conversationId") or payload.get("conversation_id") or "default")

    # Drain any bridge inbox messages
    drained = drain_inbox(conv_id)
    if drained:
        set_pending_inbox_steps([{"userMessage": m.get("message", "")} for m in drained if m.get("message")])

    if not acquire_conversation_lock(conv_id):
        fail_safe_exit(f"Concurrent audit in progress for {conv_id}")

    transcript_path = get_transcript_path(payload, conv_id)
    steps = _read_transcript_steps(transcript_path)

    # 1. Skip subagent child sessions or empty conversations
    if is_subagent_session(payload, transcript_path, "", ""):
        fail_safe_exit("Subagent session detected; skipping Lite verification")

    if not steps:
        fail_safe_exit("Empty transcript; skipping Lite verification")

    # 2. Skip if runtime reports active background work or external worker panes
    if payload.get("fullyIdle") is False or payload.get("fully_idle") is False:
        fail_safe_exit("Runtime reports active background work")

    if get_active_background_tasks(transcript_path, conv_id):
        fail_safe_exit("Active background tasks running")

    if get_active_external_panes(transcript_path):
        fail_safe_exit("Active external panes streaming")

    # 3. Mutation gating check (Pure transcript inspection)
    has_mutation, reason, true_user_prompt, last_agent_output = extract_turn_mutations_and_context(steps)
    if not has_mutation:
        log_audit(f"Lite Mode bypass: {reason}")
        fail_safe_exit(f"Lite Mode bypass: {reason}")

    # Load session state for circuit breaker & statusline
    clean_prompt, state_file, state, _ = load_and_sync_session_state(conv_id, transcript_path, true_user_prompt)
    fail_count = int(state.get("lite_fail_count", 0))

    # 3. 3-Strike Circuit Breaker
    if fail_count >= LITE_MAX_RETRIES:
        log_audit(f"Lite Mode circuit breaker tripped ({fail_count}/{LITE_MAX_RETRIES}); failing open")
        save_session_state(state_file, state, lite_fail_count=0, sage_status="idle")
        fail_safe_exit("Lite Mode circuit breaker tripped; allowing clean stop")

    # 4. Update statusline state to 'reviewing' (renders italic blue on left)
    save_session_state(state_file, state, sage_status="reviewing")

    ws_paths = payload.get("workspacePaths") or payload.get("workspace_paths") or []
    workspace_root = resolve_workspace_root(ws_paths)

    # 5. Fork conversation DB into SAGE_ISOLATED_HOME
    fork_conv_id = fork_conversation_session(conv_id)
    if not fork_conv_id:
        log_audit("Failed to fork conversation session; failing open with clean stop")
        save_session_state(state_file, state, sage_status="idle")
        fail_safe_exit("Lite Mode fork failed; allowing clean stop")

    # 6. Execute Final Verifier on forked session
    verdict: LiteVerdict = LiteVerdict(verdict="PASS", action="")
    try:
        verdict = run_lite_verification(
            parent_conv_id=conv_id,
            fork_conv_id=fork_conv_id,
            user_prompt=true_user_prompt,
            last_agent_output=last_agent_output,
            cwd=workspace_root,
        )
    finally:
        preserve_failed = (verdict.verdict == "FAIL")
        cleanup_fork_session(
            fork_conv_id,
            preserve_failed=preserve_failed,
            verifier_output=verdict.action,
        )

    # 7. Dispatch Verdict
    if verdict.verdict == "FAIL" and verdict.action:
        next_strike = fail_count + 1
        log_audit(f"Lite Mode verifier FAIL (strike {next_strike}/{LITE_MAX_RETRIES}): {verdict.action}")
        save_session_state(
            state_file,
            state,
            lite_fail_count=next_strike,
            lite_status=f"auto-continue (x{next_strike})",
            sage_status="injecting",
            last_audited_line_count=len(steps),
        )
        emit_continue_response(verdict.action)
    else:
        log_audit("Lite Mode verifier PASS; running knowledge base maintainer")
        # 8. Update statusline to 'updating knowledge/memory' and run KB maintainer
        save_session_state(
            state_file,
            state,
            lite_fail_count=0,
            lite_status="updating knowledge/memory",
            sage_status="updating",
            last_audited_line_count=len(steps),
        )
        kb_fork_id = fork_conversation_session(conv_id)
        if kb_fork_id:
            try:
                run_kb_maintenance(
                    parent_conv_id=conv_id,
                    fork_conv_id=kb_fork_id,
                    cwd=workspace_root,
                )
            finally:
                cleanup_fork_session(kb_fork_id)

        save_session_state(
            state_file,
            state,
            lite_fail_count=0,
            lite_status="delivered",
            sage_status="idle",
            recap_emitted=True,
            last_audited_line_count=len(steps),
        )
        emit_recap_response("Work verified cleanly by Lite Mode.", kind="recap")
