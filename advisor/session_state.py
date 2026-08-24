"""
advisor.session_state - Session state persistence, turn identity hashing, and resets.
"""

import hashlib
import json
import os

from advisor.advisor import _clear_advisor_session
from advisor.locking import atomic_write_json, log_audit, safe_id
from advisor.transcript import clean_user_prompt, get_active_turn_identity


def get_state_file_path(conv_id: str) -> str:
    """Returns the persistent state filepath for a conversation."""
    return f"/tmp/agy_advisor_{safe_id(conv_id)}.json"


def save_session_state(state_file: str, state: dict, **updates) -> dict:
    """Atomically persists state dict merging any specified updates."""
    state.update(updates)
    if "background_steered_tasks" in state and isinstance(state["background_steered_tasks"], (set, list)):
        state["background_steered_tasks"] = sorted(state["background_steered_tasks"])
    atomic_write_json(state_file, state)
    return state


def record_advisor_emit(state_file: str, state: dict, total_tools: int, initial_lines: int, fdec: str, ftext: str, seen_advice: dict, **extra):
    """Updates and saves state when advisor emits a steer or watchout."""
    mid_steers = state.get("mid_turn_steers", 0) + (1 if fdec == "steer" else 0)
    session_mid_steers = state.get("session_mid_turn_steers", 0) + 1
    emitted_texts = (state.get("advisor_emitted_texts", []) + [ftext])[-5:]
    save_session_state(
        state_file, state,
        mid_turn_steers=mid_steers,
        last_verified_tools=total_tools,
        advisor_status=("fired" if fdec == "steer" else "watchout"),
        session_mid_turn_steers=session_mid_steers,
        last_advisor_text=ftext,
        advisor_advice_counts=seen_advice,
        advisor_error_streak=0,
        advisor_emitted_texts=emitted_texts,
        last_audited_line_count=initial_lines,
        **extra,
    )


def record_advisor_hold(state_file: str, state: dict, total_tools: int, initial_lines: int, seen_advice=None, **extra):
    """Updates and saves state when advisor decides to hold/pass."""
    adv_holds = state.get("advisor_holds", 0) + 1
    seen = seen_advice if seen_advice is not None else state.get("advisor_advice_counts", {})
    save_session_state(
        state_file, state,
        advisor_status="hold",
        advisor_holds=adv_holds,
        last_verified_tools=total_tools,
        advisor_advice_counts=seen,
        advisor_error_streak=0,
        last_audited_line_count=initial_lines,
        **extra,
    )


def record_advisor_recap(state_file: str, state: dict, total_tools: int, initial_lines: int, recap_text: str = "", **extra):
    """Updates and saves state when advisor approves and advisor recap is emitted."""
    adv_holds = state.get("advisor_holds", 0) + 1
    recap_count = state.get("recap_count", 0) + 1
    save_session_state(
        state_file, state,
        advisor_status="recap",
        recap_emitted=True,
        advisor_holds=adv_holds,
        recap_count=recap_count,
        last_verified_tools=total_tools,
        last_audited_line_count=initial_lines,
        advisor_recap=recap_text,
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
    clean_prompt = clean_user_prompt(raw_user_prompt)
    turn_identity = get_active_turn_identity(transcript_path)
    prompt_hash = hashlib.md5(clean_prompt.encode("utf-8")).hexdigest()
    turn_key = hashlib.sha256(f"{turn_identity}\x00{clean_prompt}".encode("utf-8")).hexdigest()

    state_file = get_state_file_path(conv_id)
    raw_state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as sf:
                raw_state = json.load(sf)
                if not isinstance(raw_state, dict):
                    raw_state = {}
        except Exception:
            raw_state = {}

    is_same = raw_state.get("turn_key") == turn_key
    if not is_same:
        _clear_advisor_session(conv_id)
        log_audit(f"New turn detected [{safe_id(conv_id)}]; advisor session cleared (fresh context)")

    bg_steered = set(raw_state.get("background_steered_tasks", []))
    seen_advice = raw_state.get("advisor_advice_counts", {}) if is_same else {}
    err_streak = raw_state.get("advisor_error_streak", 0) if is_same else 0
    emitted_texts = raw_state.get("advisor_emitted_texts", []) if is_same else []
    mid_turn_steers = raw_state.get("mid_turn_steers", 0) if is_same else 0
    last_verified = raw_state.get("last_verified_tools", 0) if is_same else 0
    recap_emitted = raw_state.get("recap_emitted", False) if is_same else False
    bg_watch_count = raw_state.get("bg_watch_count", 0) if is_same else 0
    last_lines = raw_state.get("last_audited_line_count", 0) if is_same else 0
    adv_status = raw_state.get("advisor_status", "hold") if is_same else "hold"
    last_adv_text = raw_state.get("last_advisor_text", "") if is_same else ""
    adv_recap = raw_state.get("advisor_recap", "") if is_same else ""

    # Preserved across turns:
    adv_holds = raw_state.get("advisor_holds", 0)
    recap_count = raw_state.get("recap_count", 0)
    sm_steers = raw_state.get("session_mid_turn_steers", 0)
    pinned_goal = raw_state.get("pinned_goal") or raw_state.get("anchor_goal")
    revised_goal = raw_state.get("revised_goal")
    derived_tasks = list(raw_state.get("derived_tasks", []))
    goal_revisions = list(raw_state.get("goal_revisions", []))
    pinned_emitted = bool(raw_state.get("pinned_emitted", raw_state.get("anchor_emitted", False)))
    task_complexity = raw_state.get("task_complexity")

    state = {
        "turn_key": turn_key, "prompt_hash": prompt_hash,
        "mid_turn_steers": mid_turn_steers, "last_verified_tools": last_verified, "recap_emitted": recap_emitted,
        "last_audited_line_count": last_lines, "background_steered_tasks": sorted(bg_steered),
        "bg_watch_count": bg_watch_count, "advisor_status": adv_status,
        "advisor_holds": adv_holds, "recap_count": recap_count,
        "session_mid_turn_steers": sm_steers, "last_advisor_text": last_adv_text,
        "advisor_advice_counts": seen_advice, "advisor_emitted_texts": emitted_texts, "advisor_error_streak": err_streak,
        "pinned_goal": pinned_goal, "anchor_goal": pinned_goal, "revised_goal": revised_goal, "derived_tasks": derived_tasks,
        "goal_revisions": goal_revisions, "advisor_recap": adv_recap,
        "pinned_emitted": pinned_emitted, "anchor_emitted": pinned_emitted, "task_complexity": task_complexity,
    }

    return clean_prompt, state_file, state, is_same
