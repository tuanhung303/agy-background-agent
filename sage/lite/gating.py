"""sage.lite.gating - Mutation gating and transcript distillation for Lite Mode."""
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from sage.command_policy import is_sage_command_safe
from sage.config import FILE_EDITING_TOOLS
from sage.guards import is_steering_message
from sage.sanitizer import clean_user_prompt

MUTATING_TOOLS: Set[str] = {
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
    "edit_file",
    "create_file",
    "apply_diff",
    "patch",
    "modify_file",
    "write_file",
    "generate_image",
}


def is_mutating_command(cmd_str: str) -> bool:
    """Checks if a shell command is mutating by evaluating against safe command policy."""
    if not cmd_str or not isinstance(cmd_str, str):
        return False
    is_safe, _ = is_sage_command_safe(cmd_str)
    return not is_safe


PLAN_OR_QA_PATTERNS = (
    r"^/(?:plan|qa|learn|drill|bro|teach|grill-me)\b",
    r"\b(?:make\s+a\s+plan\s+first|plan\s+first|brainstorm|create\s+a\s+plan|plan\s+the)\b",
)


def is_plan_or_qa_intent(prompt: str) -> bool:
    """Checks if the user prompt is intent on planning, QA, research, or brainstorming."""
    if not prompt or not isinstance(prompt, str):
        return False
    text = prompt.strip().lower()
    return any(re.search(pat, text, re.IGNORECASE) for pat in PLAN_OR_QA_PATTERNS)


def is_mutating_tool_call(tool_name: str, tool_args: Any) -> bool:
    """Checks if a tool invocation represents a file or state mutation."""
    name = str(tool_name or "").strip().lower()
    if name in MUTATING_TOOLS:
        if isinstance(tool_args, dict):
            target = str(
                tool_args.get("TargetFile")
                or tool_args.get("target_file")
                or tool_args.get("FilePath")
                or tool_args.get("path")
                or ""
            )
            # Artifacts in brain directory or .gemini/ are planning/reasoning artifacts, not codebase mutations
            if target and ("/brain/" in target or "/.gemini/" in target):
                return False
        return True
    if name in {"run_command", "bash", "exec", "terminal"}:
        cmd_str = ""
        if isinstance(tool_args, dict):
            cmd_str = str(
                tool_args.get("CommandLine")
                or tool_args.get("command")
                or tool_args.get("cmd")
                or ""
            )
        elif isinstance(tool_args, str):
            cmd_str = tool_args
        if cmd_str and is_mutating_command(cmd_str):
            return True
    return False


def extract_turn_mutations_and_context(
    steps: List[Dict[str, Any]],
) -> Tuple[bool, str, str, str]:
    """Inspects transcript steps to detect mutations and distill true prompt and output."""
    if not steps or not isinstance(steps, list):
        return False, "Empty transcript steps", "", ""

    has_mutation = False
    mutation_reason = "No mutating tool calls detected in turn"
    true_user_prompt = ""
    last_agent_output = ""

    # Find the most recent true USER_INPUT and subsequent agent steps
    turn_steps: List[Dict[str, Any]] = []
    for s in reversed(steps):
        if not isinstance(s, dict):
            continue
        turn_steps.append(s)
        if s.get("type") == "USER_INPUT":
            raw_content = str(s.get("content") or "")
            cleaned = clean_user_prompt(raw_content)
            if is_steering_message(cleaned):
                continue
            if not true_user_prompt and cleaned:
                true_user_prompt = cleaned
            break

    turn_steps.reverse()

    # Collect agent responses and inspect tool calls
    agent_responses: List[str] = []
    for s in turn_steps:
        stype = s.get("type")
        if stype == "PLANNER_RESPONSE":
            content = str(s.get("content") or "").strip()
            if content:
                agent_responses.append(content)
            tool_calls = s.get("tool_calls") or []
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        tname = str(
                            tc.get("name")
                            or tc.get("tool_name")
                            or tc.get("tool")
                            or ""
                        )
                        targs = (
                            tc.get("args")
                            or tc.get("arguments")
                            or tc.get("parameters")
                            or {}
                        )
                        if is_mutating_tool_call(tname, targs):
                            has_mutation = True
                            mutation_reason = f"Mutating tool call executed: {tname}"

    if agent_responses:
        last_agent_output = agent_responses[-1]

    # Fallback to last known prompt if true_user_prompt is still empty
    if not true_user_prompt:
        for s in steps:
            if isinstance(s, dict) and s.get("type") == "USER_INPUT":
                true_user_prompt = clean_user_prompt(str(s.get("content") or ""))

    return has_mutation, mutation_reason, true_user_prompt, last_agent_output
