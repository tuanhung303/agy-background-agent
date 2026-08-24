"""
Unit tests for advisor.goals module.
"""

from advisor.goals import (
    extract_initial_prompt,
    extract_pinned_goal,
    extract_revised_goal,
    format_goal_context,
    is_long_code_task,
    sync_goal_state,
)


def test_is_long_code_task_short_conversational():
    assert is_long_code_task("Hello, how are you?", tool_count=0) is False
    assert is_long_code_task("What is Python?", tool_count=0) is False


def test_is_long_code_task_with_code_tools():
    assert is_long_code_task(
        "Please fix the bug in triage",
        tool_count=4,
        tool_names={"replace_file_content", "run_command"},
    ) is True


def test_is_long_code_task_long_text_and_code_keywords():
    prompt = (
        "We need to implement a full feature for pinned goals and revised goals across "
        "multiple modules including advisor, runner, session_state, and write comprehensive tests."
    )
    assert is_long_code_task(prompt, tool_count=0) is True


def test_extract_initial_prompt():
    prompt_single = "[LATEST ACTIVE USER REQUEST]:\nImplement Pinned Goal"
    assert extract_initial_prompt(prompt_single) == "Implement Pinned Goal"

    prompt_history = (
        "SESSION HISTORY:\n"
        "- Prior request 1: Build the core framework and test suite\n"
        "- Prior request 2: Add logging support\n\n"
        "[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\n"
        "Now add benchmarking"
    )
    assert extract_initial_prompt(prompt_history) == "Build the core framework and test suite"


def test_extract_pinned_goal():
    prompt = "SESSION HISTORY:\n- Prior request 1: Fix bug in locking\n\n[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\nAdd tests"
    assert extract_pinned_goal(prompt) == "Fix bug in locking"


def test_extract_revised_goal():
    prompt = "SESSION HISTORY:\n- Prior request 1: Fix bug in locking\n\n[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\nAdd tests for locking"
    pinned = "Fix bug in locking"
    assert extract_revised_goal(prompt, pinned_goal=pinned) == "Add tests for locking"


def test_format_goal_context():
    pinned = "Build optimization harness"
    revised = "Add multi-turn goal tracking"
    derived = ["Write unit tests in tests/test_goals.py", "Add prompt examples"]

    formatted = format_goal_context(pinned, revised, derived)
    assert "PINNED GOAL (Baseline Objective):\nBuild optimization harness" in formatted
    assert "REVISED GOAL (Active In-Flight Scope):\nAdd multi-turn goal tracking" in formatted
    assert "DERIVED TASKS (Sub-workstreams):\n- Write unit tests" in formatted


def test_sync_goal_state_initial_pinned():
    state = {}
    prompt = "Implement a comprehensive refactor of stop_audit policies and test suite"
    sync_goal_state(state, prompt, tool_count=3, tool_names={"replace_file_content"})
    assert state.get("pinned_goal") == "Implement a comprehensive refactor of stop_audit policies and test suite"
    assert state.get("revised_goal") is None
    assert len(state.get("goal_revisions", [])) == 1


def test_sync_goal_state_revised_goal():
    state = {"pinned_goal": "Implement initial architecture"}
    prompt = (
        "SESSION HISTORY:\n"
        "- Prior request 1: Implement initial architecture\n\n"
        "[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:\n"
        "Also add stress testing and regression benchmarks"
    )
    sync_goal_state(state, prompt, tool_count=1)
    assert state.get("pinned_goal") == "Implement initial architecture"
    assert state.get("revised_goal") == "Also add stress testing and regression benchmarks"
    assert any(r["type"] == "scope_revision" for r in state.get("goal_revisions", []))
