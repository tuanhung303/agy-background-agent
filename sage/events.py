"""
sage.events - Human-readable event summon context formatting for the strategic sage.
"""

EVENT_FINAL_STOP = "final_stop"
EVENT_HEARTBEAT = "heartbeat"
EVENT_TOOL_THRESHOLD = "tool_threshold"
EVENT_ERROR_LOOP = "error_loop"
EVENT_SENSITIVE_TOOL = "sensitive_tool"
EVENT_STALE_TASK = "stale_task"
EVENT_PARALLEL_OPP = "parallel_opportunity"


def format_summon_message(event_type, **kwargs):
    """Formats a clear, human-readable summon message for the strategic sage."""
    if event_type == EVENT_FINAL_STOP:
        return (
            "Final stop: decide recap (terminate) or steer (continue). Enforce the Final Stop Gate and live empirical evidence: "
            "You are being summoned because the agent is attempting to conclude the session. "
            "Your objective is to enforce the Final Stop Gate: verify that all user requirements are satisfied with live empirical proof, "
            "reject passive stops on trivial questions, and ask yourself before permitting completion: "
            "'Can the user confidently ship this code to production, or distribute this to the customer right now without hidden regressions or unhandled defects?' "
            "If the agent stopped on a passive question ('Shall I apply...'), or if unaddressed review findings/defects remain, do NOT recap; steer the agent to fix them."
        )

    if event_type == EVENT_HEARTBEAT:
        dur = kwargs.get("duration", 180.0)
        return (
            f"You are being summoned because the executing agent has been running tools for {dur:.0f} seconds without reporting user progress. "
            "Your objective is to inspect whether the agent is waiting on an active background task, caught in a hang or deadlock, or making healthy progress. "
            "If the agent is stuck, provide concrete steering to unblock it; otherwise, provide a brief status update."
        )

    if event_type == EVENT_TOOL_THRESHOLD:
        tools = kwargs.get("total_tools", 10)
        delta = kwargs.get("delta_tools", 10)
        goal = kwargs.get("pinned_goal") or kwargs.get("goal") or "the active user request"
        return (
            f"You are being summoned because the agent has completed a heavy sequence of {tools} tool calls (delta: {delta}). "
            f"Your objective is to evaluate the agent's trajectory against the target goal ('{goal}'), "
            "verify that the work has not drifted into unnecessary refactoring, and catch potential architectural traps early."
        )

    if event_type == EVENT_ERROR_LOOP:
        streak = kwargs.get("error_streak", 5)
        tool = kwargs.get("tool_name", "the last tool")
        sig = kwargs.get("error_sig", "repeated failure")
        return (
            f"You are being summoned because the agent has encountered {streak} consecutive tool failures while executing `{tool}`. "
            f"Error signature: '{sig}'. "
            "Your objective is to diagnose the underlying root cause, stop the agent from guessing blindly, and provide an exact, actionable fix."
        )

    if event_type == EVENT_SENSITIVE_TOOL:
        kw = kwargs.get("keyword", "sensitive command")
        cmd = kwargs.get("command_snippet", "")
        cmd_info = f" (`{cmd}`)" if cmd else ""
        return (
            f"You are being summoned because the agent invoked a sensitive command matching keyword '{kw}'{cmd_info}. "
            "Your objective is to verify that target environments, preconditions, and safety guardrails are satisfied before irreversible mutations proceed."
        )

    if event_type == EVENT_STALE_TASK:
        tid = kwargs.get("task_id", "background task")
        desc = kwargs.get("task_desc", "unnamed task")
        age = kwargs.get("age_seconds", 300.0)
        return (
            f"You are being summoned because background task '{tid}' ('{desc}') has been running for {age:.0f}s without concluding. "
            "Your objective is to determine if the task is actively producing output or confirmed hung, and advise the agent whether to keep watching or terminate it."
        )

    if event_type == EVENT_PARALLEL_OPP:
        sig_text = kwargs.get("signal_text", "multiple independent workstreams detected")
        return (
            f"You are being summoned because an opportunity for parallel execution was detected ({sig_text}). "
            "Your objective is to evaluate whether these independent workstreams can be dispatched concurrently via `invoke_subagent` to accelerate execution."
        )

    return kwargs.get("fallback_signal", "Evaluate agent trajectory and progress against the target goal.")
