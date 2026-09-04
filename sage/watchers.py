"""sage.watchers - Subagent and background task tracking from transcript logs."""

from datetime import datetime, timezone
import json
import os
import re


SUBAGENT_INVOKE_FAILURE_RE = re.compile(
    r"(?:failed|unable|error).{0,40}(?:invoke|spawn|start|create).{0,20}subagent"
    r"|invoke_subagent.{0,40}(?:failed|error|unavailable)",
    re.IGNORECASE,
)

# External worker panes spawned via run_command (default patterns cover the
# common CLI pane managers; override with AGY_SAGE_PANE_SPAWN_RE using ';;'
# as the pattern separator — see sage.workers for the same convention).
# A pane is "open" once spawned; it settles when a later transcript step
# records an idle prompt or terminal close for that handle.
_DEFAULT_PANE_SPAWN_PATTERNS = (
    r"\bterminal\s+create\b[^\n]*?--command\b",
    r"\btmux\s+(?:new-session|new-window)\b",
)
PANE_SPAWN_RES = tuple(
    re.compile(p, re.I)
    for p in (os.environ.get("AGY_SAGE_PANE_SPAWN_RE", "").split(";;")
              if os.environ.get("AGY_SAGE_PANE_SPAWN_RE", "").strip()
              else _DEFAULT_PANE_SPAWN_PATTERNS)
    if p.strip()
)
_PANE_HANDLE_RE = re.compile(r"term_[a-f0-9-]{8,}")
PANE_IDLE_PROMPT_RE = re.compile(r"(?:^|\n)[^\S\n]*(?:❯|\$|%|#|➜|>)\s*(?:\n|$)|(?:exited with code|process finished|process completed|command exited with code|state:\s*idle|status:\s*idle|\"idle\":\s*true)", re.I)
_PANE_SEND_RE = re.compile(r"terminal\s+send\b", re.I)
_PANE_CLOSE_RE = re.compile(r"terminal\s+close\b", re.I)


def _parse_iso_ts(ts_str):
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def get_active_external_panes(steps):
    """Tracks external worker panes spawned via run_command (channel-agnostic)."""
    open_handles, settled = [], set()
    for s in steps:
        content = str(s.get("content") or "")
        low = content.lower()
        for t in (s.get("tool_calls") or []):
            args = str((t.get("args") or {}).get("CommandLine") or "")
            if any(p.search(args) for p in PANE_SPAWN_RES) or (_PANE_SEND_RE.search(args) and _PANE_HANDLE_RE.search(args)):
                open_handles.extend(_PANE_HANDLE_RE.findall(args))
            elif _PANE_CLOSE_RE.search(args):
                settled.update(h.strip() for h in _PANE_HANDLE_RE.findall(args))
        if "terminal" in low or any(p.search(content) for p in PANE_SPAWN_RES):
            handles = set(_PANE_HANDLE_RE.findall(content))
            if _PANE_CLOSE_RE.search(content) or "close" in low:
                settled.update(h.strip() for h in handles)
            elif handles and PANE_IDLE_PROMPT_RE.search(content):
                settled.update(h.strip() for h in handles)
            elif "exited with code" in low or "completed" in low or "idle" in low:
                settled.update(h.strip() for h in handles)
        elif PANE_IDLE_PROMPT_RE.search(content) and open_handles:
            settled.update(h.strip() for h in open_handles)
    return [h for h in dict.fromkeys(open_handles) if h not in settled]


def get_active_subagents(steps, conv_id=None, parse_ts_func=None):
    """Tracks active and completed subagents from transcript step logs."""
    spawned, completed = {}, set()
    now_dt = datetime.now(timezone.utc)
    parse_ts = parse_ts_func or _parse_iso_ts
    pending_batches = []
    # Monotonic across the whole scan: reusing len(spawned) lets a new pending id
    # collide with a surviving one (after a spawn failure pops a batch), silently
    # overwriting a still-active subagent and unblocking a premature stop.
    pending_seq = 0
    for s in steps:
        stype, content, stools = s.get("type"), str(s.get("content") or ""), s.get("tool_calls", [])
        ts_str = s.get("created_at")
        dt = parse_ts(ts_str) if ts_str else None
        age = max(0.0, (now_dt - dt).total_seconds()) if dt else 0.0
        if isinstance(stools, list):
            for t in (t for t in stools if isinstance(t, dict)):
                tname, args = t.get("name"), t.get("args") or {}
                if tname == "invoke_subagent":
                    subs = args.get("Subagents") or args.get("subagents") or [{}]
                    if isinstance(subs, str):
                        try:
                            subs = json.loads(subs)
                        except Exception:
                            subs = [{}]
                    subs = [subs] if isinstance(subs, dict) else (subs if isinstance(subs, list) else [{}])
                    pending_batch = []
                    for sub in subs:
                        sid = f"pending_invoke_{pending_seq}"
                        pending_seq += 1
                        role = "Subagent"
                        if isinstance(sub, dict):
                            role = sub.get("Role") or sub.get("role") or sub.get("TypeName") or "Subagent"
                        elif sub:
                            role = str(sub)
                        spawned[sid] = {"subagent_id": sid, "role": role, "age_seconds": age}
                        pending_batch.append(sid)
                    pending_batches.append(pending_batch)
                elif tname == "manage_subagents":
                    completed.update(spawned.keys() if args.get("Action") == "kill_all" else (args.get("ConversationIds") or []))
        for cid in re.findall(r'"conversationId":\s*["\']([a-zA-Z0-9_-]+)["\']', content):
            if not conv_id or cid != conv_id:
                p_keys = [k for k in spawned if k.startswith("pending_invoke_")]
                if p_keys:
                    p_info = spawned.pop(p_keys[0], {})
                    for batch in pending_batches:
                        if p_keys[0] in batch:
                            batch.remove(p_keys[0])
                            break
                    pending_batches[:] = [batch for batch in pending_batches if batch]
                    role = p_info.get("role", "Subagent")
                    prev_age = p_info.get("age_seconds", age)
                    spawned[cid] = {
                        "subagent_id": cid,
                        "conversation_id": cid,
                        "role": role,
                        "age_seconds": prev_age,
                    }
                    try:
                        from sage.guards import register_known_subagent
                        register_known_subagent(cid)
                    except Exception:
                        pass
        if stype in ("GENERIC", "SYSTEM_MESSAGE") and SUBAGENT_INVOKE_FAILURE_RE.search(content):
            failed_batch = pending_batches.pop(0) if pending_batches else []
            for sid in failed_batch:
                spawned.pop(sid, None)
        if stype in ("GENERIC", "SYSTEM_MESSAGE") or (stype == "USER_INPUT" and ("sender=" in content or "[Message]" in content)):
            completed.update(c for c in re.findall(r"sender=([a-zA-Z0-9_-]+)", content) if c.lower() != "system")
            completed.update(re.findall(r"[Ss]ubagent\s+([a-zA-Z0-9_-]+)\s+has gone idle", content))
            completed.update(re.findall(r"(?:Killed|Terminated)\s+(?:subagent\s+)?['\"]?([a-zA-Z0-9_-]+)['\"]?", content, re.I))
    return [info for sid, info in spawned.items() if sid not in completed and (not conv_id or sid != conv_id)]


def get_active_background_tasks(steps, conv_id=None, parse_ts_func=None, max_age=None):
    """Tracks active and completed background tasks from transcript step logs."""
    if max_age is None:
        try:
            from sage.config import MAX_BACKGROUND_TASK_AGE
            max_age = MAX_BACKGROUND_TASK_AGE
        except Exception:
            max_age = 1800.0
    parse_ts = parse_ts_func or _parse_iso_ts
    tasks, completed_ids, now_dt = {}, set(), datetime.now(timezone.utc)
    for s in steps:
        stype, content, ts_str = s.get("type"), str(s.get("content") or ""), s.get("created_at")
        clow = content.lower()
        if "no background tasks are currently running" in clow:
            tasks.clear()
            continue
        if stype in ("GENERIC", "SYSTEM") and "tool is running as a background task with task id:" in clow:
            if any(m in content for m in ("diff_block_start", "replace_file_content", "view_file", "File Path:", "Showing lines", "The command exited with code", "Output:", "AssertionError", "pytest")):
                continue
            m_id = re.search(r"tool is running as a background task with task id:\s*([a-zA-Z0-9_-]+/task-[0-9]+|task-[0-9]+)", content, re.I)
            m_desc = re.search(r"Task Description:\s*([^\n]+)", content, re.I)
            if m_id and not (conv_id and "/" in m_id.group(1).strip() and m_id.group(1).strip().split("/")[0] != conv_id):
                desc = m_desc.group(1).strip() if m_desc else "background task"
                if desc.lower().startswith("timer:"):
                    continue
                raw_tid = m_id.group(1).strip()
                dt = parse_ts(ts_str) if ts_str else None
                tid = raw_tid if (not conv_id or "/" in raw_tid) else f"{conv_id}/{raw_tid}"
                age = max(0.0, (now_dt - dt).total_seconds()) if dt else 0.0
                if not tasks.get(tid) or age > tasks[tid].get("age_seconds", 0.0):
                    tasks[tid] = {"task_id": tid, "description": desc, "age_seconds": age}
        if stype in ("GENERIC", "SYSTEM_MESSAGE") or (stype == "USER_INPUT" and ("sender=" in content or "[Message]" in content)):
            # Status-aware completion. Two traps both observed live (2026-08-27
            # premature recap while ServiceNow queries streamed):
            #   1. manage_task POLL blocks print "Completed At:" (a timestamp)
            #      with "Status: RUNNING" — never a completion.
            #   2. view_file of a task LOG echoes "Completed At:" and the task
            #      id path (`tasks/task-51.log`) without any status change.
            # Retire an id ONLY on sender= messages or an explicit
            # finished/canceled phrase about the task itself.
            if re.search(r"Status:\s*RUNNING\b", content, re.I):
                continue
            explicit_done = ("finished with result" in clow or "was canceled" in clow
                             or "was cancelled" in clow or "status: done" in clow
                             or "status: failed" in clow or "status: completed" in clow
                             or "terminated" in clow or "killed" in clow
                             or "timer cancelled" in clow or "timer canceled" in clow)
            # Bare "Task X completed" phrasing (no status block): retire only
            # when the sentence is ABOUT the task, not a "Completed At:"
            # timestamp header echoed inside a log/poll dump.
            quoted_done = ("completed" in clow and not re.search(r"Completed At:", content))
            for cid_m in (re.findall(r"sender=([a-zA-Z0-9_-]+/task-[0-9]+|task-[0-9]+)", content, re.I)
                          + (re.findall(r"[Tt]ask(?:\s+id)?\s*['\"]?([a-zA-Z0-9_-]+/task-[0-9]+|task-[0-9]+)['\"]?\s+(?:was\s+)?completed", content) if quoted_done else [])
                          + (re.findall(r"(?:Background\s+)?task(?:\s+id|:)?\s*['\"]?([a-zA-Z0-9_-]+/task-[0-9]+|task-[0-9]+)['\"]?", content, re.I) if explicit_done else [])):
                completed_ids.update([cid_m.strip(), cid_m.strip().split("/")[-1]])
    return [
        t for tid, t in tasks.items()
        if tid not in completed_ids
        and tid.split("/")[-1] not in completed_ids
        and (not max_age or t.get("age_seconds", 0.0) <= max_age)
    ]
