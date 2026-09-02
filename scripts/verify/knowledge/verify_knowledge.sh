#!/bin/bash
# verify_knowledge.sh - Shell runner for knowledge base maintenance verification
set -e
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
exec python3 "$ROOT/scripts/verify/knowledge/verify_knowledge.py" "$@"
