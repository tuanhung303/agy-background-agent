#!/usr/bin/env python3
"""
session-sage.py - Autonomous AI Quality & Completeness Sage Hook

Entry point for AGY session stop audit hook. Resolves the repository root
to support direct symlink execution from ~/.config/agy/ or ~/.gemini/config/hooks/.
"""

import json
import os
import sys

# Hot unplug: when AGY_SAGE_DISABLED is set in the agy process environment
# (per-spawn, not global), the sage hook no-ops immediately — emitting a
# neutral pass-through payload instead of running the audit. This lets a
# benchmark arm disable sage for ITS worker only, without mv-ing the shared
# hooks.json (which silently disables sage for every other live thread).
if os.environ.get("AGY_SAGE_DISABLED") == "1":
    _is_post = any(
        a.lower() in ("post_invocation", "postinvocation", "post-invocation", "post")
        for a in sys.argv[1:]
    )
    print(json.dumps({"injectSteps": []} if _is_post else {"decision": "stop"}))
    sys.exit(0)

# Resolve real script location even when invoked via symlinks
_HOOK_DIR = os.path.dirname(os.path.realpath(__file__))
_REPO_DIR = os.path.abspath(os.path.join(_HOOK_DIR, ".."))

if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

from sage.runner import main

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            from sage.guards import fail_safe_exit
            fail_safe_exit(f"Unhandled top-level exception: {e}")
        except Exception:
            pass
        is_post = any(a.lower() in ("post_invocation", "postinvocation", "post-invocation", "post") for a in sys.argv[1:])
        print(json.dumps({"injectSteps": []} if is_post else {"decision": "stop"}))
        sys.exit(0)

