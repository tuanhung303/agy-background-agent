#!/usr/bin/env python3
"""
tests.test_verifier_integration - Backward-compatible integration tests for legacy verifier shims.
"""

import unittest

from tests.test_advisor_integration import TestAdvisorIntegration


class TestVerifierIntegration(TestAdvisorIntegration):
    """Verifies that legacy verifier integration test suite continues to run cleanly."""
    pass


if __name__ == "__main__":
    unittest.main()
