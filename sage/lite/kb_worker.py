"""sage.lite.kb_worker - Standalone background worker for asynchronous KB maintenance."""
import argparse
import os
import subprocess
import sys
import time
from typing import Optional

from sage.config import KB_MAINTENANCE_TIMEOUT, get_real_user_home
from sage.lite.fork import cleanup_fork_session
from sage.lite.verifier import run_kb_maintenance
from sage.locking import log_audit


def execute_field_notes_sync() -> None:
    """Runs field-notes daily sync script to stage and commit any working tree mutations."""
    real_home = get_real_user_home()
    sync_script = os.path.join(real_home, "Documents", "GitHub", "field-notes", "scripts", "sync.sh")
    if not os.path.isfile(sync_script):
        return
    try:
        res = subprocess.run(
            ["bash", sync_script],
            capture_output=True,
            text=True,
            timeout=15.0,
        )
        if res.returncode == 0:
            log_audit(f"Field-notes sync output: {res.stdout.strip()}")
        else:
            log_audit(f"Field-notes sync failed ({res.returncode}): {res.stderr.strip() or res.stdout.strip()}")
    except Exception as e:
        log_audit(f"Field-notes sync execution exception: {e}")


def run_async_kb_worker(
    parent_conv_id: str,
    fork_conv_id: str,
    cwd: Optional[str] = None,
    timeout: float = KB_MAINTENANCE_TIMEOUT,
) -> None:
    """Executes KB maintenance, synchronizes field-notes git status, and cleans up fork session."""
    log_audit(f"Async KB worker started [parent={parent_conv_id}, fork={fork_conv_id}]")
    start_t = time.time()
    try:
        summary = run_kb_maintenance(
            parent_conv_id=parent_conv_id,
            fork_conv_id=fork_conv_id,
            timeout=timeout,
            cwd=cwd,
        )
        dur = round(time.time() - start_t, 2)
        if summary:
            log_audit(f"Async KB worker completed in {dur}s: {summary}")
        else:
            log_audit(f"Async KB worker completed in {dur}s with no-op")
        execute_field_notes_sync()
    except Exception as e:
        dur = round(time.time() - start_t, 2)
        log_audit(f"Async KB worker exception after {dur}s: {e}")
    finally:
        cleanup_fork_session(fork_conv_id)
        log_audit(f"Async KB worker cleaned fork session {fork_conv_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Async Knowledge Base Persona Maintainer Worker")
    parser.add_argument("--parent-conv-id", required=True, help="Parent conversation ID")
    parser.add_argument("--fork-conv-id", required=True, help="Forked conversation ID")
    parser.add_argument("--cwd", default=None, help="Working directory")
    parser.add_argument("--timeout", type=float, default=KB_MAINTENANCE_TIMEOUT, help="Execution timeout in seconds")
    args = parser.parse_args()

    run_async_kb_worker(
        parent_conv_id=args.parent_conv_id,
        fork_conv_id=args.fork_conv_id,
        cwd=args.cwd,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
