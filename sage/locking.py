"""
sage.locking - Atomic file operations, fcntl locks, and audit logging.
"""

import atexit
from datetime import datetime
import fcntl
import hashlib
import json
import os
import re
import time

from sage.config import LOG_FILE

# Global lock file handle to prevent premature garbage collection
_LOCK_FH = None
SPAWN_LOCK_FILE = "/tmp/agy_auditor_spawn.lock"


def release_lock():
    """Releases and closes the current conversation lock handle."""
    global _LOCK_FH
    if _LOCK_FH:
        try:
            fcntl.flock(_LOCK_FH.fileno(), fcntl.LOCK_UN)
            _LOCK_FH.close()
        except Exception:
            pass
        _LOCK_FH = None


atexit.register(release_lock)


def acquire_spawn_lock(lock_path=None, timeout=10.0):
    """Acquires the global spawn lock to prevent duplicate AGY subprocess spawns."""
    if isinstance(lock_path, (int, float)):
        timeout, lock_path = lock_path, None
    target_file = lock_path or SPAWN_LOCK_FILE
    try:
        fd = os.open(target_file, os.O_RDWR | os.O_CREAT, 0o600)
        fh = open(fd, "w")
        start = time.time()
        while time.time() - start < timeout:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fh
            except (BlockingIOError, IOError):
                time.sleep(0.05)
        fh.close()
        return None
    except Exception:
        return None


def release_spawn_lock(fh):
    """Releases the global spawn lock file handle."""
    if fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
        except Exception:
            pass


def log_audit(msg):
    """Appends a timestamped message to the audit log."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def safe_id(val):
    """Sanitizes an arbitrary string identifier with collision-safe SHA-256 suffix."""
    val_str = str(val) if val is not None else ""
    return f"{re.sub(r'[^a-zA-Z0-9_-]', '_', val_str)[:32]}_{hashlib.sha256(val_str.encode('utf-8')).hexdigest()[:8]}"


def atomic_write_json(filepath, data):
    """Atomically writes JSON data to a file via temporary file replacement with 0600 mode."""
    temp_file = f"{filepath}.tmp.{os.getpid()}_{time.time()}"
    try:
        fd = os.open(temp_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_file, filepath)
    except Exception as e:
        log_audit(f"Atomic write error for {filepath}: {e}")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass


def acquire_conversation_lock(conv_id):
    """Acquires an exclusive, non-blocking lock for the conversation with 0600 mode."""
    global _LOCK_FH
    release_lock()
    lock_file = f"/tmp/agy_sage_{safe_id(conv_id)}.lock"
    try:
        fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        fh = open(fd, "w")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _LOCK_FH = fh
            return fh
        except (BlockingIOError, IOError):
            fh.close()
            return None
        except Exception as e:
            fh.close()
            log_audit(f"Error acquiring lock {lock_file}: {e}")
            return None
    except Exception as e:
        log_audit(f"Error opening lock file {lock_file}: {e}")
        return None


def cleanup_stale_tmp_files(max_age_seconds=7200, state_max_age_seconds=None):
    """Cleans up stale lock and temp files older than max_age_seconds, preserving state files."""
    try:
        now, tmp_dir = time.time(), "/tmp"
        if not os.path.exists(tmp_dir):
            return
        for fname in os.listdir(tmp_dir):
            if fname.startswith((
                "agy_sage_", "agy_mid_sage_",
                "agy_advisor_", "agy_stop_audit_",
                "agy_mid_advisor_", "agy_mid_verifier_",
                "agy_auditor_",
            )):
                fpath = os.path.join(tmp_dir, fname)
                try:
                    ttl = (state_max_age_seconds if state_max_age_seconds is not None else max_age_seconds) if fname.endswith(".json") else max_age_seconds
                    if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > ttl:
                        if fname.endswith(".lock"):
                            try:
                                with open(fpath, "r+") as lfh:
                                    fcntl.flock(lfh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                                    os.remove(fpath)
                            except Exception:
                                pass
                        else:
                            os.remove(fpath)
                except Exception:
                    pass
    except Exception as e:
        log_audit(f"Error cleaning stale tmp files: {e}")

