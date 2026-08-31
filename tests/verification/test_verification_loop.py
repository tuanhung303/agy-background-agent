import pytest
from sage.verification.orchestrator import run_verification_loop

def test_verification_loop_simple_addition():
    source_code = '''
def add(a, b):
    return a + b
'''

    # Run the loop with just 1 iteration and 1 test to keep it fast for testing
    history = run_verification_loop("test_conv_id", source_code, max_iterations=1, max_tests_per_iter=1)

    assert len(history) > 0
    last_iter = history[-1]

    if "error" in last_iter:
        pytest.fail(f"Loop failed to generate tests: {last_iter['error']}")

    evaluation = last_iter["evaluation"]
    assert evaluation["total"] > 0
    # For a simple add function, it should hopefully pass the generated tests
    # But even if it fails due to some adversarial case, the loop structure should be intact
    assert "is_converged" in evaluation
