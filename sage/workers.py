"""
sage.workers - Delegated-worker EVIDENCE for the sage prompt.
Channel-agnostic (AGY_SAGE_WORKER_SPAWN_RE); RAW tails + line citations.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone

from sage.sanitizer import delivered_state, is_live
from sage.watchers import _parse_iso_ts

_SPAWN_ENV, _IDLE_ENV = "AGY_SAGE_WORKER_SPAWN_RE", "AGY_SAGE_WORKER_IDLE_RE"
_DEFAULT_SPAWN_PATTERNS = (r"\bterminal\s+(?:create|split)\b", r"\btmux\s+(?:new-session|new-window|send-keys)\b", r"\bscreen\s+-[dm]m?S\b")
_DEFAULT_IDLE, _EXCERPT_CHARS = r"(?:^|\n)[^\S\n]*(?:❯|\$|%|#)\s*(?:\n|$)", 4000
_TOKEN_RE, _CLOSE_HINTS = re.compile(r"(?:term_[0-9a-f-]{8,}|task-[0-9]+|%[0-9]+)"), ("terminal close", "kill-session", "tmux kill")
_HANDLE_STATUS_RE = re.compile(
    r'"handle"\s*:\s*"([^"]+)"[^}]*?"status"\s*:\s*"(exited|closed)"|'
    r'"status"\s*:\s*"(?:exited|closed)"[^}]*?"handle"\s*:\s*"([^"]+)"'
)


def _compiled_spawn():
    pats = [p for p in os.environ.get(_SPAWN_ENV, "").strip().split(";;") if p.strip()] or list(_DEFAULT_SPAWN_PATTERNS)
    try:
        return [re.compile(p, re.I) for p in pats]
    except re.error:
        return [re.compile(p, re.I) for p in _DEFAULT_SPAWN_PATTERNS]


def _idle_re():
    try:
        return re.compile(os.environ.get(_IDLE_ENV, "").strip() or _DEFAULT_IDLE)
    except re.error:
        return re.compile(_DEFAULT_IDLE)


def _line_map(transcript_path, steps):
    if not transcript_path or not os.path.exists(transcript_path):
        return {}
    mapping, idx = {}, 0
    key = lambda o: (o.get("created_at"), o.get("type"), len(str(o.get("content") or "")), str(o.get("content") or "")[:40])
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for lno, raw in enumerate(fh, 1):
                if idx >= len(steps):
                    break
                try:
                    if key(json.loads(raw)) == key(steps[idx]):
                        mapping[idx] = lno
                        idx += 1
                except Exception:
                    continue
    except OSError:
        return {}
    return mapping


def _age(ts_str):
    dt = _parse_iso_ts(ts_str)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds()) if dt else None


def _fmt_age(age):
    return f"{age:.0f}s ago (~{int(age // 60)}m)" if age and age >= 60 else (f"{age:.0f}s ago" if age is not None else "?")


def _excerpt_from_out(out, idle_re):
    exc, joined, closed = out[:_EXCERPT_CHARS], out, False
    i = out.find("{")
    if i != -1:
        try:
            env = json.loads(out[i : out.rfind("}") + 1], strict=False)
            tails = (env.get("result") or {}).get("terminal", {}).get("tail", [])
            if tails:
                joined = "\n".join(tails)
                exc = joined[:_EXCERPT_CHARS]
            closed = str((env.get("result") or {}).get("terminal", {}).get("status") or "").lower() in ("exited", "closed")
        except Exception:
            joined, exc = out, out[:_EXCERPT_CHARS]
    live = is_live(joined)
    idle = bool(idle_re.search(joined)) and not live and not closed
    return exc, idle, live, closed


def inspect_subagent_transcript(conv_id, max_chars=2000):
    if not conv_id:
        return None
    from sage.transcript import _read_transcript_steps, _safe_tool_calls, get_transcript_path
    steps = _read_transcript_steps(get_transcript_path({}, conv_id))
    if not steps:
        return None
    tools_called, commands, final_resp = [], [], ""
    for s in steps:
        stype, content = s.get("type"), str(s.get("content") or "").strip()
        for t in _safe_tool_calls(s):
            tname = str(t.get("name") or "")
            if tname:
                tools_called.append(tname)
            if tname in ("run_command", "bash", "exec", "terminal"):
                cmd = str((t.get("args") or {}).get("CommandLine") or (t.get("args") or {}).get("command") or "").strip()
                if cmd:
                    commands.append(cmd)
        if stype == "PLANNER_RESPONSE" and content:
            final_resp = content
    parts = []
    if tools_called:
        parts.append(f"tools={len(tools_called)} ({', '.join(tools_called[:6])})")
    if commands:
        parts.append(f"commands=[{'; '.join(commands[:3])}]")
    if final_resp:
        snip = final_resp[:max_chars] if len(final_resp) <= max_chars else f"{final_resp[:max_chars//2]}...{final_resp[-max_chars//2:]}"
        parts.append(f"final_summary: {snip}")
    return " | ".join(parts) if parts else "session empty"


def extract_worker_facts(steps, transcript_path=None):
    if not steps:
        return ""
    idle_re, spawn_res, lmap = _idle_re(), _compiled_spawn(), _line_map(transcript_path, steps)
    workers, settled, order, pending_reads = {}, set(), [], []
    final_claim, claim_line = "", None

    def _note(tok, kind, cmd, line):
        if tok not in workers:
            workers[tok] = {"kind": kind, "cmd": cmd[:150], "spawn_line": line}
            order.append(tok)

    for i, s in enumerate(steps):
        content, line_no, low = str(s.get("content") or ""), lmap.get(i), str(s.get("content") or "").lower()
        for t in (s.get("tool_calls") or []):
            tname = str(t.get("name") or "")
            args = str((t.get("args") or {}).get("CommandLine") or "")
            if tname == "invoke_subagent":
                targs = t.get("args") or {}
                subs = targs.get("Subagents") or targs.get("subagents") or [{}]
                if isinstance(subs, str):
                    try:
                        subs = json.loads(subs)
                    except Exception:
                        subs = [{}]
                for sub in ([subs] if isinstance(subs, dict) else (subs if isinstance(subs, list) else [{}])):
                    role = sub.get("Role") or sub.get("role") or sub.get("TypeName") or "subagent" if isinstance(sub, dict) else "subagent"
                    _note(f"subagent:{role}", "subagent", f"invoke_subagent Role={role}", line_no)
                continue
            if ("terminal read" in args.lower() or "tmux capture-pane" in args.lower()) and "--help" not in args.lower():
                nxt = steps[i + 1] if i + 1 < len(steps) else {}
                if str(nxt.get("content") or "").strip():
                    excerpt, is_idle, busy, closed = _excerpt_from_out(str(nxt.get("content") or ""), idle_re)
                    for tok in set(_TOKEN_RE.findall(args)):
                        pending_reads.append((tok, excerpt, lmap.get(i + 1), _age(nxt.get("created_at")), is_idle, busy, closed))
            if not args or "--help" in args.lower():
                continue
            if any(p.search(args) for p in spawn_res):
                for tok in (_TOKEN_RE.findall(args) or ["w-" + hashlib.sha1(args.encode("utf-8", "replace")).hexdigest()[:10]]):
                    _note(tok, "worker", args, line_no)
            elif any(h in args.lower() for h in _CLOSE_HINTS):
                settled.update(_TOKEN_RE.findall(args))
        toks_here = set(_TOKEN_RE.findall(content))
        if toks_here and content.strip():
            unbound = [t2 for t2 in order if t2.startswith("w-") and not workers[t2].get("bound_handle")]
            first = next(iter(toks_here), None)
            if first and len(toks_here) == 1 and unbound and first not in workers:
                fb = unbound[-1]
                workers[fb]["bound_handle"] = tok_here = first
                workers[tok_here] = workers.pop(fb)
                order[order.index(fb)] = tok_here
                workers[tok_here]["handle_line"] = line_no
            for tok in toks_here:
                if workers.get(tok):
                    exc, _, live, closed = _excerpt_from_out(content, idle_re)
                    if not workers[tok].get("last"):
                        workers[tok]["last"] = (exc, line_no, _age(s.get("created_at")), False)
                    if closed:
                        settled.add(tok)
                    elif live or not exc:
                        settled.discard(tok)
            for tup in _HANDLE_STATUS_RE.findall(content):
                settled.update(h for h in (tup[0], tup[2]) if h)
            if not is_live(content) and (idle_re.search(content) and ("terminal" in low or "tmux" in low) or ("exited with code" in low and "--screen" in low)):
                settled.update(toks_here)
        for cid in re.findall(r'"conversationId":\s*["\']([a-zA-Z0-9_-]+)["\']', content):
            target = next((t2 for t2 in order if t2.startswith("subagent:") and not workers[t2].get("bound_cid")), None)
            if target:
                workers[target]["bound_cid"] = cid
        for done_cid in re.findall(r"sender=([a-zA-Z0-9_-]+)", content) + re.findall(r"[Ss]ubagent\s+([a-zA-Z0-9_-]+)\s+has gone idle", content):
            for t2 in order:
                if t2.startswith("subagent:") and (workers[t2].get("bound_cid") == done_cid or done_cid in t2):
                    settled.add(t2)
        if any(h in low for h in _CLOSE_HINTS):
            settled.update(toks_here)
        if s.get("type") == "PLANNER_RESPONSE" and content.strip():
            final_claim, claim_line = content.strip(), line_no

    if not workers:
        return ""

    handle_to_key = {w2.get("bound_handle") or t2: t2 for t2, w2 in workers.items()}
    for tok, excerpt, ln, age, is_idle, busy, closed in pending_reads:
        target = handle_to_key.get(tok) or tok
        if target not in workers:
            fb = next((t2 for t2 in order if t2.startswith("w-") and not workers[t2].get("bound_handle")), None)
            if not fb:
                continue
            workers[fb]["bound_handle"] = tok
            workers[tok] = workers.pop(fb)
            order[order.index(fb)] = tok
            target = tok
        prev = workers[target].get("last")
        if prev is None or not prev[3] or (ln or 0) >= (prev[1] or 0):
            workers[target]["last"] = (excerpt, ln, age, True)
        if busy and not closed:
            settled.discard(target)
        elif closed or is_idle:
            settled.add(target)

    lines = []
    for tok in order:
        w = workers[tok]
        last = w.get("last")
        if w["kind"] == "subagent" and w.get("bound_cid"):
            sub_tr = inspect_subagent_transcript(w["bound_cid"])
            if sub_tr:
                last = (sub_tr, w.get("spawn_line"), 0.0, True)
                settled.add(tok)
        state = "SETTLED (idle/close observed)" if tok in settled else "NOT SETTLED (no idle/completion evidence)"
        lines.append(f"worker[{tok}] kind={w['kind']} spawned@line{w['spawn_line']}: {w['cmd']}")
        delivered = "unknown"
        if last:
            exc, ln, age, _auth = last
            delivered = delivered_state(exc)
            lines.append(f"  last_output@line{ln} ({_fmt_age(age)}): {exc}")
        lines.append(f"  state: {state} | delivered: {delivered}")
        w["_delivered"] = delivered
    if final_claim:
        lines.append(f"executor_final_claim@line{claim_line}: \"{final_claim[:160]}\"")
    warn_map = {"NOT SETTLED": "NOT SETTLED — read full output before approving", "partial": "output partial — review truncated before completion", "no": "no delivered output — a stopped pane is not a finished review"}
    warns = []
    for t in order:
        if t not in settled:
            warns.append((t, warn_map["NOT SETTLED"]))
        elif workers[t].get("_delivered") in ("partial", "no"):
            warns.append((t, warn_map[workers[t]["_delivered"]]))
    lines += [f"WARNING: {toks}: {why}" for toks, why in warns[:6]]
    return "\n".join(lines)
