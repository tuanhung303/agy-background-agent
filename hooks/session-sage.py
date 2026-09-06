#!/usr/bin/env python3
"""
session-sage.py - Autonomous AI Quality & Completeness Stop Verifier Hook

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


# Hot unplug: when AGY_SAGE_DISABLED is set in the agy process environment
# (per-spawn, not global), the sage hook no-ops immediately — emitting a
# neutral pass-through payload instead of running the audit.
if os.environ.get("AGY_SAGE_DISABLED") == "1":
    _is_post = any(
        a.lower() in ("post_invocation", "postinvocation", "post-invocation", "post")
        for a in sys.argv[1:]
    )
    _resp = {"injectSteps": []} if _is_post else {"decision": "stop"}
    print(json.dumps(_resp))
    sys.exit(0)

if __name__ == "__main__":
    try:
        from sage.lite.runner import run_lite_stop_audit
        run_lite_stop_audit()
    except Exception as e:
        try:
            from sage.guards import fail_safe_exit
            fail_safe_exit(f"Unhandled top-level exception: {e}")
        except Exception:
            pass
        is_post = any(a.lower() in ("post_invocation", "postinvocation", "post-invocation", "post") for a in sys.argv[1:])
        print(json.dumps({"injectSteps": []} if is_post else {"decision": "stop"}))
        sys.exit(0)
