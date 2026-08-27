"""sage.facilitation - Post-settle delegation advice (advice-only, never blocks).

After sage approves a recap (goal_settled in session state), the main agent
becomes a facilitator: further execution and test work should be delegated to
subagents with a fully distilled dispatch payload. This module detects the
condition and builds the advisory signal; it never changes the gate verdict.
"""

from sage.events import EVENT_FACILITATION, format_summon_message
from sage.guards import is_steering_message
from sage.task_structure import EXEC_TOOLS, FILE_TOOLS, RESEARCH_TOOLS
from sage.transcript import _read_transcript_steps


def _turn_tool_calls(steps):
    turn_idxs = [
        i for i, s in enumerate(steps)
        if isinstance(s, dict) and s.get("type") == "USER_INPUT"
        and str(s.get("source") or "").upper() in ("USER_EXPLICIT", "USER", "")
        and not is_steering_message(str(s.get("content") or ""))
    ]
    t_steps = steps[turn_idxs[-1] + 1:] if turn_idxs else steps
    return [
        t for s in t_steps if isinstance(s, dict)
        for t in (s.get("tool_calls") if isinstance(s.get("tool_calls"), list) else [])
        if isinstance(t, dict) and t.get("name")
    ]


def facilitation_signal(transcript_path, state):
    """Advisory text when the main agent executes inline after goal settle."""
    if not state.get("goal_settled"):
        return ""
    steps = _read_transcript_steps(transcript_path) if transcript_path else []
    t_calls = _turn_tool_calls(steps)
    if any(str(t.get("name") or "") == "invoke_subagent" for t in t_calls):
        return ""
    exec_calls = sum(1 for t in t_calls if str(t.get("name") or "") in (FILE_TOOLS | EXEC_TOOLS | RESEARCH_TOOLS))
    if exec_calls < 1:
        return ""
    return format_summon_message(
        EVENT_FACILITATION, exec_calls=exec_calls,
        signal_text="goal settled; delegate execution to subagents with distilled payload",
    )
