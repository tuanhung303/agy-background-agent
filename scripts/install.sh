#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ADVISOR_SRC="$REPO_DIR/hooks/session-advisor.py"
TIMER_SRC="$REPO_DIR/hooks/command-timer.py"
STATUSLINE_SRC="$REPO_DIR/statusline/statusline.py"

if [[ ! -f "$ADVISOR_SRC" ]]; then
  echo "Error: Hook file not found at $ADVISOR_SRC" >&2
  exit 1
fi

if [[ ! -f "$TIMER_SRC" ]]; then
  echo "Error: Hook file not found at $TIMER_SRC" >&2
  exit 1
fi

if [[ ! -f "$STATUSLINE_SRC" ]]; then
  echo "Error: Statusline file not found at $STATUSLINE_SRC" >&2
  exit 1
fi

chmod +x "$ADVISOR_SRC" "$TIMER_SRC" "$STATUSLINE_SRC"
chmod +x "$SCRIPT_DIR/install.sh"

echo "Installing hooks and statusline from $REPO_DIR..."

mkdir -p "$HOME/.config/agy" "$HOME/.gemini/config/hooks"

# 1. session-advisor symlinks (and legacy session-stop-audit compatibility symlinks)
ln -sf "$ADVISOR_SRC" "$HOME/.config/agy/session-advisor.py"
ln -sf "$ADVISOR_SRC" "$HOME/.gemini/config/hooks/session-advisor.py"
ln -sf "$ADVISOR_SRC" "$HOME/.config/agy/session-stop-audit.py"
ln -sf "$ADVISOR_SRC" "$HOME/.gemini/config/hooks/session-stop-audit.py"
echo "✓ Symlinked session-advisor.py (with session-stop-audit.py compatibility links)"

# 2. command-timer symlinks
ln -sf "$TIMER_SRC" "$HOME/.config/agy/command-timer.py"
ln -sf "$TIMER_SRC" "$HOME/.gemini/config/hooks/command-timer.py"
echo "✓ Symlinked command-timer.py"

# 3. statusline symlink
ln -sf "$STATUSLINE_SRC" "$HOME/.config/agy/statusline.py"
echo "✓ Symlinked statusline.py"

# 4. Configure ~/.gemini/config/hooks.json if needed
HOOKS_JSON="$HOME/.gemini/config/hooks.json"
python3 - << PYEOF
import json, os

hooks_path = os.path.expanduser("~/.gemini/config/hooks.json")
data = {}
if os.path.exists(hooks_path):
    try:
        with open(hooks_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}

# Ensure session-advisor is registered
data["session-advisor"] = {
    "PostInvocation": [{
        "type": "command",
        "command": f"python3 {os.path.expanduser('~/.config/agy/session-advisor.py')} post_invocation",
        "timeout": 45
    }],
    "Stop": [{
        "type": "command",
        "command": f"python3 {os.path.expanduser('~/.config/agy/session-advisor.py')}",
        "timeout": 45
    }]
}

# Retain legacy session-stop-audit pointing to session-advisor for backward compatibility
data.setdefault("session-stop-audit", data["session-advisor"])

# Ensure command-timer is registered
data.setdefault("command-timer", {
    "PreToolUse": [{
        "matcher": "run_command",
        "hooks": [{
            "type": "command",
            "command": f"python3 {os.path.expanduser('~/.gemini/config/hooks/command-timer.py')} pre_tool",
            "timeout": 5
        }]
    }],
    "PostToolUse": [{
        "matcher": "run_command",
        "hooks": [{
            "type": "command",
            "command": f"python3 {os.path.expanduser('~/.gemini/config/hooks/command-timer.py')} post_tool",
            "timeout": 5
        }]
    }],
    "PreInvocation": [{
        "type": "command",
        "command": f"python3 {os.path.expanduser('~/.gemini/config/hooks/command-timer.py')} pre_invocation",
        "timeout": 5
    }]
})

with open(hooks_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
PYEOF
echo "✓ Verified and updated hooks.json configuration"

echo "Verifying symlink destinations:"
ls -l "$HOME/.config/agy/session-advisor.py" "$HOME/.gemini/config/hooks/session-advisor.py" "$HOME/.gemini/config/hooks/command-timer.py" "$HOME/.config/agy/statusline.py"
echo "Installation complete."
