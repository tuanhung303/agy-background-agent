"""Regression checks for verdict integrity, fork lifetime, and repeat stop decisions."""
import hashlib
import json
import subprocess
from unittest.mock import Mock

import pytest

from sage.lite import runner, verifier
from sage.lite.schemas import LiteVerdict
from sage.lite.evidence import match_tool_call_outputs
from sage.lite.gating import extract_turn_execution_provenance


@pytest.mark.parametrize("payload", [
    None, {}, {"verdict": "MAYBE", "proof": ["MFA"]},
    {"verdict": "FAIL", "action": ""},
    {"verdict": "PASS", "comment": "Done", "proof": [{"claim": "done"}]},
    {"verdict": "PASS", "comment": "Done", "proof": "verified"},
    {"verdict": "PASS", "comment": "Done", "proof": []},
    {"verdict": "PASS", "comment": "Done", "proof": ["output"], "completion": "unavailable"},
])
def test_malformed_model_verdict_is_not_completion(payload):
    with pytest.raises(ValueError):
        LiteVerdict.from_dict(payload)


def test_model_blocker_has_distinct_completion():
    result = LiteVerdict.from_dict({"verdict": "PASS", "completion": "blocked", "comment": "Export blocked", "proof": ["MFA_REQUIRED at portal after export attempt"]})
    assert result.completion == "blocked"


def test_invalid_candidate_uses_next_model_instead_of_passing(monkeypatch):
    monkeypatch.delenv("AGY_LITE_MOCK_VERDICT", raising=False)
    monkeypatch.setattr(verifier, "ensure_isolated_home", lambda: "/tmp/fixture")
    monkeypatch.setattr(verifier, "LITE_MODEL_CANDIDATES", ["first", "second"])
    run = Mock(side_effect=[
        subprocess.CompletedProcess([], 0, '{"verdict":"MAYBE","proof":["MFA"]}'),
        subprocess.CompletedProcess([], 0, '{"verdict":"FAIL","action":"Finish the export."}'),
    ])
    monkeypatch.setattr(verifier.subprocess, "run", run)
    result = verifier.run_lite_verification("parent", "fork", "Export", "Done")
    assert result.verdict == "FAIL" and run.call_count == 2


def test_exhausted_models_are_unavailable_and_do_not_extend_timeout(monkeypatch):
    monkeypatch.delenv("AGY_LITE_MOCK_VERDICT", raising=False)
    monkeypatch.setattr(verifier, "ensure_isolated_home", lambda: "/tmp/fixture")
    monkeypatch.setattr(verifier, "LITE_MODEL_CANDIDATES", ["first", "second"])
    monkeypatch.setattr(verifier.time, "monotonic", Mock(side_effect=[100, 109.25, 109.25, 110]))
    run = Mock(side_effect=subprocess.TimeoutExpired("agy", 0.75))
    monkeypatch.setattr(verifier.subprocess, "run", run)
    result = verifier.run_lite_verification("parent", "fork", "Export", "Done", timeout=10)
    assert run.call_args.kwargs["timeout"] == 0.75
    assert result.completion == "unavailable" and not result.proof


@pytest.fixture
def audit(monkeypatch):
    steps = [
        {"type": "USER_INPUT", "content": "Export records and verify them"},
        {"type": "PLANNER_RESPONSE", "content": "Please do it yourself."},
    ]
    state = {"lite_fail_count": 0}
    for name, value in {
        "acquire_conversation_lock": True, "is_subagent_session": False,
        "is_post_invocation": False, "get_active_background_tasks": [],
        "get_active_external_panes": [], "get_transcript_path": "/tmp/fixture",
        "resolve_workspace_root": "/tmp", "fork_conversation_session": "fork",
    }.items():
        monkeypatch.setattr(runner, name, Mock(return_value=value))
    monkeypatch.setattr(runner, "_read_transcript_steps", lambda _: steps)
    monkeypatch.setattr(runner, "load_and_sync_session_state", lambda *args: ("Export", "/tmp/state", state, True))
    def save(_path, current, **updates):
        current.update(updates)
    monkeypatch.setattr(runner, "save_session_state", save)
    stop, resume, cleanup = Mock(), Mock(), Mock()
    monkeypatch.setattr(runner, "fail_safe_exit", stop)
    monkeypatch.setattr(runner, "emit_continue_response", resume)
    monkeypatch.setattr(runner, "cleanup_fork_session", cleanup)
    verify = Mock(return_value=LiteVerdict("FAIL", action="Execute the export and verify row counts."))
    monkeypatch.setattr(runner, "run_lite_verification", verify)
    return steps, state, stop, resume, cleanup, verify


def test_no_action_deferral_is_audited(audit):
    _, state, stop, resume, _, verify = audit
    runner.run_lite_stop_audit('{"conversationId":"fixture"}')
    verify.assert_called_once()
    resume.assert_called_once()
    assert state["lite_fail_count"] == 1 and not stop.called


def test_duplicate_rejection_is_replayed_without_new_strike(audit):
    _, state, stop, resume, _, verify = audit
    runner.run_lite_stop_audit('{"conversationId":"fixture"}')
    runner.run_lite_stop_audit('{"conversationId":"fixture"}')
    assert verify.call_count == 1 and resume.call_count == 2
    assert state["lite_fail_count"] == 1 and not stop.called


def test_same_length_changed_transcript_is_reaudited(audit):
    steps, state, _, _, _, verify = audit
    state["lite_evidence_hash"] = hashlib.sha256(json.dumps(steps, sort_keys=True).encode()).hexdigest()
    state["last_audited_line_count"] = len(steps)
    steps[-1]["content"] = "I changed the result."
    runner.run_lite_stop_audit('{"conversationId":"fixture"}')
    verify.assert_called_once()




def test_unavailable_verifier_does_not_create_strike_or_verified_badge(audit, monkeypatch):
    _, state, stop, resume, _, verify = audit
    verify.return_value = LiteVerdict("PASS", completion="unavailable")
    runner.run_lite_stop_audit('{"conversationId":"fixture"}')
    assert state["lite_status"] == "unavailable" and state["lite_fail_count"] == 0
    assert not resume.called
    stop.assert_called_once()


def test_unexpected_verifier_exception_releases_fork_and_marks_unavailable(audit):
    _, state, stop, resume, cleanup, verify = audit
    verify.side_effect = RuntimeError("unexpected provider response")
    runner.run_lite_stop_audit('{"conversationId":"fixture"}')
    assert state["lite_status"] == "unavailable" and state["sage_status"] == "idle"
    assert state["lite_fail_count"] == 0 and not resume.called
    cleanup.assert_called_once_with("fork", preserve_failed=False, verifier_output="")
    stop.assert_called_once()


def test_fork_failure_clears_previous_verified_status(audit, monkeypatch):
    _, state, stop, resume, _, verify = audit
    state["lite_status"] = "verified"
    monkeypatch.setattr(runner, "fork_conversation_session", lambda _: None)
    runner.run_lite_stop_audit('{"conversationId":"fixture"}')
    assert state["lite_status"] == "unavailable" and state["sage_status"] == "idle"
    assert not verify.called and not resume.called
    stop.assert_called_once()


def test_blocker_is_not_reported_as_verified_delivery(audit):
    _, state, stop, resume, _, verify = audit
    verify.return_value = LiteVerdict("PASS", completion="blocked", proof=["MFA_REQUIRED; interactive login required at portal"])
    runner.run_lite_stop_audit('{"conversationId":"fixture"}')
    assert state["lite_status"] == "blocked" and not resume.called
    stop.assert_called_once_with("Execution blocked with valid escalation.")


def test_structured_exit_survives_long_output_and_inter_agent_message():
    steps = [
        {"type": "USER_INPUT", "content": "Verify export"},
        {"type": "PLANNER_RESPONSE", "tool_calls": [{"id": "c1", "name": "run_command", "args": {"CommandLine": "export"}}]},
        {"type": "USER_INPUT", "source": "SUBAGENT", "content": "[Message] worker ready"},
        {"type": "TOOL_OUTPUT", "tool_call_id": "c1", "content": "progress " * 1000, "exit_code": -9},
        {"type": "PLANNER_RESPONSE", "content": "Done"},
    ]
    evidence = extract_turn_execution_provenance(steps)
    assert "Recorded exit code: -9" in evidence["tool_executions_summary"]
    assert "Recorded exit code: -9" in evidence["most_recent_terminal_cmd"]["output"]


def test_duplicate_tool_result_ids_do_not_overwrite_failure_with_success():
    calls = [{"id": "c1", "name": "run_command"}]
    outputs = [
        {"tool_call_id": "c1", "content": "failure", "exit_code": 1},
        {"tool_call_id": "c1", "content": "success", "exit_code": 0},
    ]
    assert match_tool_call_outputs(calls, outputs) == [(None, True)]


def test_duplicate_rejections_have_bounded_replay(audit):
    _, state, stop, resume, _, verify = audit
    for _ in range(runner.LITE_MAX_RETRIES + 3):
        runner.run_lite_stop_audit('{"conversationId":"fixture"}')
    assert verify.call_count == 1
    assert resume.call_count == runner.LITE_MAX_RETRIES + 1
    assert stop.called and state["lite_status"] == "unverified"


def test_explanatory_fail_fields_cannot_turn_rejection_into_unavailable():
    verdict = LiteVerdict.from_dict({"verdict": "FAIL", "action": "Fix failing tests", "comment": "exit 1", "proof": ["pytest failure"]})
    assert verdict.verdict == "FAIL" and verdict.action == "Fix failing tests"
    assert verdict.completion == "incomplete" and not verdict.proof and not verdict.comment


def test_injected_steering_preserves_original_request(capsys, monkeypatch):
    from sage.guards import emit_continue_response
    monkeypatch.setattr("sage.guards.release_lock", lambda: None)
    with pytest.raises(SystemExit):
        emit_continue_response("Run the missing export check.", is_post=True)
    message = json.loads(capsys.readouterr().out)["injectSteps"][0]["userMessage"]
    provenance = extract_turn_execution_provenance([
        {"type": "USER_INPUT", "content": "Export records"},
        {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "write_file", "args": {"path": "/tmp/export.csv"}}]},
        {"type": "USER_INPUT", "source": "USER", "content": message},
    ])
    assert provenance["primary_goal"] == "Export records"
    assert "/tmp/export.csv" in provenance["written_files"]
