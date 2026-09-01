"""
sage.verification.orchestrator - Coordinates the TDD loop lifecycle.
"""

from sage.verification.adversary import generate_adversarial_tests
from sage.verification.concurrency import run_tests_in_parallel
from sage.verification.convergence import evaluate_outcomes

def run_verification_loop(parent_conv_id, source_code, max_iterations=3, max_tests_per_iter=3):
    """
    Runs the iterative verification TDD loop.
    1. Generates adversarial tests.
    2. Runs them in parallel using isolated subagents.
    3. Evaluates convergence.
    4. Repeats if not converged (up to max_iterations).
    """
    iteration = 1
    history = []

    while iteration <= max_iterations:
        tests = generate_adversarial_tests(parent_conv_id, source_code, max_tests=max_tests_per_iter)
        if not tests:
            history.append({"iteration": iteration, "error": "No tests generated"})
            break

        results = run_tests_in_parallel(parent_conv_id, source_code, tests)
        evaluation = evaluate_outcomes(results)

        history.append({
            "iteration": iteration,
            "evaluation": evaluation
        })

        if evaluation["is_converged"]:
            break

        iteration += 1

    return history
