"""
sage.watchers - Subagent and background task tracking from transcript logs.
"""

from datetime import datetime, timezone
import json
import re


SUBAGENT_INVOKE_FAILURE_RE = re.compile(
    r"(?:failed|unable|error).{0,40}(?:invoke|spawn|start|create).{0,20}subagent"
    r"|invoke_subagent.{0,40}(?:failed|error|unavailable)",
    re.IGNORECASE,
)


def _parse_iso_ts(ts_str):
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


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
        if stype in ("GENERIC", "SYSTEM_MESSAGE") and SUBAGENT_INVOKE_FAILURE_RE.search(content):
            failed_batch = pending_batches.pop(0) if pending_batches else []
            for sid in failed_batch:
                spawned.pop(sid, None)
        if stype in ("GENERIC", "SYSTEM_MESSAGE") or (stype == "USER_INPUT" and ("sender=" in content or "[Message]" in content)):
            completed.update(c for c in re.findall(r"sender=([a-zA-Z0-9_-]+)", content) if c.lower() != "system")
            completed.update(re.findall(r"[Ss]ubagent\s+([a-zA-Z0-9_-]+)\s+has gone idle", content))
            completed.update(re.findall(r"(?:Killed|Terminated)\s+(?:subagent\s+)?['\"]?([a-zA-Z0-9_-]+)['\"]?", content, re.I))
    return [info for sid, info in spawned.items() if sid not in completed and (not conv_id or sid != conv_id)]


def get_active_background_tasks(steps, conv_id=None, parse_ts_func=None):
    """Tracks active and completed background tasks from transcript step logs."""
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
                dt = parse_ts_func(ts_str) if parse_ts_func else None
                tid, age = raw_tid if (not conv_id or "/" in raw_tid) else f"{conv_id}/{raw_tid}", max(0.0, (now_dt - dt).total_seconds()) if dt else 0.0
                if not tasks.get(tid) or age > tasks[tid].get("age_seconds", 0.0):
                    tasks[tid] = {"task_id": tid, "description": desc, "age_seconds": age}
        if stype in ("GENERIC", "SYSTEM_MESSAGE") or (stype == "USER_INPUT" and ("sender=" in content or "[Message]" in content)):
            for cid_m in (re.findall(r"sender=([a-zA-Z0-9_-]+/task-[0-9]+|task-[0-9]+)", content, re.I) + (re.findall(r"(?:Background\s+)?task(?:\s+id|:)?\s*['\"]?([a-zA-Z0-9_-]+/task-[0-9]+|task-[0-9]+)['\"]?", content, re.I) if any(w in clow for w in ("was canceled", "was cancelled", "finished with result", "completed", "terminated", "killed", "finished", "status: done", "status: failed", "timer cancelled", "timer canceled")) else [])):
                completed_ids.update([cid_m.strip(), cid_m.strip().split("/")[-1]])
    return [t for tid, t in tasks.items() if tid not in completed_ids and tid.split("/")[-1] not in completed_ids]
