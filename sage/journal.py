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
        _rotate_if_needed()
        with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _rotate_if_needed():
    try:
        if os.path.exists(JOURNAL_PATH) and os.path.getsize(JOURNAL_PATH) > ROTATE_BYTES:
            prev = JOURNAL_PATH + ".prev"
            if os.path.exists(prev):
                os.unlink(prev)
            os.rename(JOURNAL_PATH, prev)
    except Exception:
        pass


def read(conv_id=None, event=None, tail=100):
    """Reads journal entries, newest last, with optional conv/event filters."""
    rows = []
    try:
        for path in (JOURNAL_PATH, JOURNAL_PATH + ".prev"):
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
