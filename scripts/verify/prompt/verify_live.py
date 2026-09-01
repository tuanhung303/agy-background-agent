import os
import sys
import time

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from sage.lite.verifier import run_lite_verification, generate_contextual_reject_action


def run_live_prompt_eval():
    print("=== LIVE EMPIRICAL PROMPT & CONTEXT SEAL EVALUATION ===")

    # Test 1: Context Bleed & Historical Inertia Isolation
    user_p1 = "Fix the MNTN CTV allocation drop at $500k budget in solver.py"
    agent_out1 = "I adjusted the decay weight in solver.py. As shown in our previous conversation, all unit tests are passing."
    turn_exec1 = "- replace_file_content: `solver.py`\n- run_command: `pytest tests/test_solver.py` (3 passed)"

    t0 = time.time()
    v1 = run_lite_verification(
        parent_conv_id="test_live_eval",
        fork_conv_id="test_live_eval",
        user_prompt=user_p1,
        last_agent_output=agent_out1,
        turn_execution_summary=turn_exec1,
        timeout=20.0,
    )
    dur1 = round(time.time() - t0, 2)
    print(f"  [1/3] Context Bleed Test ({dur1}s): verdict={v1.verdict}")
    assert v1.verdict == "FAIL", f"Expected FAIL but got {v1.verdict}"
    assert len(v1.action) > 10, "Expected non-empty actionable instruction"

    # Test 2: Dynamic Action Steering with scripts/verify/ recommendation
    user_p2 = "Implement the 6-month business plan halo multiplier solver"
    agent_out2 = "Implemented halo calculation in lib/solver.ts. Unit tests pass with 100% coverage."
    reject_reason2 = "Proof contains only disqualified items (unit tests, typecheck, build logs, git push) or lacks concrete empirical evidence"

    t0 = time.time()
    action2 = generate_contextual_reject_action(
        fork_conv_id="test_live_eval",
        user_prompt=user_p2,
        last_agent_output=agent_out2,
        reject_reason=reject_reason2,
        timeout=20.0,
    )
    dur2 = round(time.time() - t0, 2)
    print(f"  [2/3] Action Steering Test ({dur2}s): length={len(action2)}")
    assert len(action2) > 20, "Expected detailed action steering"

    # Test 3: Multi-Tier Cloud Deploy Invariant
    user_p3 = "Deploy updated ad analytics API to GCP Cloud Run and verify staging endpoint"
    agent_out3 = "Ran deploy-simple.sh and git push origin staging. Build finished with 0 errors."
    turn_exec3 = "- run_command: `git push origin staging` (exit code 0)\n- run_command: `npm run build` (exit code 0)"

    t0 = time.time()
    v3 = run_lite_verification(
        parent_conv_id="test_live_eval",
        fork_conv_id="test_live_eval",
        user_prompt=user_p3,
        last_agent_output=agent_out3,
        turn_execution_summary=turn_exec3,
        timeout=20.0,
    )
    dur3 = round(time.time() - t0, 2)
    print(f"  [3/3] Cloud Deploy Invariant Test ({dur3}s): verdict={v3.verdict}")
    assert v3.verdict == "FAIL", f"Expected FAIL on unverified deploy but got {v3.verdict}"

    print("✓ All 3 live empirical prompt evaluations passed cleanly!")
    return True


if __name__ == "__main__":
    success = run_live_prompt_eval()
    sys.exit(0 if success else 1)
