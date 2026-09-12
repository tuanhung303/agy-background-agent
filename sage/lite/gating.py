"""sage.lite.gating - Mutation gating and transcript distillation for Lite Mode."""
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from sage.command_policy import is_sage_command_safe
from sage.lite.evidence import (
    IMAGE_FILE_EXTENSIONS,
    IMAGE_PATH_PATTERN,
    READ_TOOLS,
    WRITE_TOOLS,
    clean_tool_output_snippet,
    extract_command_from_args,
    extract_image_from_args,
    extract_path_from_args,
    match_tool_call_outputs,
    normalize_tool_args,
)
from sage.sanitizer import sanitize_tool_output
from sage.user_context import (
    extract_substantive_user_context,
    is_real_user_step,
)

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






def is_mutating_tool_call(tool_name: str, tool_args: Any) -> bool:
    """Checks if a tool invocation represents a file or state mutation."""
    name = str(tool_name or "").strip().lower()
    args = normalize_tool_args(tool_args, name)
    if name in MUTATING_TOOLS:
        target = extract_path_from_args(args)
        if target and ("/brain/" in target or "/.gemini/" in target):
            return False
        return True
    if name in {"run_command", "bash", "exec", "terminal", "cmd", "command"}:
        cmd_str = extract_command_from_args(args)
        if cmd_str and is_mutating_command(cmd_str):
            return True
    return False


_clean_tool_output_snippet = clean_tool_output_snippet


def extract_turn_execution_provenance(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extracts mutations, timestamps, tool calls, tool outputs, and provenance artifacts for the current turn."""
    empty_res = {
        "has_mutation": False,
        "mutation_reason": "Empty transcript steps",
        "true_user_prompt": "",
        "latest_user_prompt": "",
        "primary_goal": "",
        "has_compaction": False,
        "last_agent_output": "",
        "turn_start_time": 0.0,
        "written_files": [],
        "inspected_files": [],
        "executed_commands": [],
        "generated_images": [],
        "image_files": [],
        "tool_executions_summary": "(No tool calls executed in current turn)",
        "has_asked_question": False,
        "most_recent_terminal_cmd": None,
    }
    if not steps or not isinstance(steps, list):
        return empty_res

    user_ctx = extract_substantive_user_context(steps)
    true_user_prompt = user_ctx["true_user_prompt"]
    latest_user_prompt = user_ctx["latest_user_prompt"]
    primary_goal = user_ctx["primary_goal"]
    has_compaction = user_ctx["has_compaction"]
    turn_start_time = user_ctx["turn_start_time"]

    has_mutation = False
    mutation_reason = "No mutating tool calls detected in turn"
    has_asked_question = False
    last_agent_output = ""
    written_files = set()
    inspected_files = set()
    executed_commands = []
    generated_images = set()
    image_files = set()
    tool_summary_lines = []
    most_recent_terminal_cmd: Optional[Dict[str, Any]] = None

    turn_steps: List[Dict[str, Any]] = []
    for s in reversed(steps):
        if not isinstance(s, dict):
            continue
        turn_steps.append(s)
        if is_real_user_step(s):
            break
    turn_steps.reverse()

    agent_responses: List[str] = []
    for idx, s in enumerate(turn_steps):
        stype = s.get("type")
        if stype != "PLANNER_RESPONSE":
            continue
        content = str(s.get("content") or "").strip()
        if content:
            agent_responses.append(content)

        raw_tool_calls = s.get("tool_calls") or []
        stools = [tc for tc in raw_tool_calls if isinstance(tc, dict)]
        if not stools:
            continue

        out_steps: List[Dict[str, Any]] = []
        for next_s in turn_steps[idx + 1:]:
            if not isinstance(next_s, dict):
                continue
            ntype = str(next_s.get("type") or "").upper()
            if ntype == "PLANNER_RESPONSE" or is_real_user_step(next_s):
                break
            if ntype in ("GENERIC", "SYSTEM_MESSAGE", "EPHEMERAL_MESSAGE", "TOOL_RESPONSE", "TOOL_OUTPUT") or next_s.get("tool_call_id") or next_s.get("call_id"):
                out_steps.append(next_s)

        matched_outputs = match_tool_call_outputs(stools, out_steps)

        for t_idx, tc in enumerate(stools):
            tname = str(tc.get("name") or tc.get("tool_name") or tc.get("tool") or "")
            tname_lower = tname.strip().lower()
            targs = normalize_tool_args(tc.get("args") or tc.get("arguments") or tc.get("parameters") or {}, tname)
            if tname_lower in ("ask_question", "ask_user"):
                has_asked_question = True
            if is_mutating_tool_call(tname, targs):
                has_mutation = True
                mutation_reason = f"Mutating tool call executed: {tname}"

            target_fp = extract_path_from_args(targs)
            cmd_str = extract_command_from_args(targs)
            img_name = extract_image_from_args(targs)

            if tname_lower in WRITE_TOOLS:
                if target_fp:
                    written_files.add(target_fp)
                    written_files.add(os.path.basename(target_fp))
                    if target_fp.lower().endswith(IMAGE_FILE_EXTENSIONS):
                        image_files.add(target_fp)
            elif tname_lower in READ_TOOLS:
                if target_fp:
                    inspected_files.add(target_fp)
                    inspected_files.add(os.path.basename(target_fp))
            elif target_fp:
                if is_mutating_tool_call(tname, targs):
                    written_files.add(target_fp)
                    written_files.add(os.path.basename(target_fp))
                else:
                    inspected_files.add(target_fp)

            if cmd_str:
                executed_commands.append(cmd_str)
                for match in IMAGE_PATH_PATTERN.findall(cmd_str):
                    image_files.add(match)

            if img_name:
                generated_images.add(img_name)
                image_files.add(img_name)

            raw_out, is_ambiguous = matched_outputs[t_idx]
            output_snippet = ""
            if raw_out is not None:
                output_snippet = clean_tool_output_snippet(raw_out, max_chars=300)
                for match in IMAGE_PATH_PATTERN.findall(raw_out):
                    image_files.add(match)
            elif is_ambiguous and len(stools) > 1:
                output_snippet = "output unknown: ambiguous multi-call attribution"

            if cmd_str:
                cmd_out = sanitize_tool_output(clean_tool_output_snippet(raw_out, 1200), max_chars=1200, max_line_len=1200) if raw_out else ""
                most_recent_terminal_cmd = {
                    "tool": tname,
                    "command": cmd_str,
                    "output": cmd_out,
                }
                entry = f"- {tname}: `{cmd_str[:500]}`"
                if output_snippet:
                    entry += f" -> [{output_snippet}]"
                tool_summary_lines.append(entry)
            elif target_fp:
                entry = f"- {tname}: `{target_fp}`"
                if output_snippet:
                    entry += f" -> [{output_snippet}]"
                tool_summary_lines.append(entry)
            elif img_name:
                tool_summary_lines.append(f"- {tname}: `{img_name}`")
            else:
                entry = f"- {tname}"
                if output_snippet:
                    entry += f" -> [{output_snippet}]"
                tool_summary_lines.append(entry)

    if agent_responses:
        last_agent_output = agent_responses[-1]
        for match in IMAGE_PATH_PATTERN.findall(last_agent_output):
            image_files.add(match)

    tool_exec_summary = "\n".join(tool_summary_lines) if tool_summary_lines else "(No tool calls executed in current turn)"
    return {
        "has_mutation": has_mutation,
        "mutation_reason": mutation_reason,
        "true_user_prompt": true_user_prompt,
        "latest_user_prompt": latest_user_prompt,
        "primary_goal": primary_goal,
        "has_compaction": has_compaction,
        "last_agent_output": last_agent_output,
        "turn_start_time": turn_start_time,
        "written_files": sorted(list(written_files)),
        "inspected_files": sorted(list(inspected_files)),
        "executed_commands": executed_commands,
        "generated_images": sorted(list(generated_images)),
        "image_files": sorted(list(image_files)),
        "tool_executions_summary": tool_exec_summary,
        "has_asked_question": has_asked_question,
        "most_recent_terminal_cmd": most_recent_terminal_cmd,
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
