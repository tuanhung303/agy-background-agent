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

from sage.session_state import get_state_file_path  # noqa: E402
from sage.task_structure import EXEC_TOOLS, FILE_TOOLS  # noqa: E402

BLOCKED_TOOLS = EXEC_TOOLS | FILE_TOOLS
VIOLATION_MSG = "[CMD·delegate·violation] exec inline blocked — delegate via invoke_subagent NOW"


def _passthrough():
    return {"decision": "allow"}


def _is_subagent_payload(payload):
    """Subagent tool calls share the parent's conversationId; they must never be
    blocked (the delegation command targets the MAIN agent only). Mirrors the
    payload-key checks of sage.guards.is_subagent_session."""
    if payload.get("isSubagent") or payload.get("is_subagent"):
        return True
    if payload.get("parentConversationId") or payload.get("parent_conversation_id"):
        return True
    role = str(payload.get("agentRole") or payload.get("role") or "").lower()
    if role and ("subagent" in role or "implementer" in role or "research" in role or "auditor" in role or "worker" in role or role in ("self", "scout", "qa")):
        return True
    return False


def evaluate(payload):
    """Pure decision logic: returns dict for the PreToolUse output contract."""
    if os.environ.get("AGY_SAGE_DISABLED") == "1":
        return _passthrough()
    if _is_subagent_payload(payload):
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
    return {"decision": "allow", "injectSteps": [{"ephemeralMessage": VIOLATION_MSG}]}


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
