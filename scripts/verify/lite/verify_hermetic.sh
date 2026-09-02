#!/bin/bash
# verify_hermetic.sh - Run Lite verification suite in isolated temporary sandbox
set -e
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SANDBOX="$(mktemp -d)"
mkdir -p "$SANDBOX/home" "$SANDBOX/tmp"
export HOME="$SANDBOX/home"
export TMPDIR="$SANDBOX/tmp"
export AGY_SAGE_LOG="$SANDBOX/tmp/sage_test.log"
export AGY_ADVISOR_LOG="$SANDBOX/tmp/stop_audit_test.log"
trap 'rm -rf "$SANDBOX"' EXIT
cd "$ROOT"
exec python3 -m unittest discover -s tests -p "test_lite_*.py" -p "test_stop_*.py" -p "test_knowledge_*.py" "$@"
