#!/usr/bin/env python3
"""Topic: context - User context distillation and transcript compaction live verification."""
import os
import subprocess
import sys

root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
script_path = os.path.join(os.path.dirname(__file__), "verify_live.py")

res = subprocess.run([sys.executable, script_path], cwd=root)
sys.exit(res.returncode)
