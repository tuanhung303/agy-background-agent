#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SAGE_SRC="$REPO_DIR/hooks/session-sage.py"
TIMER_SRC="$REPO_DIR/hooks/command-timer.py"
STATUSLINE_SRC="$REPO_DIR/statusline/statusline.py"
PROMPT_SRC="$REPO_DIR/sage/sage_prompt.md"
ENFORCE_SRC="$REPO_DIR/hooks/sage-enforce.py"

MODE="symlink"
for arg in "$@"; do
  case "$arg" in
    --copy|--sync|-c)
      MODE="copy"
      ;;
    --symlink|-s)
      MODE="symlink"
      ;;
  esac
done

if [[ ! -f "$SAGE_SRC" ]]; then
  echo "Error: Hook file not found at $SAGE_SRC" >&2
  exit 1
fi

if [[ ! -f "$ENFORCE_SRC" ]]; then
  echo "Error: Hook file not found at $ENFORCE_SRC" >&2
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

if [[ -f "$PROMPT_SRC" ]]; then
  chmod +x "$PROMPT_SRC" 2>/dev/null || true
fi

chmod +x "$SAGE_SRC" "$ENFORCE_SRC" "$TIMER_SRC" "$STATUSLINE_SRC"
chmod +x "$SCRIPT_DIR/install.sh"
if [[ -f "$SCRIPT_DIR/sync.sh" ]]; then
  chmod +x "$SCRIPT_DIR/sync.sh"
fi

echo "Installing hooks, statusline, and prompt from $REPO_DIR (mode: $MODE)..."

mkdir -p "$HOME/.config/agy" "$HOME/.gemini/config/hooks"

install_file() {
  local src="$1"
  local dst="$2"
  local label="$3"
  rm -f "$dst"
  if [[ "$MODE" == "copy" ]]; then
    cp -p "$src" "$dst"
    if command -v xattr >/dev/null 2>&1; then
      xattr -c "$dst" 2>/dev/null || true
    fi
    echo "✓ Copied $label"
  else
    ln -sf "$src" "$dst"
    echo "✓ Symlinked $label"
  fi
}

# 1. session-sage (and legacy session-advisor / session-stop-audit compatibility links)
install_file "$SAGE_SRC" "$HOME/.config/agy/session-sage.py" "session-sage.py"
install_file "$SAGE_SRC" "$HOME/.gemini/config/hooks/session-sage.py" "session-sage.py (gemini hook)"
install_file "$SAGE_SRC" "$HOME/.config/agy/session-advisor.py" "session-advisor.py compatibility"
install_file "$SAGE_SRC" "$HOME/.gemini/config/hooks/session-advisor.py" "session-advisor.py compatibility (gemini hook)"
install_file "$SAGE_SRC" "$HOME/.config/agy/session-stop-audit.py" "session-stop-audit.py compatibility"
install_file "$SAGE_SRC" "$HOME/.gemini/config/hooks/session-stop-audit.py" "session-stop-audit.py compatibility (gemini hook)"

# 2. sage-enforce
install_file "$ENFORCE_SRC" "$HOME/.config/agy/sage-enforce.py" "sage-enforce.py"
install_file "$ENFORCE_SRC" "$HOME/.gemini/config/hooks/sage-enforce.py" "sage-enforce.py (gemini hook)"

# 3. sage prompt (and legacy advisor_prompt compatibility link)
if [[ -f "$PROMPT_SRC" ]]; then
  install_file "$PROMPT_SRC" "$HOME/.config/agy/sage_prompt.md" "sage_prompt.md"
  install_file "$PROMPT_SRC" "$HOME/.config/agy/advisor_prompt.md" "advisor_prompt.md compatibility"
fi

# 4. command-timer
install_file "$TIMER_SRC" "$HOME/.config/agy/command-timer.py" "command-timer.py"
install_file "$TIMER_SRC" "$HOME/.gemini/config/hooks/command-timer.py" "command-timer.py (gemini hook)"

# 5. statusline
install_file "$STATUSLINE_SRC" "$HOME/.config/agy/statusline.py" "statusline.py"

# 6. sage package copy when mode is copy
if [[ "$MODE" == "copy" ]]; then
  rm -rf "$HOME/.config/agy/sage"
  cp -Rp "$REPO_DIR/sage" "$HOME/.config/agy/sage"
  if command -v xattr >/dev/null 2>&1; then
    xattr -rc "$HOME/.config/agy/sage" 2>/dev/null || true
  fi
  echo "✓ Copied sage package to $HOME/.config/agy/sage"
fi

# 7. Configure ~/.gemini/config/hooks.json if needed
HOOKS_JSON="$HOME/.gemini/config/hooks.json"
/usr/bin/python3 - << PYEOF
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

# Ensure sage-enforce is registered
data["sage-enforce"] = {
    "PreToolUse": [{
        "matcher": "*",
        "hooks": [{
            "type": "command",
            "command": f"python3 {os.path.expanduser('~/.config/agy/sage-enforce.py')} pre_tool",
            "timeout": 5
        }]
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

echo "Verifying installation targets:"
ls -l "$HOME/.config/agy/session-sage.py" "$HOME/.gemini/config/hooks/session-sage.py" "$HOME/.config/agy/sage-enforce.py" "$HOME/.gemini/config/hooks/sage-enforce.py" "$HOME/.config/agy/session-advisor.py" "$HOME/.gemini/config/hooks/command-timer.py" "$HOME/.config/agy/statusline.py"
echo "Installation complete."
