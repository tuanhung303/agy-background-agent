#!/usr/bin/env python3
"""Topic: knowledge - Knowledge Base Maintenance 5-Stage Verification Suite."""
import os
import subprocess
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
script = os.path.join(os.path.dirname(__file__), "verify_knowledge.py")

res = subprocess.run([sys.executable, script], cwd=root)
sys.exit(res.returncode)
