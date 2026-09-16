#!/usr/bin/env bash
# sync.sh - Copy update hooks, statusline, and sage package to user configs
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/install.sh" --copy "$@"
