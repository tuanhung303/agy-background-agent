"""Model-produced steering, integrity reconsideration, and absence of canned repairs."""
import json
import subprocess
from unittest.mock import Mock

import pytest

from sage.lite import verifier
from sage.lite.prompt import JUDGMENT_GUIDANCE


@pytest.fixture
def model(monkeypatch):
    monkeypatch.delenv("AGY_LITE_MOCK_VERDICT", raising=False)
    monkeypatch.setattr(verifier, "ensure_isolated_home", lambda: "/tmp/isolated-fixture")
    monkeypatch.setattr(verifier, "LITE_MODEL_CANDIDATES", ["Gemini 3.8 Flash (Medium)"])
    run = Mock()
    monkeypatch.setattr(verifier.subprocess, "run", run)
    return run


def result(verdict, proof=None, action=""):
    return subprocess.CompletedProcess([], 0, json.dumps({
        "verdict": verdict, "completion": "complete" if verdict == "PASS" else "incomplete",
        "action": action, "comment": "Evidence inspected" if verdict == "PASS" else "",
        "proof": proof or [],
    }))


def test_planning_steering_is_model_output_not_a_fixed_interview(model):
    action = "Update the retention table to cover the offline archive before finalizing."
    model.return_value = result("FAIL", action=action)
    verdict = verifier.run_lite_verification("parent", "fork", "/plan retention; choices are supplied", "Done")
    assert verdict.action == action and model.call_count == 1


def test_proof_reconsideration_uses_same_context_and_only_model_action(model):
    action = "Open the actual exported figure and compare its scale against the source values."
    model.side_effect = [result("PASS", ["Inspected /nonexistent-steering-fixture/chart.png"]), result("FAIL", action=action)]
    verdict = verifier.run_lite_verification(
        "parent", "fork", "Verify chart scale", "Chart is ready",
        turn_execution_summary="chart export exited 1; later lint exited 0",
    )
    assert verdict.action == action and model.call_count == 2
    prompts = [call.args[0][call.args[0].index("-p") + 1] for call in model.call_args_list]
    for prompt in prompts:
        assert prompt.count(JUDGMENT_GUIDANCE.strip()) == 1
        assert "chart export exited 1; later lint exited 0" in prompt
    assert "proof_integrity_diagnostic" in prompts[1]
    assert "missing or empty" in prompts[1]
    assert "Verify chart scale" in prompts[1] and "Chart is ready" in prompts[1]


def test_reconsideration_can_correct_its_own_citation_without_inventing_work(model):
    model.side_effect = [result("PASS", ["Inspected /nonexistent-steering-fixture/figure.png"]),
                         result("PASS", ["Recorded DOM measurement confirms no clipping"])]
    verdict = verifier.run_lite_verification("parent", "fork", "Verify clipping using the recorded DOM inspection", "Done")
    assert verdict.completion == "complete" and not verdict.action
    assert verdict.proof == ["Recorded DOM measurement confirms no clipping"]


def test_repeated_invalid_proof_is_unavailable_without_generic_repair(model):
    model.return_value = result("PASS", ["Inspected /nonexistent-steering-fixture/figure.png"])
    verdict = verifier.run_lite_verification("parent", "fork", "Verify figure", "Done")
    assert model.call_count == 2
    assert verdict.completion == "unavailable" and not verdict.action and not verdict.proof


def test_reconsideration_shares_original_deadline(model, monkeypatch):
    monkeypatch.setattr(verifier.time, "monotonic", Mock(side_effect=[100, 101, 101, 103, 104, 104, 105]))
    model.side_effect = [result("PASS", ["Inspected /nonexistent-steering-fixture/chart.png"]),
                         result("FAIL", action="Check the failed figure export before inspecting it.")]
    verdict = verifier.run_lite_verification("parent", "fork", "Verify figure", "Done", timeout=20)
    assert verdict.verdict == "FAIL"
    assert [call.kwargs["timeout"] for call in model.call_args_list] == [19, 16]


@pytest.mark.parametrize("raw", ["", "not JSON", '{"verdict":"FAIL"}', '{"verdict":"MAYBE"}'])
def test_model_failure_never_invents_a_corrective_action(model, raw):
    model.return_value = subprocess.CompletedProcess([], 0, raw)
    verdict = verifier.run_lite_verification("parent", "fork", "Export", "Done")
    assert verdict.completion == "unavailable" and not verdict.action
