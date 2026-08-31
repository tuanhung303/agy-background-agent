"""
sage.session_state - Session state persistence, turn identity hashing, and resets.
"""

import hashlib
import json
import os

from sage.guards import is_steering_message
from sage.locking import atomic_write_json, log_audit, safe_id
from sage.sage import _clear_sage_session
from sage.transcript import clean_user_prompt, get_active_turn_identity


def get_state_file_path(conv_id: str) -> str:
    """Returns the persistent state filepath for a conversation."""
    return f"/tmp/agy_sage_{safe_id(conv_id)}.json"


def save_session_state(state_file: str, state: dict, **updates) -> dict:
    """Atomically persists state dict merging any specified updates."""
    if "sage_status" in updates and "advisor_status" not in updates:
        updates["advisor_status"] = updates["sage_status"]
    elif "advisor_status" in updates and "sage_status" not in updates:
        updates["sage_status"] = updates["advisor_status"]
    if "sage_error_streak" in updates and "advisor_error_streak" not in updates:
        updates["advisor_error_streak"] = updates["sage_error_streak"]
    elif "advisor_error_streak" in updates and "sage_error_streak" not in updates:
        updates["sage_error_streak"] = updates["advisor_error_streak"]
    if "delegate_cmd_turn" in updates and "facilitation_cmd_turn" not in updates:
        updates["facilitation_cmd_turn"] = updates["delegate_cmd_turn"]
    if "cmd_ignored" in updates and "facilitation_cmd_ignored" not in updates:
        updates["facilitation_cmd_ignored"] = updates["cmd_ignored"]
    elif "facilitation_cmd_ignored" in updates and "cmd_ignored" not in updates:
        updates["cmd_ignored"] = updates["facilitation_cmd_ignored"]
    state.update(updates)
    if "background_steered_tasks" in state and isinstance(state["background_steered_tasks"], (set, list)):
        state["background_steered_tasks"] = sorted(state["background_steered_tasks"])
    atomic_write_json(state_file, state)
    return state


def record_sage_emit(state_file: str, state: dict, total_tools: int, initial_lines: int, fdec: str, ftext: str, seen_advice: dict, **extra):
    """Updates and saves state when sage emits a steer or watchout."""
    mid_steers = state.get("mid_turn_steers", 0) + (1 if fdec == "steer" else 0)
    session_mid_steers = state.get("session_mid_turn_steers", 0) + 1
    emitted_texts = (state.get("sage_emitted_texts", state.get("advisor_emitted_texts", [])) + [ftext])[-5:]
    save_session_state(
        state_file, state,
        mid_turn_steers=mid_steers,
        last_verified_tools=total_tools,
        sage_status=("fired" if fdec == "steer" else "watchout"),
        advisor_status=("fired" if fdec == "steer" else "watchout"),
        session_mid_turn_steers=session_mid_steers,
        last_sage_text=ftext,
        last_advisor_text=ftext,
        sage_advice_counts=seen_advice,
        advisor_advice_counts=seen_advice,
        sage_error_streak=0,
        advisor_error_streak=0,
        consecutive_on_track=0,
        sage_emitted_texts=emitted_texts,
        advisor_emitted_texts=emitted_texts,
        last_audited_line_count=initial_lines,
        **extra,
    )


def record_sage_hold(state_file: str, state: dict, total_tools: int, initial_lines: int, seen_advice=None, **extra):
    """Updates and saves state when sage decides to hold/pass."""
    sage_holds = state.get("sage_holds", state.get("advisor_holds", 0)) + 1
    seen = seen_advice if seen_advice is not None else state.get("sage_advice_counts", state.get("advisor_advice_counts", {}))
    consecutive = state.get("consecutive_on_track", 0) + 1
    save_session_state(
        state_file, state,
        sage_status="hold",
        advisor_status="hold",
        sage_holds=sage_holds,
        advisor_holds=sage_holds,
        consecutive_on_track=consecutive,
        last_verified_tools=total_tools,
        sage_advice_counts=seen,
        advisor_advice_counts=seen,
        sage_error_streak=0,
        advisor_error_streak=0,
        last_audited_line_count=initial_lines,
        **extra,
    )


def record_sage_recap(state_file: str, state: dict, total_tools: int, initial_lines: int, recap_text: str = "", **extra):
    """Updates and saves state when sage approves and sage recap is emitted."""
    sage_holds = state.get("sage_holds", state.get("advisor_holds", 0)) + 1
    recap_count = state.get("recap_count", 0) + 1
    consecutive = state.get("consecutive_on_track", 0) + 1
    fac_turn = state.get("facilitation_cmd_turn")
    save_session_state(
        state_file, state,
        sage_status="recap",
        advisor_status="recap",
        recap_emitted=True,
        sage_holds=sage_holds,
        advisor_holds=sage_holds,
        consecutive_on_track=consecutive,
        recap_count=recap_count,
        last_verified_tools=total_tools,
        last_audited_line_count=initial_lines,
        sage_error_streak=0,
        advisor_error_streak=0,
        sage_recap=recap_text,
        advisor_recap=recap_text,
        facilitation_cmd_turn=fac_turn,
        **extra,
    )



def record_background_steer(state_file: str, state: dict, tid: str, initial_lines: int):
    """Updates and saves state when steering for a running background task."""
    bg_steered = set(state.get("background_steered_tasks", []))
    bg_steered.add(tid)
    save_session_state(
        state_file, state,
        background_steered_tasks=bg_steered,
        last_audited_line_count=initial_lines,
    )


def record_background_grace(state_file: str, state: dict, count: int, initial_lines: int):
    """Updates and saves state when advancing background task grace count."""
    save_session_state(
        state_file, state,
        bg_watch_count=count,
        last_audited_line_count=initial_lines,
    )


def load_and_sync_session_state(conv_id: str, transcript_path: str, raw_user_prompt: str):
    """Loads session state, computes turn hashes, handles turn resets, and returns synced state."""
    state_file = get_state_file_path(conv_id)
    raw_state = {}
    legacy_file = f"/tmp/agy_advisor_{safe_id(conv_id)}.json"
    target_read = state_file if os.path.exists(state_file) else (legacy_file if os.path.exists(legacy_file) else None)
    if target_read:
        try:
            with open(target_read, "r", encoding="utf-8") as sf:
                raw_state = json.load(sf)
                if not isinstance(raw_state, dict):
                    raw_state = {}
        except Exception:
            raw_state = {}

    is_steer_input = is_steering_message(raw_user_prompt)
    if is_steer_input and raw_state.get("turn_key"):
        turn_key = raw_state["turn_key"]
        clean_prompt = clean_user_prompt(raw_user_prompt)
        prompt_hash = raw_state.get("prompt_hash", "")
        is_same = True
    else:
        clean_prompt = clean_user_prompt(raw_user_prompt)
        turn_identity = get_active_turn_identity(transcript_path)
        prompt_hash = hashlib.md5(clean_prompt.encode("utf-8")).hexdigest()
        turn_key = hashlib.sha256(f"{turn_identity}\x00{clean_prompt}".encode("utf-8")).hexdigest()
        is_same = raw_state.get("turn_key") == turn_key

    if not is_same:
        _clear_sage_session(conv_id)
        log_audit(f"New turn detected [{safe_id(conv_id)}]; sage session cleared (fresh context)")

    bg_steered = set(raw_state.get("background_steered_tasks", []))
    seen_advice = (raw_state.get("sage_advice_counts") or raw_state.get("advisor_advice_counts", {})) if is_same else {}
    err_streak = (raw_state.get("sage_error_streak") or raw_state.get("advisor_error_streak", 0)) if is_same else 0
    emitted_texts = (raw_state.get("sage_emitted_texts") or raw_state.get("advisor_emitted_texts", [])) if is_same else []
    mid_turn_steers, last_verified = (raw_state.get("mid_turn_steers", 0), raw_state.get("last_verified_tools", 0)) if is_same else (0, 0)
    recap_emitted, bg_watch_count, last_lines = (raw_state.get("recap_emitted", False), raw_state.get("bg_watch_count", 0), raw_state.get("last_audited_line_count", 0)) if is_same else (False, 0, 0)
    last_final_lines = raw_state.get("last_final_gate_lines", 0) if is_same else 0
    sage_status = (raw_state.get("sage_status") or raw_state.get("advisor_status", "hold")) if is_same else "hold"
    last_sage_text = (raw_state.get("last_sage_text") or raw_state.get("last_advisor_text", "")) if is_same else ""
    last_steer_category = raw_state.get("last_steer_category") if is_same else None
    last_steer_tools = raw_state.get("last_steer_tools", 0) if is_same else 0
    steer_suppress_count = raw_state.get("steer_suppress_count", 0) if is_same else 0
    sage_recap = (raw_state.get("sage_recap") or raw_state.get("advisor_recap", "")) if is_same else ""
    consecutive_on_track = raw_state.get("consecutive_on_track", 0) if is_same else 0

    # Preserved across turns:
    sage_holds = raw_state.get("sage_holds", raw_state.get("advisor_holds", 0))
    recap_count, sm_steers = raw_state.get("recap_count", 0), raw_state.get("session_mid_turn_steers", 0)
    goal_settled = bool(raw_state.get("goal_settled", False)) if is_same else False
    delegate_cmd_turn = (raw_state.get("delegate_cmd_turn") or raw_state.get("facilitation_cmd_turn")) if is_same else None
    review_base_sha = raw_state.get("review_base_sha") if is_same else None
    facilitation_cmd_turn = delegate_cmd_turn
    cmd_ignored = int(raw_state.get("cmd_ignored", 0) or raw_state.get("facilitation_cmd_ignored", 0) or 0) if is_same else 0
    facilitation_cmd_ignored = cmd_ignored
    pinned_goal = raw_state.get("pinned_goal") or raw_state.get("anchor_goal")
    revised_goal = raw_state.get("revised_goal")
    derived_tasks = list(raw_state.get("derived_tasks", []))
    goal_revisions = list(raw_state.get("goal_revisions", []))
    pinned_emitted = bool(raw_state.get("pinned_emitted", raw_state.get("anchor_emitted", False)))
    task_complexity = raw_state.get("task_complexity")
    last_par_cats = list(raw_state.get("last_par_cats", [])) if is_same else []
    last_par_fp = raw_state.get("last_par_fp", []) if is_same else []
    review_gate_fired = bool(raw_state.get("review_gate_fired", False)) if is_same else False

    state = {
        "turn_key": turn_key, "prompt_hash": prompt_hash,
        "mid_turn_steers": mid_turn_steers, "last_verified_tools": last_verified, "recap_emitted": recap_emitted,
        "last_audited_line_count": last_lines, "last_final_gate_lines": last_final_lines,
        "background_steered_tasks": sorted(bg_steered),
        "bg_watch_count": bg_watch_count, "sage_status": sage_status, "advisor_status": sage_status,
        "sage_holds": sage_holds, "advisor_holds": sage_holds, "recap_count": recap_count,
        "session_mid_turn_steers": sm_steers, "last_sage_text": last_sage_text, "last_advisor_text": last_sage_text,
        "sage_advice_counts": seen_advice, "advisor_advice_counts": seen_advice,
        "sage_emitted_texts": emitted_texts, "advisor_emitted_texts": emitted_texts,
        "sage_error_streak": err_streak, "advisor_error_streak": err_streak,
        "consecutive_on_track": consecutive_on_track,
        "pinned_goal": pinned_goal, "anchor_goal": pinned_goal, "revised_goal": revised_goal, "derived_tasks": derived_tasks,
        "goal_revisions": goal_revisions, "sage_recap": sage_recap, "advisor_recap": sage_recap,
        "pinned_emitted": pinned_emitted, "anchor_emitted": pinned_emitted, "task_complexity": task_complexity,
        "last_steer_category": last_steer_category, "last_steer_tools": last_steer_tools,
        "steer_suppress_count": steer_suppress_count,
        "last_par_cats": last_par_cats, "last_par_fp": last_par_fp,
        "goal_settled": goal_settled,
        "facilitation_cmd_turn": facilitation_cmd_turn,
        "delegate_cmd_turn": delegate_cmd_turn,
        "review_base_sha": review_base_sha,
        "facilitation_cmd_ignored": facilitation_cmd_ignored,
        "cmd_ignored": cmd_ignored,
        "review_gate_fired": review_gate_fired,
    }

    return clean_prompt, state_file, state, is_same
