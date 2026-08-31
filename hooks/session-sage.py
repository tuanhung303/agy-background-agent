#!/usr/bin/env python3
"""
session-sage.py - Autonomous AI Quality & Completeness Sage Hook

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


def _safe_drain_inbox_from_stdin():
    try:
        from sage.mcp_bridge_helpers import drain_inbox
    except Exception:
        drain_inbox = lambda cid: []
    try:
        raw = sys.stdin.read()
        if raw.strip():
            payload = json.loads(raw)
            cid = payload.get("conversationId") or payload.get("conversation_id") or "default"
            return drain_inbox(cid)
    except Exception:
        pass
    return []


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
    _drained = _safe_drain_inbox_from_stdin()
    _steps = [{"userMessage": m.get("message", "")} for m in _drained if m.get("message")]
    if _is_post:
        _resp = {"injectSteps": _steps}
        if _steps:
            _resp["terminationBehavior"] = "force_continue"
    else:
        if _steps:
            _resp = {"decision": "continue", "reason": "Drained sage messages", "injectSteps": _steps}
        else:
            _resp = {"decision": "stop"}
    print(json.dumps(_resp))
    sys.exit(0)

if __name__ == "__main__":
    try:
        from sage.runner import main
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

