#!/usr/bin/env python3
"""Topic: memory - Empirical verification of Hermes memory invariants, delimiters, character bounds, and prompt injection."""
import os
import subprocess
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
script = os.path.join(os.path.dirname(__file__), "verify_hermes_memory.py")

res = subprocess.run([sys.executable, script], cwd=root)
sys.exit(res.returncode)
