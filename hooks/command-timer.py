#!/usr/bin/env python3
"""
command_timer.py - AGY Lifecycle Hook for Bash Execution Time Tracking & Guidance.

Events:
  1. PreToolUse (matcher: run_command): Records command start time (monotonic) and metadata.
  2. PostToolUse (matcher: run_command): Computes execution duration, categorizes into 5 tiers,
     and records sanitized feedback.
  3. PreInvocation: Injects bounded, sanitized ephemeral guidance into agent context.

Duration Tiers:
  - 0s - 10s: OK (no warning required)
  - 10s - 30s: Consider to improve next time (optimize piping, scoping, arguments)
  - 30s - 90s: Consider to adjust filter, recheck carefully before running next time
  - 90s - 900s (1.5m - 15m): Heavy long-running task recommendation (background execution, pagination, caching)
  - 15m+ (>900s): Forbidden / Limit Exceeded (hard limit violation)
"""

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Safe, per-user state directory with strict permissions
UID = os.getuid() if hasattr(os, "getuid") else 1000
RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR")
if RUNTIME_DIR and os.path.isdir(RUNTIME_DIR):
    STATE_DIR = Path(RUNTIME_DIR) / "agy_cmd_timer"
else:
    STATE_DIR = Path(tempfile.gettempdir()) / f"agy_cmd_timer_{UID}"

try:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
except Exception:
    pass

# Duration tier definitions: (max_seconds, tier_name, guidance_template)
TIERS = [
    (10.0, "OK", None),
    (
        30.0,
        "IMPROVE_NEXT_TIME",
        "Command took {dur:.1f}s (10s - 30s). Consider optimizing command efficiency, piping, or scoping next time."
    ),
    (
        90.0,
        "ADJUST_FILTER",
        "Command took {dur:.1f}s (30s - 90s). Consider adjusting filters, narrowing file paths/globs, or rechecking search queries before running next time."
    ),
    (
        900.0,
        "HEAVY_RECOMMEND_BACKGROUND",
        "Command took {dur:.1f}s (1.5m - 15m). Heavy task detected: strongly recommend offloading to background task (WaitMsBeforeAsync), using pagination/limits, caching results, or optimizing parallelization."
    ),
    (
        float("inf"),
        "FORBIDDEN_EXCEEDED_LIMIT",
        "Command exceeded 15 minutes limit ({dur:.1f}s). Running synchronous blocking commands >15m is forbidden. Use background execution or split the task."
    ),
]

MAX_FEEDBACK_ITEMS = 10
MAX_INJECTED_CHARS = 4000


def get_safe_hash(conv_id: Any) -> str:
    conv_str = str(conv_id or "default")
    return hashlib.sha256(conv_str.encode("utf-8", errors="ignore")).hexdigest()[:24]


def get_state_file(conv_id: Any, step_idx: Optional[int] = None) -> Path:
    h = get_safe_hash(conv_id)
    if step_idx is not None:
        try:
            safe_step = int(step_idx)
            return STATE_DIR / f"state_{h}_step_{safe_step}.json"
        except (ValueError, TypeError):
            pass
    return STATE_DIR / f"state_{h}_latest.json"


def get_feedback_file(conv_id: Any) -> Path:
    h = get_safe_hash(conv_id)
    return STATE_DIR / f"feedback_{h}.json"


def sanitize_command_text(cmd: Any) -> str:
    if not isinstance(cmd, str):
        return "run_command"
    # Strip ANSI escape codes
    cleaned = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", cmd)
    # Collapse newlines and whitespace
    cleaned = " ".join(cleaned.split())
    # Sanitize backticks to prevent Markdown injection
    cleaned = cleaned.replace("`", "'")
    # Redact common token patterns or sensitive keys
    cleaned = re.sub(r"(key|token|password|secret|auth)[=\s:]+[A-Za-z0-9_\-\.]{8,}", r"\1=REDACTED", cleaned, flags=re.IGNORECASE)
    # Limit length
    if len(cleaned) > 120:
        cleaned = cleaned[:117] + "..."
    return cleaned or "run_command"


def atomic_write_json(file_path: Path, data: Any) -> None:
    try:
        file_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp_file = file_path.with_suffix(f".tmp.{os.getpid()}_{time.monotonic_ns()}")
        temp_file.write_text(json.dumps(data), encoding="utf-8")
        temp_file.chmod(0o600)
        temp_file.replace(file_path)
    except Exception as exc:
        sys.stderr.write(f"[command_timer] atomic_write_json error: {exc}\n")


def read_stdin_json() -> Dict[str, Any]:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        sys.stderr.write(f"[command_timer] read_stdin_json error: {exc}\n")
        return {}


def classify_duration(dur: float) -> Tuple[str, Optional[str]]:
    for max_sec, tier, template in TIERS:
        if dur <= max_sec:
            guidance = template.format(dur=dur) if template else None
            return tier, guidance
    tier, template = TIERS[-1][1], TIERS[-1][2]
    return tier, template.format(dur=dur) if template else None


def handle_pre_tool(payload: Dict[str, Any]) -> None:
    try:
        conv_id = payload.get("conversationId", "default")
        step_idx = payload.get("stepIdx")
        tool_call = payload.get("toolCall") or {}
        args = tool_call.get("args") if isinstance(tool_call, dict) else {}
        cmd = args.get("CommandLine", "") if isinstance(args, dict) else ""

        now_mono = time.monotonic_ns()
        state = {
            "conversationId": str(conv_id),
            "stepIdx": step_idx,
            "startMonoNs": now_mono,
            "commandLine": sanitize_command_text(cmd),
        }

        if step_idx is not None:
            atomic_write_json(get_state_file(conv_id, step_idx), state)
        atomic_write_json(get_state_file(conv_id), state)
    except Exception as exc:
        sys.stderr.write(f"[command_timer] handle_pre_tool error: {exc}\n")

    # PreToolUse output contract requires decision field in AGY
    sys.stdout.write(json.dumps({"decision": "allow"}))


def handle_post_tool(payload: Dict[str, Any]) -> None:
    try:
        conv_id = payload.get("conversationId", "default")
        step_idx = payload.get("stepIdx")
        error = payload.get("error")

        now_mono = time.monotonic_ns()
        state_file = get_state_file(conv_id, step_idx) if step_idx is not None else get_state_file(conv_id)
        if not state_file.exists():
            state_file = get_state_file(conv_id)

        dur = 0.0
        cmd = "run_command"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
                start_mono = state.get("startMonoNs")
                if isinstance(start_mono, (int, float)):
                    dur = max(0.0, (now_mono - start_mono) / 1_000_000_000.0)
                cmd = state.get("commandLine", "run_command")
                state_file.unlink(missing_ok=True)
            except Exception as exc:
                sys.stderr.write(f"[command_timer] parse state error: {exc}\n")

        tier, guidance = classify_duration(dur)

        # If non-OK tier, store bounded feedback
        if guidance:
            feedback_file = get_feedback_file(conv_id)
            feedback_list: List[Dict[str, Any]] = []
            if feedback_file.exists():
                try:
                    loaded = json.loads(feedback_file.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        feedback_list = loaded
                except Exception:
                    feedback_list = []

            # Enforce max feedback items (P1 fix)
            if len(feedback_list) >= MAX_FEEDBACK_ITEMS:
                feedback_list = feedback_list[-(MAX_FEEDBACK_ITEMS - 1):]

            feedback_list.append({
                "command": cmd,
                "duration": round(dur, 2),
                "tier": tier,
                "guidance": guidance,
                "error": str(error) if error else None,
                "timestampMono": now_mono,
            })
            atomic_write_json(feedback_file, feedback_list)
    except Exception as exc:
        sys.stderr.write(f"[command_timer] handle_post_tool error: {exc}\n")

    # PostToolUse output contract
    sys.stdout.write(json.dumps({}))


def handle_pre_invocation(payload: Dict[str, Any]) -> None:
    inject_steps: List[Dict[str, Any]] = []
    try:
        conv_id = payload.get("conversationId", "default")
        feedback_file = get_feedback_file(conv_id)

        if feedback_file.exists():
            try:
                items = json.loads(feedback_file.read_text(encoding="utf-8"))
                if isinstance(items, list) and items:
                    messages: List[str] = []
                    for item in items:
                        tier = item.get("tier", "INFO")
                        cmd = item.get("command", "")
                        dur = item.get("duration", 0.0)
                        guidance = item.get("guidance", "")
                        prefix = "⚠️" if "IMPROVE" in tier or "FILTER" in tier else ("🚨" if "FORBIDDEN" in tier else "💡")
                        messages.append(
                            f"{prefix} [Command Timer - {tier}]\n"
                            f"- Command: `{cmd}`\n"
                            f"- Duration: {dur}s\n"
                            f"- Note: {guidance}"
                        )
                    if messages:
                        combined_msg = "\n\n".join(messages)
                        if len(combined_msg) > MAX_INJECTED_CHARS:
                            combined_msg = combined_msg[:MAX_INJECTED_CHARS - 40] + "\n... [Additional feedback truncated]"
                        inject_steps.append({
                            "ephemeralMessage": combined_msg
                        })
                feedback_file.unlink(missing_ok=True)
            except Exception as exc:
                sys.stderr.write(f"[command_timer] pre_invocation read feedback error: {exc}\n")

        # Cleanup stale state files (> 2 hours)
        try:
            now_time = time.time()
            for f in STATE_DIR.glob("*.json"):
                if now_time - f.stat().st_mtime > 7200:
                    f.unlink(missing_ok=True)
        except Exception:
            pass
    except Exception as exc:
        sys.stderr.write(f"[command_timer] handle_pre_invocation error: {exc}\n")

    # PreInvocation output contract
    sys.stdout.write(json.dumps({"injectSteps": inject_steps}))


def main() -> None:
    try:
        if len(sys.argv) < 2:
            sys.stdout.write(json.dumps({}))
            return

        action = sys.argv[1].lower()
        payload = read_stdin_json()

        if action in ("pre_tool", "pretooluse"):
            handle_pre_tool(payload)
        elif action in ("post_tool", "posttooluse"):
            handle_post_tool(payload)
        elif action in ("pre_invocation", "preinvocation"):
            handle_pre_invocation(payload)
        else:
            sys.stdout.write(json.dumps({}))
    except Exception as exc:
        sys.stderr.write(f"[command_timer] main fatal error: {exc}\n")
        sys.stdout.write(json.dumps({}))


if __name__ == "__main__":
    main()
