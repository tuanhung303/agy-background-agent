#!/bin/bash
# verify_memory.sh - Shell runner for Hermes memory verification topic
set -e
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
exec python3 "$ROOT/scripts/verify/memory/main.py" "$@"
