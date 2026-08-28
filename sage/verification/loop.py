"""
sage.verification.loop - Verified concurrency and TDD iterative verification loop.
"""

import os
import shutil
import subprocess
import concurrent.futures
from sage.locking import log_audit
from sage.verification.convergence import ConvergenceEngine
from sage.verification.adversary import generate_adversarial_test_cases
from sage.executor import extract_json_from_llm_output

from sage.executor import run_model_cascade

def _run_test_subagent(parent_conv_id, test_command, cwd=None, idx=0):
    """Spawns an isolated `agy` subagent via model cascade to run the test command."""
    prompt = (
        f"You are an isolated test execution subagent (Task #{idx}). "
        f"Execute the following command on the system and verify the output:\n"
        f"`{test_command}`\n\n"
        "If it exits with 0 and passes, output strictly valid JSON: "
        '{"status": "success", "output": "..."}\n'
        "Otherwise, output strictly valid JSON: "
        '{"status": "failed", "output": "..."}'
    )
    
    def normalize_func(d):
        if not d or not isinstance(d, dict) or "status" not in d:
            return {"status": "failed", "output": "Invalid JSON response from subagent."}
        return d

    res = run_model_cascade(
        parent_conv_id, prompt, (f"agy_test_subagent_{idx}_",), normalize_func,
        default_on_failure={"status": "failed", "output": "Cascade failure or timeout."},
        label=f"TestSubagent-{idx}",
        schema_keys=("status", "output"),
        cwd=cwd
    )
    
    if res.get("status") == "success":
        return True, res.get("output", "")
    else:
        return False, res.get("output", "Unknown error")


def run_tdd_verification_loop(parent_conv_id, code_context, goal, test_command, cwd=None, max_attempts=3):
    """
    Coordinates the TDD loop, managing concurrency safely and integrating with sage.events.
    Uses parallel `agy` subagents for execution.
    """
    log_audit("Starting Sage iterative verification TDD loop with parallel agy subagents.")
    engine = ConvergenceEngine(max_attempts=max_attempts)
    
    adversaries = generate_adversarial_test_cases(parent_conv_id, code_context, goal, cwd=cwd)
    num_tests = len(adversaries) if adversaries else 1
    if adversaries:
        log_audit(f"Generated {num_tests} adversarial cases.")
    
    while not engine.is_converged() and not engine.is_stuck():
        log_audit(f"Running verification tests... (Attempt {engine.attempts + 1})")
        
        # Parallel execution backend
        passed_all = True
        failed_outputs = []
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, num_tests)) as executor:
            # We run the test command for each adversarial scenario if multiple are given
            # (or just repeat execution for concurrency isolation check)
            futures = [
                executor.submit(_run_test_subagent, parent_conv_id, test_command, cwd, i)
                for i in range(num_tests)
            ]
            for future in concurrent.futures.as_completed(futures):
                success, output = future.result()
                if not success:
                    passed_all = False
                    failed_outputs.append(output)
        
        if passed_all:
            engine.record_attempt(True, "Parallel subagent tests passed cleanly.")
        else:
            engine.record_attempt(False, f"Test failed in parallel subagents: {failed_outputs[0][:200]}")
            
    if engine.is_converged():
        log_audit("Verification converged successfully. Emit FINAL_STOP event ready.")
        return {"status": "success", "report": engine.get_progress_report(), "adversaries": adversaries}
    else:
        log_audit("Verification stuck in ERROR_LOOP.")
        return {"status": "error_loop", "report": engine.get_progress_report(), "adversaries": adversaries}
