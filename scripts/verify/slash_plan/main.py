#!/usr/bin/env python3
"""Topic: slash_plan - Verification of Slash Plan Grill-Me gating and steering."""
import os
import subprocess
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))

tests = [
    'tests/test_lite_mode.py',
]

res = subprocess.run([sys.executable, '-m', 'unittest'] + tests, cwd=root)
sys.exit(res.returncode)
