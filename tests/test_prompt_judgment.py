"""Prompt wiring and evaluation integrity; these tests do not claim LLM judgment quality."""
import json
import subprocess
from unittest.mock import patch

import pytest

from sage.lite.prompt import JUDGMENT_GUIDANCE, build_lite_verifier_prompt
from sage.lite.verifier import generate_contextual_reject_action
from scripts.verify.prompt.verify_adversarial import load_cases, score_output
from scripts.verify.prompt.verify_live import evaluate_case


def test_current_evidence_is_preserved_without_inventing_clean_exit():
    prompt = build_lite_verifier_prompt(
        'Check payload {"id": 1}', "No outcome recorded.",
        turn_provenance={"most_recent_terminal_cmd": {"command": "restore backup", "output": ""}},
    )
    assert 'Check payload {"id": 1}' in prompt
    assert "No output recorded; exit status unknown" in prompt
    assert "No output or clean exit" not in prompt
    assert prompt.count(JUDGMENT_GUIDANCE.strip()) == 1


def test_judgment_reaches_rejection_synthesis_without_losing_failure_evidence(monkeypatch):
    monkeypatch.delenv("AGY_LITE_MOCK_VERDICT", raising=False)
    result = subprocess.CompletedProcess([], 0, "Rerun the restore path after the change.", "")
    with patch("sage.lite.verifier.ensure_isolated_home", return_value="/tmp/test-home"), patch(
        "sage.lite.verifier.subprocess.run", return_value=result,
    ) as execute:
        action = generate_contextual_reject_action(
            "fixture", "Verify restore", "Fixed now", "Restore state not verified",
            turn_execution_summary="restore exit 1; edit restore.sh; lint exit 0",
            most_recent_terminal_cmd={"command": "lint restore.sh", "output": "exit 0"},
        )
    command = execute.call_args.args[0]
    prompt = command[command.index("-p") + 1]
    assert JUDGMENT_GUIDANCE.strip() in prompt
    assert "restore exit 1; edit restore.sh; lint exit 0" in prompt
    assert "Most recent terminal command: `lint restore.sh`" in prompt
    assert "Restore state not verified" in prompt
    assert "do NOT prescribe re-running that exact check" not in prompt
    assert action == result.stdout


@pytest.mark.parametrize("raw", ["", "not json", "{}", '{"verdict":"MAYBE"}', "[]"])
def test_evaluation_never_coerces_missing_or_invalid_verdict_to_pass(raw):
    _, status = score_output(raw, "PASS")
    assert status in ("invalid_json", "invalid_verdict")


def test_evaluation_counts_false_positives_and_missed_failures_separately():
    fail = json.dumps({"verdict": "FAIL", "action": "Verify restore counts.", "comment": "", "proof": []})
    passed = json.dumps({"verdict": "PASS", "action": "", "comment": "Restore verified.", "proof": ["restore rows=12"]})
    assert score_output(fail, "PASS")[1] == "false_rejection"
    assert score_output(passed, "FAIL")[1] == "missed_failure"
    assert score_output(passed, "PASS")[1] == "matched"
    assert score_output(fail, "FAIL")[1] == "matched"


def test_evaluation_rejects_empty_proof_or_missing_corrective_action():
    assert score_output(json.dumps({"verdict": "PASS", "action": "", "comment": "Done", "proof": []}), "PASS")[1] == "unsupported_pass"
    assert score_output(json.dumps({"verdict": "FAIL", "action": "", "comment": "", "proof": []}), "FAIL")[1] == "invalid_fail"


def test_evaluation_checks_process_status_and_hides_expected_label():
    case = load_cases()[0]
    stdout = json.dumps({"verdict": "PASS", "action": "", "comment": "Access verified.", "proof": ["GET /profile -> 200"]})
    with patch("scripts.verify.prompt.verify_live.subprocess.run", return_value=subprocess.CompletedProcess([], 1, stdout, "failure")) as execute:
        record = evaluate_case(case, build_lite_verifier_prompt, "test-model", 1, {}, "/tmp")
    assert record["status"] == "execution_error"
    assert record["returncode"] == 1
    command = execute.call_args.args[0]
    prompt = command[command.index("-p") + 1]
    assert case["id"] not in prompt
    assert '"expected"' not in prompt
    assert case["request"] in prompt and case["evidence"] in prompt


def test_evaluation_records_timeout_instead_of_pass():
    with patch("scripts.verify.prompt.verify_live.subprocess.run", side_effect=subprocess.TimeoutExpired("agy", 1, b"partial")):
        record = evaluate_case(load_cases()[0], build_lite_verifier_prompt, "test-model", 1, {}, "/tmp")
    assert record["status"] == "timeout"
    assert record["stdout"] == "partial"
