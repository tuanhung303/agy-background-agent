"""
sage.session_state - Session state persistence, turn identity hashing, and resets.
"""

import hashlib
import json
import os

from sage.executor import clean_resume_history, clear_session_id, load_session_id
from sage.guards import is_steering_message
from sage.locking import atomic_write_json, log_audit, safe_id
from sage.transcript import clean_user_prompt, get_active_turn_identity


def _clear_sage_session(parent_conv_id: str) -> None:
    old_sid = load_session_id(parent_conv_id, ("agy_mid_sage_session_", "agy_mid_advisor_session_", "agy_mid_verifier_session_"))
    if old_sid:
        clean_resume_history(old_sid)
    clear_session_id(parent_conv_id, ("agy_mid_sage_session_", "agy_mid_advisor_session_", "agy_mid_verifier_session_"))


def get_state_file_path(conv_id: str) -> str:
    """Returns the persistent state filepath for a conversation."""
    return f"/tmp/agy_sage_{safe_id(conv_id)}.json"


def save_session_state(state_file: str, state: dict, **updates) -> dict:
    """Atomically persists state dict merging any specified updates."""
    if "sage_status" in updates and "advisor_status" not in updates:
        updates["advisor_status"] = updates["sage_status"]
    elif "advisor_status" in updates and "sage_status" not in updates:
        updates["sage_status"] = updates["advisor_status"]
    state.update(updates)
    atomic_write_json(state_file, state)
    return state


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

    last_lines = raw_state.get("last_audited_line_count", 0) if is_same else 0
    recap_emitted = raw_state.get("recap_emitted", False) if is_same else False
    sage_status = (raw_state.get("sage_status") or raw_state.get("advisor_status", "hold")) if is_same else "hold"
    lite_status = str(raw_state.get("lite_status", "")) if is_same else ""
    lite_fail_count = int(raw_state.get("lite_fail_count", 0)) if is_same else 0

    state = {
        "turn_key": turn_key,
        "prompt_hash": prompt_hash,
        "recap_emitted": recap_emitted,
        "last_audited_line_count": last_lines,
        "sage_status": sage_status,
        "advisor_status": sage_status,
        "lite_status": lite_status,
        "lite_fail_count": lite_fail_count,
    }

    return clean_prompt, state_file, state, is_same

