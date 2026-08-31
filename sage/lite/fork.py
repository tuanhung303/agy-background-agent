"""sage.lite.fork - SQLite online backup session branching and cleanup."""
import os
import shutil
import sqlite3
import time
from typing import Optional

from sage.executor import SAGE_CLI_DIR, SAGE_ISOLATED_HOME, clean_resume_history, ensure_isolated_home
from sage.locking import log_audit, safe_id

FAILED_FORKS_DIR = "/tmp/agy_failed_forks"
MAX_FAILED_FORKS = 20
MAX_FAILED_AGE_SECONDS = 86400.0


def prune_failed_forks_dir() -> None:
    """Limits failed fork logs to MAX_FAILED_FORKS and prunes files older than 24h."""
    if not os.path.isdir(FAILED_FORKS_DIR):
        return
    try:
        now = time.time()
        files = []
        for f in os.listdir(FAILED_FORKS_DIR):
            fp = os.path.join(FAILED_FORKS_DIR, f)
            if os.path.isfile(fp):
                mtime = os.path.getmtime(fp)
                if now - mtime > MAX_FAILED_AGE_SECONDS:
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
                else:
                    files.append((mtime, fp))
        files.sort(key=lambda x: x[0], reverse=True)
        for _, old_fp in files[MAX_FAILED_FORKS:]:
            try:
                os.remove(old_fp)
            except OSError:
                pass
    except Exception as e:
        log_audit(f"Failed to prune {FAILED_FORKS_DIR}: {e}")


def fork_conversation_session(parent_conv_id: str) -> Optional[str]:
    """Clones parent SQLite DB and transcript into SAGE_ISOLATED_HOME via online backup."""
    if not parent_conv_id:
        return None

    iso_home = ensure_isolated_home()
    ts = int(time.time() * 1000)
    fork_id = f"{safe_id(parent_conv_id)[:24]}_lite_{ts}"

    real_cli_dir = os.path.expanduser("~/.gemini/antigravity-cli")
    real_conv_dir = os.path.join(real_cli_dir, "conversations")
    iso_conv_dir = os.path.join(SAGE_CLI_DIR, "conversations")
    os.makedirs(iso_conv_dir, exist_ok=True)

    # 1. Locate parent DB
    parent_db = None
    for name in (parent_conv_id, safe_id(parent_conv_id)):
        cand = os.path.join(real_conv_dir, f"{name}.db")
        if os.path.isfile(cand):
            parent_db = cand
            break

    if parent_db:
        target_db = os.path.join(iso_conv_dir, f"{fork_id}.db")
        try:
            with sqlite3.connect(f"file:{parent_db}?mode=ro", uri=True, timeout=3.0) as src_conn:
                with sqlite3.connect(target_db, timeout=3.0) as dst_conn:
                    src_conn.backup(dst_conn)
        except Exception as e:
            log_audit(f"SQLite backup failed for {parent_conv_id} -> {fork_id}: {e}")
            return None

    # 2. Locate parent transcript and copy to isolated home
    real_brain_dir = os.path.join(real_cli_dir, "brain")
    iso_brain_dir = os.path.join(SAGE_CLI_DIR, "brain")
    for name in (parent_conv_id, safe_id(parent_conv_id)):
        src_log = os.path.join(real_brain_dir, name, ".system_generated", "logs", "transcript.jsonl")
        if os.path.isfile(src_log):
            dst_log_dir = os.path.join(iso_brain_dir, fork_id, ".system_generated", "logs")
            os.makedirs(dst_log_dir, exist_ok=True)
            try:
                shutil.copy2(src_log, os.path.join(dst_log_dir, "transcript.jsonl"))
            except Exception as e:
                log_audit(f"Transcript copy failed for {fork_id}: {e}")
            break

    return fork_id


def cleanup_fork_session(fork_conv_id: str, preserve_failed: bool = False, verifier_output: str = "") -> None:
    """Safely removes ephemeral database files and preserves failed transcripts for debugging."""
    if not fork_conv_id:
        return

    if preserve_failed:
        try:
            os.makedirs(FAILED_FORKS_DIR, exist_ok=True)
            dst_fp = os.path.join(FAILED_FORKS_DIR, f"{fork_conv_id}.jsonl")
            iso_log = os.path.join(SAGE_CLI_DIR, "brain", fork_conv_id, ".system_generated", "logs", "transcript.jsonl")
            if os.path.isfile(iso_log):
                shutil.copy2(iso_log, dst_fp)
            elif verifier_output:
                with open(dst_fp, "w", encoding="utf-8") as f:
                    f.write(verifier_output)
            prune_failed_forks_dir()
        except Exception as e:
            log_audit(f"Failed to preserve failed fork {fork_conv_id}: {e}")

    clean_resume_history(fork_conv_id)
