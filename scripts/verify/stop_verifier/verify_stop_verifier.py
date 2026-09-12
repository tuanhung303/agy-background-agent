#!/usr/bin/env python3
"""Run structural hook regressions; model semantics are evaluated by the prompt topic."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    """Execute actual schema, provenance, artifact, and lifecycle checks in a subprocess."""
    return subprocess.run([
        sys.executable, "-m", "pytest", "-q",
        "tests/test_stop_runtime.py", "tests/test_semantic_runtime.py", "tests/test_proof_integrity.py",
        "tests/test_evidence_context.py", "tests/test_lite_integration.py",
        "tests/test_stop_verifier_cases.py",
    ], cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
