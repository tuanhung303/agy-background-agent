"""sage.lite.verifier - Model executor and JSON verdict parser for Lite Mode."""
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from sage.config import LITE_MODE_TIMEOUT, LITE_MODEL_CANDIDATES, get_real_user_home
from sage.executor import ensure_isolated_home, extract_json_from_llm_output
from sage.lite.prompt import build_lite_verifier_prompt
from sage.lite.schemas import LiteVerdict
from sage.locking import log_audit


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
    """Executes Gemini 3.8 Flash (Low) cascade on the forked conversation and returns a LiteVerdict."""
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


def generate_contextual_reject_action(
    fork_conv_id: str,
    user_prompt: str,
    last_agent_output: str,
    reject_reason: str,
    timeout: float = 12.0,
    cwd: Optional[str] = None,
    turn_execution_summary: Optional[str] = None,
    most_recent_terminal_cmd: Optional[Dict[str, Any]] = None,
) -> str:
    """Invokes verifier model to generate domain-specific actionable instruction instead of static boilerplate."""
    mock_val = os.environ.get("AGY_LITE_MOCK_VERDICT", "").strip()
    if mock_val and mock_val.upper().startswith("FAIL"):
        return mock_val.split(":", 1)[1].strip() if ":" in mock_val else "Mandatory verification required."

    from sage.lite.gating import is_slash_plan_intent
    if is_slash_plan_intent(user_prompt):
        return (
            "Run grill-me to verify the plan with the user: audit the implementation plan for blind spots, "
            "hidden assumptions, and design trade-offs, then use ask_question to interview the user and confirm "
            "critical decisions before proceeding."
        )

    clean_user = (user_prompt or "").strip()
    clean_agent = (last_agent_output or "").strip()

    exec_parts = []
    if most_recent_terminal_cmd and isinstance(most_recent_terminal_cmd, dict) and most_recent_terminal_cmd.get("command"):
        cmd_text = str(most_recent_terminal_cmd.get("command") or "").strip()
        cmd_out = str(most_recent_terminal_cmd.get("output") or "").strip()
        exec_parts.append(f"Most recent terminal command: `{cmd_text}`\nOutput:\n{cmd_out}")
    elif turn_execution_summary:
        exec_parts.append(f"Turn tool executions:\n{turn_execution_summary.strip()}")

    exec_block = "<recent_tool_executions>\n" + "\n\n".join(exec_parts) + "\n</recent_tool_executions>\n\n" if exec_parts else ""

    prompt = (
        "You are the Quality Gate Verifier. The agent attempted to stop on this request:\n"
        f"<user_request>\n{clean_user}\n</user_request>\n\n"
        f"<last_agent_response>\n{clean_agent}\n</last_agent_response>\n\n"
        + exec_block +
        f"Empirical proof validation failed: {reject_reason}\n\n"
        "State in 1-2 direct imperative sentences the exact, concrete verification action or proof the agent must perform for this specific task and codebase before stopping.\n"
        "For implementation tasks, bug fixes, or schema alterations touching an enumerable collection or sibling entity (e.g. data feeds, tenant configs, calculation formulas, API routes, parser schemas), instruct the agent to declare universe U from authoritative manifests or registries and verify the entire class under scripts/verify/<topic>/ (orchestrated by scripts/verify/all.py or npm run verify).\n"
        "Do NOT prescribe creating test scripts under scripts/verify/ if the user merely asked an informational, conversational, or data reconciliation question; instead instruct the agent to provide factual, field-level quantitative citations in the response.\n"
        "If <recent_tool_executions> shows the agent already executed a relevant query or command, do NOT prescribe re-running that exact check; instruct them on the specific unverified assertion or factual discrepancy.\n"
        "If the agent encountered an external blocker (MFA, corporate SSO/ADFS, in-use RDP lock), instruct the agent to satisfy the escalation contract with technical details and the exact user action needed rather than re-running blocked commands.\n"
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

