"""sage.facilitation - Pin-time and post-settle delegation command (command + fail-closed recap gate).

At pin-time (or post-settle), the main agent becomes an orchestrator:
further execution and test work MUST be delegated to subagents via invoke_subagent
with a fully distilled dispatch payload. Mid-turn execution inline triggers a 1-line
violation alert. Inline execution is rejected at the final recap gate unless an
explicit receipt proves inline is the sole viable path.
"""

from sage.events import (
    EVENT_DELEGATE, EVENT_DELEGATE_VIOLATION, EVENT_FACILITATION,
    EVENT_FACILITATION_REPEAT, format_summon_message,
)
from sage.task_structure import EXEC_TOOLS, FILE_TOOLS, RESEARCH_TOOLS
from sage.transcript import _read_transcript_steps, is_explicit_user_input


def immediate_delegate_message(state=None, pinned_goal=None):
    """Full delegation command dispatched at pin-time."""
    kwargs = {"signal_text": "DELEGATE execution to subagents via invoke_subagent with a distilled payload."}
    if pinned_goal:
        kwargs["goal"] = pinned_goal
    return format_summon_message(EVENT_DELEGATE, **kwargs)


def immediate_settle_message(state=None, exec_calls=None, repeat=0):
    """Command message dispatched at settle. Deduplicates payload if already commanded at pin."""
    if state and (state.get("delegate_cmd_turn") or (state.get("facilitation_cmd_turn") and not state.get("goal_settled"))):
        return "[CMD·delegate·confirm] facilitation compliance confirmed — subagents executed."
    ev = EVENT_FACILITATION_REPEAT if (repeat and repeat > 0) else EVENT_FACILITATION
    kwargs = {"signal_text": "DELEGATE execution to subagents via invoke_subagent with a distilled payload."}
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
    return [
        t for s in t_steps if isinstance(s, dict)
        for t in (s.get("tool_calls") if isinstance(s.get("tool_calls"), list) else [])
        if isinstance(t, dict) and t.get("name")
    ]


def facilitation_signal(transcript_path, state):
    """Violation-only alert when the main agent executes inline after goal pin or settle."""
    if not state.get("goal_settled") and not state.get("facilitation_cmd_turn") and not state.get("delegate_cmd_turn"):
        return ""
    steps = _read_transcript_steps(transcript_path) if transcript_path else []
    t_calls = _turn_tool_calls(steps)
    if any(str(t.get("name") or "") == "invoke_subagent" for t in t_calls):
        return ""
    exec_calls = sum(1 for t in t_calls if str(t.get("name") or "") in (FILE_TOOLS | EXEC_TOOLS | RESEARCH_TOOLS))
    if exec_calls < 1:
        return ""
    return "[CMD·delegate·violation] exec inline detected — delegate NOW"


def check_facilitation_compliance(transcript_path, state):
    """Checks whether the agent has complied with the delegation command."""
    if not state.get("goal_settled") and not state.get("facilitation_cmd_turn") and not state.get("delegate_cmd_turn"):
        return {"required": False, "compliant": True, "exec_calls": 0, "has_subagent": False}
    steps = _read_transcript_steps(transcript_path) if transcript_path else []
    cmd_turn = state.get("delegate_cmd_turn") or state.get("facilitation_cmd_turn")
    t_calls = _turn_tool_calls(steps, from_turn_idx=cmd_turn)
    has_subagent = any(str(t.get("name") or "") == "invoke_subagent" for t in t_calls)
    exec_calls = sum(1 for t in t_calls if str(t.get("name") or "") in (FILE_TOOLS | EXEC_TOOLS | RESEARCH_TOOLS))
    if has_subagent:
        return {"required": True, "compliant": True, "exec_calls": exec_calls, "has_subagent": True}
    if exec_calls > 0:
        return {"required": True, "compliant": False, "exec_calls": exec_calls, "has_subagent": False}
    return {"required": True, "compliant": True, "exec_calls": 0, "has_subagent": False}

