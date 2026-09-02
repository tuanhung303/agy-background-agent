#!/usr/bin/env python3
"""pdd-adapt benchmark runner — 2 tasks × 2 arms (sage ON/OFF) × N trials, serial.

Adapted from promptdriven/pdd research harness (omlx-qwen38-pi-deepseek, 2026-08-23).
Protocol preserved: paired matrix, serial cells, reversed arm order per trial,
per-cell isolated workspace + git baseline, fail-closed manifest, resume via
results.jsonl. Deviations (documented): no metering proxy (agy uses cloud auth
with its own endpoint config — usage not interceptable without breaking auth);
metrics come from agy brain transcripts + sage journal + host-side grading.
No Seatbelt sandbox for the agent (agy needs real HOME for auth); checker still
runs with isolated env.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent  # agy-background-agent
BENCH = Path(__file__).resolve().parent
TASKS = ("tb-wal", "tb-sched")
ARMS = ("on", "off")
DEFAULT_TRIALS = 3
DEFAULT_TIMEOUT = 2400
SMOKE_TIMEOUT = 900
RESULT_SCHEMA_VERSION = "pdd-adapt-1"
MODEL_ID = "Gemini 3.7 Flash (Medium)"
BRAIN = Path.home() / ".gemini/antigravity-cli/brain"

# task source: copied from repo benchmark/ dirs (pinned by tree hash in manifest)
TASK_SOURCE = {
    "tb-wal": REPO / "benchmark/terminal-bench-wal/sage_on",
    "tb-sched": REPO / "benchmark/terminal-bench-scheduler/sage_on",
}


def sha256_tree(root: Path) -> str:
    skip_parts = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and not (set(p.parts) & skip_parts):
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


@dataclass
class CellMetrics:
    steps: int = 0
    planner_turns: int = 0
    subagent_convs: int = 0
    real_done: bool = False
    conv_ids: list[str] | None = None

    def __post_init__(self) -> None:
        if self.conv_ids is None:
            self.conv_ids = []


def prepare_workspace(task: str, run_root: Path) -> Path:
    import shutil

    src = TASK_SOURCE[task]
    ws = run_root / "workspace"
    if task == "tb-wal":
        shutil.copytree(src / "environment/app", ws)
    else:
        shutil.copytree(src / "environment/task_file", ws)
    shutil.copy2(src / "instruction.md", ws / "instruction.md")
    shutil.copytree(src / "tests", ws / "tests")
    return ws


def init_git(ws: Path) -> None:
    env = {"PATH": os.environ["PATH"], "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@local",
           "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@local"}
    subprocess.run(["git", "init", "-q"], cwd=ws, env=env, check=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, env=env, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=ws, env=env, check=True)


def run_agy(ws: Path, run_root: Path, arm: str, timeout: int) -> dict:
    instruction = (ws / "instruction.md").read_text().strip()
    instruction += ("\n\nWork ONLY inside the current workspace directory. Do NOT read, diff, or modify "
                    "any file outside it (especially not the benchmark source tree or other task copies). "
                    "Implement the requested change, run the relevant tests, and finish when correct. "
                    "Finish with a final line '計画通り: <one-line result>'.")
    env = dict(os.environ)
    env["AGY_SAGE_DISABLED"] = "0" if arm == "on" else "1"
    env.pop("TASK_FILE_DIR", None)
    started = time.monotonic()
    start_epoch = time.time()
    attempts = 0
    while True:
        attempts += 1
        proc = subprocess.Popen(
            ["agy", "-p", instruction, "--effort", "medium", "--disable-slash-commands"],
            cwd=ws, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
        timed_out = False
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            out, err = proc.communicate()
        # retry only transient CLI aborts (API timeout, rc=1, mid-task death),
        # not task timeouts (retrying those doubles wall time for nothing)
        if timed_out or proc.returncode == 0 or attempts >= 2:
            break
        err_tail = err[-500:]
        if "timeout waiting for response" not in err_tail and "ECONNRESET" not in err_tail and "fetch failed" not in err_tail:
            break
        print(f"    transient agy abort (rc=1), retry {attempts}/2", flush=True)
    elapsed = time.monotonic() - started
    (run_root / "stdout.txt").write_text(out[-100_000:], encoding="utf-8")
    (run_root / "stderr.txt").write_text(err[-100_000:], encoding="utf-8")
    # collect convs created during this cell (all attempts included)
    convs = []
    if BRAIN.is_dir():
        for d in BRAIN.iterdir():
            if d.is_dir() and d.stat().st_mtime >= start_epoch - 5:
                convs.append(d)
    metrics = read_convs(convs, ws)
    return {"returncode": proc.returncode, "timed_out": timed_out, "wall_seconds": elapsed,
            "metrics": asdict(metrics)}


def read_convs(conv_dirs: list[Path], ws: Path) -> CellMetrics:
    import glob
    steps_total = 0
    planner = 0
    real_done = False
    ids: list[str] = []
    for d in conv_dirs:
        files = sorted(glob.glob(str(d / ".system_generated/logs/chunks/transcript_full/*.jsonl")))
        if not files:
            continue
        steps = 0
        last_pr = None
        for fp in files:
            for line in open(fp, errors="replace"):
                try:
                    s = json.loads(line)
                except json.JSONDecodeError:
                    continue
                steps += 1
                if s.get("type") == "PLANNER_RESPONSE":
                    last_pr = s
        if last_pr is None:
            continue
        ids.append(d.name)
        steps_total += steps
        prs = 1  # count per conv conservatively (exact per-conv turn count not needed for verdict)
        planner += prs
        if last_pr and not last_pr.get("tool_calls") and "計画通り" in (last_pr.get("content") or "")[-400:]:
            real_done = True
    return CellMetrics(steps=steps_total, planner_turns=planner, subagent_convs=max(0, len(ids) - 1),
                       real_done=real_done, conv_ids=ids)


def grade(task: str, ws: Path) -> dict:
    ws = ws.resolve()  # gates use absolute APP_ROOT/PYTHONPATH: relative paths fail if child changes cwd
    env = dict(os.environ)
    env["APP_ROOT"] = str(ws)
    env["PYTHONPATH"] = str(ws)
    env["TASK_FILE_DIR"] = str(ws)
    gates: dict[str, bool] = {}
    if task == "tb-wal":
        for name, script in (("structural", "tests/structural_gate.py"),
                             ("performance", "tests/performance_gate.py")):
            r = subprocess.run([sys.executable, script], cwd=ws, env=env,
                               capture_output=True, text=True, timeout=300)
            gates[name] = r.returncode == 0 and "passed" in (r.stdout + r.stderr).lower()
        # privilege-drop test (scenario_30) is container-only (see RESULTS-R3:
        # "24/25 host, 1 env-only deselect privilege-drop"): deselect on host
        r = subprocess.run([sys.executable, "-m", "pytest", "tests/_hidden_outputs.py", "-q",
                            "--deselect", "tests/_hidden_outputs.py::TestSuite04A::test_scenario_30"],
                           cwd=ws, env=env, capture_output=True, text=True, timeout=600)
        # pytest -q with dots instead of verbose: no per-line " PASSED"; parse summary line
        tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        gates["hidden"] = r.returncode == 0 and " passed" in tail and " failed" not in tail
    else:
        r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_outputs.py", "-q"],
                           cwd=ws, env=env, capture_output=True, text=True, timeout=600)
        tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        gates["tests"] = r.returncode == 0 and "6 passed" in tail
    return {"gates": gates, "score": sum(gates.values()) / len(gates), "passed": all(gates.values())}


def sage_events(conv_ids: list[str]) -> dict[str, int]:
    journal = Path("/tmp/agy_sage_events.jsonl")
    counts: dict[str, int] = {}
    if not journal.exists() or not conv_ids:
        return counts
    wanted = set(conv_ids)
    for line in journal.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("conv_id") in wanted:
            counts[e.get("event", "?")] = counts.get(e.get("event", "?"), 0) + 1
    return counts


def run_cell(task: str, arm: str, trial: int, run_base: Path, timeout: int, phase: str) -> dict:
    run_id = f"{trial:02d}-{task}-{arm}"
    run_root = run_base / "raw" / run_id
    if run_root.exists():  # fresh cell each time; resume is via results.jsonl skip
        import shutil
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True)
    ws = prepare_workspace(task, run_root)
    init_git(ws)
    agy = run_agy(ws, run_root, arm, timeout)
    g = grade(task, ws)
    events = sage_events(agy["metrics"]["conv_ids"] or [])
    result = {
        "schema_version": RESULT_SCHEMA_VERSION, "run_id": run_id, "phase": phase,
        "task": task, "task_sha256": sha256_tree(TASK_SOURCE[task]), "arm": arm, "trial": trial,
        "model": MODEL_ID, "timeout_seconds": timeout, "agy": agy, "grade": g,
        "sage_events": events,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with (run_base / "results.jsonl").open("a") as fh:
        fh.write(json.dumps(result, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    print(f"{run_id}: score={g['score']:.2f} pass={g['passed']} real_done={agy['metrics']['real_done']} "
          f"wall={agy['wall_seconds']:.0f}s steps={agy['metrics']['steps']}", flush=True)
    return result


def build_schedule(tasks, trials):
    """Serial schedule, arm order reversed on odd trials (pdd order-effect control)."""
    sched = []
    for t in range(1, trials + 1):
        arms = ARMS if t % 2 == 1 else tuple(reversed(ARMS))
        for task in tasks:
            for arm in arms:
                sched.append((t, task, arm))
    return sched


def validate(tasks_root_map: dict) -> None:
    for task, src in tasks_root_map.items():
        assert (src / "instruction.md").is_file(), f"missing instruction for {task}"
        assert (src / "tests").is_dir(), f"missing tests for {task}"


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--smoke-timeout", type=int, default=SMOKE_TIMEOUT)
    ap.add_argument("--run-base", type=Path, default=BENCH / "runs")
    ap.add_argument("--tasks", default=",".join(TASKS))
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--skip-smoke", action="store_true")
    args = ap.parse_args()
    tasks = tuple(x.strip() for x in args.tasks.split(",") if x.strip())
    validate(TASK_SOURCE)
    if args.validate_only:
        print("validate: OK")
        return
    args.run_base.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": RESULT_SCHEMA_VERSION, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": MODEL_ID, "tasks": list(tasks),
        "task_hashes": {t: sha256_tree(TASK_SOURCE[t]) for t in tasks},
        "arms": list(ARMS), "trials": args.trials, "timeout_seconds": args.timeout,
        "sage_gate": "AGY_SAGE_DISABLED=1 for arm=off (per-spawn env, hot unplug)",
        "metrics_source": "agy brain transcript chunks + host grading + /tmp/agy_sage_events.jsonl",
        "deviation_note": "no metering proxy: agy cloud auth not interceptable; cost proxied by steps/turns/wall",
    }
    mpath = args.run_base / "manifest.json"
    if mpath.exists():
        existing = json.loads(mpath.read_text())
        for k in ("task_hashes", "arms", "timeout_seconds", "model"):
            if existing.get(k) != manifest[k]:
                raise RuntimeError(f"Resume manifest mismatch on {k}")
    else:
        mpath.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    # smoke gate: first task, trial 1, both arms, short timeout
    if not args.skip_smoke:
        smoke_base = args.run_base / "smoke"
        smoke_base.mkdir(exist_ok=True)
        done_arms = set()
        if (smoke_base / "results.jsonl").exists():
            for ln in (smoke_base / "results.jsonl").read_text().splitlines():
                if ln.strip():
                    done_arms.add(json.loads(ln)["arm"])
        for arm in ARMS:
            if arm in done_arms:
                continue
            r = run_cell(tasks[0], arm, 1, smoke_base, args.smoke_timeout, phase="smoke")
            if not r["agy"]["metrics"]["real_done"] and not r["grade"]["passed"]:
                raise RuntimeError(f"smoke gate failed for arm={arm}: no real-DONE and no passing grade")
        print("smoke gate passed", flush=True)

    bench_base = args.run_base / "benchmark"
    bench_base.mkdir(exist_ok=True)
    done: set[str] = set()
    rp = bench_base / "results.jsonl"
    if rp.exists():
        done = {json.loads(ln)["run_id"] for ln in rp.read_text().splitlines() if ln.strip()}
    for trial, task, arm in build_schedule(tasks, args.trials):
        run_id = f"{trial:02d}-{task}-{arm}"
        if run_id in done:
            print(f"{run_id}: already complete; skipping", flush=True)
            continue
        run_cell(task, arm, trial, bench_base, args.timeout, phase="benchmark")


if __name__ == "__main__":
    main()
