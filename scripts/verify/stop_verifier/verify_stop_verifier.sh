#!/bin/bash
# verify_stop_verifier.sh - Shell runner for stop verifier topic verification
set -e
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
exec python3 "$ROOT/scripts/verify/stop_verifier/verify_stop_verifier.py" "$@"
