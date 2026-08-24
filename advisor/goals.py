"""
advisor.goals - Anchor goal synthesis, in-flight revision tracking, and derived task management.
"""

import re
from typing import Dict, List, Optional, Set, Tuple

CODE_EDIT_KEYWORDS = (
    "fix", "refactor", "implement", "create", "test", "edit", "add",
    "feature", "build", "script", "update", "patch", "modify", "delete",
    "write", "develop", "optimize", "benchmark", "pipeline", "schema",
)
LONG_TASK_INDICATORS = (
    "plan", "e2e", "suite", "multi", "milestone", "step", "architect",
    "benchmark", "stress", "hardening", "phase", "comprehensive", "full",
    "overnight", "large", "system", "subagent", "workflow", "audit",
)
GOAL_MARKERS = (
    "[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:",
    "[LATEST ACTIVE USER REQUEST]:",
    "[TARGET GOAL]:",
)


def is_long_code_task(user_prompt: str, tool_count: int = 0, tool_names: Optional[Set[str]] = None) -> bool:
    """Heuristic detecting if the turn/session involves a long task requiring code changes."""
    text = str(user_prompt or "").lower()
    edit_tools = {
        "write_to_file", "replace_file_content", "multi_replace_file_content",
        "edit_file", "create_file", "modify_file", "write_file", "apply_diff",
    }
    has_code_tool = bool(tool_names and edit_tools.intersection(tool_names))
    has_code_kw = any(kw in text for kw in CODE_EDIT_KEYWORDS)
    has_long_kw = any(ind in text for ind in LONG_TASK_INDICATORS)
    has_many_tools = tool_count >= 5
    is_long_text = len(text.split()) >= 30

    if has_code_tool and (has_long_kw or has_many_tools or is_long_text):
        return True
    if has_code_kw and (has_long_kw or has_many_tools or is_long_text or tool_count >= 3):
        return True
    return False


def extract_initial_prompt(user_prompt: str) -> str:
    """Extracts the initial user prompt from SESSION HISTORY if available."""
    text = str(user_prompt or "").strip()
    if "SESSION HISTORY:" in text:
        m = re.search(r"Prior request 1:\s*(.+?)(?:\n- Prior request|\n\n\[LATEST|\Z)", text, re.DOTALL)
        if m:
            return m.group(1).strip()
    for marker in GOAL_MARKERS:
        idx = text.rfind(marker)
        if idx != -1:
            return text[idx + len(marker):].strip()
    return text


def extract_pinned_goal(user_prompt: str, limit: int = 500) -> str:
    """Extracts baseline pinned goal from initial turn or user prompt."""
    init = extract_initial_prompt(user_prompt)
    lines = [line.strip() for line in init.splitlines() if line.strip()]
    if not lines:
        return ""
    summary = " ".join(lines)
    return summary[:limit].strip()


extract_anchor_goal = extract_pinned_goal


def extract_revised_goal(user_prompt: str, pinned_goal: Optional[str] = None, limit: int = 500, **kwargs) -> Optional[str]:
    """Extracts revised goal when subsequent turns introduce new scope."""
    base_goal = pinned_goal or kwargs.get("anchor_goal")
    text = str(user_prompt or "").strip()
    if "SESSION HISTORY:" not in text:
        return None
    for marker in GOAL_MARKERS:
        idx = text.rfind(marker)
        if idx != -1:
            latest = text[idx + len(marker):].strip()
            latest_clean = " ".join(latest.splitlines())[:limit].strip()
            if base_goal and latest_clean and latest_clean != base_goal.strip()[:limit]:
                return latest_clean
    return None


def format_goal_context(
    pinned_goal: Optional[str] = None,
    revised_goal: Optional[str] = None,
    derived_tasks: Optional[List[str]] = None,
    **kwargs,
) -> str:
    """Formats structured goal block for Advisor and Auditor prompt injection."""
    base_goal = pinned_goal or kwargs.get("anchor_goal")
    sections = []
    if base_goal and base_goal.strip():
        sections.append(f"PINNED GOAL (Baseline Objective):\n{base_goal.strip()}")
    if revised_goal and revised_goal.strip() and revised_goal.strip() != (base_goal or "").strip():
        sections.append(f"REVISED GOAL (Active In-Flight Scope):\n{revised_goal.strip()}")
    if derived_tasks:
        clean_tasks = [t.strip() for t in derived_tasks if isinstance(t, str) and t.strip()]
        if clean_tasks:
            tasks_fmt = "\n".join(f"- {t}" for t in clean_tasks[:10])
            sections.append(f"DERIVED TASKS (Sub-workstreams):\n{tasks_fmt}")
    return "\n\n".join(sections)


def sync_goal_state(
    state: dict,
    user_prompt: str,
    tool_count: int = 0,
    tool_names: Optional[Set[str]] = None,
) -> dict:
    """Updates and synchronizes pinned_goal, revised_goal, and derived_tasks in session state."""
    pinned = state.get("pinned_goal") or state.get("anchor_goal")
    revised = state.get("revised_goal")
    derived = list(state.get("derived_tasks", []))
    revisions = list(state.get("goal_revisions", []))

    if not pinned:
        if is_long_code_task(user_prompt, tool_count, tool_names):
            pinned = extract_pinned_goal(user_prompt)
            if pinned:
                revisions.append({"type": "pinned_init", "goal": pinned})
    else:
        new_revised = extract_revised_goal(user_prompt, pinned_goal=pinned)
        if new_revised and new_revised != revised:
            revised = new_revised
            revisions.append({"type": "scope_revision", "goal": revised})

    state["pinned_goal"] = pinned
    state["anchor_goal"] = pinned
    state["revised_goal"] = revised
    state["derived_tasks"] = derived
    state["goal_revisions"] = revisions[-20:]
    return state
