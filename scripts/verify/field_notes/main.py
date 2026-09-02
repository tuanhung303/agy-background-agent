#!/usr/bin/env python3
"""Topic: field_notes - Field Notes Execution and Permissions Verification Suite."""
import os
import subprocess
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
test_file = os.path.join(os.path.dirname(__file__), "test_fetch.py")

res = subprocess.run([sys.executable, "-m", "unittest", test_file], cwd=root)
sys.exit(res.returncode)
