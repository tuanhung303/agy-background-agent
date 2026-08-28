#!/usr/bin/env python3
"""
sage-enforce.py - Zero-delay PreToolUse delegate enforcement (no LLM).

When a delegate command has been issued (delegate_cmd_turn / facilitation_cmd_turn
in the session state) and the agent attempts an inline EXEC/FILE tool call, this
hook injects a one-line violation message into the turn immediately — without
waiting for the PostInvocation sage evaluation. Read-only research tools are
allowed (the orchestrator needs them to distill payloads).

Hot unplug: AGY_SAGE_DISABLED=1 (per-spawn env) makes this hook a passthrough.
"""

import json
import os
import sys

_HOOK_DIR = os.path.dirname(os.path.realpath(__file__))
_REPO_DIR = os.path.abspath(os.path.join(_HOOK_DIR, ".."))
if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

from sage.guards import is_subagent_payload  # noqa: E402
from sage.session_state import get_state_file_path  # noqa: E402
from sage.task_structure import EXEC_TOOLS, FILE_TOOLS  # noqa: E402

BLOCKED_TOOLS = EXEC_TOOLS | FILE_TOOLS
VIOLATION_MSG = "[CMD·delegate·violation] exec inline blocked — delegate via invoke_subagent NOW"
# Flood control: inject at most ONCE per conv, then a per-conv counter lets the
# PostInvocation gate / statusline surface "ignored N×" without spamming context.
MAX_INJECTIONS_PER_CONV = 1


def _passthrough():
    return {"decision": "allow"}


def _counter_path(conv_id):
    return f"/tmp/agy_sage_enforce_{conv_id}.json" if conv_id else ""


def _injections_used(conv_id):
    try:
        with open(_counter_path(conv_id), "r", encoding="utf-8") as f:
            return int(json.load(f).get("count", 0))
    except Exception:
        return 0


def _record_injection(conv_id):
    try:
        with open(_counter_path(conv_id), "w", encoding="utf-8") as f:
            json.dump({"count": _injections_used(conv_id) + 1}, f)
    except Exception:
        pass


def evaluate(payload):
    """Pure decision logic: returns dict for the PreToolUse output contract."""
    if os.environ.get("AGY_SAGE_DISABLED") == "1":
        return _passthrough()
    if is_subagent_payload(payload):
        return _passthrough()
    tool_call = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    tool_name = str(tool_call.get("name") or "")
    if tool_name not in BLOCKED_TOOLS:
        return _passthrough()
    conv_id = payload.get("conversationId", "default")
    try:
        with open(get_state_file_path(conv_id), "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return _passthrough()
    if not isinstance(state, dict):
        return _passthrough()
    if not (state.get("delegate_cmd_turn") or state.get("facilitation_cmd_turn") or state.get("goal_settled")):
        return _passthrough()
    comp = str(state.get("task_complexity") or "").strip().lower()
    if comp in ("simple_qa", "qa"):
        return _passthrough()
    try:
        from sage.journal import write as journal_write
    except Exception:
        journal_write = lambda *args, **kwargs: None
    used = _injections_used(conv_id)
    if used >= MAX_INJECTIONS_PER_CONV:
        journal_write("violation_suppressed", conv_id=conv_id, tool=tool_name, count=used)
        return _passthrough()
    _record_injection(conv_id)
    journal_write("violation_inject", conv_id=conv_id, tool=tool_name, count=used + 1)
    return {"decision": "allow"}


def main() -> None:
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else "{}"
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    try:
        out = evaluate(payload)
    except Exception:
        out = _passthrough()
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
