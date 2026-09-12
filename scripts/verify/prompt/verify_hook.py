"""Exercise the real stop hook and model using isolated sessions and executed CSV fixtures."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sage.config import LITE_MODEL_CANDIDATES
from sage.executor import clean_resume_history, ensure_isolated_home
from sage.locking import safe_id

GOOD_PARSER = '''import csv, json, sys
try:
    with open(sys.argv[1], newline="") as source:
        rows = list(csv.reader(source, strict=True))
    print(json.dumps(rows))
except csv.Error:
    print("invalid CSV", file=sys.stderr)
    sys.exit(2)
'''
BAD_PARSER = '''import json, sys
with open(sys.argv[1]) as source:
    print(json.dumps([line.strip().split(",") for line in source]))
'''
VERIFY = '''import json, subprocess, sys
from pathlib import Path
root = Path(__file__).resolve().parents[3]
good = subprocess.run([sys.executable, str(root/"csv_import.py"), str(root/"good.csv")], capture_output=True, text=True)
assert good.returncode == 0, good.stderr
assert json.loads(good.stdout) == [["id", "label"], ["1", "a,b"]], good.stdout
bad = subprocess.run([sys.executable, str(root/"csv_import.py"), str(root/"bad.csv")], capture_output=True, text=True)
assert bad.returncode == 2 and bad.stderr.strip() == "invalid CSV", (bad.returncode, bad.stdout, bad.stderr)
assert (root/"good.csv").read_text() == 'id,label\\n1,"a,b"\\n'
assert (root/"bad.csv").read_text() == 'id,label\\n1,"unfinished\\n'
print("U=[csv_import], 1/1: quoted comma preserved; malformed input exit=2; source files unchanged; harness exit=0")
'''
REQUEST = "Fix the CSV import CLI to preserve quoted commas and reject malformed quotes with exit 2. Verify valid input, rejection, and unchanged input files."


def run(command, cwd, env=None, input_text="", timeout=40):
    """Capture actual process output and status for the evidence record."""
    result = subprocess.run(command, cwd=cwd, env=env, input=input_text, capture_output=True, text=True, timeout=timeout)
    return {"command": command, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def event(steps, name, args, output):
    """Record a tool-shaped event for an operation actually executed by this harness."""
    steps.extend([
        {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": name, "args": args}]},
        {"type": "GENERIC", "content": output},
    ])


def prepare_home(source_home, parent_home):
    """Link existing authentication without copying or printing credentials."""
    cli = parent_home / ".gemini/antigravity-cli"
    (cli / "conversations").mkdir(parents=True)
    for source in (source_home / ".gemini/antigravity-cli").iterdir():
        if source.is_file() and (any(key in source.name for key in ("token", "auth", "credential")) or source.name == "installation_id"):
            (cli / source.name).symlink_to(source)
    keychains = source_home / "Library/Keychains"
    if keychains.is_dir():
        (parent_home / "Library").mkdir()
        (parent_home / "Library/Keychains").symlink_to(keychains)
    config = parent_home / ".gemini/config"
    config.mkdir(parents=True)
    (config / "hooks.json").write_text("{}")
    return cli


def exercise(case, root, source_db, parent_cli, parent_home, repair_source=None):
    """Assert actual model verdict, emitted lifecycle response, and persisted state agree."""
    workspace = root / case
    workspace.mkdir()
    steps = [{"type": "USER_INPUT", "content": REQUEST, "timestamp": datetime.now(timezone.utc).isoformat()}]
    (workspace / "good.csv").write_text('id,label\n1,"a,b"\n')
    (workspace / "bad.csv").write_text('id,label\n1,"unfinished\n')
    (workspace / "parsers.json").write_text('{"parsers":["csv_import"],"downstream_consumers":[]}')
    topic = workspace / "scripts/verify/import"
    topic.mkdir(parents=True)
    (topic / "main.py").write_text(VERIFY)
    (workspace / "scripts/verify/all.py").write_text('import runpy\nfrom pathlib import Path\nrunpy.run_path(str(Path(__file__).parent/"import/main.py"), run_name="__main__")\n')
    (workspace / "csv_import.py").write_text(BAD_PARSER)
    event(steps, "write_to_file", {"TargetFile": str(workspace / "csv_import.py")}, "Parser and persistent verification module created.")
    event(steps, "view_file", {"AbsolutePath": str(workspace / "parsers.json")}, (workspace / "parsers.json").read_text())
    verification = run([sys.executable, "scripts/verify/all.py"], workspace)
    assert verification["returncode"] != 0
    executions = [verification]
    event(steps, "run_command", {"CommandLine": f"{sys.executable} scripts/verify/all.py"}, f"exit={verification['returncode']}\n{verification['stderr']}")
    if case == "recovered_stop":
        (workspace / "csv_import.py").write_bytes(repair_source.read_bytes() if repair_source else GOOD_PARSER.encode())
        event(steps, "replace_file_content", {"TargetFile": str(workspace / "csv_import.py")}, "Corrected parsing using strict CSV reader.")
        compile_result = run([sys.executable, "-m", "py_compile", "csv_import.py"], workspace)
        assert compile_result["returncode"] == 0
        executions.append(compile_result)
        event(steps, "run_command", {"CommandLine": f"{sys.executable} -m py_compile csv_import.py"}, "exit=0")
        verification = run([sys.executable, "scripts/verify/all.py"], workspace)
        assert verification["returncode"] == 0, verification
        executions.append(verification)
        event(steps, "run_command", {"CommandLine": f"{sys.executable} scripts/verify/all.py"}, verification["stdout"])
    steps.append({"type": "PLANNER_RESPONSE", "content": "Fixed csv_import.py and verified valid and malformed inputs with unchanged source files. U=[csv_import], 1/1 parsers, no downstream consumers. Saved scripts/verify/import/main.py under scripts/verify/all.py."})
    transcript = workspace / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(step) + "\n" for step in steps))
    conv_id = str(uuid.uuid4())
    parent_db = parent_cli / "conversations" / f"{conv_id}.db"
    with sqlite3.connect(f"file:{source_db}?mode=ro", uri=True) as source, sqlite3.connect(parent_db) as dest:
        source.backup(dest)
        dest.execute("UPDATE trajectory_meta SET cascade_id = ?", (conv_id,))
    before = hashlib.sha256(parent_db.read_bytes()).hexdigest()
    audit = workspace / "audit.log"
    env = dict(os.environ, HOME=str(parent_home), AGY_REAL_HOME=str(parent_home), SAGE_ISOLATED_HOME=str(root / "child-home"), AGY_SAGE_LOG=str(audit))
    for key in ("AGY_LITE_MOCK_VERDICT", "AGY_STOP_AUDIT_ACTIVE", "AGY_SAGE_DISABLED", "AGY_STOP_AUDIT_TEST"):
        env.pop(key, None)
    payload = {"conversationId": conv_id, "transcriptPath": str(transcript), "workspacePaths": [str(workspace)], "fullyIdle": True}
    command = [sys.executable, str(ROOT / "hooks/session-sage.py")]
    if case == "failed_post":
        command.append("post_invocation")
    started = time.monotonic()
    hook = run(command, workspace, env, json.dumps(payload), timeout=60)
    duration = round(time.monotonic() - started, 3)
    state_path = Path(f"/tmp/agy_sage_{safe_id(conv_id)}.json")
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    log = audit.read_text() if audit.exists() else ""
    record = {"case": case, "conversation_id": conv_id, "executions": executions, "hook": hook, "audit": log, "state": state, "duration_seconds": duration, "parent_unchanged": before == hashlib.sha256(parent_db.read_bytes()).hexdigest(), "parser_sha256": hashlib.sha256((workspace / "csv_import.py").read_bytes()).hexdigest()}
    (workspace / "result.json").write_text(json.dumps(record, indent=2) + "\n")
    state_path.unlink(missing_ok=True)
    expected = "PASS" if case == "recovered_stop" else "FAIL"
    assert hook["returncode"] == 0, record
    assert re.search(r"Lite verifier finished in [^\n]+: " + expected + r"\s*$", log, re.MULTILINE), record
    assert not any(word in log.lower() for word in ("failing open", "cascade exhausted", "timed out", "exception")), record
    response = json.loads(hook["stdout"])
    if case == "failed_post":
        assert response["terminationBehavior"] == "force_continue" and response["injectSteps"][0]["userMessage"], record
    else:
        assert response["decision"] == ("stop" if expected == "PASS" else "continue"), record
    assert state.get("lite_status") == ("verified" if expected == "PASS" else "auto-continue (x1)"), record
    assert record["parent_unchanged"], record
    print(f"{case}: model={expected}, hook/state/parent isolation matched ({duration}s)", flush=True)
    return record


def main():
    """Run the native-model hook scenarios and preserve all evidence under tmp/."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repair-source", type=Path, help="Verify an actual worker repair using only the recovered Stop case")
    args = parser.parse_args()
    if args.repair_source:
        args.repair_source = args.repair_source.resolve()
        if not args.repair_source.is_file():
            parser.error("repair-source must be an existing Python parser")
    if os.environ.get("AGY_LITE_MOCK_VERDICT"):
        parser.error("Mock verdict must be unset")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source_home = Path(ensure_isolated_home())
    seed_env = dict(os.environ, HOME=str(source_home), AGY_STOP_AUDIT_ACTIVE="1")
    seed = run([
        shutil.which("agy") or str(Path.home() / ".local/bin/agy"),
        "-p", "Initialize an isolated verifier test session. Reply only READY and do not use tools.",
        "--model", LITE_MODEL_CANDIDATES[0], "--disable-slash-commands", "--output-format", "json",
    ], root, seed_env)
    (root / "seed.json").write_text(json.dumps(seed, indent=2) + "\n")
    assert seed["returncode"] == 0, "Seed CLI failed; inspect seed.json"
    seed_result = json.loads(seed["stdout"])
    assert seed_result["status"] == "SUCCESS", "Seed session unavailable"
    seed_id = str(uuid.UUID(seed_result["conversation_id"]))
    source_db = source_home / ".gemini/antigravity-cli/conversations" / f"{seed_id}.db"
    assert source_db.is_file(), "Seed conversation database does not exist"
    parent_home = root / "parent-home"
    try:
        parent_cli = prepare_home(source_home, parent_home)
        cases = ("recovered_stop",) if args.repair_source else ("failed_stop", "recovered_stop", "failed_post")
        records = [exercise(case, root, source_db, parent_cli, parent_home, args.repair_source) for case in cases]
    finally:
        clean_resume_history(seed_id)
        shutil.rmtree(parent_home, ignore_errors=True)
        shutil.rmtree(root / "child-home", ignore_errors=True)
    (root / "results.json").write_text(json.dumps(records, indent=2) + "\n")
    print(f"{len(records)}/{len(cases)} native hook cases passed. Evidence: {root / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
