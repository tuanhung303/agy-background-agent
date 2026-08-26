"""
sage.workers - Delegated-worker EVIDENCE for the sage prompt.
Channel-agnostic (AGY_SAGE_WORKER_SPAWN_RE); RAW tails + line citations.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone

from sage.sanitizer import sanitize_tool_output

_SPAWN_ENV = "AGY_SAGE_WORKER_SPAWN_RE"
_DEFAULT_SPAWN_PATTERNS = (
    r"\borca\s+terminal\s+(?:create|split)\b",
    r"\btmux\s+(?:new-session|new-window|send-keys)\b",
    r"\bscreen\s+-[dm]m?S\b")
_IDLE_ENV = "AGY_SAGE_WORKER_IDLE_RE"
_DEFAULT_IDLE = r"(?:^|\n)[^\S\n]*(?:❯|\$|%|#)\s*(?:\n|$)"
_TOKEN_RE = re.compile(r"(?:term_[0-9a-f-]{8,}|task-[0-9]+|%[0-9]+)")
_CLOSE_HINTS = ("terminal close", "kill-session", "tmux kill")
_EXCERPT_CHARS = 4000
_LIVE_MARKERS = ("✻", "✽", "✶", "Sprouting", "Churning", "Sautéing", "Baking",
                 "Pollinating", "almost done thinking", "Running…")


def _is_live(text):
    """Busy = spinner/thinking marker anywhere (TUI shows prompt always)."""
    return bool(text.strip()) and any(m in text for m in _LIVE_MARKERS)


def _compiled_spawn():
    raw = os.environ.get(_SPAWN_ENV, "").strip()
    pats = [p for p in raw.split(";;") if p.strip()] if raw else list(_DEFAULT_SPAWN_PATTERNS)
    try:
        return [re.compile(p, re.I) for p in pats]
    except re.error:
        return [re.compile(p, re.I) for p in _DEFAULT_SPAWN_PATTERNS]


def _idle_re():
    raw = os.environ.get(_IDLE_ENV, "").strip()
    try:
        return re.compile(raw or _DEFAULT_IDLE)
    except re.error:
        return re.compile(_DEFAULT_IDLE)


def _line_map(transcript_path, steps):
    """Index -> source line; keyed on ts+type+len+prefix (timestamps repeat)."""
    if not transcript_path or not os.path.exists(transcript_path):
        return {}
    mapping, idx = {}, 0

    key = lambda o: (o.get("created_at"), o.get("type"),
                     len(str(o.get("content") or "")), str(o.get("content") or "")[:40])
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line_no, raw in enumerate(fh, 1):
                if idx >= len(steps):
                    break
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                if key(d) == key(steps[idx]):
                    mapping[idx] = line_no
                    idx += 1
    except OSError:
        return {}
    return mapping


def _age(ts_str):
    try:
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:
        return None


def _fmt_age(age):
    if age is None:
        return "?"
    return f"{age:.0f}s ago (~{int(age // 60)}m)" if age >= 60 else f"{age:.0f}s ago"  # noqa: E501


_CLOSED_STATUS_RE = re.compile(r'"status"\s*:\s*"(exited|closed|exit|done)"')


def _excerpt_from_out(out, idle_re):
    """Decode JSON envelope -> tail array, head-first; also flags closed status."""
    exc, joined = out[:_EXCERPT_CHARS], out
    closed, i = False, out.find("{")
    if i != -1:
        try:
            env = json.loads(out[i:out.rfind("}") + 1], strict=False)
            term = (env.get("result") or {}).get("terminal") or {}
            tails = term.get("tail") or []
            if tails:
                joined = "\n".join(tails)
                exc = joined[:_EXCERPT_CHARS]
            closed = str(term.get("status") or "").lower() in ("exited", "closed", "exit", "done")
        except Exception:
            joined, exc = out, out[:_EXCERPT_CHARS]
    return (exc, bool(idle_re.search(joined)) and not _is_live(joined), _is_live(joined), closed)


def extract_worker_facts(steps, transcript_path=None):
    """Raw-evidence worker report; empty when no workers. Two signals per
    worker: state (did the pane stop) and delivered (did work land). A settled
    pane with a spinner tail is "stopped without delivering" — the lie here."""
    if not steps:
        return ""
    idle_re = _idle_re()
    spawn_res = _compiled_spawn()
    lmap = _line_map(transcript_path, steps)

    workers = {}  # token -> {"kind","cmd","spawn_line","last":(excerpt,line,age,auth)}
    settled, order = set(), []
    pending_reads = []  # (handle, excerpt, line, age, idle_bool) — resolved post-bind
    final_claim, claim_line = "", None

    def _note(tok, kind, cmd, line):
        if tok not in workers:
            workers[tok] = {"kind": kind, "cmd": cmd[:150], "spawn_line": line}
            order.append(tok)

    chrome_re = re.compile(r"^[^\n]*(?:⏺|⎿|─{6,}|⏵⏵|Tip: |✻|✽|Sprouting|Churning|Sautéing"
                          r"|Baking|still thinking|Running…|Working…|Claude Team"
                          r"|[▝▖▗▘▙▚▛▜▟██]).*?$", re.M)

    def _delivered_state(excerpt):
        """Strip chrome; a LIVE tail with prose is partial, not a clean yes."""
        if not excerpt.strip():
            return "no"
        if "<truncated" in excerpt:
            return "partial"
        stripped = chrome_re.sub("", re.sub(r"<(?:USER_REQUEST|ADDITIONAL_METADATA)>[\s\S]*?(?:</|$)", "", excerpt))
        prose = re.sub(r"\s+", " ", re.sub(r"<(?:USER_REQUEST|ADDITIONAL_METADATA)>[\s\S]*?(?:</|$)", "", stripped)).strip(" ─-\n❯⏵")
        prose = re.sub(r"bypass permissions on.*$", "", prose).strip(" ─-")
        if len(prose) < 80 or len(re.findall(r"[.!?](?:\s|$)", prose)) < 3:
            return "no"
        return "partial" if prose[-1:] in (":", "-", "…") or _is_live(excerpt) else "yes"

    for i, s in enumerate(steps):
        content = str(s.get("content") or "")
        line_no = lmap.get(i)
        low = content.lower()
        for t in (s.get("tool_calls") or []):
            args = str((t.get("args") or {}).get("CommandLine") or "")
            if not args or "--help" in args.lower():
                continue
            if any(p.search(args) for p in spawn_res):
                toks = _TOKEN_RE.findall(args) or ["w-" + hashlib.sha1(args.encode("utf-8", "replace")).hexdigest()[:10]]
                for tok in toks:
                    _note(tok, "worker", args, line_no)
            elif any(h in args.lower() for h in _CLOSE_HINTS):
                settled.update(_TOKEN_RE.findall(args))
            if t.get("name") == "invoke_subagent":
                for role in re.findall(r'"Role"\s*:\s*"([^"]+)"', args) or ["subagent"]:
                    _note(f"subagent:{role}", "subagent", f"invoke_subagent Role={role}", line_no)
        # A pane-read call at step i produces its screen output in step i+1
        # (usually an orca JSON envelope with the real tail array inside).
        for t in (s.get("tool_calls") or []):
            args = str((t.get("args") or {}).get("CommandLine") or "")
            low_args = args.lower()
            if ("terminal read" in low_args or "tmux capture-pane" in low_args) and "--help" not in low_args:
                nxt = steps[i + 1] if i + 1 < len(steps) else {}
                out = str(nxt.get("content") or "")
                if not out.strip():
                    continue
                excerpt, is_idle, is_live, closed = _excerpt_from_out(out, idle_re)
                for tok in set(_TOKEN_RE.findall(args)):
                    pending_reads.append((tok, excerpt, lmap.get(i + 1), _age(nxt.get("created_at")), is_idle, is_live, closed))
        toks_here = set(_TOKEN_RE.findall(content))
        if toks_here and content.strip():
            # A create's JSON response arrives in the NEXT GENERIC step with the
            # real handle; bind it to the most recent fallback-keyed worker.
            unbound = [t2 for t2 in order if t2.startswith("w-") and not workers[t2].get("bound_handle")]
            if len(toks_here) == 1 and unbound:
                fb = unbound[-1]
                workers[fb]["bound_handle"] = tok_here = next(iter(toks_here))
                workers[tok_here] = workers.pop(fb)
                order[order.index(fb)] = tok_here
                workers[tok_here]["handle_line"] = line_no
            for tok in toks_here:
                target = tok if tok in workers else None
                if target is None:
                    for t2, w2 in workers.items():
                        if w2.get("bound_handle") == tok:
                            target = t2
                            break
                if workers.get(tok):
                    exc, _, live, closed = _excerpt_from_out(content, idle_re)
                    if not workers[tok].get("last"):
                        workers[tok]["last"] = (exc, line_no, _age(s.get("created_at")), False)
                    if closed:
                        settled.add(tok)
                    elif live or not exc:
                        settled.discard(tok)
            if _CLOSED_STATUS_RE.search(content):
                settled.update(toks_here)
            elif not _is_live(content) and (idle_re.search(content) and "orca terminal" in low
                                          or ("exited with code" in low and "--screen" in low)):
                settled.update(toks_here)
        if any(h in low for h in _CLOSE_HINTS):
            settled.update(toks_here)
        if s.get("type") == "PLANNER_RESPONSE" and content.strip():
            final_claim, claim_line = content.strip(), line_no

    if not workers:
        return ""

    # Resolve pending pane-read outputs onto their (possibly re-keyed) workers.
    handle_to_key = {w2.get("bound_handle") or t2: t2 for t2, w2 in workers.items()}
    for tok, excerpt, ln, age, is_idle, is_live, closed in pending_reads:
        target = handle_to_key.get(tok) or tok
        if target not in workers:
            continue
        prev = workers[target].get("last")
        if prev is None or not prev[3] or (ln or 0) >= (prev[1] or 0):
            workers[target]["last"] = (excerpt, ln, age, True)  # authoritative screen
        if closed:
            settled.add(target)
        elif is_live:
            settled.discard(target)
        elif is_idle:
            settled.add(target)

    lines = []
    for tok in order:
        w = workers[tok]
        state = ("SETTLED (idle/close observed)" if tok in settled else "NOT SETTLED (no idle/completion evidence)")
        lines.append(f"worker[{tok}] kind={w['kind']} spawned@line{w['spawn_line']}: {w['cmd']}")
        last = w.get("last")
        delivered = "unknown"
        if last:
            exc, ln, age, _auth = last
            delivered = _delivered_state(exc)
            lines.append(f"  last_output@line{ln} ({_fmt_age(age)}): {exc}")
        lines.append(f"  state: {state} | delivered: {delivered}")
        w["_delivered"] = delivered
    if final_claim:
        lines.append(f"executor_final_claim@line{claim_line}: \"{final_claim[:160]}\"")
    warnings = [(t, msg) for t in order for msg in (
        ("NOT SETTLED — read full output before approving",) if t not in settled else (),
        ("output partial — review truncated before completion",) if workers[t].get("_delivered") == "partial" else (),
        ("no delivered output — a stopped pane is not a finished review",) if workers[t].get("_delivered") == "no" else ())]  # noqa: E501
    lines += [f"WARNING: {toks}: {why}" for toks, why in warnings[:4]]
    return "\n".join(lines)
