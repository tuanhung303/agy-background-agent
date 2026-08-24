#!/usr/bin/env python3
"""
tests.test_mid_verifier - Backward-compatible unit tests for mid_verifier shims.
"""

import unittest

from tests.test_advisor import TestAdvisor


class TestMidVerifier(TestAdvisor):
    """Verifies that legacy mid_verifier unit test suite continues to run cleanly."""
    pass


if __name__ == "__main__":
    unittest.main()
