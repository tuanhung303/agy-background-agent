#!/usr/bin/env python3
"""Topic: lite - Lite Mode Stop Verifier unit tests and integration tests."""
import os
import subprocess
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

tests = [
    "tests/test_stop_verifier_cases.py",
    "tests/test_lite_mode.py",
    "tests/test_lite_integration.py",
    "tests/test_knowledge_maintenance.py",
]

res = subprocess.run([sys.executable, "-m", "unittest"] + tests, cwd=root)
sys.exit(res.returncode)
