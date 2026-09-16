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
# A start record older than this cannot belong to a command finishing now
# (orphaned pre_tool, reboot resetting the monotonic clock). Reconstructing a
# duration from one fabricates bogus FORBIDDEN_EXCEEDED_LIMIT violations.
MAX_TRACKED_SECONDS = 86400.0


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


def classify_command_guidance(cmd: str, dur: float) -> Tuple[str, Optional[str], Optional[str]]:
    """Classifies execution duration using command intent, pattern heuristics, and anti-pattern detection.

    Returns:
        (tier, note, tip)
    """
    if dur > 900.0:
        return (
            "FORBIDDEN_EXCEEDED_LIMIT",
            f"Command exceeded 15 minutes limit ({dur:.1f}s). Running synchronous blocking commands >15m is forbidden.",
            "Use background execution (WaitMsBeforeAsync) or split the task into independent steps.",
        )

    c = cmd.strip() if cmd else ""

    # 1. Search operations (find, grep, rg, fd)
    if re.search(r"\b(grep|rg|find|fd|locate|ack)\b", c):
        if dur <= 5.0:
            return "OK", None, None
        tier = "SEARCH_SLOW_UNINDEXED" if dur <= 20.0 else "SEARCH_HEAVY_SCAN"
        note = f"Search took {dur:.1f}s (> 5s). Broad file scan detected."
        if re.search(r"\bgrep\s+-[a-zA-Z]*r", c) and not re.search(r"--exclude(-dir)?", c):
            tip = "Use Antigravity native tool grep_search or add --exclude-dir={.git,node_modules,.venv,dist,target}."
        elif re.search(r"\bfind\s+", c) and not re.search(r"-maxdepth|-prune", c):
            tip = "Use Antigravity native tool find_by_name or add -maxdepth 3 and directory pruning."
        else:
            tip = "Prefer native tools grep_search or find_by_name (auto-capped at 50 results and respect .gitignore)."
        return tier, note, tip

    # 2. Test execution (pytest, vitest, cargo test, go test)
    if re.search(r"\b(pytest|vitest|jest|cargo\s+test|go\s+test)\b", c) or re.search(r"\b(npm|pnpm|yarn)\s+test\b", c):
        if dur <= 15.0:
            return "OK", None, None
        tier = "TEST_SUITE_UNSCOPED" if dur <= 45.0 else "TEST_SUITE_LONG_RUNNING"
        note = f"Test execution took {dur:.1f}s (> 15s)."
        if re.search(r"\bpytest\b", c) and not re.search(r"tests?/|\.py|-k\b", c):
            tip = "Scope pytest to specific test file or use -k <expression> and -x (--fail-fast)."
        else:
            tip = "Scope tests to target files or offload to background (WaitMsBeforeAsync: 1000)."
        return tier, note, tip

    # 3. Build & Package management (npm, pip, cargo build, docker)
    if (
        re.search(r"\b(npm|pnpm|yarn)\s+(install|i|ci|build|run\s+build)\b", c)
        or re.search(r"\b(pip|pip3|uv\s+pip)\s+install\b", c)
        or re.search(r"\b(cargo|go)\s+build\b", c)
        or re.search(r"\bdocker\s+(build|compose)\b", c)
    ):
        if dur <= 30.0:
            return "OK", None, None
        tier = "BUILD_CONSIDER_BACKGROUND" if dur <= 90.0 else "BUILD_HEAVY_BACKGROUND_RECOMMENDED"
        note = f"Build/package install took {dur:.1f}s (> 30s)."
        if re.search(r"\bpip3?\s+install\b", c):
            tip = "Use 'uv pip install' for faster resolution or offload to background (WaitMsBeforeAsync: 1000)."
        else:
            tip = "Offload long-running builds/installs to background (WaitMsBeforeAsync: 1000) or check caching."
        return tier, note, tip

    # 4. Git operations (git log, git diff)
    if re.search(r"\bgit\s+(log|diff|status|show|branch)\b", c):
        if dur <= 5.0:
            return "OK", None, None
        tier = "GIT_UNPAGED_OR_UNSCOPED" if dur <= 25.0 else "GIT_HEAVY_DIFF"
        note = f"Git operation took {dur:.1f}s (> 5s)."
        if re.search(r"\bgit\s+log\b", c) and not re.search(r"-(n\b|\d+|max-count)", c):
            tip = "Pass -n 20 or --oneline to limit git log output size."
        elif re.search(r"\bgit\s+diff\b", c) and not re.search(r"--stat|--name-only", c):
            tip = "Pass specific file paths or --stat to avoid massive diff output."
        else:
            tip = "Limit git query scope or specify paths."
        return tier, note, tip

    # 5. Network operations (curl, wget, git clone)
    if re.search(r"\b(curl|wget|git\s+clone|rsync|scp)\b", c):
        if dur <= 10.0:
            return "OK", None, None
        tier = "NETWORK_TIMEOUT_RECOMMENDED" if dur <= 30.0 else "NETWORK_SLOW_OR_BLOCKING"
        note = f"Network operation took {dur:.1f}s (> 10s)."
        if re.search(r"\bcurl\b", c) and not re.search(r"--max-time|-m\b", c):
            tip = "Add --max-time 15 or --connect-timeout 5 to prevent hanging requests."
        elif re.search(r"\bgit\s+clone\b", c) and not re.search(r"--depth\b", c):
            tip = "Use --depth 1 for shallow clone to reduce download overhead."
        else:
            tip = "Check network connection or specify timeout flags."
        return tier, note, tip

    # 6. General commands / fallback
    for max_sec, tier, template in TIERS:
        if dur <= max_sec:
            note = template.format(dur=dur) if template else None
            return tier, note, "Optimize command arguments, filtering, or scoping." if note else None

    tier, template = TIERS[-1][1], TIERS[-1][2]
    note = template.format(dur=dur) if template else None
    return tier, note, "Use background execution or split the task."


def classify_duration(dur: float, cmd: str = "") -> Tuple[str, Optional[str]]:
    """Backward-compatible duration classification returning (tier, guidance)."""
    tier, note, tip = classify_command_guidance(cmd, dur)
    if not note:
        return tier, None
    guidance = f"{note} Tip: {tip}" if tip and ("Tip:" not in note) else note
    return tier, guidance


def _state_matches_call(state: Dict[str, Any], step_idx: Any) -> bool:
    """True when a start record can legitimately belong to the call finishing now."""
    if not isinstance(state, dict):
        return False
    started = state.get("startWall")
    if isinstance(started, (int, float)) and not 0.0 <= (time.time() - started) <= MAX_TRACKED_SECONDS:
        return False
    recorded = state.get("stepIdx")
    if step_idx is None or recorded is None:
        return True
    try:
        return int(recorded) == int(step_idx)
    except (ValueError, TypeError):
        return False


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
            "startWall": time.time(),
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
        latest_file = get_state_file(conv_id)
        state_file = get_state_file(conv_id, step_idx) if step_idx is not None else latest_file
        if not state_file.exists():
            state_file = latest_file

        dur = 0.0
        cmd = "run_command"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
                if _state_matches_call(state, step_idx):
                    start_mono = state.get("startMonoNs")
                    if isinstance(start_mono, (int, float)):
                        dur = max(0.0, (now_mono - start_mono) / 1_000_000_000.0)
                    cmd = state.get("commandLine", "run_command")
                state_file.unlink(missing_ok=True)
                # The pre_tool hook mirrors every start into `_latest`; leaving it
                # behind lets a later unmatched post_tool bill this command's
                # start time to an unrelated one.
                latest_file.unlink(missing_ok=True)
            except Exception as exc:
                sys.stderr.write(f"[command_timer] parse state error: {exc}\n")

        tier, note, tip = classify_command_guidance(cmd, dur)

        # If non-OK tier, store bounded feedback
        if note:
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

            guidance = f"{note} Tip: {tip}" if tip and ("Tip:" not in note) else note
            feedback_list.append({
                "command": cmd,
                "duration": round(dur, 2),
                "tier": tier,
                "note": note,
                "tip": tip,
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
        # Clear previous turn lite/sage status on new user prompt
        if conv_id:
            try:
                cid_str = str(conv_id)
                sid = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', cid_str)[:32]}_{hashlib.sha256(cid_str.encode('utf-8')).hexdigest()[:8]}"
                sf = f"/tmp/agy_sage_{sid}.json"
                if os.path.exists(sf):
                    with open(sf, "r", encoding="utf-8") as f:
                        st = json.load(f)
                    if isinstance(st, dict) and (st.get("lite_status") or st.get("sage_status") in ("reviewing", "injecting")):
                        st["lite_status"] = ""
                        st["sage_status"] = "idle"
                        with open(sf, "w", encoding="utf-8") as f:
                            json.dump(st, f)
            except Exception:
                pass

        feedback_file = get_feedback_file(conv_id)

        if feedback_file.exists():
            try:
                items = json.loads(feedback_file.read_text(encoding="utf-8"))
                if isinstance(items, list) and items:
                    if len(items) == 1:
                        item = items[0]
                        tier = item.get("tier", "INFO")
                        cmd = item.get("command", "")
                        dur = item.get("duration", 0.0)
                        guidance = item.get("guidance", "")
                        prefix = (
                            "⚠️"
                            if (
                                "IMPROVE" in tier
                                or "FILTER" in tier
                                or "UNSCOPED" in tier
                                or "SLOW" in tier
                                or "TIMEOUT" in tier
                            )
                            else ("🚨" if "FORBIDDEN" in tier else "💡")
                        )
                        combined_msg = (
                            f"{prefix} [Command Timer - {tier}]\n"
                            f"- Command: `{cmd}`\n"
                            f"- Duration: {dur}s\n"
                            f"- Note: {guidance}"
                        )
                    else:
                        lines = [f"⚠️ [Command Timer - {len(items)} Slow Commands Detected]"]
                        for i, item in enumerate(items, 1):
                            tier = item.get("tier", "INFO")
                            cmd = item.get("command", "")
                            dur = item.get("duration", 0.0)
                            tip = item.get("tip") or item.get("guidance", "")
                            lines.append(f"{i}. `{cmd}` ({dur}s) - {tier}")
                            if tip:
                                lines.append(f"   Tip: {tip}")
                        combined_msg = "\n".join(lines)

                    if len(combined_msg) > MAX_INJECTED_CHARS:
                        combined_msg = combined_msg[:MAX_INJECTED_CHARS - 40] + "\n... [Additional feedback truncated]"
                    inject_steps.append({
                        "ephemeralMessage": combined_msg
                    })
            except Exception as exc:
                sys.stderr.write(f"[command_timer] pre_invocation read feedback error: {exc}\n")
            finally:
                # Unconditional: a feedback file that failed to parse must still be
                # dropped, or it is re-read (and re-logged) on every invocation.
                feedback_file.unlink(missing_ok=True)

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
