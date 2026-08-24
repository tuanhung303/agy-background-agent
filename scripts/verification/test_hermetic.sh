#!/bin/bash
# test_hermetic.sh - run the suite inside a throwaway HOME/TMPDIR so real
# /tmp session files, live brain dirs, and cached model state can never
# leak in (root cause of the environment-sensitive flake class).
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SANDBOX="$(mktemp -d)"
mkdir -p "$SANDBOX/home" "$SANDBOX/tmp"
export HOME="$SANDBOX/home"
export TMPDIR="$SANDBOX/tmp"
export AGY_ADVISOR_LOG="$SANDBOX/tmp/stop_audit_test.log"
trap 'rm -rf "$SANDBOX"' EXIT
cd "$ROOT"
exec python3 -m unittest discover -s tests -p "test_*.py" "$@"
