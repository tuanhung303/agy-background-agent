#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SAGE_SRC="$REPO_DIR/hooks/session-sage.py"
TIMER_SRC="$REPO_DIR/hooks/command-timer.py"
STATUSLINE_SRC="$REPO_DIR/statusline/statusline.py"
PROMPT_SRC="$REPO_DIR/sage/sage_prompt.md"

if [[ ! -f "$SAGE_SRC" ]]; then
  echo "Error: Hook file not found at $SAGE_SRC" >&2
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

if [[ ! -f "$PROMPT_SRC" ]]; then
  echo "Error: Sage prompt file not found at $PROMPT_SRC" >&2
  exit 1
fi

chmod +x "$SAGE_SRC" "$TIMER_SRC" "$STATUSLINE_SRC"
chmod +x "$SCRIPT_DIR/install.sh"

echo "Installing hooks, statusline, and prompt from $REPO_DIR..."

mkdir -p "$HOME/.config/agy" "$HOME/.gemini/config/hooks"

# 1. session-sage symlinks (and legacy session-advisor / session-stop-audit compatibility symlinks)
ln -sf "$SAGE_SRC" "$HOME/.config/agy/session-sage.py"
ln -sf "$SAGE_SRC" "$HOME/.gemini/config/hooks/session-sage.py"
ln -sf "$SAGE_SRC" "$HOME/.config/agy/session-advisor.py"
ln -sf "$SAGE_SRC" "$HOME/.gemini/config/hooks/session-advisor.py"
ln -sf "$SAGE_SRC" "$HOME/.config/agy/session-stop-audit.py"
ln -sf "$SAGE_SRC" "$HOME/.gemini/config/hooks/session-stop-audit.py"
echo "✓ Symlinked session-sage.py (with session-advisor.py and session-stop-audit.py compatibility links)"

# 2. sage prompt symlink (and legacy advisor_prompt compatibility link)
ln -sf "$PROMPT_SRC" "$HOME/.config/agy/sage_prompt.md"
ln -sf "$PROMPT_SRC" "$HOME/.config/agy/advisor_prompt.md"
echo "✓ Symlinked sage_prompt.md (with advisor_prompt.md compatibility link)"

# 3. command-timer symlinks
ln -sf "$TIMER_SRC" "$HOME/.config/agy/command-timer.py"
ln -sf "$TIMER_SRC" "$HOME/.gemini/config/hooks/command-timer.py"
echo "✓ Symlinked command-timer.py"

# 4. statusline symlink
ln -sf "$STATUSLINE_SRC" "$HOME/.config/agy/statusline.py"
echo "✓ Symlinked statusline.py"

# 5. Configure ~/.gemini/config/hooks.json if needed
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

# Ensure session-sage is registered
data["session-sage"] = {
    "PostInvocation": [{
        "type": "command",
        "command": f"python3 {os.path.expanduser('~/.config/agy/session-sage.py')} post_invocation",
        "timeout": 45
    }],
    "Stop": [{
        "type": "command",
        "command": f"python3 {os.path.expanduser('~/.config/agy/session-sage.py')}",
        "timeout": 45
    }]
}

# Remove redundant legacy session-advisor and session-stop-audit hook entries to prevent duplicate execution
data.pop("session-advisor", None)
data.pop("session-stop-audit", None)

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
ls -l "$HOME/.config/agy/session-sage.py" "$HOME/.gemini/config/hooks/session-sage.py" "$HOME/.config/agy/session-advisor.py" "$HOME/.gemini/config/hooks/command-timer.py" "$HOME/.config/agy/statusline.py" "$HOME/.config/agy/sage_prompt.md" "$HOME/.config/agy/advisor_prompt.md"
echo "Installation complete."
