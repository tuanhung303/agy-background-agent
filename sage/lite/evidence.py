"""sage.lite.evidence - Tool argument normalization, output attribution, and evidence formatting."""
import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

WRITE_TOOLS: Set[str] = {
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
    "edit_file",
    "create_file",
    "apply_diff",
    "patch",
    "modify_file",
    "write_file",
}

READ_TOOLS: Set[str] = {
    "view_file",
    "read_file",
    "cat",
    "open_file",
    "read_url_content",
    "read_browser_page",
    "read_resource",
    "list_dir",
    "find_by_name",
    "grep_search",
}

PATH_ARG_KEYS: Tuple[str, ...] = (
    "TargetFile", "target_file", "targetFile",
    "FilePath", "file_path", "filePath",
    "AbsolutePath", "absolute_path", "absolutePath",
    "TargetPath", "target_path", "targetPath",
    "path", "Path",
    "file", "File",
    "filename", "fileName", "file_name",
    "dest", "destination",
    "output_file", "output_path",
)

CMD_ARG_KEYS: Tuple[str, ...] = (
    "CommandLine", "command_line", "commandLine",
    "command", "Command",
    "cmd", "Cmd",
    "script", "Script",
)

IMG_ARG_KEYS: Tuple[str, ...] = (
    "ImageName", "image_name", "imageName",
    "ImagePath", "image_path", "imagePath",
    "image", "Image",
)

IMAGE_FILE_EXTENSIONS: Tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".bmp", ".tiff",
)

IMAGE_PATH_PATTERN = re.compile(
    r"(/[a-zA-Z0-9_\-\.\/]+\.(?:png|jpg|jpeg|webp|svg|gif|bmp|tiff))",
    re.IGNORECASE,
)


def normalize_tool_args(tool_args: Any, tool_name: str = "") -> Dict[str, Any]:
    """Normalizes string or dictionary tool arguments, parsing JSON or raw command strings."""
    if isinstance(tool_args, str):
        raw = tool_args.strip()
        if raw.startswith("{") and raw.endswith("}"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        name_lower = str(tool_name or "").strip().lower()
        if name_lower in {"run_command", "bash", "exec", "terminal", "cmd", "command"}:
            return {"CommandLine": raw}
        if name_lower in READ_TOOLS or name_lower in WRITE_TOOLS:
            return {"TargetFile": raw}
        return {"raw_arg": raw}
    if isinstance(tool_args, dict):
        return tool_args
    return {}


def extract_path_from_args(args: Dict[str, Any]) -> str:
    """Extracts target or absolute file path across supported key aliases."""
    for key in PATH_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().strip("\"'")
    return ""


def extract_command_from_args(args: Dict[str, Any]) -> str:
    """Extracts terminal command line string across supported key aliases."""
    for key in CMD_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().strip("\"'")
    return ""


def extract_image_from_args(args: Dict[str, Any]) -> str:
    """Extracts image name or image path across supported key aliases."""
    for key in IMG_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().strip("\"'")
    return ""


def clean_tool_output_snippet(raw_content: str, max_chars: int = 300) -> str:
    """Extracts a concise summary snippet preserving exit codes and trailing failures."""
    if not raw_content or not isinstance(raw_content, str):
        return ""
    lines = [
        l.strip()
        for l in raw_content.splitlines()
        if l.strip()
        and not l.strip().startswith(
            ("Created At:", "Completed At:", "The following is the entire", "Log:", "Task logs are available")
        )
    ]
    if not lines:
        return ""
    full_text = " ".join(lines).strip()
    if len(full_text) <= max_chars:
        return full_text

    exit_match = re.search(
        r"\b(?:exit(?:ed)?\s+(?:code|status)\s*[:=]?\s*(\d+)|exit\s+(\d+)|command\s+failed\s+with\s+exit\s+code\s+(\d+))\b",
        full_text,
        re.I,
    )
    exit_phrase = exit_match.group(0) if exit_match else ""

    fail_re = re.compile(
        r"\b(?:error|fail(?:ed|ure)?|exception|traceback|fatal|denied|cannot\s+find|command\s+not\s+found)\b",
        re.I,
    )
    trailing_fails = [l for l in lines[-5:] if fail_re.search(l)]
    tail_line = " ".join(trailing_fails).strip() if trailing_fails else lines[-1]
    if exit_phrase and exit_phrase.lower() not in tail_line.lower():
        tail_line = f"{tail_line} ({exit_phrase})"

    tail_budget = min(len(tail_line), int(max_chars * 0.45))
    tail_snip = tail_line[-tail_budget:].strip() if len(tail_line) > tail_budget else tail_line
    sep = " ... "
    head_budget = max(20, max_chars - len(tail_snip) - len(sep))
    head_snip = full_text[:head_budget].strip()
    return f"{head_snip}{sep}{tail_snip}"


def format_tool_result(step: Dict[str, Any]) -> str:
    """Preserve structured process status even when content is empty or misleading."""
    content = step.get("content", "")
    rendered = content if isinstance(content, str) else json.dumps(content)
    status = []
    for source in (step, step.get("metadata", {})):
        if not isinstance(source, dict):
            continue
        for key in ("exit_code", "exitCode", "returncode"):
            value = source.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                status.append(f"Recorded exit code: {value}")
        if source.get("is_error") is True or source.get("isError") is True:
            status.append("Recorded tool error: true")
    return "\n".join([rendered, *status]).strip()


def match_tool_call_outputs(
    stools: List[Dict[str, Any]],
    out_steps: List[Dict[str, Any]],
) -> List[Tuple[Optional[str], bool]]:
    """Attributes outputs to tool calls by ID when present, or positionally when 1-to-1 unambiguous.

    Returns a list of (raw_output, is_ambiguous) pairs corresponding to stools.
    """
    out_id_map: Dict[str, Dict[str, Any]] = {}
    duplicate_ids = set()
    for os_step in out_steps:
        oid = os_step.get("tool_call_id") or os_step.get("call_id")
        if not oid and isinstance(os_step.get("metadata"), dict):
            oid = os_step["metadata"].get("tool_call_id") or os_step["metadata"].get("call_id")
        if oid:
            if str(oid) in out_id_map:
                duplicate_ids.add(str(oid))
            out_id_map[str(oid)] = os_step

    has_any_id = bool(out_id_map) or any(
        bool(tc.get("id") or tc.get("tool_call_id") or tc.get("call_id"))
        for tc in stools
    )

    results: List[Tuple[Optional[str], bool]] = []
    for t_idx, tc in enumerate(stools):
        tc_id = str(tc.get("id") or tc.get("tool_call_id") or tc.get("call_id") or "")
        if tc_id and tc_id in out_id_map and tc_id not in duplicate_ids:
            raw_out = format_tool_result(out_id_map[tc_id])
            results.append((raw_out, False))
        elif not has_any_id:
            if len(stools) == len(out_steps):
                raw_out = format_tool_result(out_steps[t_idx])
                results.append((raw_out, False))
            else:
                results.append((None, True))
        else:
            results.append((None, True))
    return results
