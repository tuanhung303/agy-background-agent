"""
sage.mcp_bridge_helpers - Helper routines for the Sage MCP verification bridge.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time


ALLOWED_GIT_CMDS = {"status", "diff", "log", "show"}


def get_inbox_dir():
    return os.environ.get("SAGE_INBOX_DIR") or os.path.expanduser("~/.gemini/antigravity-cli/sage_inbox")


def get_brain_dir():
    return os.environ.get("BRAIN_DIR") or os.path.expanduser("~/.gemini/antigravity-cli/brain")


def view_file(path, start=None, end=None):
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    if not p.exists():
        return f"Error: File not found: {path}"
    if p.is_dir():
        return f"Error: Path is a directory: {path}"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        s = max(1, int(start)) if start is not None else 1
        e = min(total, int(end)) if end is not None else total
        if s > total:
            return f"File has {total} lines; start={s} is past end of file."
        selected = lines[s - 1 : e]
        return "\n".join(f"{s + i}: {line}" for i, line in enumerate(selected))
    except Exception as exc:
        return f"Error reading {path}: {exc}"


def grep_search(pattern, path="."):
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"Error: Invalid regex '{pattern}': {exc}"
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    if not p.exists():
        return f"Error: Path not found: {path}"
    results = []
    ignored = {".git", ".pytest_cache", "__pycache__", ".venv", "node_modules"}
    if p.is_file():
        files = [p]
    else:
        files = []
        for root, dirs, filenames in os.walk(p):
            dirs[:] = [d for d in dirs if d not in ignored and not d.startswith(".")]
            for fn in filenames:
                if not fn.startswith("."):
                    files.append(Path(root) / fn)
    for f in sorted(files):
        try:
            for lineno, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
                if regex.search(line):
                    results.append(f"{f}:{lineno}: {line.strip()}")
                    if len(results) >= 100:
                        break
        except Exception:
            continue
        if len(results) >= 100:
            results.append("... [truncated at 100 matches]")
            break
    return "\n".join(results) if results else "No matches found."


def git_read(args):
    if isinstance(args, str):
        cmd_args = shlex.split(args)
    elif isinstance(args, (list, tuple)):
        cmd_args = [str(a) for a in args]
    else:
        return "Error: Invalid args for git_read."
    if not cmd_args:
        return "Error: No git subcommand specified."
    sub = cmd_args[0].lower()
    if sub not in ALLOWED_GIT_CMDS:
        return f"Error: Forbidden git subcommand: '{cmd_args[0]}'. Only status, diff, log, show allowed."
    try:
        res = subprocess.run(["git"] + cmd_args, capture_output=True, text=True, timeout=30)
        return res.stdout if res.returncode == 0 else f"Git error ({res.returncode}):\n{res.stderr}"
    except Exception as exc:
        return f"Git error: {exc}"


def run_command(cmd):
    if os.environ.get("SAGE_MCP_EXEC") != "1":
        return {"error": "run_command is disabled (SAGE_MCP_EXEC!=1)"}
    inbox_dir = get_inbox_dir()
    os.makedirs(inbox_dir, mode=0o700, exist_ok=True)
    exec_log = os.path.join(inbox_dir, "exec.log")
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with open(exec_log, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] START: {cmd}\n")
    except Exception:
        pass
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        try:
            with open(exec_log, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] END (code {res.returncode})\n")
        except Exception:
            pass
        return {"stdout": res.stdout, "stderr": res.stderr, "returncode": res.returncode}
    except Exception as exc:
        return {"error": str(exc), "returncode": -1}


def sage_send(conv_id, message):
    inbox_dir = get_inbox_dir()
    os.makedirs(inbox_dir, mode=0o700, exist_ok=True)
    inbox_file = os.path.join(inbox_dir, f"{conv_id}.jsonl")
    receipt_file = os.path.join(inbox_dir, f"{conv_id}.receipt")
    max_seq = 0
    if os.path.exists(receipt_file):
        try:
            with open(receipt_file, "r", encoding="utf-8") as rf:
                rec = json.load(rf)
                max_seq = max(max_seq, int(rec.get("seq", 0)))
        except Exception:
            pass
    if os.path.exists(inbox_file):
        try:
            with open(inbox_file, "r", encoding="utf-8") as jf:
                for line in jf:
                    if line.strip():
                        item = json.loads(line)
                        max_seq = max(max_seq, int(item.get("seq", 0)))
        except Exception:
            pass
    seq = max_seq + 1
    record = {"seq": seq, "ts": time.time(), "message": message}
    with open(inbox_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return {"ack": "queued", "seq": seq}


def drain_inbox(conv_id):
    inbox_dir = get_inbox_dir()
    inbox_file = os.path.join(inbox_dir, f"{conv_id}.jsonl")
    receipt_file = os.path.join(inbox_dir, f"{conv_id}.receipt")
    if not os.path.exists(inbox_file):
        return []
    try:
        with open(inbox_file, "r+", encoding="utf-8") as f:
            content = f.read()
            f.seek(0)
            f.truncate(0)
        messages = []
        for line in content.splitlines():
            if line.strip():
                try:
                    messages.append(json.loads(line))
                except Exception:
                    pass
        if messages:
            max_seq = max(m.get("seq", 0) for m in messages)
            receipt = {"seq": max_seq, "ts": time.time(), "count": len(messages)}
            tmp_receipt = f"{receipt_file}.tmp.{os.getpid()}"
            with open(tmp_receipt, "w", encoding="utf-8") as f:
                json.dump(receipt, f)
            os.replace(tmp_receipt, receipt_file)
        return messages
    except Exception:
        return []
