"""
advisor.executor - Subprocess AGY execution, session persistence, and JSON decoding.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time

from advisor.config import ADVISOR_EXEC_TIMEOUT, ADVISOR_TIMEOUT_BUDGET
from advisor.locking import acquire_spawn_lock, log_audit, release_spawn_lock, safe_id
from advisor.models import cache_working_model, resolve_model_candidates

SPAWN_LOCK_FILE = "/tmp/agy_auditor_spawn.lock"
CONV_DB_DIR = os.path.expanduser("~/.gemini/antigravity-cli/conversations")


def clean_resume_history(conv_id):
    if not conv_id:
        return
    sid = safe_id(conv_id)
    try:
        db_path = os.path.expanduser("~/.gemini/antigravity-cli/conversation_summaries.db")
        if os.path.isfile(db_path):
            with sqlite3.connect(db_path, timeout=5) as conn:
                conn.execute("DELETE FROM conversation_summaries WHERE conversation_id IN (?, ?)", (conv_id, sid))
                conn.commit()
    except Exception as e:
        log_audit(f"Failed to clean summary db for {conv_id}: {e}")
    for name in {conv_id, sid}:
        try:
            conv_dir = os.path.expanduser("~/.gemini/antigravity-cli/conversations")
            if os.path.isdir(conv_dir):
                for suffix in ("", ".db", ".db-wal", ".db-shm"):
                    p = os.path.join(conv_dir, f"{name}{suffix}" if suffix else name)
                    if os.path.isfile(p):
                        os.remove(p)
            brain_dir = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{name}")
            if os.path.isdir(brain_dir):
                shutil.rmtree(brain_dir, ignore_errors=True)
        except Exception as e:
            log_audit(f"Failed to remove artifacts for {name}: {e}")


def get_session_file(parent_conv_id, prefix="agy_stop_audit_session_"):
    return f"/tmp/{prefix}{safe_id(parent_conv_id)}.txt"


def load_session_id(parent_conv_id, prefixes=("agy_stop_audit_session_",)):
    if not parent_conv_id:
        return None
    target_prefixes = (prefixes,) if isinstance(prefixes, str) else prefixes
    for prefix in target_prefixes:
        sf = get_session_file(parent_conv_id, prefix)
        if os.path.exists(sf):
            try:
                with open(sf, "r", encoding="utf-8") as f:
                    sid = f.read().strip()
                if sid:
                    return sid
            except Exception:
                pass
    return None


def save_session_id(parent_conv_id, session_id, prefix="agy_stop_audit_session_"):
    if parent_conv_id and session_id:
        try:
            with open(get_session_file(parent_conv_id, prefix), "w", encoding="utf-8") as f:
                f.write(session_id.strip())
        except Exception as e:
            log_audit(f"Failed to persist session {prefix}: {e}")


def clear_session_id(parent_conv_id, prefixes=("agy_stop_audit_session_",), prefix=None):
    if not parent_conv_id:
        return
    targets = prefix if prefix is not None else prefixes
    target_prefixes = (targets,) if isinstance(targets, str) else targets
    for p in target_prefixes:
        sf = get_session_file(parent_conv_id, p)
        if os.path.exists(sf):
            try:
                os.remove(sf)
            except Exception:
                pass


def extract_json_from_llm_output(raw_text, schema_keys=()):
    if not raw_text or not raw_text.strip():
        return None
    raw = raw_text.strip()
    for cand in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL):
        try:
            d = json.loads(cand)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", raw):
        try:
            d, _ = dec.raw_decode(raw[m.start():])
            if isinstance(d, dict) and (not schema_keys or any(k in d for k in schema_keys)):
                return d
        except Exception:
            continue
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    try:
        return json.loads(raw)
    except Exception:
        return None


def _find_new_conv_id(conv_dir, before_dbs):
    if not os.path.exists(conv_dir):
        return None
    diffs = sorted([f for f in (set(os.listdir(conv_dir)) - before_dbs) if f.endswith(".db")], key=lambda f: os.path.getmtime(os.path.join(conv_dir, f)), reverse=True)
    return diffs[0].replace(".db", "") if diffs else None


def _clean_cascade_session(parent_conv_id, prefixes, conv_dir, before_dbs, clean_fn, existing=None):
    cid = existing or _find_new_conv_id(conv_dir, before_dbs)
    if cid:
        clean_fn(cid)
    clear_session_id(parent_conv_id, prefixes)
    return cid


def run_model_cascade(
    parent_conv_id, prompt, prefixes, normalize_func, default_on_failure,
    label="Advisor", timeout_budget=ADVISOR_TIMEOUT_BUDGET, schema_keys=(),
    acquire_lock_fn=acquire_spawn_lock, release_lock_fn=release_spawn_lock,
    resolve_candidates_fn=resolve_model_candidates, clean_resume_fn=clean_resume_history,
):
    existing_session = load_session_id(parent_conv_id, prefixes)
    start_t, agy_bin = time.time(), (shutil.which("agy") or os.path.expanduser("~/.local/bin/agy"))
    candidates, conv_dir = (resolve_candidates_fn() or [])[:4], CONV_DB_DIR
    spawn_lock_fh = acquire_lock_fn() if not existing_session else None
    before_dbs = set(os.listdir(conv_dir)) if os.path.exists(conv_dir) else set()

    try:
        env = dict(os.environ, AGY_STOP_AUDIT_ACTIVE="1", HOME=os.path.expanduser("~"))
        env["PATH"] = f"{os.path.expanduser('~/.local/bin')}:{os.environ.get('PATH', '')}"

        for model in candidates:
            remaining = timeout_budget - (time.time() - start_t)
            if remaining <= 2.0:
                log_audit(f"{label} overall timeout budget reached; halting fallback attempts")
                break
            if not existing_session and not spawn_lock_fh:
                spawn_lock_fh = acquire_lock_fn()
                before_dbs = set(os.listdir(conv_dir)) if os.path.exists(conv_dir) else set()
            try:
                cmd = [agy_bin]
                if existing_session:
                    cmd.extend(["--conversation", existing_session])
                cmd.extend(["-p", prompt, "--model", model, "--disable-slash-commands"])
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=min(ADVISOR_EXEC_TIMEOUT, max(5.0, remaining)), env=env)
                elapsed = round(time.time() - start_t, 2)
                if res.returncode != 0 or not res.stdout.strip():
                    log_audit(f"{label} model '{model}' failed ({res.returncode}): {res.stderr.strip()[:100]}")
                    _clean_cascade_session(parent_conv_id, prefixes, conv_dir, before_dbs, clean_resume_fn, existing_session)
                    existing_session = None
                    continue
                raw_json = extract_json_from_llm_output(res.stdout, schema_keys=schema_keys)
                if raw_json is None:
                    log_audit(f"{label} model '{model}' output failed JSON decoding; clearing session")
                    _clean_cascade_session(parent_conv_id, prefixes, conv_dir, before_dbs, clean_resume_fn, existing_session)
                    existing_session = None
                    continue
                parsed = normalize_func(raw_json)
                new_conv_id = _clean_cascade_session(parent_conv_id, prefixes, conv_dir, before_dbs, clean_resume_fn, existing_session)
                cache_working_model(model)
                log_audit(f"{label} finished in {elapsed}s with {model} (session={new_conv_id})")
                return parsed
            except Exception as e:
                log_audit(f"{label} candidate '{model}' exception: {e}")
                _clean_cascade_session(parent_conv_id, prefixes, conv_dir, before_dbs, clean_resume_fn, existing_session)
                existing_session = None
                continue
        return default_on_failure
    except Exception as e:
        log_audit(f"{label} fatal exception ({round(time.time() - start_t, 2)}s): {e}")
        clear_session_id(parent_conv_id, prefixes)
        return default_on_failure
    finally:
        if spawn_lock_fh:
            release_lock_fn(spawn_lock_fh)
