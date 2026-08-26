"""
sage.sage - Mid-turn slow-thinking strategic sage steering the fast executor.
"""

import os
import re

from sage.config import REVIEWER_MODEL, SAGE_TOOL_INTERVAL, SAGE_TOOL_SCORE_THRESHOLD
from sage.executor import (
    acquire_spawn_lock, clean_resume_history, clear_session_id,
    extract_json_from_llm_output, load_session_id, release_spawn_lock,
    run_model_cascade, save_session_id,
)
from sage.goals import format_goal_context
from sage.guards import is_destructive_action
from sage.locking import log_audit
from sage.models import resolve_model_candidates
from sage.sanitizer import clamp_diff
from sage.transcript import calculate_turn_tool_score

TEMPLATE_CANDIDATES = (
    os.path.expanduser("~/.config/agy/sage_prompt.md"), os.path.abspath(os.path.join(os.path.dirname(__file__), "sage_prompt.md")),
    os.path.expanduser("~/.config/agy/advisor_prompt.md"), os.path.abspath(os.path.join(os.path.dirname(__file__), "advisor_prompt.md")),
    os.path.expanduser("~/.config/agy/verifier_prompt.md"),
)
TEMPLATE_FILE, DEFAULT_TEMPLATE = TEMPLATE_CANDIDATES[0], 'Acts as wise strategist sage. JSON output: {"status": "on_track"|"watchout"|"off_track", ...}.'
_clamp_diff, _acquire_spawn_lock, _release_spawn_lock = clamp_diff, acquire_spawn_lock, release_spawn_lock


def get_or_create_sage_session(parent_conv_id):
    return load_session_id(parent_conv_id, ("agy_mid_sage_session_", "agy_mid_advisor_session_", "agy_mid_verifier_session_"))


def save_sage_session(parent_conv_id, session_id):
    save_session_id(parent_conv_id, session_id, "agy_mid_sage_session_")


def _clear_sage_session(parent_conv_id):
    old_sid = get_or_create_sage_session(parent_conv_id)
    if old_sid:
        clean_resume_history(old_sid)
    clear_session_id(parent_conv_id, ("agy_mid_sage_session_", "agy_mid_advisor_session_", "agy_mid_verifier_session_"))


def load_sage_template():
    for p in TEMPLATE_CANDIDATES:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    c = f.read().strip()
                if c:
                    return c
            except Exception:
                pass
    return DEFAULT_TEMPLATE

GOAL_MARKERS = ("[LATEST ACTIVE USER REQUEST (CURRENT GOAL)]:", "[LATEST ACTIVE USER REQUEST]:")
STATUS_LEGEND = (
    "Role lock: you remain the Sage to a separate executing agent. Do not write code, edit files, or finish the work; "
    "fetch more intermediate steps or run read-only validation commands (e.g. transcript log inspection, `git status`, test runs, `grep_search`, `view_file`) "
    "whenever needed to verify evidence, ground realistic goals, and steer accurately. Never emit `passed`; never answer the user's request yourself or continue the agent's task. "
    'Judge the context and tool outputs, address the agent as "you", and reply with exactly one JSON object -- no preamble, no fence.\n'
    "Status legend: `on_track` = clean progress; `watchout` = trap/risk/missing deliverable; `off_track` = error loop/drift.\n"
    'Respond JSON: `{"status": "on_track"|"watchout"|"off_track", "task_complexity": "simple_qa"|"complex_code"|"multi_file", "category": "pinned_goal"|"loop_detection"|"irreversible_risk"|"missing_deliverable"|"algorithmic_bottleneck"|"scope_drift"|"fake_verification"|"parallelize_subagent"|"parallelize"|"architectural_trap"|"general", "action": "exact command/path", "evidence": "...", "confidence": 0.0-1.0, "guidance": "...", "recap": "...", "escalation": "first_warning"|"ignored_advice", "pinned_goal": "optional", "revised_goal": "optional", "derived_tasks": ["optional"]}`.'
)


def extract_target_goal(user_prompt, limit=500):
    if not user_prompt:
        return ""
    text = str(user_prompt)
    for marker in GOAL_MARKERS:
        idx = text.rfind(marker)
        if idx != -1:
            goal = text[idx + len(marker) :].strip()
            if goal:
                return goal[:limit].strip()
    return text[-limit:].strip()


def build_sage_prompt(conv_id, user_prompt, agent_steps_summary, is_update=False, git_diff="", signals="", pinned_goal=None, revised_goal=None, derived_tasks=None, **kwargs):
    steps_txt = agent_steps_summary[-4000:] if len(agent_steps_summary) > 4000 else agent_steps_summary
    workers_block = ""
    workers_facts = str(kwargs.get("worker_facts") or "")
    if workers_facts:
        workers_block = f"\n\nDELEGATED WORKERS (deterministic facts — trust these over any 'completed' claim):\n{workers_facts}\n"
    diff_txt, sig_txt = clamp_diff(git_diff), (f"\n\nACTIVE SIGNALS:\n{signals}" if signals else "")
    base_goal = pinned_goal or kwargs.get("anchor_goal")
    goal_block = format_goal_context(base_goal, revised_goal, derived_tasks)
    if is_update:
        goal_txt = f"{goal_block}\n\n" if goal_block else (f"TARGET GOAL (unchanged): {extract_target_goal(user_prompt)}\n\n" if extract_target_goal(user_prompt) else "")
        return f"SAGE UPDATE (Follow-up Check for conversation {conv_id or 'default'}):\n{goal_txt}Evaluate recent agent actions against TARGET GOAL. If this is your first check in this conversation, evaluate the actions as-is.\n\nAGENT ACTIONS (RECENT):\n{steps_txt}{workers_block}\n\nGIT DIFF / MODIFICATIONS:\n{diff_txt}\n\n{STATUS_LEGEND}{sig_txt}"
    tpl = load_sage_template().replace("{update_marker}", "")
    goal = extract_target_goal(user_prompt, limit=2000)
    prompt_txt = f"{user_prompt[:2000]}\n\n{goal_block}" if goal_block else (user_prompt[:2000] if goal in user_prompt[:2000] else f"[TARGET GOAL]:\n{goal}")
    tpl = tpl.replace("{agent_steps}", f"{steps_txt}{workers_block}")
    return tpl.replace("{conv_id}", str(conv_id or "default")).replace("{user_prompt}", prompt_txt).replace("{git_diff}", diff_txt) + sig_txt


def _normalize_sage_dict(d):
    if not isinstance(d, dict):
        return {"healthy": True, "blind_spots": [], "guidance": "", "status": "on_track"}
    raw_status, raw_healthy = str(d.get("status") or "").strip(), d.get("healthy")
    compact_status = re.sub(r"[^a-z]", "", re.sub(r"[\s-]+", "_", raw_status.lower()))
    blind_spots = d.get("blind_spots", []) if isinstance(d.get("blind_spots"), list) else ([d["blind_spots"]] if d.get("blind_spots") else [])
    watchouts = d.get("watchouts", []) if isinstance(d.get("watchouts"), list) else ([d["watchouts"]] if d.get("watchouts") else [])
    if "watchout" in d and isinstance(d["watchout"], str) and d["watchout"].strip() and d["watchout"] not in watchouts:
        watchouts.append(d["watchout"].strip())
    suppressed = "[Destructive command suppressed] Avoid destructive commands; verify first."
    sanitize = lambda value: suppressed if is_destructive_action(str(value or "")) else str(value or "").strip()
    blind_spots = [sanitize(item) for item in blind_spots if str(item or "").strip()]
    watchouts = [sanitize(item) for item in watchouts if str(item or "").strip()]
    guidance = sanitize(d.get("guidance") or d.get("steering") or "")

    if compact_status in ("offtrack", "unhealthy", "steer", "fail", "failed", "intervention", "error", "err", "broken", "bug") or raw_healthy is False or (blind_spots and raw_healthy is not True):
        healthy, status = False, "off_track"
    elif compact_status in ("watchout", "warning", "caution", "headsup", "gotcha", "watch") or watchouts or ("watch" in compact_status):
        healthy, status = True, "watchout"
    else:
        healthy, status = True, "on_track"

    action = str(d.get("action") or "").strip()
    if is_destructive_action(action):
        action = "[Destructive action suppressed] Use safe verification."
    if status == "watchout" and not watchouts and not guidance and not action:
        status = "on_track"

    res = {"healthy": healthy, "blind_spots": blind_spots, "guidance": guidance, "status": status}
    if "recap" in d and d["recap"] is not None and str(d["recap"]).strip():
        res["recap"] = sanitize(d["recap"])
    if watchouts:
        res["watchouts"] = watchouts
    if action:
        res["action"] = action
    for k in ("evidence", "confidence", "category", "escalation", "pinned_goal", "anchor_goal", "revised_goal", "goal_status", "task_complexity"):
        if k in d and d[k] is not None:
            res[k] = sanitize(d[k]) if k in ("evidence", "pinned_goal", "anchor_goal", "revised_goal") else d[k]
    p_goal = res.get("pinned_goal") or res.get("anchor_goal")
    if p_goal:
        res["pinned_goal"] = p_goal
        res["anchor_goal"] = p_goal
    if "task_complexity" in res and res["task_complexity"]:
        tc = re.sub(r"[\s-]+", "_", str(res["task_complexity"]).strip().lower())
        res["task_complexity"] = "simple_qa" if ("simple" in tc or "qa" in tc) else ("multi_file" if "multi" in tc else ("complex_code" if ("complex" in tc or "code" in tc) else tc))
    if "derived_tasks" in d and isinstance(d["derived_tasks"], list):
        res["derived_tasks"] = [sanitize(t) for t in d["derived_tasks"] if str(t or "").strip()][:10]
    return res


def parse_sage_output(raw_text):
    if not raw_text or not raw_text.strip():
        return {"healthy": True, "blind_spots": [], "guidance": "", "status": "on_track"}
    d = extract_json_from_llm_output(raw_text, schema_keys=("status", "healthy", "blind_spots", "watchouts", "guidance"))
    return _normalize_sage_dict(d) if d is not None else {"healthy": True, "blind_spots": [], "guidance": "", "status": "on_track"}


def run_sage_model(parent_conv_id, user_prompt, agent_steps_summary, git_diff="", signals="", pinned_goal=None, revised_goal=None, derived_tasks=None, **kwargs):
    existing_session = get_or_create_sage_session(parent_conv_id)
    log_audit(f"Sage prompt mode: {'update' if existing_session else 'initial'} [{parent_conv_id}]")
    base_goal = pinned_goal or kwargs.get("anchor_goal")
    prompt = build_sage_prompt(
        parent_conv_id, user_prompt, agent_steps_summary, bool(existing_session),
        git_diff, signals=signals, pinned_goal=base_goal, revised_goal=revised_goal, derived_tasks=derived_tasks,
        worker_facts=kwargs.get("worker_facts"),
    )
    default_res = {"healthy": True, "blind_spots": [], "guidance": "", "status": "error"}
    return run_model_cascade(
        parent_conv_id, prompt, ("agy_mid_sage_session_", "agy_mid_advisor_session_", "agy_mid_verifier_session_"),
        _normalize_sage_dict, default_on_failure=default_res, label="Sage",
        schema_keys=("status", "healthy", "recap", "guidance"),
        acquire_lock_fn=acquire_spawn_lock, release_lock_fn=release_spawn_lock,
        resolve_candidates_fn=resolve_model_candidates, clean_resume_fn=clean_resume_history,
    )


_ORIG_RUN = run_sage_model


def evaluate_mid_turn_progress(conv_id, transcript_path, total_tool_calls, turn_tool_names, user_prompt, agent_steps, git_diff, state, is_forced=False, signals=""):
    last_verified = state.get("last_verified_tools", 0)
    delta = total_tool_calls - last_verified
    delta_score, _ = calculate_turn_tool_score(transcript_path, last_verified) if transcript_path and os.path.exists(transcript_path) else (0.0, 0)
    if not is_forced and delta < SAGE_TOOL_INTERVAL and delta_score < SAGE_TOOL_SCORE_THRESHOLD:
        return {"healthy": True, "skipped": True, "tool_delta": delta, "score_delta": delta_score}
    steps_summary = "\n".join(agent_steps[-10:]) if agent_steps else "No step details recorded."
    from sage.transcript import _read_transcript_steps
    from sage.workers import extract_worker_facts
    worker_facts = extract_worker_facts(_read_transcript_steps(transcript_path)) if transcript_path and os.path.exists(transcript_path) else ""
    log_audit(f"Running mid-turn sage (tools={total_tool_calls}, delta={delta}, model={REVIEWER_MODEL})...")
    _run_fn = run_sage_model if run_sage_model is not _ORIG_RUN else (run_advisor_model if run_advisor_model is not _ORIG_RUN else (run_verifier_model if run_verifier_model is not _ORIG_RUN else run_sage_model))
    return _run_fn(
        conv_id, user_prompt, steps_summary, git_diff=git_diff, signals=signals,
        pinned_goal=state.get("pinned_goal") or state.get("anchor_goal"), revised_goal=state.get("revised_goal"),
        derived_tasks=state.get("derived_tasks"), worker_facts=worker_facts,
    )


# Backward-compat aliases
get_or_create_advisor_session = get_or_create_verifier_session = get_or_create_sage_session
save_advisor_session = save_verifier_session = save_sage_session
_clear_advisor_session = _clear_verifier_session = _clear_sage_session
load_advisor_template = load_sage_template
build_advisor_prompt = build_verifier_prompt = build_sage_prompt
_normalize_advisor_dict = _normalize_verifier_dict = _normalize_sage_dict
parse_advisor_output = parse_verifier_output = parse_sage_output
run_advisor_model = run_verifier_model = run_sage_model
