#!/usr/bin/env bash
set -euo pipefail

cmd="${1:-}"

case "$cmd" in
  spawn)
    if [[ $# -lt 3 ]]; then
      echo "Usage: $0 spawn <session-id> <leg-id>" >&2
      exit 1
    fi
    session_id="$2"
    leg_id="$3"
    wt_dir=".worktrees/sage/${session_id}-${leg_id}"
    branch_name="sage/${session_id}/${leg_id}"

    mkdir -p ".worktrees/sage"
    git worktree add -b "${branch_name}" "${wt_dir}"

    now=$(date +%s)
    cat <<EOF > "${wt_dir}/.sage-ephemeral"
{"session": "${session_id}", "leg": "${leg_id}", "created_at": ${now}}
EOF
    ;;

  prune)
    dry_run=1
    shift || true
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --dry-run)
          dry_run=1
          shift
          ;;
        --force|-f|--apply|--execute|--no-dry-run)
          dry_run=0
          shift
          ;;
        *)
          shift
          ;;
      esac
    done

    current_wt=""
    current_branch=""

    while IFS= read -r line || [[ -n "$line" ]]; do
      if [[ "$line" =~ ^worktree[[:space:]]+(.*)$ ]]; then
        current_wt="${BASH_REMATCH[1]}"
        current_branch=""
      elif [[ "$line" =~ ^branch[[:space:]]+refs/heads/(.*)$ ]]; then
        current_branch="${BASH_REMATCH[1]}"
      elif [[ -z "$line" && -n "$current_wt" ]]; then
        marker="${current_wt}/.sage-ephemeral"
        if [[ -f "$marker" ]]; then
          if [[ $dry_run -eq 1 ]]; then
            echo "[dry-run] would remove ${current_wt}"
          else
            if [[ -z "$current_branch" ]]; then
              s_id=$(grep -o '"session": "[^"]*"' "$marker" | cut -d'"' -f4 || true)
              l_id=$(grep -o '"leg": "[^"]*"' "$marker" | cut -d'"' -f4 || true)
              if [[ -n "$s_id" && -n "$l_id" ]]; then
                current_branch="sage/${s_id}/${l_id}"
              fi
            fi
            git worktree remove --force "${current_wt}"
            if [[ -n "$current_branch" ]]; then
              git branch -D "${current_branch}" 2>/dev/null || true
            fi
          fi
        fi
        current_wt=""
        current_branch=""
      fi
    done < <(git worktree list --porcelain; echo "")
    ;;

  *)
    echo "Usage: $0 {spawn <session-id> <leg-id>|prune [--dry-run|--force]}" >&2
    exit 1
    ;;
esac
