#!/bin/bash
# verify_browser.sh - Shell runner for browser verification topic
set -e
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
exec python3 "$ROOT/scripts/verify/browser/main.py" "$@"
