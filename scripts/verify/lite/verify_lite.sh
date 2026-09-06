#!/bin/bash
# verify_lite.sh - Shell runner for lite verification topic
set -e
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
exec python3 "$ROOT/scripts/verify/lite/main.py" "$@"
