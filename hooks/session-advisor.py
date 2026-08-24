#!/usr/bin/env python3
"""
session-advisor.py - Autonomous AI Quality & Completeness Auditor Hook

Entry point for AGY session stop audit hook. Resolves the repository root
to support direct symlink execution from ~/.config/agy/ or ~/.gemini/config/hooks/.
"""

import json
import os
import sys

# Resolve real script location even when invoked via symlinks
_HOOK_DIR = os.path.dirname(os.path.realpath(__file__))
_REPO_DIR = os.path.abspath(os.path.join(_HOOK_DIR, ".."))

if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

from advisor.runner import main

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            from advisor.guards import fail_safe_exit
            fail_safe_exit(f"Unhandled top-level exception: {e}")
        except Exception:
            pass
        is_post = any(a.lower() in ("post_invocation", "postinvocation", "post-invocation", "post") for a in sys.argv[1:])
        print(json.dumps({"injectSteps": []} if is_post else {"decision": "stop"}))
        sys.exit(0)
