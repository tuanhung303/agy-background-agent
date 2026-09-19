"""sage.dispatches - External CLI dispatches and interactive background stall tracking."""

from datetime import datetime, timezone
import os
import re

from sage.watchers import _parse_iso_ts, get_active_background_tasks

EXTERNAL_DISPATCH_TASK_RE = re.compile(r"(?:^|\n)TASK:\s*([^\r\n]+)", re.I)
EXTERNAL_DISPATCH_PID_RE = re.compile(r"(?:^|\n)PID:\s*(\d+)", re.I)
EXTERNAL_DISPATCH_EXIT_CODE_RE = re.compile(r"(?:^|\n)EXIT_CODE:\s*([^\s\r\n]+)", re.I)

INTERACTIVE_STALL_PATTERNS = (
    r"(?:ok\s+to\s+proceed\?|proceed\?|are\s+you\s+sure|confirm\?|do\s+you\s+want\s+to\s+continue\?|continue\?)\s*[\(\[][yYnN/ ]+[\)\]]",
    r"need\s+to\s+install\s+the\s+following\s+packages:.*?(?:ok\s+to\s+proceed\?|\([yYnN]\))",
    r"(?:password|passphrase)\s*(?:for\s+[^:\n]+)?:\s*$",
    r"(?:username|login)\s*(?:for\s+[^:\n]+)?:\s*$",
    r"\([yY]es\/[nN]o(?:\/\[fingerprint\])?\)\?\s*$",
    r"\[(?:[yY]\/[nN]|[nN]\/[yY])\]\s*$",
)
INTERACTIVE_STALL_RE = re.compile("|".join(INTERACTIVE_STALL_PATTERNS), re.IGNORECASE | re.MULTILINE)


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


def get_active_external_dispatches(steps, max_age=1800.0, parse_ts_func=None):
    """Tracks active detached CLI dispatches from codex/claude/antigravity/opencode dispatches."""
    parse_ts = parse_ts_func or _parse_iso_ts
    now_dt = datetime.now(timezone.utc)
    dispatches = {}

    for s in steps:
        content = str(s.get("content") or "")
        if "EXIT_CODE:" not in content and "PID:" not in content and "TASK:" not in content:
            continue
        pid_m = EXTERNAL_DISPATCH_PID_RE.search(content)
        task_m = EXTERNAL_DISPATCH_TASK_RE.search(content)
        exit_m = EXTERNAL_DISPATCH_EXIT_CODE_RE.search(content)
        if not (pid_m and task_m):
            continue
        pid = int(pid_m.group(1))
        task_dir = task_m.group(1).strip()
        exit_code_path = exit_m.group(1).strip() if exit_m else os.path.join(task_dir, "exit.code")
        if "(" in exit_code_path:
            exit_code_path = exit_code_path.split("(")[0].strip()

        ts_str = s.get("created_at")
        dt = parse_ts(ts_str) if ts_str else None
        age = max(0.0, (now_dt - dt).total_seconds()) if dt else 0.0

        if max_age and age > max_age:
            continue

        if os.path.isfile(exit_code_path):
            continue

        if _is_pid_alive(pid):
            dispatches[task_dir] = {
                "task_dir": task_dir,
                "pid": pid,
                "exit_code_path": exit_code_path,
                "age_seconds": age,
            }

    return list(dispatches.values())


def has_active_external_dispatches(steps, max_age=1800.0, parse_ts_func=None):
    return bool(get_active_external_dispatches(steps, max_age=max_age, parse_ts_func=parse_ts_func))


def get_stalled_background_tasks(steps, transcript_path=None, conv_id=None):
    """Detects active background tasks waiting for interactive stdin input (e.g. npx/npm prompts)."""
    active_tasks = get_active_background_tasks(steps, conv_id=conv_id)
    if not active_tasks:
        return []

    stalled = []
    base_tasks_dir = None
    if transcript_path:
        gen_dir = os.path.dirname(os.path.dirname(os.path.abspath(transcript_path)))
        cand = os.path.join(gen_dir, "tasks")
        if os.path.isdir(cand):
            base_tasks_dir = cand

    for t in active_tasks:
        if t.get("is_timer"):
            continue
        tid = t["task_id"]
        raw_id = tid.split("/")[-1]

        matched_prompt = None
        for s in reversed(steps[-20:]):
            content = str(s.get("content") or "")
            if raw_id in content and INTERACTIVE_STALL_RE.search(content):
                m = INTERACTIVE_STALL_RE.search(content)
                matched_prompt = m.group(0).strip()
                break

        if not matched_prompt and base_tasks_dir:
            log_path = os.path.join(base_tasks_dir, f"{raw_id}.log")
            if os.path.isfile(log_path):
                try:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(max(0, os.path.getsize(log_path) - 4096))
                        tail = f.read()
                    m = INTERACTIVE_STALL_RE.search(tail)
                    if m:
                        matched_prompt = m.group(0).strip()
                except Exception:
                    pass

        if matched_prompt:
            stalled.append({
                "task_id": tid,
                "description": t.get("description", ""),
                "stall_prompt": matched_prompt,
                "age_seconds": t.get("age_seconds", 0.0),
            })

    return stalled
