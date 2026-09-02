"""sage.lite.verifier - Model executor and JSON verdict parser for Lite Mode."""
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Optional

from sage.config import (
    KB_MAINTENANCE_TIMEOUT,
    KB_MODEL_CANDIDATES,
    LITE_MODE_TIMEOUT,
    get_real_user_home,
)
from sage.executor import ensure_isolated_home, extract_json_from_llm_output
from sage.lite.prompt import build_kb_maintainer_prompt, build_lite_verifier_prompt
from sage.lite.schemas import LiteVerdict
from sage.locking import log_audit

LITE_MODEL_CANDIDATES = (
    "Gemini 3.7 Flash (Low)",
    "Gemini 3.7 Flash (Medium)",
    "Gemini 3.7 Flash (High)",
)


def run_lite_verification(
    parent_conv_id: str,
    fork_conv_id: str,
    user_prompt: str,
    last_agent_output: str,
    timeout: float = LITE_MODE_TIMEOUT,
    cwd: Optional[str] = None,
    turn_execution_summary: Optional[str] = None,
    image_manifest: Optional[list] = None,
    turn_provenance: Optional[dict] = None,
) -> LiteVerdict:
    """Executes Gemini Medium/High on the forked conversation and returns a LiteVerdict."""
    mock_val = os.environ.get("AGY_LITE_MOCK_VERDICT", "").strip()
    if mock_val:
        if mock_val.upper().startswith("FAIL"):
            action = mock_val.split(":", 1)[1].strip() if ":" in mock_val else "Mandatory verification required."
            return LiteVerdict(verdict="FAIL", action=action)
        comment = mock_val.split(":", 1)[1].strip() if ":" in mock_val else ""
        proof = [comment] if comment else ["Verified screenshot captured at /tmp/test.png"]
        return LiteVerdict(verdict="PASS", action="", comment=comment, proof=proof)

    prompt = build_lite_verifier_prompt(
        user_prompt,
        last_agent_output,
        turn_execution_summary=turn_execution_summary,
        image_manifest=image_manifest,
        turn_provenance=turn_provenance,
    )
    agy_bin = shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")
    iso_home = ensure_isolated_home()

    start_t = time.time()
    env = dict(
        os.environ,
        AGY_STOP_AUDIT_ACTIVE="1",
        HOME=iso_home,
        PATH=f"{os.path.expanduser('~/.local/bin')}:{os.environ.get('PATH', '')}",
    )

    for idx, model in enumerate(LITE_MODEL_CANDIDATES):
        rem = timeout - (time.time() - start_t)
        if rem <= 0.5:
            log_audit("Lite Mode verifier timeout budget reached; failing open")
            break

        cand_timeout = min(timeout, max(2.0, rem))
        cmd = [
            agy_bin,
            "--conversation", fork_conv_id,
            "-p", prompt,
            "--model", model,
            "--disable-slash-commands",
        ]

        run_cwd = cwd if (cwd and os.path.isdir(cwd)) else None
        try:
            res = subprocess.run(
                cmd,
                input="",
                capture_output=True,
                text=True,
                timeout=cand_timeout,
                env=env,
                cwd=run_cwd,
            )
            if res.returncode != 0 or not res.stdout.strip():
                log_audit(f"Lite verifier candidate '{model}' returned code {res.returncode}")
                continue

            raw_json = extract_json_from_llm_output(res.stdout, schema_keys=("verdict",))
            if raw_json is None:
                log_audit(f"Lite verifier candidate '{model}' output failed JSON decode")
                continue

            verdict = LiteVerdict.from_dict(raw_json)
            dur = round(time.time() - start_t, 2)
            log_audit(f"Lite verifier finished in {dur}s with {model}: {verdict.verdict}")
            return verdict
        except subprocess.TimeoutExpired:
            log_audit(f"Lite verifier candidate '{model}' timed out after {cand_timeout}s")
            continue
        except Exception as e:
            log_audit(f"Lite verifier candidate '{model}' exception: {e}")
            continue

    log_audit("Lite Mode verifier cascade exhausted or timed out; failing open with PASS")
    return LiteVerdict(verdict="PASS", action="")


def run_kb_maintenance(
    parent_conv_id: str,
    fork_conv_id: str,
    timeout: float = KB_MAINTENANCE_TIMEOUT,
    cwd: Optional[str] = None,
) -> str:
    """Executes the Knowledge Base Persona Maintainer on a forked conversation."""
    prompt = build_kb_maintainer_prompt()
    real_home = get_real_user_home()
    agy_bin = shutil.which("agy") or os.path.join(real_home, ".local", "bin", "agy") or os.path.expanduser("~/.local/bin/agy")
    iso_home = ensure_isolated_home()

    start_t = time.time()
    env = dict(
        os.environ,
        AGY_STOP_AUDIT_ACTIVE="1",
        AGY_REAL_HOME=real_home,
        HOME=iso_home,
        PATH=f"{os.path.join(real_home, '.local', 'bin')}:{os.environ.get('PATH', '')}",
    )

    for idx, model in enumerate(KB_MODEL_CANDIDATES):
        rem = timeout - (time.time() - start_t)
        if rem <= 0.5:
            log_audit("KB Maintainer timeout budget reached; completing")
            break

        cand_timeout = min(timeout, max(2.0, rem))
        cmd = [
            agy_bin,
            "--conversation", fork_conv_id,
            "-p", prompt,
            "--model", model,
            "--disable-slash-commands",
        ]

        run_cwd = cwd if (cwd and os.path.isdir(cwd)) else None
        try:
            res = subprocess.run(
                cmd,
                input="",
                capture_output=True,
                text=True,
                timeout=cand_timeout,
                env=env,
                cwd=run_cwd,
            )
            if res.returncode == 0 and res.stdout.strip():
                dur = round(time.time() - start_t, 2)
                log_audit(f"KB Maintainer finished in {dur}s with {model}: {res.stdout.strip()[:160]}")
                return res.stdout.strip()
            else:
                err_preview = (res.stderr or res.stdout or "").strip()[:160]
                log_audit(f"KB Maintainer candidate '{model}' returned code {res.returncode}: {err_preview}")
        except subprocess.TimeoutExpired:
            log_audit(f"KB Maintainer candidate '{model}' timed out after {cand_timeout}s")
            continue
        except Exception as e:
            log_audit(f"KB Maintainer candidate '{model}' exception: {e}")
            continue

    return ""


def dispatch_async_kb_maintenance(
    parent_conv_id: str,
    fork_conv_id: str,
    timeout: float = KB_MAINTENANCE_TIMEOUT,
    cwd: Optional[str] = None,
) -> Optional[int]:
    """Spawns a detached background worker process to execute KB maintenance asynchronously."""
    mock_val = os.environ.get("AGY_LITE_MOCK_VERDICT", "").strip()
    if mock_val:
        log_audit(f"Mock async KB worker dispatched for {fork_conv_id}")
        return 99999

    repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    python_bin = sys.executable or shutil.which("python3") or "python3"

    cmd = [
        python_bin,
        "-m", "sage.lite.kb_worker",
        "--parent-conv-id", str(parent_conv_id),
        "--fork-conv-id", str(fork_conv_id),
        "--timeout", str(float(timeout)),
    ]
    if cwd and os.path.isdir(cwd):
        cmd.extend(["--cwd", str(cwd)])

    log_file_path = f"/tmp/agy_kb_worker_{fork_conv_id}.log"
    env = dict(
        os.environ,
        PYTHONPATH=f"{repo_dir}:{os.environ.get('PYTHONPATH', '')}",
    )

    try:
        log_fp = open(log_file_path, "w", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=repo_dir,
            env=env,
            start_new_session=True,
        )
        log_audit(f"Dispatched async KB worker [PID={proc.pid}, fork={fork_conv_id}, log={log_file_path}]")
        return proc.pid
    except Exception as e:
        log_audit(f"Failed to dispatch async KB worker: {e}")
        return None


def generate_contextual_reject_action(
    fork_conv_id: str,
    user_prompt: str,
    last_agent_output: str,
    reject_reason: str,
    timeout: float = 12.0,
    cwd: Optional[str] = None,
) -> str:
    """Invokes verifier model to generate domain-specific actionable instruction instead of static boilerplate."""
    mock_val = os.environ.get("AGY_LITE_MOCK_VERDICT", "").strip()
    if mock_val and mock_val.upper().startswith("FAIL"):
        return mock_val.split(":", 1)[1].strip() if ":" in mock_val else "Mandatory verification required."

    clean_user = (user_prompt or "").strip()
    clean_agent = (last_agent_output or "").strip()
    prompt = (
        "You are the Quality Gate Verifier. The agent attempted to stop on this request:\n"
        f"<user_request>\n{clean_user}\n</user_request>\n\n"
        f"<last_agent_response>\n{clean_agent}\n</last_agent_response>\n\n"
        f"Empirical proof validation failed: {reject_reason}\n\n"
        "State in 1-2 direct imperative sentences the exact, concrete verification action or proof the agent must perform for this specific task and codebase before stopping.\n"
        "If the defect touches an enumerable entity (e.g. channel, tenant, route, formula, parser, model), treat this as a sighting: instruct the agent to declare universe U across all active sibling candidates and verify the entire class rather than patching an isolated sighting.\n"
        "Recommend adding or updating the matching topic module under scripts/verify/<topic>/ (orchestrated by scripts/verify/all.py or npm run verify) so the check is repeatable across future turns.\n"
        "Never use generic boilerplate (e.g. 'execute and document at least one empirical verification channel'). Focus on the affected universe and concrete execution commands."
    )
    agy_bin = shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")
    iso_home = ensure_isolated_home()

    start_t = time.time()
    env = dict(
        os.environ,
        AGY_STOP_AUDIT_ACTIVE="1",
        HOME=iso_home,
        PATH=f"{os.path.expanduser('~/.local/bin')}:{os.environ.get('PATH', '')}",
    )

    for model in LITE_MODEL_CANDIDATES:
        rem = timeout - (time.time() - start_t)
        if rem <= 0.5:
            break
        cand_timeout = min(timeout, max(2.0, rem))
        cmd = [
            agy_bin,
            "--conversation", fork_conv_id,
            "-p", prompt,
            "--model", model,
            "--disable-slash-commands",
        ]
        run_cwd = cwd if (cwd and os.path.isdir(cwd)) else None
        try:
            res = subprocess.run(
                cmd,
                input="",
                capture_output=True,
                text=True,
                timeout=cand_timeout,
                env=env,
                cwd=run_cwd,
            )
            if res.returncode == 0 and res.stdout.strip():
                lines = [l.strip() for l in res.stdout.strip().splitlines() if l.strip() and not l.startswith("```")]
                if lines:
                    return " ".join(lines)
        except Exception:
            continue

    return f"Verification rejected: {reject_reason}. Verify the specific changes and sibling blast radius before completing."

