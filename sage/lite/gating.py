"""sage.lite.gating - Mutation gating and transcript distillation for Lite Mode."""
import os
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
    r"(?:^|\s)/(?:plan|qa|learn|drill|bro|teach|grill-me|grill_me|grill|boost)\b",
    r"<GRILL_ME>",
    r"\b(?:make\s+a\s+plan\s+first|plan\s+first|brainstorm|create\s+a\s+plan|plan\s+the|research|search\s+for|check\s+the\s+slides|find\s+where|find\s+all|investigate|audit\s+the|interview\s+me|ask\s+clarifying\s+questions|grill\s+me)\b",
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


def _parse_ts_to_epoch(ts_str: Any) -> float:
    """Safely converts ISO timestamp string to epoch float timestamp."""
    if not ts_str:
        return 0.0
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return float(dt.timestamp())
    except Exception:
        return 0.0


IMAGE_FILE_EXTENSIONS: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".bmp", ".tiff")
IMAGE_PATH_PATTERN = re.compile(r"(/[a-zA-Z0-9_\-\.\/]+\.(?:png|jpg|jpeg|webp|svg|gif|bmp|tiff))", re.IGNORECASE)


def _clean_tool_output_snippet(raw_content: str) -> str:
    """Extracts a concise, non-empty summary snippet from tool result content."""
    if not raw_content or not isinstance(raw_content, str):
        return ""
    meaningful = [l.strip() for l in raw_content.splitlines() if l.strip() and not l.strip().startswith(("Created At:", "Completed At:", "The following is the entire", "Log:", "Task logs are available"))]
    snippet = " ".join(meaningful).strip()
    return (snippet[:177] + "...") if len(snippet) > 180 else snippet


def extract_turn_execution_provenance(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extracts mutations, timestamps, tool calls, tool outputs, and provenance artifacts for the current turn."""
    empty_res = {
        "has_mutation": False, "mutation_reason": "Empty transcript steps",
        "true_user_prompt": "", "last_agent_output": "", "turn_start_time": 0.0,
        "written_files": [], "executed_commands": [], "generated_images": [],
        "image_files": [], "tool_executions_summary": "(No tool calls executed in current turn)",
    }
    if not steps or not isinstance(steps, list):
        return empty_res

    has_mutation, mutation_reason = False, "No mutating tool calls detected in turn"
    true_user_prompt, last_agent_output, turn_start_time = "", "", 0.0
    written_files, executed_commands = set(), []
    generated_images, image_files, tool_summary_lines = set(), set(), []

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
                turn_start_time = _parse_ts_to_epoch(s.get("created_at"))
            break
    turn_steps.reverse()

    agent_responses: List[str] = []
    for idx, s in enumerate(turn_steps):
        stype = s.get("type")
        if stype == "PLANNER_RESPONSE":
            content = str(s.get("content") or "").strip()
            if content:
                agent_responses.append(content)
            for tc in (s.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                tname = str(tc.get("name") or tc.get("tool_name") or tc.get("tool") or "")
                targs = tc.get("args") or tc.get("arguments") or tc.get("parameters") or {}
                if is_mutating_tool_call(tname, targs):
                    has_mutation, mutation_reason = True, f"Mutating tool call executed: {tname}"

                target_fp, cmd_str, img_name, output_snippet = "", "", "", ""
                if isinstance(targs, dict):
                    raw_target = str(targs.get("TargetFile") or targs.get("target_file") or targs.get("FilePath") or targs.get("AbsolutePath") or targs.get("path") or "").strip().strip("\"'")
                    if raw_target:
                        target_fp = raw_target
                        written_files.add(raw_target)
                        written_files.add(os.path.basename(raw_target))
                        if raw_target.lower().endswith(IMAGE_FILE_EXTENSIONS):
                            image_files.add(raw_target)

                    raw_cmd = str(targs.get("CommandLine") or targs.get("command") or targs.get("cmd") or "").strip().strip("\"'")
                    if raw_cmd:
                        cmd_str = raw_cmd
                        executed_commands.append(raw_cmd)
                        for match in IMAGE_PATH_PATTERN.findall(raw_cmd):
                            image_files.add(match)

                    raw_img = str(targs.get("ImageName") or targs.get("image_name") or "").strip().strip("\"'")
                    if raw_img:
                        img_name = raw_img
                        generated_images.add(raw_img)
                        image_files.add(raw_img)

                if idx + 1 < len(turn_steps):
                    next_step = turn_steps[idx + 1]
                    if next_step.get("type") in ("GENERIC", "SYSTEM_MESSAGE", "EPHEMERAL_MESSAGE"):
                        raw_out = str(next_step.get("content") or "")
                        output_snippet = _clean_tool_output_snippet(raw_out)
                        for match in IMAGE_PATH_PATTERN.findall(raw_out):
                            image_files.add(match)

                if cmd_str:
                    entry = f"- {tname}: `{cmd_str[:140]}`"
                    if output_snippet:
                        entry += f" -> [{output_snippet[:100]}]"
                    tool_summary_lines.append(entry)
                elif target_fp:
                    entry = f"- {tname}: `{target_fp}`"
                    if output_snippet:
                        entry += f" -> [{output_snippet[:100]}]"
                    tool_summary_lines.append(entry)
                elif img_name:
                    tool_summary_lines.append(f"- {tname}: `{img_name}`")
                else:
                    entry = f"- {tname}"
                    if output_snippet:
                        entry += f" -> [{output_snippet[:100]}]"
                    tool_summary_lines.append(entry)

    if agent_responses:
        last_agent_output = agent_responses[-1]
        for match in IMAGE_PATH_PATTERN.findall(last_agent_output):
            image_files.add(match)

    if not true_user_prompt:
        for s in steps:
            if isinstance(s, dict) and s.get("type") == "USER_INPUT":
                true_user_prompt = clean_user_prompt(str(s.get("content") or ""))
                if not turn_start_time:
                    turn_start_time = _parse_ts_to_epoch(s.get("created_at"))

    tool_exec_summary = "\n".join(tool_summary_lines) if tool_summary_lines else "(No tool calls executed in current turn)"
    return {
        "has_mutation": has_mutation, "mutation_reason": mutation_reason,
        "true_user_prompt": true_user_prompt, "last_agent_output": last_agent_output,
        "turn_start_time": turn_start_time, "written_files": list(written_files),
        "executed_commands": executed_commands, "generated_images": list(generated_images),
        "image_files": list(image_files), "tool_executions_summary": tool_exec_summary,
    }


def extract_turn_mutations_and_context(steps: List[Dict[str, Any]]) -> Tuple[bool, str, str, str]:
    """Inspects transcript steps to detect mutations and distill true prompt and output."""
    prov = extract_turn_execution_provenance(steps)
    return (
        prov["has_mutation"],
        prov["mutation_reason"],
        prov["true_user_prompt"],
        prov["last_agent_output"],
    )

