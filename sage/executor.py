"""
sage.executor - Subprocess AGY execution with isolated home and session persistence.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time

from sage.config import ADVISOR_EXEC_TIMEOUT, SAGE_EXEC_TIMEOUT, SAGE_TIMEOUT_BUDGET
from sage.locking import acquire_spawn_lock, log_audit, release_spawn_lock, safe_id
from sage.models import cache_working_model, resolve_model_candidates

SAGE_ISOLATED_HOME = os.path.expanduser("~/.gemini/antigravity-cli/sage_isolated_home")
SAGE_CLI_DIR = os.path.join(SAGE_ISOLATED_HOME, ".gemini", "antigravity-cli")
CONV_DB_DIR = os.path.join(SAGE_CLI_DIR, "conversations")


def _link_files(src_dir, dst_dir, condition_fn):
    if os.path.isdir(src_dir):
        for f in os.listdir(src_dir):
            if condition_fn(f):
                src, dst = os.path.join(src_dir, f), os.path.join(dst_dir, f)
                if os.path.exists(src) and not os.path.exists(dst):
                    try:
                        os.symlink(src, dst)
                    except OSError:
                        pass


def ensure_isolated_home():
    iso_cli, iso_cfg = SAGE_CLI_DIR, os.path.join(SAGE_ISOLATED_HOME, ".gemini", "config")
    os.makedirs(iso_cli, exist_ok=True)
    os.makedirs(iso_cfg, exist_ok=True)
    _link_files(os.path.expanduser("~/.gemini/antigravity-cli"), iso_cli, lambda f: "token" in f or "auth" in f or "credential" in f or f in ("settings.json", "installation_id"))
    _link_files(os.path.expanduser("~/.gemini/config"), iso_cfg, lambda f: True)
    return SAGE_ISOLATED_HOME


def clean_summary_only(conv_id, parent_conv_id=None):
    if not conv_id or (parent_conv_id and conv_id in (parent_conv_id, safe_id(parent_conv_id))):
        return
    sid = safe_id(conv_id)
    for db_path in (os.path.join(SAGE_CLI_DIR, "conversation_summaries.db"), os.path.expanduser("~/.gemini/antigravity-cli/conversation_summaries.db")):
        if os.path.isfile(db_path):
            try:
                with sqlite3.connect(db_path, timeout=3) as conn:
                    conn.execute("DELETE FROM conversation_summaries WHERE conversation_id IN (?, ?)", (conv_id, sid))
            except Exception as e:
                log_audit(f"Failed to clean summary db for {conv_id}: {e}")


def clean_resume_history(conv_id, parent_conv_id=None):
    if not conv_id or (parent_conv_id and conv_id in (parent_conv_id, safe_id(parent_conv_id))):
        return
    clean_summary_only(conv_id, parent_conv_id=parent_conv_id)
    sid = safe_id(conv_id)
    for c_dir, b_dir in ((CONV_DB_DIR, os.path.join(SAGE_CLI_DIR, "brain")), (os.path.expanduser("~/.gemini/antigravity-cli/conversations"), os.path.expanduser("~/.gemini/antigravity-cli/brain"))):
        for name in {conv_id, sid}:
            for s in ("", ".db", ".db-wal", ".db-shm"):
                p = os.path.join(c_dir, f"{name}{s}" if s else name)
                if os.path.isfile(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            shutil.rmtree(os.path.join(b_dir, name), ignore_errors=True)


def get_session_file(parent_conv_id, prefix="agy_stop_audit_session_"):
    return f"/tmp/{prefix}{safe_id(parent_conv_id)}.txt"


def load_session_id(parent_conv_id, prefixes=("agy_stop_audit_session_",)):
    if not parent_conv_id:
        return None
    for p in ((prefixes,) if isinstance(prefixes, str) else prefixes):
        sf = get_session_file(parent_conv_id, p)
        if os.path.exists(sf):
            try:
                with open(sf, "r", encoding="utf-8") as f:
                    sid = f.read().strip()
                if sid and sid != parent_conv_id:
                    return sid
            except Exception:
                pass
    return None


def save_session_id(parent_conv_id, session_id, prefix="agy_stop_audit_session_"):
    if parent_conv_id and session_id and session_id != parent_conv_id:
        try:
            with open(get_session_file(parent_conv_id, prefix), "w", encoding="utf-8") as f:
                f.write(session_id.strip())
        except Exception as e:
            log_audit(f"Failed to persist session {prefix}: {e}")


def clear_session_id(parent_conv_id, prefixes=("agy_stop_audit_session_",), prefix=None):
    if not parent_conv_id:
        return
    for p in ((prefix,) if prefix is not None else ((prefixes,) if isinstance(prefixes, str) else prefixes)):
        sf = get_session_file(parent_conv_id, p)
        if os.path.exists(sf):
            try:
                os.remove(sf)
            except Exception:
                pass


def extract_json_from_llm_output(raw_text, schema_keys=()):
    if not raw_text or not raw_text.strip():
        return None
    raw, dec = raw_text.strip(), json.JSONDecoder()
    for m in re.finditer(r"\{", raw):
        try:
            d, _ = dec.raw_decode(raw[m.start():])
            if isinstance(d, dict) and (not schema_keys or any(k in d for k in schema_keys)):
                return d
        except Exception:
            pass
    return None


def _find_new_conv_id(conv_dir, before_dbs, parent_conv_id=None):
    if not os.path.exists(conv_dir):
        return None
    parent_sid = safe_id(parent_conv_id) if parent_conv_id else None
    diffs = [f for f in (set(os.listdir(conv_dir)) - before_dbs) if f.endswith(".db") and f.replace(".db", "") not in (parent_conv_id, parent_sid)]
    return diffs[0].replace(".db", "") if len(diffs) == 1 else None


def run_model_cascade(
    parent_conv_id, prompt, prefixes, normalize_func, default_on_failure,
    label="Sage", timeout_budget=SAGE_TIMEOUT_BUDGET, schema_keys=(),
    acquire_lock_fn=acquire_spawn_lock, release_lock_fn=release_spawn_lock,
    resolve_candidates_fn=resolve_model_candidates, clean_resume_fn=clean_resume_history,
):
    primary_prefix = prefixes[0] if isinstance(prefixes, (list, tuple)) else prefixes
    existing_session, start_t = load_session_id(parent_conv_id, prefixes), time.time()
    agy_bin, candidates = (shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")), (resolve_candidates_fn() or [])[:4]
    iso_home = ensure_isolated_home()
    conv_dir = os.path.join(iso_home, ".gemini", "antigravity-cli", "conversations")
    os.makedirs(conv_dir, exist_ok=True)
    spawn_lock_fh, before_dbs = (acquire_lock_fn() if not existing_session else None), set(os.listdir(conv_dir))

    def _reset_bad():
        nonlocal existing_session
        if existing_session:
            clean_resume_fn(existing_session, parent_conv_id=parent_conv_id)
            clear_session_id(parent_conv_id, prefixes)
            existing_session = None

    try:
        env = dict(os.environ, AGY_STOP_AUDIT_ACTIVE="1", HOME=iso_home, PATH=f"{os.path.expanduser('~/.local/bin')}:{os.environ.get('PATH', '')}")
        for model in candidates:
            rem = timeout_budget - (time.time() - start_t)
            if rem <= 2.0:
                log_audit(f"{label} timeout reached; halting fallbacks")
                break
            if not existing_session and not spawn_lock_fh:
                spawn_lock_fh, before_dbs = acquire_lock_fn(), set(os.listdir(conv_dir))
            try:
                cmd = [agy_bin] + (["--conversation", existing_session] if existing_session else []) + ["-p", prompt, "--model", model, "--disable-slash-commands"]
                res = subprocess.run(cmd, input="", capture_output=True, text=True, timeout=min(ADVISOR_EXEC_TIMEOUT, max(5.0, rem)), env=env)
                elapsed = round(time.time() - start_t, 2)
                if res.returncode != 0 or not res.stdout.strip():
                    log_audit(f"{label} '{model}' failed ({res.returncode}) in {elapsed}s: {res.stderr.strip()[:80]}")
                    _reset_bad()
                    continue
                raw_json = extract_json_from_llm_output(res.stdout, schema_keys=schema_keys)
                if raw_json is None:
                    log_audit(f"{label} '{model}' output failed JSON decoding; resetting")
                    _reset_bad()
                    continue
                parsed, active_cid = normalize_func(raw_json), (existing_session or _find_new_conv_id(conv_dir, before_dbs, parent_conv_id=parent_conv_id))
                if active_cid and active_cid != parent_conv_id:
                    save_session_id(parent_conv_id, active_cid, primary_prefix)
                    clean_summary_only(active_cid, parent_conv_id=parent_conv_id)
                cache_working_model(model)
                log_audit(f"{label} finished in {round(time.time() - start_t, 2)}s with {model} ({active_cid})")
                return parsed
            except Exception as e:
                log_audit(f"{label} candidate '{model}' exception: {e}")
                _reset_bad()
                continue
        return default_on_failure
    except Exception as e:
        log_audit(f"{label} fatal exception: {e}")
        if existing_session:
            clean_resume_fn(existing_session, parent_conv_id=parent_conv_id)
        clear_session_id(parent_conv_id, prefixes)
        return default_on_failure
    finally:
        if spawn_lock_fh:
            release_lock_fn(spawn_lock_fh)
