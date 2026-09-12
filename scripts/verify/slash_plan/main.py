#!/usr/bin/env python3
"""Compatibility topic for evaluating an explicitly required planning interview."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[3]
result = subprocess.run([sys.executable, "scripts/verify/prompt/main.py", "--case", "binding_plan_interview"], cwd=root)
sys.exit(result.returncode)
