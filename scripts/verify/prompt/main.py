#!/usr/bin/env python3
"""Prompt topic entrypoint: real model evaluation with strict expected verdicts."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

if __name__ == "__main__":
    script = Path(__file__).with_name("verify_live.py")
    result = subprocess.run([sys.executable, str(script), *sys.argv[1:]], cwd=ROOT)
    sys.exit(result.returncode)
