"""sage.lite.runner - Main lifecycle runner for Stop Hook Lite Mode and Persistent Review."""
import hashlib
import json
from typing import Optional

from sage.config import (
    _safe_bool_multi, _safe_int_multi,
    LITE_ASSIGNMENT_ID, LITE_MAX_NO_PROGRESS, LITE_MAX_RETRIES,
    LITE_PERSISTENT_MODE, LITE_REVIEW_RECORD_PATH,
)
from sage.git import resolve_workspace_root
from sage.guards import (
    check_payload_and_lifecycle, emit_continue_response,
    fail_safe_exit, is_post_invocation, is_subagent_session,
)
from sage.lite.fork import cleanup_fork_session, fork_conversation_session
from sage.lite.gating import extract_turn_execution_provenance
from sage.lite.review_context import (
    compute_review_state_fingerprint, format_review_context_for_prompt,
    resolve_review_record_path,
)
from sage.lite.schemas import LiteVerdict
from sage.lite.verifier import run_lite_verification
from sage.locking import acquire_conversation_lock, log_audit
from sage.session_state import load_and_sync_session_state, save_session_state
from sage.transcript import (
    _read_transcript_steps, get_active_background_tasks,
    get_active_external_panes, get_transcript_path,
    is_post_invocation_completion_candidate,
)


def run_lite_stop_audit(raw_payload: Optional[str] = None) -> None:
    """Executes the Lite Mode Stop Hook verification gate with persistent review support."""
    payload = json.loads(raw_payload) if raw_payload else check_payload_and_lifecycle()
    conv_id = str(payload.get("conversationId") or payload.get("conversation_id") or "default")

    if not acquire_conversation_lock(conv_id):
        fail_safe_exit(f"Concurrent audit in progress for {conv_id}")

    transcript_path = get_transcript_path(payload, conv_id)
    steps = _read_transcript_steps(transcript_path)

    # 1. Skip subagent child sessions or empty conversations
    if is_subagent_session(payload, transcript_path, "", ""):
        fail_safe_exit("Subagent session detected; skipping Lite verification")

    if not steps:
        fail_safe_exit("Empty transcript; skipping Lite verification")

    # 2. If post-invocation, only audit when the response is ready (not mid-tool calls)
    if is_post_invocation() and not is_post_invocation_completion_candidate(transcript_path, conv_id):
        fail_safe_exit("Mid-turn in progress (active tool calls)")

    # 3. Skip if runtime reports active background work or external worker panes
    if payload.get("fullyIdle") is False or payload.get("fully_idle") is False:
        fail_safe_exit("Runtime reports active background work")

    if get_active_background_tasks(transcript_path, conv_id):
        fail_safe_exit("Active background tasks running")

    if get_active_external_panes(transcript_path):
        fail_safe_exit("Active external panes streaming")

    ws_paths = payload.get("workspacePaths") or payload.get("workspace_paths") or []
    workspace_root = resolve_workspace_root(ws_paths)

    # 4. Mutation gating check & turn provenance distillation
    turn_provenance = extract_turn_execution_provenance(steps, workspace_root=workspace_root)
    has_mutation = turn_provenance["has_mutation"]
    reason = turn_provenance["mutation_reason"]
    true_user_prompt = turn_provenance["true_user_prompt"]
    last_agent_output = turn_provenance["last_agent_output"]

    # Load session state for circuit breaker & statusline
    _, state_file, state, _ = load_and_sync_session_state(conv_id, transcript_path, true_user_prompt)
    fail_count = int(state.get("lite_fail_count", 0))

    persistent_cfg = _safe_bool_multi(("AGY_LITE_PERSISTENT_MODE", "AGY_STOP_AUDIT_PERSISTENT_MODE", "AGY_PERSISTENT_REVIEW"), LITE_PERSISTENT_MODE)
    review_record_path = resolve_review_record_path(
        workspace_root=workspace_root,
        explicit_path=LITE_REVIEW_RECORD_PATH,
        allow_workspace_discovery=persistent_cfg,
    )
    is_persistent = bool(persistent_cfg or LITE_REVIEW_RECORD_PATH)
    max_no_progress = _safe_int_multi(("AGY_LITE_MAX_NO_PROGRESS", "AGY_STOP_AUDIT_MAX_NO_PROGRESS"), LITE_MAX_NO_PROGRESS)
    max_retries = _safe_int_multi(("AGY_LITE_MAX_RETRIES",), LITE_MAX_RETRIES)

    if not has_mutation and fail_count == 0 and not true_user_prompt and not review_record_path:
        log_audit(f"Lite Mode bypass: {reason}")
        fail_safe_exit(f"Lite Mode bypass: {reason}")

    review_context = format_review_context_for_prompt(
        review_record_path,
        workspace_root=workspace_root,
        expected_assignment=LITE_ASSIGNMENT_ID,
    ) if review_record_path else None
    rfp = compute_review_state_fingerprint(review_record_path, None, workspace_root) if review_record_path else ""

    evidence_hash = hashlib.sha256((json.dumps(steps, sort_keys=True) + ":" + rfp).encode()).hexdigest()
    if state.get("lite_evidence_hash") == evidence_hash:
        if state.get("lite_reject_action"):
            replay_count = int(state.get("lite_replay_count", 0))
            if replay_count >= max_retries:
                save_session_state(state_file, state, sage_status="idle", lite_status="unverified")
                fail_safe_exit("Lite Mode unchanged rejection replay limit reached; work remains unverified.")
                return
            save_session_state(state_file, state, lite_replay_count=replay_count + 1)
            emit_continue_response(state["lite_reject_action"])
        else:
            fail_safe_exit("Lite Mode: unchanged transcript already audited")
        return

    # 5. Circuit breaker or persistent stall check
    if not is_persistent:
        if fail_count >= max_retries:
            log_audit(f"Lite Mode circuit breaker tripped ({fail_count}/{max_retries}); failing open")
            save_session_state(state_file, state, lite_fail_count=0, sage_status="idle", lite_status="unverified")
            fail_safe_exit("Lite Mode circuit breaker tripped; allowing clean stop")
            return
    else:
        no_progress_count = int(state.get("lite_no_progress_count", 0))
        if no_progress_count >= max_no_progress:
            log_audit(f"Lite Mode persistent review stalled ({no_progress_count}/{max_no_progress} consecutive no-progress rounds)")
            save_session_state(state_file, state, sage_status="idle", lite_status="stalled")
            fail_safe_exit("Lite Mode persistent review stalled without forward progress; work remains incomplete.")
            return

    # 6. Update statusline state to 'reviewing' (renders italic blue on left)
    save_session_state(state_file, state, sage_status="reviewing")

    # 7. Fork conversation DB into SAGE_ISOLATED_HOME
    fork_conv_id = fork_conversation_session(conv_id)
    if not fork_conv_id:
        log_audit("Failed to fork conversation session; failing open with clean stop")
        save_session_state(state_file, state, sage_status="idle", lite_status="unavailable")
        fail_safe_exit("Lite Mode fork failed; allowing clean stop")
        return

    # 8. Execute Final Verifier on forked session
    verdict = LiteVerdict(verdict="PASS", completion="unavailable")
    try:
        images = turn_provenance.get("image_files") or turn_provenance.get("generated_images") or []
        verdict = run_lite_verification(
            parent_conv_id=conv_id,
            fork_conv_id=fork_conv_id,
            user_prompt=true_user_prompt,
            last_agent_output=last_agent_output,
            cwd=workspace_root,
            turn_execution_summary=turn_provenance.get("tool_executions_summary"),
            image_manifest=images,
            turn_provenance=turn_provenance,
            review_context=review_context,
            no_progress_count=int(state.get("lite_no_progress_count", 0)),
        )
    except Exception as exc:
        log_audit(f"Lite Mode verifier exception: {exc}")
    finally:
        cleanup_fork_session(
            fork_conv_id,
            preserve_failed=verdict.verdict == "FAIL",
            verifier_output=verdict.action,
        )

    if verdict.completion in ("unavailable", "timed_out"):
        status_label = "timed_out" if verdict.completion == "timed_out" else "unavailable"
        save_session_state(state_file, state, sage_status="idle", lite_status=status_label)
        fail_safe_exit(f"Lite Mode verifier {status_label}; work has not been verified.")
        return

    if verdict.completion == "stalled":
        save_session_state(
            state_file,
            state,
            sage_status="idle",
            lite_status="stalled",
            lite_evidence_hash=evidence_hash,
            lite_review_fingerprint=rfp,
            lite_reject_action="",
            lite_replay_count=0,
        )
        fail_safe_exit("Lite Mode review stalled; work remains incomplete.")
        return

    # 9. Dispatch Verdict
    if verdict.verdict == "FAIL" and verdict.action:
        next_total = int(state.get("lite_total_rounds", 0)) + 1
        next_fail = fail_count + 1
        next_no_prog = 0 if verdict.progress_observed else (int(state.get("lite_no_progress_count", 0)) + 1)
        status_label = f"auto-continue (round {next_total})" if is_persistent else f"auto-continue (x{next_fail})"

        log_audit(f"Lite Mode verifier FAIL (round {next_total}, strike {next_fail}, no_prog {next_no_prog}): {verdict.action}")
        save_session_state(
            state_file,
            state,
            lite_total_rounds=next_total,
            lite_fail_count=next_fail,
            lite_no_progress_count=next_no_prog,
            lite_status=status_label,
            sage_status="injecting",
            last_audited_line_count=len(steps),
            lite_evidence_hash=evidence_hash,
            lite_review_fingerprint=rfp,
            lite_reject_action=verdict.action,
            lite_replay_count=0,
            lite_unresolved_findings=verdict.unresolved_findings,
        )
        emit_continue_response(verdict.action)
    else:
        if verdict.proof:
            log_audit(f"Lite Mode verifier PASS proofs: {verdict.proof}")
        save_session_state(
            state_file,
            state,
            lite_fail_count=0,
            lite_no_progress_count=0,
            lite_status="blocked" if verdict.completion == "blocked" else "verified",
            sage_status="idle",
            recap_emitted=True,
            last_audited_line_count=len(steps),
            lite_evidence_hash=evidence_hash,
            lite_review_fingerprint=rfp,
            lite_reject_action="",
            lite_replay_count=0,
        )
        fail_safe_exit("Execution blocked with valid escalation." if verdict.completion == "blocked" else "Work verified cleanly by Lite Mode.")
