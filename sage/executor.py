"""
sage.executor - Subprocess AGY execution with isolated home and session persistence.
"""
import json, os, re, shutil, sqlite3, subprocess, time
from sage.config import SAGE_EXEC_TIMEOUT, SAGE_TIMEOUT_BUDGET
from sage.locking import acquire_spawn_lock, log_audit, release_spawn_lock, safe_id
from sage.models import cache_working_model, resolve_model_candidates

SAGE_ISOLATED_HOME = os.path.expanduser("~/.gemini/antigravity-cli/sage_isolated_home")
SAGE_CLI_DIR = os.path.join(SAGE_ISOLATED_HOME, ".gemini", "antigravity-cli")
CONV_DB_DIR = os.path.join(SAGE_CLI_DIR, "conversations")


def _is_parent(cid, parent):
    return bool(parent) and str(cid) in (str(parent), safe_id(parent))


def _link_file(src, dst):
    if (os.path.isfile(src) or os.path.isdir(src)) and (not os.path.lexists(dst) or (os.path.islink(dst) and not os.path.exists(dst))):
        try:
            if os.path.islink(dst):
                os.unlink(dst)
            os.symlink(src, dst)
        except OSError:
            pass


def ensure_isolated_home():
    if os.path.realpath(os.environ.get("HOME", "")).startswith(os.path.realpath(SAGE_ISOLATED_HOME)):
        return SAGE_ISOLATED_HOME
    iso_cli, iso_cfg = SAGE_CLI_DIR, os.path.join(SAGE_ISOLATED_HOME, ".gemini", "config")
    os.makedirs(iso_cli, mode=0o700, exist_ok=True)
    os.makedirs(iso_cfg, mode=0o700, exist_ok=True)
    real_cli = os.path.expanduser("~/.gemini/antigravity-cli")
    for f in os.listdir(real_cli) if os.path.isdir(real_cli) else []:
        if "token" in f or "auth" in f or "credential" in f or f in ("settings.json", "installation_id"):
            _link_file(os.path.join(real_cli, f), os.path.join(iso_cli, f))
    real_kc = os.path.expanduser("~/Library/Keychains")
    if os.path.isdir(real_kc):
        iso_lib = os.path.join(SAGE_ISOLATED_HOME, "Library")
        os.makedirs(iso_lib, mode=0o700, exist_ok=True)
        _link_file(real_kc, os.path.join(iso_lib, "Keychains"))
    iso_hooks = os.path.join(iso_cfg, "hooks.json")
    try:
        if os.path.islink(iso_hooks) or os.path.lexists(iso_hooks):
            os.unlink(iso_hooks)
        with open(iso_hooks, "w", encoding="utf-8") as f:
            f.write("{}")
    except OSError:
        pass
    return SAGE_ISOLATED_HOME


def clean_summary_only(conv_id, parent_conv_id=None):
    if conv_id and not _is_parent(conv_id, parent_conv_id):
        for db in (os.path.join(SAGE_CLI_DIR, "conversation_summaries.db"), os.path.expanduser("~/.gemini/antigravity-cli/conversation_summaries.db")):
            if os.path.isfile(db):
                try:
                    with sqlite3.connect(db, timeout=3) as conn:
                        conn.execute("DELETE FROM conversation_summaries WHERE conversation_id IN (?, ?)", (conv_id, safe_id(conv_id)))
                        conn.commit()
                except Exception as e:
                    log_audit(f"Failed to clean summary db for {conv_id}: {e}")


def clean_resume_history(conv_id, parent_conv_id=None):
    if conv_id and not _is_parent(conv_id, parent_conv_id):
        clean_summary_only(conv_id, parent_conv_id=parent_conv_id)
        for c_dir, b_dir in ((CONV_DB_DIR, os.path.join(SAGE_CLI_DIR, "brain")), (os.path.expanduser("~/.gemini/antigravity-cli/conversations"), os.path.expanduser("~/.gemini/antigravity-cli/brain"))):
            for name in {conv_id, safe_id(conv_id)}:
                for s in ("", ".db", ".db-wal", ".db-shm"):
                    try:
                        os.remove(os.path.join(c_dir, f"{name}{s}" if s else name))
                    except OSError:
                        pass
                shutil.rmtree(os.path.join(b_dir, name), ignore_errors=True)


def get_session_file(parent_conv_id, prefix="agy_stop_audit_session_"):
    return f"/tmp/{prefix}{safe_id(parent_conv_id)}.txt"


def load_session_id(parent_conv_id, prefixes=("agy_stop_audit_session_",)):
    if parent_conv_id:
        for p in ((prefixes,) if isinstance(prefixes, str) else prefixes):
            sf = get_session_file(parent_conv_id, p)
            if os.path.exists(sf):
                try:
                    with open(sf, "r", encoding="utf-8") as f:
                        sid = f.read().strip()
                    if sid and not _is_parent(sid, parent_conv_id):
                        return sid
                except Exception:
                    pass
    return None


def save_session_id(parent_conv_id, session_id, prefix="agy_stop_audit_session_"):
    if parent_conv_id and session_id and not _is_parent(session_id, parent_conv_id):
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
    diffs = [f for f in (set(os.listdir(conv_dir)) - before_dbs) if f.endswith(".db") and not _is_parent(f.replace(".db", ""), parent_conv_id)]
    return diffs[0].replace(".db", "") if len(diffs) == 1 else None


def run_model_cascade(
    parent_conv_id, prompt, prefixes, normalize_func, default_on_failure,
    label="Sage", timeout_budget=SAGE_TIMEOUT_BUDGET, schema_keys=(),
    acquire_lock_fn=acquire_spawn_lock, release_lock_fn=release_spawn_lock,
    resolve_candidates_fn=resolve_model_candidates, clean_resume_fn=clean_resume_history,
    cwd=None,
):
    primary_prefix = prefixes[0] if isinstance(prefixes, (list, tuple)) else prefixes
    # Run inside the workspace so the sage's read tools resolve project-relative
    # paths; HOME stays rebound to the isolated home for session/auth isolation.
    run_cwd = cwd if cwd and os.path.isdir(cwd) else None
    existing_session, start_t = load_session_id(parent_conv_id, prefixes), time.time()
    agy_bin, candidates = (shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")), (resolve_candidates_fn() or [])[:4]
    iso_home = ensure_isolated_home()
    conv_dir = os.path.join(iso_home, ".gemini", "antigravity-cli", "conversations")
    os.makedirs(conv_dir, exist_ok=True)
    spawn_lock_fh, before_dbs = (acquire_lock_fn() if not existing_session else None), set(os.listdir(conv_dir))

    def _reset_bad():
        nonlocal existing_session, before_dbs
        if existing_session:
            clean_resume_fn(existing_session, parent_conv_id=parent_conv_id)
            clear_session_id(parent_conv_id, prefixes)
            existing_session, before_dbs = None, set(os.listdir(conv_dir))

    try:
        env = dict(os.environ, AGY_STOP_AUDIT_ACTIVE="1", HOME=iso_home, PATH=f"{os.path.expanduser('~/.local/bin')}:{os.environ.get('PATH', '')}")
        for idx, model in enumerate(candidates):
            rem = timeout_budget - (time.time() - start_t)
            if rem <= 6.0:
                log_audit(f"{label} timeout reached; halting fallbacks")
                break
            if not existing_session and not spawn_lock_fh:
                spawn_lock_fh, before_dbs = acquire_lock_fn(), set(os.listdir(conv_dir))
            try:
                cand_timeout = min(SAGE_EXEC_TIMEOUT, max(8.0, rem * 0.7 if (len(candidates) - idx) > 1 else rem))
                cmd = [agy_bin] + (["--conversation", existing_session] if existing_session else []) + ["-p", prompt, "--model", model, "--disable-slash-commands"]
                res = subprocess.run(cmd, input="", capture_output=True, text=True, timeout=cand_timeout, env=env, cwd=run_cwd)
                if res.returncode != 0 or not res.stdout.strip():
                    log_audit(f"{label} '{model}' failed ({res.returncode}) in {round(time.time() - start_t, 2)}s")
                    _reset_bad()
                    continue
                raw_json = extract_json_from_llm_output(res.stdout, schema_keys=schema_keys)
                if raw_json is None:
                    log_audit(f"{label} '{model}' output failed JSON decoding; resetting")
                    _reset_bad()
                    continue
                parsed, active_cid = normalize_func(raw_json), (existing_session or _find_new_conv_id(conv_dir, before_dbs, parent_conv_id=parent_conv_id))
                if active_cid and not _is_parent(active_cid, parent_conv_id):
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
