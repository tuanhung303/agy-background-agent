"""
sage.mcp_bridge_wait - Wait and poll logic for Sage steering ACK channel.
"""
from datetime import datetime
import json
import os
import time

from sage.mcp_bridge_helpers import get_brain_dir, get_inbox_dir


def parse_step_ts(step):
    ca = step.get("created_at") or step.get("timestamp") or step.get("ts")
    if isinstance(ca, (int, float)):
        return float(ca)
    if isinstance(ca, str) and ca.strip():
        try:
            return datetime.fromisoformat(ca.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    return None


def check_transcript_reaction(transcript_path, receipt_ts):
    if not os.path.exists(transcript_path):
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    step = json.loads(line)
                except Exception:
                    continue
                sts = parse_step_ts(step)
                if sts is not None and sts < receipt_ts - 0.001:
                    continue
                stype = str(step.get("type") or "").upper()
                content = str(step.get("content") or "").strip()
                tcalls = step.get("tool_calls") or step.get("toolCalls") or []
                if tcalls:
                    return {"status": "tool_ran", "detail": f"Tool calls: {tcalls}"[:300]}
                if stype == "PLANNER_RESPONSE" and content:
                    return {"status": "replied", "detail": content[:300]}
                if stype in ("TOOL_USE", "GENERIC_TOOL", "COMMAND", "TOOL_RESULT"):
                    return {"status": "tool_ran", "detail": (content or stype)[:300]}
    except Exception:
        pass
    return None


def sage_wait(conv_id, seq, timeout_s=10.0, sleep_fn=time.sleep, time_fn=time.time):
    inbox_dir = get_inbox_dir()
    receipt_file = os.path.join(inbox_dir, f"{conv_id}.receipt")
    brain_dir = get_brain_dir()
    transcript_path = os.path.join(brain_dir, conv_id, ".system_generated", "logs", "transcript.jsonl")
    start_t = time_fn()
    drained_receipt = None
    while (time_fn() - start_t) <= timeout_s:
        if not drained_receipt and os.path.exists(receipt_file):
            try:
                with open(receipt_file, "r", encoding="utf-8") as rf:
                    rec = json.load(rf)
                    if int(rec.get("seq", 0)) >= int(seq):
                        drained_receipt = rec
            except Exception:
                pass
        if drained_receipt:
            rec_ts = float(drained_receipt.get("ts", start_t))
            reaction = check_transcript_reaction(transcript_path, rec_ts)
            if reaction:
                return reaction
        elapsed = time_fn() - start_t
        if elapsed >= timeout_s:
            break
        sleep_fn(min(0.5, timeout_s - elapsed))
    if drained_receipt:
        return {
            "status": "injected_only",
            "detail": f"Seq {seq} drained at {drained_receipt.get('ts')}, but no reaction observed within {timeout_s}s",
        }
    return {
        "status": "timeout",
        "detail": f"Seq {seq} drain receipt not observed within {timeout_s}s",
    }
