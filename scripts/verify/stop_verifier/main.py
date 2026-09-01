#!/usr/bin/env python3
"""Topic: stop_verifier - 5-channel Stop Verifier generalization verification."""
import os
import subprocess
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
script = os.path.join(root, "scripts", "verify_stop_verifier.py")

res = subprocess.run([sys.executable, script], cwd=root)
sys.exit(res.returncode)
