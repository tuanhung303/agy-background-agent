"""
sage.executor - Subprocess AGY execution, session persistence, and JSON decoding.
"""

import json, os, re, shutil, sqlite3, subprocess, time

from sage.config import ADVISOR_EXEC_TIMEOUT, SAGE_EXEC_TIMEOUT, SAGE_TIMEOUT_BUDGET
from sage.locking import acquire_spawn_lock, log_audit, release_spawn_lock, safe_id
from sage.models import cache_working_model, resolve_model_candidates
CONV_DB_DIR = os.path.expanduser("~/.gemini/antigravity-cli/conversations")


def clean_summary_only(conv_id):
    if not conv_id:
        return
    sid, db_path = safe_id(conv_id), os.path.expanduser("~/.gemini/antigravity-cli/conversation_summaries.db")
    if os.path.isfile(db_path):
        try:
            with sqlite3.connect(db_path, timeout=3) as conn:
                conn.execute("DELETE FROM conversation_summaries WHERE conversation_id IN (?, ?)", (conv_id, sid))
                conn.commit()
            conn.close()
        except Exception as e:
            log_audit(f"Failed to clean summary db for {conv_id}: {e}")


def clean_resume_history(conv_id):
    if not conv_id:
        return
    clean_summary_only(conv_id)
    sid, conv_dir = safe_id(conv_id), os.path.expanduser("~/.gemini/antigravity-cli/conversations")
    for name in {conv_id, sid}:
        try:
            if os.path.isdir(conv_dir):
                for s in ("", ".db", ".db-wal", ".db-shm"):
                    p = os.path.join(conv_dir, f"{name}{s}" if s else name)
                    if os.path.isfile(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
            shutil.rmtree(os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{name}"), ignore_errors=True)
        except Exception as e:
            log_audit(f"Failed to remove artifacts for {name}: {e}")


def get_session_file(parent_conv_id, prefix="agy_stop_audit_session_"):
    return f"/tmp/{prefix}{safe_id(parent_conv_id)}.txt"


def load_session_id(parent_conv_id, prefixes=("agy_stop_audit_session_",)):
    if not parent_conv_id:
        return None
    for prefix in ((prefixes,) if isinstance(prefixes, str) else prefixes):
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
    raw, fallback, dec = raw_text.strip(), None, json.JSONDecoder()
    for cand in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL):
        try:
            d = json.loads(cand)
            if isinstance(d, dict) and (not schema_keys or any(k in d for k in schema_keys)):
                return d
            fallback = fallback or (d if isinstance(d, dict) else None)
        except Exception:
            pass
    for m in re.finditer(r"\{", raw):
        try:
            d, _ = dec.raw_decode(raw[m.start():])
            if isinstance(d, dict) and (not schema_keys or any(k in d for k in schema_keys)):
                return d
            fallback = fallback or (d if isinstance(d, dict) else None)
        except Exception:
            continue
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict) and (not schema_keys or any(k in d for k in schema_keys)):
                return d
            fallback = fallback or (d if isinstance(d, dict) else None)
        except Exception:
            pass
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else fallback
    except Exception:
        return fallback


def _find_new_conv_id(conv_dir, before_dbs):
    if not os.path.exists(conv_dir):
        return None
    diffs = [f for f in (set(os.listdir(conv_dir)) - before_dbs) if f.endswith(".db")]
    return diffs[0].replace(".db", "") if len(diffs) == 1 else None


def run_model_cascade(
    parent_conv_id, prompt, prefixes, normalize_func, default_on_failure,
    label="Sage", timeout_budget=SAGE_TIMEOUT_BUDGET, schema_keys=(),
    acquire_lock_fn=acquire_spawn_lock, release_lock_fn=release_spawn_lock,
    resolve_candidates_fn=resolve_model_candidates, clean_resume_fn=clean_resume_history,
):
    primary_prefix = prefixes[0] if isinstance(prefixes, (list, tuple)) else prefixes
    existing_session, start_t = load_session_id(parent_conv_id, prefixes), time.time()
    agy_bin, candidates, conv_dir = (shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")), (resolve_candidates_fn() or [])[:4], CONV_DB_DIR
    spawn_lock_fh, before_dbs = (acquire_lock_fn() if not existing_session else None), (set(os.listdir(conv_dir)) if os.path.exists(conv_dir) else set())

    def _reset_bad_session():
        nonlocal existing_session
        if existing_session:
            clean_resume_fn(existing_session)
            clear_session_id(parent_conv_id, prefixes)
            existing_session = None

    try:
        env = dict(os.environ, AGY_STOP_AUDIT_ACTIVE="1", HOME=os.path.expanduser("~"), PATH=f"{os.path.expanduser('~/.local/bin')}:{os.environ.get('PATH', '')}")

        for model in candidates:
            remaining = timeout_budget - (time.time() - start_t)
            if remaining <= 2.0:
                log_audit(f"{label} overall timeout budget reached; halting fallback attempts")
                break
            if not existing_session and not spawn_lock_fh:
                spawn_lock_fh = acquire_lock_fn()
                before_dbs = set(os.listdir(conv_dir)) if os.path.exists(conv_dir) else set()
            try:
                cmd = [agy_bin] + (["--conversation", existing_session] if existing_session else []) + ["-p", prompt, "--model", model, "--disable-slash-commands"]
                res = subprocess.run(cmd, input="", capture_output=True, text=True, timeout=min(ADVISOR_EXEC_TIMEOUT, max(5.0, remaining)), env=env)
                elapsed = round(time.time() - start_t, 2)
                if res.returncode != 0 or not res.stdout.strip():
                    log_audit(f"{label} model '{model}' failed ({res.returncode}): {res.stderr.strip()[:100]}")
                    _reset_bad_session()
                    continue
                raw_json = extract_json_from_llm_output(res.stdout, schema_keys=schema_keys)
                if raw_json is None:
                    log_audit(f"{label} model '{model}' output failed JSON decoding; clearing session")
                    _reset_bad_session()
                    continue
                parsed, active_cid = normalize_func(raw_json), (existing_session or _find_new_conv_id(conv_dir, before_dbs))
                if active_cid:
                    save_session_id(parent_conv_id, active_cid, primary_prefix)
                    clean_summary_only(active_cid)
                cache_working_model(model)
                log_audit(f"{label} finished in {elapsed}s with {model} (session={active_cid})")
                return parsed
            except Exception as e:
                log_audit(f"{label} candidate '{model}' exception: {e}")
                _reset_bad_session()
                continue
        return default_on_failure
    except Exception as e:
        log_audit(f"{label} fatal exception ({round(time.time() - start_t, 2)}s): {e}")
        if existing_session:
            clean_resume_fn(existing_session)
        clear_session_id(parent_conv_id, prefixes)
        return default_on_failure
    finally:
        if spawn_lock_fh:
            release_lock_fn(spawn_lock_fh)
