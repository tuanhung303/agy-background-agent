"""
sage.interactivity - Can a question put to the user actually be answered?

Two sage categories (`grill_me`, `confused_goal`) end the turn by asking the user
something. Under `agy -p` nobody is attached, so the run terminates on a dead
question. Measured in koota r10 (2026-08-31): sage rejected the recap demanding
an `ask_question` interview, costing 56 turns and leaving a spec clause
unverified, which lost the round.

The hook payload carries no interactivity flag, so this module is the signal.
Deliberately dependency-free: no sage imports, so anything may use it.
"""

import os
import re
import subprocess

_PRINT_MODE_CACHE = {}
_AGY_RE = re.compile(r"(?:^|/)agy\b")
_PRINT_FLAG_RE = re.compile(r"(?<!\S)(?:-p|--print)(?:\s|$)")


def can_ask_user():
    """True when a question to the user can actually reach one.

    Precedence:
      1. SAGE_INTERACTIVE=0/1 — explicit, wins. Harnesses should pin it.
      2. An `agy -p` / `--print` ancestor process => print mode, nobody to ask.
      3. Otherwise ASKABLE.

    Default-askable is deliberate: turn 1 of a real session is exactly when a
    clarifying question earns its keep, so an unrecognised environment must not
    silently lose it. Wrongly suppressing a question is worse than wrongly asking.
    """
    flag = str(os.environ.get("SAGE_INTERACTIVE", "")).strip().lower()
    if flag in ("0", "false", "no"):
        return False
    if flag in ("1", "true", "yes"):
        return True
    return not in_print_mode()


def in_print_mode():
    """True when an ancestor process is agy in print/headless mode.

    The hook runs as a descendant of agy, so its argv is the only place print
    mode is observable. Best-effort: any failure returns False (assume
    interactive), keeping the safe default above.
    """
    pid = os.getpid()
    if pid in _PRINT_MODE_CACHE:
        return _PRINT_MODE_CACHE[pid]
    result = False
    try:
        cur, hops = os.getppid(), 0
        while cur and cur > 1 and hops < 6:
            # ppid FIRST and args LAST, with -ww. On macOS BSD ps a non-final
            # `command=`/`args=` column is truncated to 16 characters, which
            # silently ate the flags this function exists to read:
            #   ps -o command= -o ppid= -p $$  ->  "/bin/zsh -c sour 45715"
            # Trailing args= is unbounded; -ww lifts the terminal-width clamp.
            res = subprocess.run(
                ["ps", "-ww", "-o", "ppid=", "-o", "args=", "-p", str(cur)],
                capture_output=True, text=True, timeout=2,
            )
            line = (res.stdout or "").strip()
            if not line:
                break
            parts = line.split(None, 1)
            nxt, cmd = (parts[0], parts[1]) if len(parts) == 2 else ("", line)
            if _AGY_RE.search(cmd) and _PRINT_FLAG_RE.search(cmd):
                result = True
                break
            try:
                cur = int(nxt)
            except ValueError:
                break
            hops += 1
    except Exception:
        result = False
    _PRINT_MODE_CACHE[pid] = result
    return result
