#!/usr/bin/env python3
"""Prompt topic entrypoint: real model evaluation with strict expected verdicts."""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--native-hook", action="store_true")
    args, remaining = parser.parse_known_args()
    script = Path(__file__).with_name("verify_hook.py" if args.native_hook else "verify_live.py")
    result = subprocess.run([sys.executable, str(script), *remaining], cwd=ROOT)
    sys.exit(result.returncode)
