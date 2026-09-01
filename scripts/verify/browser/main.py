#!/usr/bin/env python3
"""Topic: browser - Live browser Playwright verification with 60s hard timeout and DOMContentLoaded gating."""
import os
import subprocess
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
mjs_script = os.path.join(os.path.dirname(__file__), "verify_live_full.mjs")

# Execute with outer 65s subprocess timeout to guarantee clean lifecycle
try:
    res = subprocess.run(
        ["node", mjs_script],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=65,
    )
    if res.stdout:
        print(res.stdout)
    if res.stderr:
        print(res.stderr, file=sys.stderr)
    sys.exit(res.returncode)
except subprocess.TimeoutExpired:
    print("ERROR: Browser verification subprocess timed out after 65s", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"ERROR: Failed to run browser verification: {e}", file=sys.stderr)
    sys.exit(1)
