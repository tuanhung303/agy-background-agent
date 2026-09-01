#!/usr/bin/env python3
"""Topic: prompt - Live empirical evaluation of prompt templates and context seals against real LLM inference."""
import os
import subprocess
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
script = os.path.join(os.path.dirname(__file__), "verify_live.py")

res = subprocess.run([sys.executable, script], cwd=root)
sys.exit(res.returncode)
