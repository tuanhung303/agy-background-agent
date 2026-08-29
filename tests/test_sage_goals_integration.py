"""
Integration tests for Pinned Goal, Revised Goal, and Derived Tasks in the Strategic Advisor.
"""

import json
from unittest.mock import patch

from sage.sage import build_sage_prompt, parse_sage_output
from sage.policies import sage_flow
from sage.triage import classify_advice


def test_advisor_prompt_includes_pinned_and_revised_goal():
    prompt = build_sage_prompt(
        conv_id="test_conv_123",
        user_prompt="Build core engine",
        agent_steps_summary="Step 1: wrote code",
        is_update=False,
        pinned_goal="Build core engine",
        revised_goal="Build core engine and add benchmarks",
        derived_tasks=["Add tests/test_bench.py"],
    )
    assert "PINNED GOAL (Baseline Objective):\nBuild core engine" in prompt
    assert "REVISED GOAL (Active In-Flight Scope):\nBuild core engine and add benchmarks" in prompt
    assert "DERIVED TASKS (Sub-workstreams):\n- Add tests/test_bench.py" in prompt


def test_advisor_prompt_update_mode_includes_goal_context():
    prompt = build_sage_prompt(
        conv_id="test_conv_123",
        user_prompt="Add benchmarks",
        agent_steps_summary="Step 2: running benchmarks",
        is_update=True,
        pinned_goal="Build core engine",
        revised_goal="Add benchmarks",
    )
    assert "SAGE UPDATE" in prompt
    assert "PINNED GOAL (Baseline Objective):\nBuild core engine" in prompt
    assert "REVISED GOAL (Active In-Flight Scope):\nAdd benchmarks" in prompt


def test_parse_advisor_output_normalizes_goal_fields():
    raw_json = json.dumps({
        "status": "watchout",
        "category": "scope_drift",
        "action": "Run `pytest tests/test_core.py`",
        "evidence": "Pinned goal unit tests missing",
        "confidence": 0.9,
        "guidance": "Validate pinned goal before advancing derived tasks.",
        "pinned_goal": "Build core engine",
        "revised_goal": "Add secondary UI",
        "derived_tasks": ["Create UI widget", "Add styles"],
        "goal_status": "drifted",
    })
    parsed = parse_sage_output(raw_json)
    assert parsed["status"] == "watchout"
    assert parsed["category"] == "scope_drift"
    assert parsed["pinned_goal"] == "Build core engine"
    assert parsed["revised_goal"] == "Add secondary UI"
    assert parsed["derived_tasks"] == ["Create UI widget", "Add styles"]
    assert parsed["goal_status"] == "drifted"


def test_classify_advice_propagates_goal_fields():
    ver_res = {
        "status": "watchout",
        "category": "scope_drift",
        "action": "Run `pytest tests/test_core.py`",
        "evidence": "Pinned goal tests missing",
        "confidence": 0.92,
        "guidance": "Ensure pinned goal compliance.",
        "pinned_goal": "Baseline engine",
        "revised_goal": "Added feature",
        "goal_status": "revised",
    }
    decision = classify_advice(ver_res, seen_advice={})
    assert decision["decision"] == "watchout"
    assert decision["pinned_goal"] == "Baseline engine"
    assert decision["revised_goal"] == "Added feature"
    assert decision["goal_status"] == "revised"
    assert decision["category"] == "scope_drift"
    assert "pinned goal tests missing. run `pytest tests/test_core.py`" in decision["text"]


@patch("sage.policies.has_new_user_activity", return_value=False)
@patch("sage.policies.extract_session_and_turn_data", return_value=(None, None, None, 15, None, None, None, 0))
@patch("sage.policies.evaluate_mid_turn_progress")
def test_advisor_flow_propagates_goal_state(mock_eval, mock_extract, mock_activity):
    mock_eval.return_value = {
        "status": "watchout",
        "category": "scope_drift",
        "action": "Run tests",
        "confidence": 0.9,
        "pinned_goal": "Engine",
        "revised_goal": "New feature",
    }
    state = {
        "pinned_goal": "Engine",
        "revised_goal": "New feature",
        "last_verified_tools": 0,
    }
    res = sage_flow(
        "midturn",
        conv_id="c1",
        transcript_path="/dummy",
        clean_prompt="Do work",
        initial_line_count=0,
        total_tool_calls=15,
        turn_tool_names={"write_to_file"},
        user_prompt="Do work",
        agent_steps=["Step 1"],
        git_diff="",
        state=state,
        forced=True,
    )
    assert res["action"] == "emit"
    assert res["decision"] == "watchout"
    assert res["pinned_goal"] == "Engine"
    assert res["revised_goal"] == "New feature"


def test_first_action_pinned_goal_emitted_on_complex_task():
    ver_res = {
        "status": "on_track",
        "task_complexity": "complex_code",
        "category": "pinned_goal",
        "action": "Implement core parser in advisor/goals.py",
        "guidance": "Pin baseline goal before multi-file modifications.",
        "pinned_goal": "Refactor optimizer and add AST invariants",
        "confidence": 0.95,
    }
    decision = classify_advice(ver_res, seen_advice={}, anchor_emitted=False)
    assert decision["decision"] == "watchout"
    assert decision["pinned_emitted"] is True
    assert "refactor optimizer and add AST invariants. next: implement core parser in advisor/goals.py" in decision["text"]


def test_simple_qa_task_complexity_suppresses_pinned_noise():
    ver_res = {
        "status": "on_track",
        "task_complexity": "simple_qa",
        "category": "general",
        "pinned_goal": "Answer question about config",
    }
    decision = classify_advice(ver_res, seen_advice={}, anchor_emitted=False)
    assert decision["decision"] == "hold"
    assert "text" not in decision or not decision.get("text")


def test_pinned_goal_triggers_delegate_command_at_pin():
    from sage.facilitation import immediate_delegate_message
    msg = immediate_delegate_message(pinned_goal="Refactor optimizer and add AST invariants")
    assert "[CMD·delegate" in msg
    assert "delegate execution+tests to subagents via invoke_subagent" in msg


