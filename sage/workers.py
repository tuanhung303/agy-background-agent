"""sage.workers - Delegated-worker facts extracted from transcript steps.

Builds the DELEGATED WORKERS block for the sage prompt: every worker the
executor spawned (Orca pane, invoke_subagent, background task) with its
settlement state, so the model reasons over live evidence instead of a
truncated 10-step window that no longer shows the spawn command.
"""

import re

from sage.watchers import (
    _ORCA_HANDLE_RE,
    ORCA_TERMINAL_CREATE_RE,
    get_active_external_panes,
)

_SPAWN_HINTS = (
    "invoke_subagent",
    "running as a background task with task id:",
)
_AGE_WARN_SECS = 60.0


def _fmt_age(age_secs):
    if age_secs is None:
        return "unknown"
    if age_secs >= _AGE_WARN_SECS:
        mins = int(age_secs // 60)
        return f"{age_secs:.0f}s ago (~{mins}m)"
    return f"{age_secs:.0f}s ago"


def extract_worker_facts(steps):
    """Returns a human-readable DELEGATED WORKERS report (str, possibly empty).

    Deterministic: scans the WHOLE turn for spawn/send commands and settlement
    signals; cross-checks with get_active_external_panes for live status.
    """
    workers = []  # list of dicts {kind, ident, command, last_seen_age}
    seen = set()

    def _add(kind, ident, command, last_seen_age=None):
        key = (kind, ident)
        if key in seen:
            for w in workers:
                if (w["kind"], w["ident"]) == key:
                    if last_seen_age is not None and (w["last_seen_age"] is None or last_seen_age < w["last_seen_age"]):
                        w["last_seen_age"] = last_seen_age
                    return
            return
        seen.add(key)
        workers.append({"kind": kind, "ident": ident, "command": command[:160], "last_seen_age": last_seen_age})

    from datetime import datetime, timezone

    now_dt = datetime.now(timezone.utc)

    def _age(ts_str):
        try:
            dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (now_dt - dt).total_seconds())
        except Exception:
            return None

    active_panes = set(get_active_external_panes(steps))
    for s in steps:
        content = str(s.get("content") or "")
        ts_age = _age(s.get("created_at"))
        for t in (s.get("tool_calls") or []):
            args_map = t.get("args") or {}
            args = str(args_map.get("CommandLine") or "")
            if not args:
                continue
            if ORCA_TERMINAL_CREATE_RE.search(args):
                handles = _ORCA_HANDLE_RE.findall(args) or ["(handle pending)"]
                cmd_short = re.sub(r"\s+", " ", args).strip()
                for h in handles:
                    state = "ACTIVE STREAMING (no idle prompt observed)" if h in active_panes else "settled/closed"
                    _add("orca-pane", h, f"{cmd_short} -> {state}", ts_age)
            elif "orca terminal send" in args.lower() and "--help" not in args.lower():
                handles = _ORCA_HANDLE_RE.findall(args)
                if not handles:
                    continue
                cmd_short = re.sub(r"\s+", " ", args).strip()
                for h in handles:
                    state = "ACTIVE STREAMING (no idle prompt observed)" if h in active_panes else "prompt sent, awaiting output"
                    _add("orca-pane", h, f"{cmd_short[:120]} -> {state}", ts_age)
            elif t.get("name") == "invoke_subagent":
                roles = re.findall(r'"Role"\s*:\s*"([^"]+)"', args) or ["subagent"]
                for r in roles:
                    _add("subagent", r, f"invoke_subagent(Role={r})", ts_age)
        low = content.lower()
        m = re.search(r"task id:\s*([a-zA-Z0-9_/-]+/task-[0-9]+|task-[0-9]+)", low)
        if m and any(h in low for h in _SPAWN_HINTS[1:]):
            _add("background-task", m.group(1), f"spawned background task {m.group(1)}", ts_age)

    if not workers:
        return ""
    lines = ["KIND | IDENTITY | SPAWN COMMAND / STATE | LAST EVIDENCE"]
    for w in workers:
        lines.append(f"{w['kind']} | {w['ident']} | {w['command']} | {_fmt_age(w['last_seen_age'])}")
    active = [w for w in workers if "ACTIVE STREAMING" in w["command"] or "awaiting output" in w["command"]]
    if active:
        lines.insert(1, "WARNING: delegated worker(s) have pending/unread output. Do NOT approve completion "
                        "while any worker below is not confirmed settled — read its full output first.")
    return "\n".join(lines)
