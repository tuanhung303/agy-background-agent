#!/bin/bash
# verify_prompt.sh - Shell runner for prompt & seal verification topic
set -e
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
exec python3 "$ROOT/scripts/verify/prompt/main.py" "$@"
