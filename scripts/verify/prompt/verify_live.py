"""Run real model judgments on controlled fixtures; retain raw evidence and exact verdict scores."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sage.config import LITE_MODEL_CANDIDATES, LITE_MODE_TIMEOUT
from sage.executor import ensure_isolated_home
from sage.lite.prompt import build_lite_verifier_prompt
from scripts.verify.prompt.verify_adversarial import load_cases, score_output


def load_builder(path):
    """Optionally load a saved trusted baseline prompt for a paired comparison."""
    if not path:
        return build_lite_verifier_prompt
    spec = importlib.util.spec_from_file_location("baseline_prompt", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_lite_verifier_prompt


def evaluate_case(case, builder, model, timeout, env, cwd):
    """Call the actual CLI directly so timeout/cascade fallback cannot fabricate PASS."""
    prompt = builder(case["request"], case["response"], turn_execution_summary=case["evidence"])
    prompt = (
        "Controlled evaluation: assess the supplied synthetic transcript as the complete recorded evidence. "
        "Paths and endpoints refer to this fixture, not this machine. Do not execute or modify them. "
        "Judge the supplied record using the verifier policy below.\n\n" + prompt
    )
    command = [
        shutil.which("agy") or str(Path.home() / ".local/bin/agy"),
        "-p", prompt, "--model", model, "--disable-slash-commands",
    ]
    start = time.monotonic()
    record = {
        "case": case["id"], "expected": case["expected"], "model": model,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "fixture_sha256": hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest(),
    }
    try:
        result = subprocess.run(command, input="", capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
        record.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        verdict, status = score_output(result.stdout, case["expected"])
        record.update(verdict=verdict, status=status if result.returncode == 0 else "execution_error")
        if case.get("expected_completion"):
            record["completion_matches"] = isinstance(verdict, dict) and verdict.get("completion") == case["expected_completion"]
            if record["status"] == "matched" and not record["completion_matches"]:
                record["status"] = "completion_mismatch"
    except subprocess.TimeoutExpired as exc:
        record.update(status="timeout", stdout=_text(exc.stdout), stderr=_text(exc.stderr))
    except OSError as exc:
        record.update(status="execution_error", error=str(exc))
    record["duration_seconds"] = round(time.monotonic() - start, 3)
    return record


def _text(value):
    """Normalize partial subprocess output from a timeout."""
    return value.decode(errors="replace") if isinstance(value, bytes) else value or ""


def main():
    """Execute the fixture matrix and fail on every mismatch or unavailable evaluation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-prompt", type=Path)
    parser.add_argument("--cases", type=Path, help="Optional independent fixture file")
    parser.add_argument("--model", default=LITE_MODEL_CANDIDATES[0])
    parser.add_argument("--timeout", type=float, default=LITE_MODE_TIMEOUT)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1 or args.timeout <= 0:
        parser.error("repeats and timeout must be positive")
    if os.environ.get("AGY_LITE_MOCK_VERDICT", "").strip():
        parser.error("AGY_LITE_MOCK_VERDICT must be unset for a live evaluation")
    cases = load_cases(args.cases)
    if args.case_ids:
        unknown = set(args.case_ids) - {case["id"] for case in cases}
        if unknown:
            parser.error(f"Unknown cases: {sorted(unknown)}")
        cases = [case for case in cases if case["id"] in args.case_ids]
    builder = load_builder(args.baseline_prompt)
    output = args.output or ROOT / "tmp" / f"prompt-eval-{time.time_ns()}.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HOME=ensure_isolated_home(), AGY_STOP_AUDIT_ACTIVE="1")
    counts = {}
    # Fresh CLI sessions, isolated home, empty cwd, no real parent conversation.
    with tempfile.TemporaryDirectory(prefix="stop-prompt-eval-") as cwd, output.open("x") as log:
        for repeat in range(args.repeats):
            for case in cases:
                record = evaluate_case(case, builder, args.model, args.timeout, env, cwd)
                record["repeat"] = repeat + 1
                log.write(json.dumps(record) + "\n")
                log.flush()
                status = record["status"]
                counts[status] = counts.get(status, 0) + 1
                print(f"{repeat + 1}: {case['id']}: {status} ({record['duration_seconds']}s)", flush=True)
                # Do not repeat authentication/infrastructure failures across the matrix.
                if status in ("timeout", "execution_error"):
                    print(f"Evaluation stopped; remaining cases were not evaluated. Evidence: {output}", flush=True)
                    return 1
    print(json.dumps({"counts": counts, "evidence": str(output)}), flush=True)
    return 0 if counts.get("matched", 0) == len(cases) * args.repeats else 1


if __name__ == "__main__":
    sys.exit(main())
