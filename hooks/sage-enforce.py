#!/usr/bin/env python3
"""
sage-enforce.py - Zero-delay PreToolUse pass-through hook for Antigravity (AGY).

Returns an immediate allow decision on PreToolUse events with zero blocking.
"""

import json
import sys


def main() -> None:
    try:
        if not sys.stdin.isatty():
            _ = sys.stdin.read()
    except Exception:
        pass
    try:
        sys.stdout.write(json.dumps({"decision": "allow"}))
    except Exception:
        pass


if __name__ == "__main__":
    main()

