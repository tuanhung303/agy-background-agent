#!/bin/bash
# verify_context.sh - Shell runner for context & compaction verification topic
set -e
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
exec python3 "$ROOT/scripts/verify/context/main.py" "$@"
