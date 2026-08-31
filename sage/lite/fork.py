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
            with sqlite3.connect(target_db, timeout=3.0) as dst_conn:
                dst_conn.execute("UPDATE trajectory_meta SET cascade_id = ?", (fork_id,))
                dst_conn.commit()
        except Exception as e:
            log_audit(f"SQLite backup failed for {parent_conv_id} -> {fork_id}: {e}")
            return None

        real_sum_db = os.path.join(real_cli_dir, "conversation_summaries.db")
        iso_sum_db = os.path.join(SAGE_CLI_DIR, "conversation_summaries.db")
        if os.path.isfile(real_sum_db):
            try:
                with sqlite3.connect(f"file:{real_sum_db}?mode=ro", uri=True, timeout=3.0) as s_conn:
                    s_cur = s_conn.cursor()
                    s_cur.execute(
                        "SELECT * FROM conversation_summaries WHERE conversation_id IN (?, ?)",
                        (parent_conv_id, safe_id(parent_conv_id)),
                    )
                    row = s_cur.fetchone()
                    col_names = [d[0] for d in s_cur.description] if s_cur.description else []
                    schema_row = s_conn.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name='conversation_summaries'"
                    ).fetchone()
                    schema = schema_row[0] if schema_row else ""

                if row and col_names and schema:
                    row_dict = dict(zip(col_names, row))
                    row_dict["conversation_id"] = fork_id
                    with sqlite3.connect(iso_sum_db, timeout=3.0) as d_conn:
                        try:
                            d_conn.execute(schema)
                        except Exception:
                            pass
                        placeholders = ", ".join(["?"] * len(row_dict))
                        cols = ", ".join(row_dict.keys())
                        d_conn.execute(
                            f"INSERT OR REPLACE INTO conversation_summaries ({cols}) VALUES ({placeholders})",
                            list(row_dict.values()),
                        )
                        d_conn.commit()
            except Exception as e:
                log_audit(f"Failed to clone conversation summary for {fork_id}: {e}")

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
