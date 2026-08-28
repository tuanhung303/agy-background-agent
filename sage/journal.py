#!/usr/bin/env python3
"""
sage-journal.py - Centralized event journal for sage enforcement & advisories.

One JSONL file (/tmp/agy_sage_events.jsonl, override via AGY_SAGE_JOURNAL):
every event the enforcement/supervision layer emits or suppresses is appended
as a single line for later debugging — NEVER injected into agent context.

Usage (standalone):
  echo '{"conv_id":"c1","event":"violation_inject","tool":"run_command"}' | python3 sage-journal.py
Or import: from sage.journal import journal
"""
import json
import os
import sys
from datetime import datetime

JOURNAL_PATH = os.environ.get("AGY_SAGE_JOURNAL", "/tmp/agy_sage_events.jsonl")
# Journal hygiene: rotate at ~2MB, keep one .prev generation.
ROTATE_BYTES = 2_000_000

# Fixed schema fields keep lines greppable; unknown keys go into "extra".
FIELDS = ("ts", "conv_id", "event", "tool", "decision", "count", "detail")


def _get_path():
    return os.environ.get("AGY_SAGE_JOURNAL", JOURNAL_PATH)


def write(event, conv_id="", tool="", decision="", count=None, detail="", **extra):
    """Appends one structured event line. Best-effort: never raises."""
    try:
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "conv_id": conv_id,
            "event": event,
            "tool": tool,
            "decision": decision,
            "count": count,
            "detail": str(detail)[:300],
        }
        if extra:
            rec["extra"] = {k: str(v)[:120] for k, v in extra.items() if v not in (None, "")}
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
        path = _get_path()
        _rotate_if_needed(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _rotate_if_needed(path=None):
    try:
        p = path or _get_path()
        if os.path.exists(p) and os.path.getsize(p) > ROTATE_BYTES:
            prev = p + ".prev"
            if os.path.exists(prev):
                os.unlink(prev)
            os.rename(p, prev)
    except Exception:
        pass


def read(conv_id=None, event=None, tail=100):
    """Reads journal entries, newest last, with optional conv/event filters."""
    rows = []
    try:
        p = _get_path()
        for path in (p, p + ".prev"):
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if conv_id and r.get("conv_id") != conv_id:
                        continue
                    if event and r.get("event") != event:
                        continue
                    rows.append(r)
    except Exception:
        pass
    return rows[-tail:]


if __name__ == "__main__":
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else "{}"
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    payload.pop("ts", None)
    write(**payload)
    print(json.dumps({"ok": True, "journal": JOURNAL_PATH}))
