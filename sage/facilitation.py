"""sage.facilitation - Pin-time and post-settle delegation command (command + fail-closed recap gate).

At pin-time (or post-settle), the main agent becomes an orchestrator:
further execution and test work MUST be delegated to subagents via invoke_subagent
with a fully distilled dispatch payload. Mid-turn execution inline triggers a 1-line
violation alert. Inline execution is rejected at the final recap gate unless an
explicit receipt proves inline is the sole viable path.
"""

from sage.events import (
    EVENT_DELEGATE, EVENT_FACILITATION,
    EVENT_FACILITATION_REPEAT, format_summon_message,
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
        if path.endswith((".md", ".txt", ".csv", ".json", ".jsonl")) or "/scratch/" in path or "/brain/" in path:
            return True
            
    if name in ("run_command", "bash", "exec", "terminal"):
        cmd = str(args.get("CommandLine") or args.get("command") or args.get("cmd") or "").strip().lower()
        if cmd.startswith(("ls", "cat", "echo", "grep", "find", "fd", "tree", "pwd", "head", "tail", "wc")):
            if not any(op in cmd for op in (">", "rm ", "mv ", "cp ", "chmod ", "chown ", "wget", "curl")):
                return True
                
    return False


def immediate_delegate_message(state=None, pinned_goal=None):
    """Full delegation command dispatched at pin-time."""
    kwargs = {"signal_text": "Consider delegating execution to subagents via invoke_subagent. Should we split this into parallel tasks?"}
    if pinned_goal:
        kwargs["goal"] = pinned_goal
    return format_summon_message(EVENT_DELEGATE, **kwargs)


def immediate_settle_message(state=None, exec_calls=None, repeat=0):
    """Command message dispatched at settle. Deduplicates payload if already commanded at pin."""
    if state and not repeat:
        repeat = state.get("cmd_ignored", state.get("facilitation_cmd_ignored", 0))
    if not repeat and state and (state.get("delegate_cmd_turn") or (state.get("facilitation_cmd_turn") and not state.get("goal_settled"))):
        return "[WATCH·delegate·confirm] Facilitation compliance confirmed — subagents executed."
    ev = EVENT_FACILITATION_REPEAT if (repeat and repeat > 0) else EVENT_FACILITATION
    if repeat and repeat > 0:
        try:
            from sage.journal import write as journal_write
            conv_id = (state or {}).get("conv_id") or (state or {}).get("conversation_id") or ""
            journal_write("cmd_repeat", conv_id=conv_id)
        except Exception:
            pass
    kwargs = {"signal_text": "Consider delegating execution to subagents via invoke_subagent. Should we split this into parallel tasks?"}
    if exec_calls is not None:
        kwargs["exec_calls"] = exec_calls
    return format_summon_message(ev, **kwargs)


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

