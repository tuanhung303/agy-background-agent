"""sage.facilitation - Pin-time and post-settle delegation command (command + fail-closed recap gate).

At pin-time (or post-settle), the main agent becomes an orchestrator:
further execution and test work MUST be delegated to subagents via invoke_subagent
with a fully distilled dispatch payload. Mid-turn execution inline triggers a 1-line
violation alert. Inline execution is rejected at the final recap gate unless an
explicit receipt proves inline is the sole viable path.
"""

from sage.events import (
    EVENT_DELEGATE, format_summon_message,
)
from sage.task_structure import EXEC_TOOLS, FILE_TOOLS
from sage.transcript import (
    _read_transcript_steps,
    _safe_tool_calls,
    is_explicit_user_input,
)


def _is_safe_inline_tool(t):
    name = str(t.get("name") or "")
    args = t.get("args") or t.get("arguments") or {}

    if name in ("write_to_file", "write_file", "create_file"):
        path = str(args.get("TargetFile") or args.get("file") or args.get("AbsolutePath") or "").lower()
        if path.endswith((".md", ".txt", ".csv", ".json", ".jsonl", ".yml", ".yaml")) or "/scratch/" in path or "/brain/" in path:
            return True

    if name in ("run_command", "bash", "exec", "terminal"):
        cmd = str(args.get("CommandLine") or args.get("command") or args.get("cmd") or "").strip().lower()
        if cmd.startswith("git ") or cmd == "git":
            return True
        safe_prefixes = (
            "ls", "cat", "echo", "grep", "find", "fd", "tree", "pwd", "head", "tail", "wc",
            "pytest", "uv run pytest", "npm test", "pnpm test", "cargo test", "go test", "statusline"
        )
        if any(cmd.startswith(p) for p in safe_prefixes):
            if not any(op in cmd for op in (">", "rm ", "mv ", "cp ", "chmod ", "chown ", "wget", "curl")):
                return True

    return False


def immediate_delegate_message(state=None, pinned_goal=None, shared=None):
    """Full delegation command dispatched at pin-time."""
    kwargs = {"signal_text": "Delegate execution to subagents via invoke_subagent. Split this into parallel tasks."}
    if pinned_goal:
        kwargs["goal"] = pinned_goal
    if shared is None and state:
        shared = state.get("shared_files")
    if shared:
        kwargs["shared"] = shared
    if state:
        roles = state.get("delegate_roles")
        if roles:
            kwargs["roles"] = list(roles)
        legs = state.get("delegate_legs")
        if legs:
            kwargs["legs"] = "; ".join(str(d) for d in legs)
    return format_summon_message(EVENT_DELEGATE, **kwargs)


def immediate_settle_message(state=None, transcript_path=None, assist_active=False, conv_id=""):
    """Recap postscript for delegation compliance: honest or silent.

    Never emits a fresh delegation order at settle: the recap already approved the
    work, and ordering delegation after completion (or into Assist Mode) breaks the
    routing doctrine. Confirms only when the transcript shows subagents actually ran;
    a commanded-but-ignored delegation is journaled, not praised.

    Routing is read from state, never recomputed. `delegate_cmd_turn` is set only when
    the pin routed to Teamplay, so its presence IS the pin-time verdict; recomputing
    Assist at settle let accumulated writes retract a decision already acted on,
    silencing both the confirmation and the journal.
    """
    if assist_active:
        return ""
    st = state or {}
    if not (st.get("delegate_cmd_turn") or st.get("facilitation_cmd_turn")):
        return ""
    if not transcript_path:
        # Never looked: silence is honest, a journaled "missed" would not be.
        return ""
    comp = check_facilitation_compliance(transcript_path, st)
    if comp.get("has_subagent"):
        return "[WATCH·delegate·confirm] Facilitation compliance confirmed — subagents executed."
    if not comp.get("required"):
        # simple_qa or no pin: delegation was never owed, so nothing was missed.
        return ""
    try:
        from sage.journal import write as journal_write
        cid = conv_id or st.get("conv_id") or st.get("conversation_id") or ""
        journal_write("settle_delegate_missed", conv_id=cid, count=comp.get("exec_calls"))
    except Exception:
        pass
    return ""


def _turn_tool_calls(steps, from_turn_idx=None):
    turn_idxs = [
        i for i, s in enumerate(steps)
        if isinstance(s, dict) and is_explicit_user_input(s)
    ]
    if from_turn_idx is not None and from_turn_idx > 0 and turn_idxs:
        idx = max(0, min(from_turn_idx - 1, len(turn_idxs) - 1))
        t_steps = steps[turn_idxs[idx]:]
    elif turn_idxs:
        t_steps = steps[turn_idxs[-1] + 1:]
    else:
        t_steps = steps
    return [t for s in t_steps for t in _safe_tool_calls(s) if t.get("name")]


def facilitation_signal(transcript_path, state):
    """Violation-only alert when the main agent executes inline after goal pin or settle."""
    complexity = str((state or {}).get("task_complexity") or "").strip().lower()
    if complexity in ("simple_qa", "qa"):
        return ""
    if not (state or {}).get("goal_settled") and not (state or {}).get("facilitation_cmd_turn") and not (state or {}).get("delegate_cmd_turn"):
        return ""
    steps = _read_transcript_steps(transcript_path) if transcript_path else []
    t_calls = _turn_tool_calls(steps)
    if any(str(t.get("name") or "") == "invoke_subagent" for t in t_calls):
        return ""
    forbidden_tools = FILE_TOOLS if (state or {}).get("goal_settled") else (FILE_TOOLS | EXEC_TOOLS)
    exec_calls = sum(1 for t in t_calls if str(t.get("name") or "") in forbidden_tools and not _is_safe_inline_tool(t))
    if exec_calls < 1:
        return ""
    return "[WATCH·delegate] Inline execution detected. Are you sure you don't want to delegate this to subagents?"


def check_facilitation_compliance(transcript_path, state):
    """Checks whether the agent has complied with the delegation command."""
    complexity = str((state or {}).get("task_complexity") or "").strip().lower()
    if complexity in ("simple_qa", "qa"):
        return {"required": False, "compliant": True, "exec_calls": 0, "has_subagent": False}
    if not (state or {}).get("goal_settled") and not (state or {}).get("facilitation_cmd_turn") and not (state or {}).get("delegate_cmd_turn"):
        return {"required": False, "compliant": True, "exec_calls": 0, "has_subagent": False}
    steps = _read_transcript_steps(transcript_path) if transcript_path else []
    cmd_turn = (state or {}).get("delegate_cmd_turn") or (state or {}).get("facilitation_cmd_turn")
    t_calls = _turn_tool_calls(steps, from_turn_idx=cmd_turn)
    has_subagent = any(str(t.get("name") or "") == "invoke_subagent" for t in t_calls)
    forbidden_tools = FILE_TOOLS if (state or {}).get("goal_settled") else (FILE_TOOLS | EXEC_TOOLS)
    exec_calls = sum(1 for t in t_calls if str(t.get("name") or "") in forbidden_tools and not _is_safe_inline_tool(t))
    if has_subagent:
        return {"required": True, "compliant": True, "exec_calls": exec_calls, "has_subagent": True}
    if exec_calls > 0:
        return {"required": True, "compliant": False, "exec_calls": exec_calls, "has_subagent": False}
    return {"required": True, "compliant": True, "exec_calls": 0, "has_subagent": False}

