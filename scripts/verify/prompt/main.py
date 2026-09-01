#!/usr/bin/env python3
"""Topic: prompt - Live empirical evaluation of prompt templates and context seals against real LLM inference."""
import os
import subprocess
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

for script_name in ("verify_live.py", "verify_adversarial.py"):
    script = os.path.join(os.path.dirname(__file__), script_name)
    res = subprocess.run([sys.executable, script], cwd=root)
    if res.returncode != 0:
        sys.exit(res.returncode)

sys.exit(0)
