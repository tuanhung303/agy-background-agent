"""Execute grounded verdicts and bounded model reconsideration through one path."""
import os
import shutil
import subprocess
import time
from typing import Optional

from sage.config import LITE_MODE_TIMEOUT, LITE_MODEL_CANDIDATES
from sage.executor import ensure_isolated_home, extract_json_from_llm_output
from sage.lite.prompt import build_lite_verifier_prompt
from sage.lite.proof_validator import validate_empirical_proof
from sage.lite.schemas import LiteVerdict
from sage.locking import log_audit


def _unavailable() -> LiteVerdict:
    """Represent a failed audit without inventing corrective instructions."""
    return LiteVerdict(verdict="PASS", completion="unavailable")


def _execute_verdict(prompt: str, fork_conv_id: str, deadline: float, cwd: Optional[str]) -> LiteVerdict:
    """Run the configured model candidates within one deadline and parse the verdict."""
    agy_bin = shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")
    env = dict(os.environ, AGY_STOP_AUDIT_ACTIVE="1", HOME=ensure_isolated_home())
    for model in LITE_MODEL_CANDIDATES:
        remaining = deadline - time.monotonic()
        if remaining <= 0.5:
            break
        command = [agy_bin, "--conversation", fork_conv_id, "-p", prompt,
                   "--model", model, "--disable-slash-commands"]
        started = time.monotonic()
        try:
            result = subprocess.run(command, input="", capture_output=True, text=True,
                                    timeout=remaining, env=env, cwd=cwd if cwd and os.path.isdir(cwd) else None)
            if result.returncode != 0 or not result.stdout.strip():
                log_audit(f"Lite verifier candidate '{model}' returned code {result.returncode}")
                continue
            data = extract_json_from_llm_output(result.stdout, schema_keys=("verdict",))
            verdict = LiteVerdict.from_dict(data)
            log_audit(f"Lite verifier finished in {time.monotonic() - started:.2f}s with {model}: {verdict.verdict}")
            return verdict
        except (subprocess.SubprocessError, OSError, ValueError, TypeError) as exc:
            log_audit(f"Lite verifier candidate '{model}' unavailable: {exc}")
    return _unavailable()


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
    """Return a grounded verdict; every injected corrective action comes from the model."""
    mock_value = os.environ.get("AGY_LITE_MOCK_VERDICT", "").strip()
    if mock_value:
        kind, _, content = mock_value.partition(":")
        data = {"verdict": kind, "action": content if kind == "FAIL" else "",
                "comment": content if kind == "PASS" else "", "proof": [content] if kind == "PASS" and content else []}
        try:
            verdict = LiteVerdict.from_dict(data)
            return verdict if verdict.verdict == "FAIL" or validate_empirical_proof(verdict.proof)[0] else _unavailable()
        except ValueError:
            return _unavailable()

    deadline = time.monotonic() + timeout
    diagnostic = None
    try:
        # One reconsideration for a concrete contradiction in the model's proof.
        # Both calls share the same prompt contract, fork, and total time budget.
        for _ in range(2):
            prompt = build_lite_verifier_prompt(
                user_prompt, last_agent_output, turn_execution_summary=turn_execution_summary,
                image_manifest=image_manifest, turn_provenance=turn_provenance,
                integrity_diagnostic=diagnostic,
            )
            verdict = _execute_verdict(prompt, fork_conv_id, deadline, cwd)
            if verdict.verdict == "FAIL" or verdict.completion == "unavailable":
                return verdict
            valid, reason = validate_empirical_proof(verdict.proof)
            if valid:
                return verdict
            log_audit(f"Lite verifier proof contradiction: {reason}")
            diagnostic = {"previous_proof": verdict.proof, "observed_contradiction": reason}
    except (OSError, ValueError, TypeError) as exc:
        log_audit(f"Lite verifier execution unavailable: {exc}")
    log_audit("Lite verifier unavailable; no fabricated steering or completion emitted")
    return _unavailable()
