#!/usr/bin/env python3
"""
sage eval harness - drive the REAL sage policy pipeline over scenario files.

NOT a one-off test rig: scenarios live as JSON in scripts/eval/scenarios/.
Adding coverage = dropping a new .json file, zero code changes.

Real layers under test: guards, cadence gate, repeat-loop override, deferral
scan, triage/classify, hammer guard, dedup state. The only stub is the model
call itself (deterministic verdict scripts per scenario) so runs are free,
fast, and reproducible.

Usage:
  python3 scripts/eval/run_eval.py                     # all scenarios
  python3 scripts/eval/run_eval.py loop_early          # one scenario
  python3 scripts/eval/run_eval.py --json out.json     # machine report

Exit code: 0 all pass, 1 any fail.
"""

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sage.policies as P
from sage import sage as S

SCENARIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios")


def load_scenarios(names=None):
    scenarios = []
    for fn in sorted(os.listdir(SCENARIO_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(SCENARIO_DIR, fn), encoding="utf-8") as f:
            sc = json.load(f)
        if names and sc.get("id") not in names:
            continue
        scenarios.append(sc)
    return scenarios


def write_transcript(tpath, steps):
    with open(tpath, "w", encoding="utf-8") as f:
        for s in steps:
            f.write(json.dumps(s) + "\n")


def _fresh_state():
    return {"sage_advice_counts": {}, "sage_emitted_texts": [],
            "mid_turn_steers": 0, "last_steer_tools": 0, "steer_suppress_count": 0}


def drive_turn(sc):
    """Replay a turn checkpoint-by-checkpoint through the real sage_flow."""
    import tempfile
    conv_id = f"eval_{sc['id']}_{os.getpid()}"
    tpath = os.path.join(tempfile.gettempdir(), f"{conv_id}.jsonl")
    user = sc["user_prompt"]
    tools = [(t[0], t[1]) for t in sc["tools"]]
    script = list(sc.get("script") or [])

    def row(name, content):
        return {"type": "PLANNER_RESPONSE", "content": content,
                "tool_calls": [{"name": name, "args": {"args": {"command": content}}}]}

    outcomes, steered_cats = [], []
    state = _fresh_state()
    orig = S.run_sage_model
    eval_idx = [0]

    def fake_run(*a, **kw):
        # Script advances per MODEL CALL (evaluation), not per checkpoint:
        # checkpoints skipped by cadence don't consume script entries.
        i = min(eval_idx[0], len(script) - 1)
        v = script[i]
        if isinstance(v, dict):
            eval_idx[0] += 1
            return dict(v)
        return None  # explicit null: model called but returns nothing -> 'error' path

    S.run_sage_model = fake_run
    try:
        for cp in range(1, len(tools) + 1):
            if cp % 2:      # checkpoints every 2 tools like the real hook cadence
                continue
            prefix = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": user}] + \
                     [row(n, c) for n, c in tools[:cp]]
            write_transcript(tpath, prefix)
            names = [n for n, _ in tools[:cp]]
            res = P.sage_flow(
                "midturn", conv_id=conv_id, transcript_path=tpath, clean_prompt=user,
                initial_line_count=len(prefix), total_tool_calls=len(names),
                turn_tool_names=set(names), user_prompt=user, agent_steps=[], git_diff="",
                state=state, workspace_root=os.path.join(tempfile.gettempdir(), "agy_eval_ws"))
            outcomes.append({"checkpoint": cp, "action": res.get("action"),
                             "decision": res.get("decision"), "category": res.get("category"),
                             "reason": (res.get("reason") or "")[:120]})
            # Mirror runner: stash pending_clarify into state (surfaces at final)
            if res.get("pending_clarify"):
                state["pending_clarify"] = res["pending_clarify"]
            if res.get("action") == "emit":
                state["sage_advice_counts"] = res.get("seen", state.get("sage_advice_counts", {}))
                if res.get("category"):
                    state["last_steer_category"] = res["category"]
                    state["last_steer_tools"] = len(names)
                    state["steer_suppress_count"] = 0
                steered_cats.append(res["category"])
    finally:
        S.run_sage_model = orig
        if os.path.exists(tpath):
            os.unlink(tpath)

    # Optional final-gate phase
    final = None
    fs = sc.get("final_script")
    if fs is not None:
        fpath = os.path.join(tempfile.gettempdir(), f"{conv_id}_final.jsonl")
        steps_all = [{"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": user}] + \
                    [row(n, c) for n, c in tools]
        S.run_sage_model = lambda *a, **kw: dict(fs)
        try:
            write_transcript(fpath, steps_all)
            names_all = [n for n, _ in tools]
            fres = P.final_sage_gate(
                conv_id=conv_id, transcript_path=fpath, clean_prompt=user,
                initial_line_count=len(steps_all), total_tool_calls=len(names_all),
                turn_tool_names=set(names_all), user_prompt=user,
                agent_steps=[], git_diff=sc.get("git_diff", ""), state=state)
            final = {"action": fres.get("action"), "category": fres.get("category"),
                     "reason": (fres.get("reason") or "")[:120]}
            if fres.get("pending_clarify"):
                final["pending_clarify"] = True
            # Mirror the runner's two-phase clarify: a pending_clarify stashed
            # mid-turn surfaces at final-gate time even when this gate itself
            # emits something else (runner checks state BEFORE gate dispatch).
            if not final.get("pending_clarify") and state.get("pending_clarify"):
                final["pending_clarify"] = True
                final.setdefault("category", "confused_goal")
        finally:
            S.run_sage_model = orig
            if os.path.exists(fpath):
                os.unlink(fpath)

    return {"id": sc["id"], "desc": sc.get("desc", ""), "midturn": outcomes,
            "final": final, "steered_cats": steered_cats}


def grade(res, expect):
    problems = []
    ffcp = expect.get("first_fire_cp")
    if ffcp is not None:
        fires = [o for o in res["midturn"] if o["action"] == "emit"]
        got = fires[0]["checkpoint"] if fires else None
        if got != ffcp:
            problems.append(f"expected fire at cp={ffcp}, got {got}")
    cat = expect.get("decision_category")
    if cat is not None:
        if cat not in res["steered_cats"]:
            problems.append(f"missing {cat} emission; cats={res['steered_cats']}")
    maxp = expect.get("max_parallel_emissions")
    if maxp is not None:
        n = sum(1 for o in res["midturn"] if o["action"] == "emit")
        if n > maxp:
            problems.append(f"emitted {n}x > max {maxp} (hammer)")
    noint = expect.get("midturn_interrupts")
    if noint is not None:
        n = sum(1 for o in res["midturn"] if o["action"] == "emit")
        if n != noint:
            problems.append(f"midturn interrupts {n} != {noint}")
    if expect.get("final_pending_clarify_ok") and not (
            (res["final"] or {}).get("pending_clarify")
            # Runner surfacing: gate emitted confused_goal itself -> emits [CLARIFY]
            or ((res["final"] or {}).get("category") == "confused_goal"
                and (res["final"] or {}).get("action") in ("emit", "healthy"))):
        problems.append(f"final confused_goal missing; action={(res['final'] or {}).get('action')}")
    probe = res.get("dedup_probe")
    if probe is not None:
        ratio, lo, hi = probe
        if not (lo < ratio < hi):
            problems.append(f"dedup ratio {ratio:.2f} not in ({lo}, {hi})")
    return problems


def dedup_probe(sc):
    """prompt_dedup_bytes: measure prompt shrinkage from pane-read dedupe."""
    p = sc.get("dedup_probe") or {}
    if not p:
        return None
    from sage.sage import _dedupe_pane_reads
    pre, post, ch, nrep, nsteps = p["pad"]
    facts = p["worker_facts"]
    steps = [pre + ch * nrep + post for _ in range(nsteps - 1)] + list(p["steps"])[-1:]
    raw = "\n".join(steps)
    deduped = "\n".join(_dedupe_pane_reads(steps, facts))
    ratio = len(deduped) / max(1, len(raw))
    return (round(ratio, 3), 0.05, 0.85)


def main():
    args = sys.argv[1:]
    out_json = None
    if "--json" in args:
        i = args.index("--json")
        out_json = args[i + 1]
        del args[i:i + 2]
    results, npass = [], 0
    for sc in load_scenarios(set(args) or None):
        res = drive_turn(sc)
        probe = dedup_probe(sc)
        if probe:
            res["dedup_probe"] = probe
        problems = grade(res, sc.get("expect") or {})
        npass += 0 if problems else 1
        status = "PASS" if not problems else "FAIL"
        print(f"[{status}] {res['id']}: {res['desc']}")
        for p in problems:
            print(f"   - {p}")
        results.append({"scenario": sc, "result": res, "problems": problems})
    total = len(results)
    print(f"\n{npass}/{total} passed")
    if out_json:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1, default=str)
    sys.exit(0 if npass == total else 1)


if __name__ == "__main__":
    main()
