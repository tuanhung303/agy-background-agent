"""
sage.verification.convergence - Evaluates outcomes of the verification loop.
"""

def evaluate_outcomes(results):
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    failed = total - passed

    is_converged = (total > 0 and failed == 0)

    summary = {
        "total": total,
        "passed": passed,
        "failed": failed,
        "is_converged": is_converged,
        "details": results
    }
    return summary
