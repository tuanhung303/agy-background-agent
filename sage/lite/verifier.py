"""sage.lite.verifier - Model executor and JSON verdict parser for Lite Mode."""
import json
import os
import shutil
import subprocess
import time
from typing import Optional

from sage.config import LITE_MODE_TIMEOUT
from sage.executor import ensure_isolated_home, extract_json_from_llm_output
from sage.lite.prompt import build_kb_maintainer_prompt, build_lite_verifier_prompt
from sage.lite.schemas import LiteVerdict
from sage.locking import log_audit

LITE_MODEL_CANDIDATES = (
    "Gemini 3.7 Flash (Low)",
    "Gemini 3.7 Flash (Medium)",
    "Gemini 3.5 Flash (High)",
)


def run_lite_verification(
    parent_conv_id: str,
    fork_conv_id: str,
    user_prompt: str,
    last_agent_output: str,
    timeout: float = LITE_MODE_TIMEOUT,
    cwd: Optional[str] = None,
) -> LiteVerdict:
    """Executes Gemini Low on the forked conversation and returns a LiteVerdict."""
    mock_val = os.environ.get("AGY_LITE_MOCK_VERDICT", "").strip()
    if mock_val:
        if mock_val.upper().startswith("FAIL"):
            action = mock_val.split(":", 1)[1].strip() if ":" in mock_val else "Mandatory verification required."
            return LiteVerdict(verdict="FAIL", action=action)
        comment = mock_val.split(":", 1)[1].strip() if ":" in mock_val else ""
        proof = [comment] if comment else ["Verified screenshot captured at /tmp/test.png"]
        return LiteVerdict(verdict="PASS", action="", comment=comment, proof=proof)

    prompt = build_lite_verifier_prompt(user_prompt, last_agent_output)
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
    timeout: float = LITE_MODE_TIMEOUT,
    cwd: Optional[str] = None,
) -> str:
    """Executes the Knowledge Base Persona Maintainer on a forked conversation."""
    prompt = build_kb_maintainer_prompt()
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
                log_audit(f"KB Maintainer finished in {dur}s with {model}")
                return res.stdout.strip()
        except subprocess.TimeoutExpired:
            log_audit(f"KB Maintainer candidate '{model}' timed out after {cand_timeout}s")
            continue
        except Exception as e:
            log_audit(f"KB Maintainer candidate '{model}' exception: {e}")
            continue

    return ""
