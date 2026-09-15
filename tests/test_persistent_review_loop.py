"""tests.test_persistent_review_loop - Comprehensive test suite for persistent review and repair loop."""
import json
import os
import tempfile
import pytest

from sage.lite.review_context import (
    compute_review_state_fingerprint,
    format_review_context_for_prompt,
    inspect_findings_and_requirements,
    load_review_record,
    resolve_review_record_path,
    validate_review_record_scope,
)
from sage.lite.runner import run_lite_stop_audit
from sage.lite.schemas import LiteVerdict
from sage.session_state import (
    get_state_file_path,
    load_and_sync_session_state,
    save_session_state,
)


@pytest.fixture
def tmp_workspace(tmp_path):
    ws = tmp_path / "test_workspace"
    ws.mkdir()
    return str(ws)


def test_review_record_schema_validation(tmp_workspace):
    """Scenario 11: Validates version-1 review record parsing and structure."""
    record_path = os.path.join(tmp_workspace, "review-state.json")
    valid_record = {
        "schema_version": 1,
        "session_id": "test-session-123",
        "revision": 2,
        "workspace": tmp_workspace,
        "scope_type": "lead",
        "assignment_id": None,
        "requirements": [
            {"id": "R1", "description": "Core export feature", "status": "pending", "evidence_refs": []}
        ],
        "findings": [
            {
                "id": "F1",
                "title": "Off-by-one in export date range",
                "state": "confirmed",
                "severity": "major",
                "requirement_ref": "R1",
                "owner": "builder",
                "reproduction_evidence": "pytest tests/test_export.py fails on week boundary",
                "fix_ref": None,
                "verification_evidence": None,
                "disposition_reason": None,
                "history": [{"state": "confirmed", "actor": "lead", "note": "Confirmed bug"}]
            }
        ],
        "artifacts": [
            {"path": "export.py", "description": "Main export script"}
        ],
        "review_obligations": ["Verify date arithmetic for leap year"],
        "coverage_gaps": [],
        "prior_attempts": []
    }
    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(valid_record, f)

    record, err = load_review_record(record_path)
    assert err is None
    assert record["session_id"] == "test-session-123"

    audit = inspect_findings_and_requirements(record)
    assert len(audit["open_findings"]) == 1
    assert audit["open_findings"][0]["id"] == "F1"
    assert audit["has_unresolved_work"] is True


def test_review_record_malformed_and_oversize(tmp_workspace):
    """Scenario 11: Rejects oversized or malformed review records."""
    record_path = os.path.join(tmp_workspace, "oversize-record.json")
    with open(record_path, "w", encoding="utf-8") as f:
        f.write(" " * 2000)

    # Max bytes set to 1000
    record, err = load_review_record(record_path, max_bytes=1000)
    assert record is None
    assert "exceeds max allowed size" in err

    # Non-json file
    invalid_json_path = os.path.join(tmp_workspace, "invalid.json")
    with open(invalid_json_path, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    record, err = load_review_record(invalid_json_path)
    assert record is None
    assert "Failed to read/parse" in err


def test_review_record_scope_mismatch(tmp_workspace):
    """Scenario 11: Detects wrong workspace or assignment scope."""
    record = {
        "schema_version": 1,
        "session_id": "test-session",
        "workspace": "/different/unrelated/path",
        "scope_type": "assignment",
        "assignment_id": "A1"
    }
    errors = validate_review_record_scope(record, expected_workspace=tmp_workspace, expected_assignment="A2")
    assert any("Workspace mismatch" in e for e in errors)
    assert any("Assignment ID mismatch" in e for e in errors)


def test_verified_finding_missing_evidence(tmp_workspace):
    """Scenario 3 & 4: Flag findings claimed as verified without backing evidence."""
    record = {
        "findings": [
            {
                "id": "F1",
                "title": "Bug 1",
                "state": "verified",
                "verification_evidence": ""  # Missing!
            }
        ]
    }
    audit = inspect_findings_and_requirements(record)
    assert len(audit["missing_evidence"]) == 1
    assert "without verification_evidence" in audit["missing_evidence"][0]
    assert audit["has_unresolved_work"] is True


def test_artifact_change_invalidates_cache(tmp_workspace):
    """Scenario 8: Artifact modification by another worker invalidates cached review fingerprint."""
    art_file = os.path.join(tmp_workspace, "deliverable.py")
    with open(art_file, "w") as f:
        f.write("version = 1\n")

    record_path = os.path.join(tmp_workspace, "review-state.json")
    record_data = {
        "schema_version": 1,
        "session_id": "sess-1",
        "artifacts": [{"path": "deliverable.py"}]
    }
    with open(record_path, "w") as f:
        json.dump(record_data, f)

    # Call with record_data=None to match production runner.py convention
    fp1 = compute_review_state_fingerprint(record_path, None, tmp_workspace)

    # External modification
    with open(art_file, "w") as f:
        f.write("version = 2; patched = True\n")

    fp2 = compute_review_state_fingerprint(record_path, None, tmp_workspace)
    assert fp1 != fp2, "Fingerprint must change when an artifact file on disk changes even when record_data=None"


def test_more_than_three_productive_rounds_allowed(monkeypatch, tmp_workspace):
    """Scenario 1: Persistent review continues beyond 3 strikes if progress is made."""
    conv_id = "test_persistent_5_rounds"
    state_file = get_state_file_path(conv_id)

    # Mock session state with 4 prior rounds and persistent mode
    state = {
        "turn_key": "k1",
        "lite_total_rounds": 4,
        "lite_fail_count": 4,
        "lite_no_progress_count": 0,
        "lite_status": "auto-continue (round 4)",
        "sage_status": "idle",
        "lite_evidence_hash": "",
        "lite_replay_count": 0,
    }

    transcript_path = os.path.join(tmp_workspace, "transcript.jsonl")
    steps = [
        {"type": "USER_INPUT", "content": "Fix all bugs in math module"},
        {"type": "PLANNER_RESPONSE", "content": "Working on bug 5..."}
    ]

    # Enable persistent mode
    monkeypatch.setenv("AGY_LITE_PERSISTENT_MODE", "1")
    monkeypatch.setenv("AGY_LITE_MOCK_VERDICT", json.dumps({
        "verdict": "FAIL",
        "action": "Fix remaining low-severity bug in math.py",
        "progress_observed": True,
        "progress_summary": "Bug 4 fixed cleanly"
    }))

    emitted = []
    exits = []

    monkeypatch.setattr("sage.lite.runner.get_transcript_path", lambda p, c: transcript_path)
    monkeypatch.setattr("sage.lite.runner._read_transcript_steps", lambda p: steps)
    monkeypatch.setattr("sage.lite.runner.acquire_conversation_lock", lambda c: True)
    monkeypatch.setattr("sage.lite.runner.load_and_sync_session_state", lambda *a: ("Fix all bugs in math module", state_file, state, True))
    monkeypatch.setattr("sage.lite.runner.emit_continue_response", lambda a: emitted.append(a))
    monkeypatch.setattr("sage.lite.runner.fail_safe_exit", lambda m: exits.append(m))
    monkeypatch.setattr("sage.lite.runner.fork_conversation_session", lambda c: "mock_fork")
    monkeypatch.setattr("sage.lite.runner.cleanup_fork_session", lambda *a, **k: None)

    payload = json.dumps({
        "conversationId": conv_id,
        "workspacePaths": [tmp_workspace]
    })

    run_lite_stop_audit(payload)

    assert len(emitted) == 1
    assert "Fix remaining low-severity bug" in emitted[0]
    # Verify state updated to round 5 without circuit breaker tripping
    assert state["lite_total_rounds"] == 5
    assert state["lite_no_progress_count"] == 0
    assert state["lite_fail_count"] == 5


def test_consecutive_no_progress_stall(monkeypatch, tmp_workspace):
    """Scenario 9 & 13: Consecutive no-progress attempts trigger truthful stalled state instead of infinite loop."""
    conv_id = "test_no_progress_stall"
    state_file = get_state_file_path(conv_id)

    # Already had 3 consecutive no-progress rounds
    state = {
        "turn_key": "k2",
        "lite_total_rounds": 3,
        "lite_fail_count": 3,
        "lite_no_progress_count": 3,
        "lite_status": "auto-continue (round 3)",
        "sage_status": "idle",
        "lite_evidence_hash": "",
        "lite_replay_count": 0,
    }

    transcript_path = os.path.join(tmp_workspace, "transcript.jsonl")
    steps = [
        {"type": "USER_INPUT", "content": "Fix deadlock in thread pool"},
        {"type": "PLANNER_RESPONSE", "content": "Still stuck..."}
    ]

    monkeypatch.setenv("AGY_LITE_PERSISTENT_MODE", "1")
    monkeypatch.setenv("AGY_LITE_MAX_NO_PROGRESS", "3")

    exits = []
    monkeypatch.setattr("sage.lite.runner.get_transcript_path", lambda p, c: transcript_path)
    monkeypatch.setattr("sage.lite.runner._read_transcript_steps", lambda p: steps)
    monkeypatch.setattr("sage.lite.runner.acquire_conversation_lock", lambda c: True)
    monkeypatch.setattr("sage.lite.runner.load_and_sync_session_state", lambda *a: ("Fix deadlock in thread pool", state_file, state, True))
    monkeypatch.setattr("sage.lite.runner.fail_safe_exit", lambda m: exits.append(m))

    payload = json.dumps({
        "conversationId": conv_id,
        "workspacePaths": [tmp_workspace]
    })

    run_lite_stop_audit(payload)

    assert any("persistent review stalled" in m for m in exits)
    assert state["lite_status"] == "stalled"


def test_duplicate_event_deduplication(monkeypatch, tmp_workspace):
    """Scenario 7: Duplicate Stop/PostInvocation events replay without extra model calls up to limit."""
    conv_id = "test_dedup"
    state_file = get_state_file_path(conv_id)

    transcript_path = os.path.join(tmp_workspace, "transcript.jsonl")
    steps = [
        {"type": "USER_INPUT", "content": "Run tests"},
        {"type": "PLANNER_RESPONSE", "content": "Tests ran"}
    ]

    ev_hash = json.dumps(steps, sort_keys=True)
    import hashlib
    ev_digest = hashlib.sha256((ev_hash + ":").encode()).hexdigest()

    state = {
        "turn_key": "k3",
        "lite_total_rounds": 1,
        "lite_fail_count": 1,
        "lite_no_progress_count": 0,
        "lite_status": "auto-continue (x1)",
        "sage_status": "injecting",
        "lite_evidence_hash": ev_digest,
        "lite_reject_action": "Fix test_auth failure",
        "lite_replay_count": 1,
    }

    emitted = []
    exits = []
    monkeypatch.setattr("sage.lite.runner.get_transcript_path", lambda p, c: transcript_path)
    monkeypatch.setattr("sage.lite.runner._read_transcript_steps", lambda p: steps)
    monkeypatch.setattr("sage.lite.runner.acquire_conversation_lock", lambda c: True)
    monkeypatch.setattr("sage.lite.runner.load_and_sync_session_state", lambda *a: ("Run tests", state_file, state, True))
    monkeypatch.setattr("sage.lite.runner.emit_continue_response", lambda a: emitted.append(a))
    monkeypatch.setattr("sage.lite.runner.fail_safe_exit", lambda m: exits.append(m))

    payload = json.dumps({
        "conversationId": conv_id,
        "workspacePaths": [tmp_workspace]
    })

    # Trigger duplicate
    run_lite_stop_audit(payload)

    assert len(emitted) == 1
    assert emitted[0] == "Fix test_auth failure"


def test_prompt_review_context_injection(tmp_workspace):
    """Scenario 2 & 6: Verifier prompt includes structured review context with open low-severity bugs."""
    record_path = os.path.join(tmp_workspace, "review-state.json")
    record = {
        "schema_version": 1,
        "session_id": "sess-low-sev",
        "workspace": tmp_workspace,
        "findings": [
            {
                "id": "F2",
                "title": "Low severity log typo",
                "state": "confirmed",
                "severity": "low",
                "owner": "builder"
            }
        ]
    }
    with open(record_path, "w") as f:
        json.dump(record, f)

    context_str = format_review_context_for_prompt(record_path, workspace_root=tmp_workspace)
    assert "[F2] (CONFIRMED, low)" in context_str
    assert "Even low-severity confirmed bugs must be repaired" in context_str


def test_five_productive_rounds_then_clean_completion(monkeypatch, tmp_workspace):
    """Scenario 1: 5 productive failure rounds followed by evidence-backed completion."""
    conv_id = "test_5_rounds_then_pass"
    state_file = get_state_file_path(conv_id)

    state = {
        "turn_key": "k_pass",
        "lite_total_rounds": 5,
        "lite_fail_count": 5,
        "lite_no_progress_count": 0,
        "lite_status": "auto-continue (round 5)",
        "sage_status": "idle",
        "lite_evidence_hash": "",
        "lite_replay_count": 0,
    }

    transcript_path = os.path.join(tmp_workspace, "transcript.jsonl")
    steps = [
        {"type": "USER_INPUT", "content": "Fix all 5 test failures"},
        {"type": "PLANNER_RESPONSE", "content": "All 5 tests fixed and passing."}
    ]

    monkeypatch.setenv("AGY_LITE_PERSISTENT_MODE", "1")
    monkeypatch.setenv("AGY_LITE_MOCK_VERDICT", json.dumps({
        "verdict": "PASS",
        "comment": "All 5 defects resolved with clean test run",
        "proof": ["pytest tests/ passes with 5 passed"]
    }))

    emitted = []
    exits = []

    monkeypatch.setattr("sage.lite.runner.get_transcript_path", lambda p, c: transcript_path)
    monkeypatch.setattr("sage.lite.runner._read_transcript_steps", lambda p: steps)
    monkeypatch.setattr("sage.lite.runner.acquire_conversation_lock", lambda c: True)
    monkeypatch.setattr("sage.lite.runner.load_and_sync_session_state", lambda *a: ("Fix all 5 test failures", state_file, state, True))
    monkeypatch.setattr("sage.lite.runner.emit_continue_response", lambda a: emitted.append(a))
    monkeypatch.setattr("sage.lite.runner.fail_safe_exit", lambda m: exits.append(m))
    monkeypatch.setattr("sage.lite.runner.fork_conversation_session", lambda c: "mock_fork")
    monkeypatch.setattr("sage.lite.runner.cleanup_fork_session", lambda *a, **k: None)

    payload = json.dumps({
        "conversationId": conv_id,
        "workspacePaths": [tmp_workspace]
    })

    run_lite_stop_audit(payload)

    assert len(emitted) == 0
    assert any("verified cleanly" in m for m in exits)
    assert state["lite_status"] == "verified"
    assert state["lite_fail_count"] == 0
    assert state["lite_no_progress_count"] == 0


def test_unmet_acceptance_without_defect_continues(tmp_workspace):
    """Scenario 4: Unmet acceptance requirement keeps review open even before a bug is confirmed."""
    record = {
        "schema_version": 1,
        "session_id": "sess-req-check",
        "requirements": [
            {"id": "R1", "description": "Support negative numbers in parser", "status": "pending", "evidence_refs": []}
        ],
        "findings": []
    }
    audit = inspect_findings_and_requirements(record)
    assert len(audit["unmet_requirements"]) == 1
    assert audit["has_unresolved_work"] is True


def test_regression_reopens_finding(tmp_workspace):
    """Scenario 5: A finding can be reopened after regression with appended history."""
    finding = {
        "id": "F1",
        "title": "Date parser bug",
        "state": "confirmed",
        "severity": "major",
        "history": [
            {"state": "proposed", "actor": "auditor", "note": "Initial bug"},
            {"state": "fix_reported", "actor": "builder", "note": "Patched in PR 1"},
            {"state": "confirmed", "actor": "auditor", "note": "Regression in test case 3"}
        ]
    }
    assert finding["state"] == "confirmed"
    assert len(finding["history"]) == 3
    assert finding["history"][-1]["note"] == "Regression in test case 3"


def test_explanation_only_task_stops_promptly(monkeypatch, tmp_workspace):
    """Scenario 14: Explanation-only task passes cleanly without review loop overhead."""
    conv_id = "test_explanation_task"
    state_file = get_state_file_path(conv_id)

    state = {
        "turn_key": "k_exp",
        "lite_total_rounds": 0,
        "lite_fail_count": 0,
        "lite_no_progress_count": 0,
        "lite_status": "",
        "sage_status": "idle",
        "lite_evidence_hash": "",
        "lite_replay_count": 0,
    }

    transcript_path = os.path.join(tmp_workspace, "transcript.jsonl")
    steps = [
        {"type": "USER_INPUT", "content": "Explain how RSA encryption works"},
        {"type": "PLANNER_RESPONSE", "content": "RSA is an asymmetric cryptographic algorithm..."}
    ]

    monkeypatch.setenv("AGY_LITE_MOCK_VERDICT", json.dumps({
        "verdict": "PASS",
        "comment": "Complete explanation provided",
        "proof": ["explanation covers public/private keys and prime factorization"]
    }))

    exits = []
    monkeypatch.setattr("sage.lite.runner.get_transcript_path", lambda p, c: transcript_path)
    monkeypatch.setattr("sage.lite.runner._read_transcript_steps", lambda p: steps)
    monkeypatch.setattr("sage.lite.runner.acquire_conversation_lock", lambda c: True)
    monkeypatch.setattr("sage.lite.runner.load_and_sync_session_state", lambda *a: ("Explain RSA", state_file, state, True))
    monkeypatch.setattr("sage.lite.runner.fail_safe_exit", lambda m: exits.append(m))
    monkeypatch.setattr("sage.lite.runner.fork_conversation_session", lambda c: "mock_fork")
    monkeypatch.setattr("sage.lite.runner.cleanup_fork_session", lambda *a, **k: None)

    payload = json.dumps({
        "conversationId": conv_id,
        "workspacePaths": [tmp_workspace]
    })

    run_lite_stop_audit(payload)

    assert any("verified cleanly" in m for m in exits)
    assert state["lite_status"] == "verified"


def test_provider_unavailable_records_unavailable(monkeypatch, tmp_workspace):
    """Scenario 13: Provider unavailable reports status honestly without fake completion."""
    conv_id = "test_unavail"
    state_file = get_state_file_path(conv_id)

    state = {
        "turn_key": "k_unavail",
        "lite_total_rounds": 1,
        "lite_fail_count": 1,
        "lite_no_progress_count": 0,
        "lite_status": "auto-continue (x1)",
        "sage_status": "idle",
        "lite_evidence_hash": "",
        "lite_replay_count": 0,
    }

    transcript_path = os.path.join(tmp_workspace, "transcript.jsonl")
    steps = [
        {"type": "USER_INPUT", "content": "Deploy changes"},
        {"type": "PLANNER_RESPONSE", "content": "Deployed."}
    ]

    monkeypatch.setattr("sage.lite.runner.run_lite_verification", lambda *a, **k: LiteVerdict("PASS", completion="unavailable"))

    exits = []
    monkeypatch.setattr("sage.lite.runner.get_transcript_path", lambda p, c: transcript_path)
    monkeypatch.setattr("sage.lite.runner._read_transcript_steps", lambda p: steps)
    monkeypatch.setattr("sage.lite.runner.acquire_conversation_lock", lambda c: True)
    monkeypatch.setattr("sage.lite.runner.load_and_sync_session_state", lambda *a: ("Deploy", state_file, state, True))
    monkeypatch.setattr("sage.lite.runner.fail_safe_exit", lambda m: exits.append(m))
    monkeypatch.setattr("sage.lite.runner.fork_conversation_session", lambda c: "mock_fork")
    monkeypatch.setattr("sage.lite.runner.cleanup_fork_session", lambda *a, **k: None)

    payload = json.dumps({
        "conversationId": conv_id,
        "workspacePaths": [tmp_workspace]
    })

    run_lite_stop_audit(payload)

    assert any("verifier unavailable" in m for m in exits)
    assert state["lite_status"] == "unavailable"


def test_replay_limit_exceeded_marks_unverified(monkeypatch, tmp_workspace):
    """Scenario 7: Replay limit exceeded exits with unverified status."""
    conv_id = "test_replay_limit"
    state_file = get_state_file_path(conv_id)

    transcript_path = os.path.join(tmp_workspace, "transcript.jsonl")
    steps = [
        {"type": "USER_INPUT", "content": "Run tests"},
        {"type": "PLANNER_RESPONSE", "content": "Tests ran"}
    ]

    ev_hash = json.dumps(steps, sort_keys=True)
    import hashlib
    ev_digest = hashlib.sha256((ev_hash + ":").encode()).hexdigest()

    # Replay count already at max (3)
    state = {
        "turn_key": "k_rep",
        "lite_total_rounds": 1,
        "lite_fail_count": 1,
        "lite_no_progress_count": 0,
        "lite_status": "auto-continue (x1)",
        "sage_status": "injecting",
        "lite_evidence_hash": ev_digest,
        "lite_reject_action": "Fix test_auth failure",
        "lite_replay_count": 3,
    }

    exits = []
    monkeypatch.setattr("sage.lite.runner.get_transcript_path", lambda p, c: transcript_path)
    monkeypatch.setattr("sage.lite.runner._read_transcript_steps", lambda p: steps)
    monkeypatch.setattr("sage.lite.runner.acquire_conversation_lock", lambda c: True)
    monkeypatch.setattr("sage.lite.runner.load_and_sync_session_state", lambda *a: ("Run tests", state_file, state, True))
    monkeypatch.setattr("sage.lite.runner.fail_safe_exit", lambda m: exits.append(m))

    payload = json.dumps({
        "conversationId": conv_id,
        "workspacePaths": [tmp_workspace]
    })

    run_lite_stop_audit(payload)

    assert any("replay limit reached" in m for m in exits)
    assert state["lite_status"] == "unverified"


def test_scope_validation_sibling_prefix_rejected(tmp_workspace):
    """Lead finding 3: Sibling workspace with prefix match must be rejected."""
    sibling_ws = tmp_workspace + "-other"
    record = {
        "schema_version": 1,
        "session_id": "test-session",
        "workspace": sibling_ws,
        "scope_type": "lead"
    }
    errors = validate_review_record_scope(record, expected_workspace=tmp_workspace)
    assert any("Workspace mismatch" in e for e in errors)


def test_scope_validation_missing_assignment_id_rejected():
    """Lead finding 3 / Reviewer finding 2: Missing or null assignment ID must be rejected when expected or scope_type is assignment."""
    # When expected_assignment is set, missing assignment_id must error
    record1 = {
        "schema_version": 1,
        "session_id": "test-session",
        "scope_type": "lead",
        "assignment_id": None
    }
    errors1 = validate_review_record_scope(record1, expected_assignment="A1")
    assert any("Assignment ID missing" in e for e in errors1)

    # When scope_type is assignment, null assignment_id must error
    record2 = {
        "schema_version": 1,
        "session_id": "test-session",
        "scope_type": "assignment",
        "assignment_id": None
    }
    errors2 = validate_review_record_scope(record2)
    assert any("Assignment-scoped review record requires a non-empty string assignment_id" in e for e in errors2)


def test_coverage_gaps_in_unresolved_work_and_prompt(tmp_workspace):
    """Lead finding 2: Coverage gaps keep has_unresolved_work=True and are formatted in prompt."""
    record = {
        "schema_version": 1,
        "session_id": "sess-cov-gap",
        "workspace": tmp_workspace,
        "coverage_gaps": ["Missing test coverage for partial week boundary"],
        "findings": [],
        "requirements": []
    }
    audit = inspect_findings_and_requirements(record)
    assert audit["has_unresolved_work"] is True
    assert len(audit["coverage_gaps"]) == 1

    record_path = os.path.join(tmp_workspace, "review-state.json")
    with open(record_path, "w") as f:
        json.dump(record, f)

    context_str = format_review_context_for_prompt(record_path, workspace_root=tmp_workspace)
    assert "Outstanding Coverage Gaps (1):" in context_str
    assert "Missing test coverage for partial week boundary" in context_str
    assert "Do NOT return PASS if open findings, unmet requirements, open obligations, coverage gaps, or missing evidence remain." in context_str


def test_auto_discovery_policy(tmp_workspace):
    """Lead finding 4 / Reviewer finding 3: Workspace review-state.json is only discovered when allow_workspace_discovery=True."""
    record_path = os.path.join(tmp_workspace, "review-state.json")
    with open(record_path, "w") as f:
        json.dump({"schema_version": 1, "session_id": "s1"}, f)

    # When allow_workspace_discovery is False (e.g. persistent mode not enabled), do not discover
    assert resolve_review_record_path(workspace_root=tmp_workspace, allow_workspace_discovery=False) is None

    # When allow_workspace_discovery is True, discover standard path
    assert resolve_review_record_path(workspace_root=tmp_workspace, allow_workspace_discovery=True) == record_path

    # Explicit path is always honored
    assert resolve_review_record_path(workspace_root=tmp_workspace, explicit_path=record_path, allow_workspace_discovery=False) == record_path


def test_end_to_end_artifact_mutation_invalidates_stop_audit_cache(monkeypatch, tmp_workspace):
    """End-to-end integration: runner recalculates fingerprint and re-audits when artifact changes."""
    conv_id = "test_e2e_artifact_invalidation"
    state_file = get_state_file_path(conv_id)

    art_file = os.path.join(tmp_workspace, "generated_output.py")
    with open(art_file, "w") as f:
        f.write("result = 42\n")

    record_path = os.path.join(tmp_workspace, "review-state.json")
    record_data = {
        "schema_version": 1,
        "session_id": "s_e2e",
        "workspace": tmp_workspace,
        "artifacts": [{"path": "generated_output.py"}]
    }
    with open(record_path, "w") as f:
        json.dump(record_data, f)

    transcript_path = os.path.join(tmp_workspace, "transcript.jsonl")
    steps = [
        {"type": "USER_INPUT", "content": "Generate calculation"},
        {"type": "PLANNER_RESPONSE", "content": "Done"}
    ]

    # Compute initial cached state fingerprint
    initial_rfp = compute_review_state_fingerprint(record_path, None, tmp_workspace)
    import hashlib
    cached_hash = hashlib.sha256((json.dumps(steps, sort_keys=True) + ":" + initial_rfp).encode()).hexdigest()

    state = {
        "turn_key": "k_e2e",
        "lite_total_rounds": 1,
        "lite_fail_count": 0,
        "lite_no_progress_count": 0,
        "lite_status": "verified",
        "sage_status": "idle",
        "lite_evidence_hash": cached_hash,
        "lite_review_fingerprint": initial_rfp,
        "lite_reject_action": "",
        "lite_replay_count": 0,
    }

    # Now an external worker edits generated_output.py on disk
    with open(art_file, "w") as f:
        f.write("result = 999; # bug introduced\n")

    monkeypatch.setenv("AGY_LITE_PERSISTENT_MODE", "1")
    monkeypatch.setenv("AGY_LITE_MOCK_VERDICT", json.dumps({
        "verdict": "FAIL",
        "action": "Fix calculation error in generated_output.py",
        "progress_observed": False,
        "progress_summary": "Bug introduced"
    }))

    emitted = []
    exits = []
    monkeypatch.setattr("sage.lite.runner.get_transcript_path", lambda p, c: transcript_path)
    monkeypatch.setattr("sage.lite.runner._read_transcript_steps", lambda p: steps)
    monkeypatch.setattr("sage.lite.runner.acquire_conversation_lock", lambda c: True)
    monkeypatch.setattr("sage.lite.runner.load_and_sync_session_state", lambda *a: ("Generate calculation", state_file, state, True))
    monkeypatch.setattr("sage.lite.runner.emit_continue_response", lambda a: emitted.append(a))
    monkeypatch.setattr("sage.lite.runner.fail_safe_exit", lambda m: exits.append(m))
    monkeypatch.setattr("sage.lite.runner.fork_conversation_session", lambda c: "mock_fork")
    monkeypatch.setattr("sage.lite.runner.cleanup_fork_session", lambda *a, **k: None)

    payload = json.dumps({
        "conversationId": conv_id,
        "workspacePaths": [tmp_workspace]
    })

    # Run audit: should NOT exit with "unchanged transcript already audited", but re-verify and emit FAIL
    run_lite_stop_audit(payload)

    assert len(emitted) == 1
    assert "Fix calculation error in generated_output.py" in emitted[0]
    assert state["lite_review_fingerprint"] != initial_rfp

