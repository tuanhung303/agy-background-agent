#!/usr/bin/env bash
# deepswe_ab.sh — run one DeepSWE task with agy in a given sage arm, grade it.
#
# Usage: deepswe_ab.sh <task-id> <arm:off|on> <label>
# Requires: cloned upstream work at $WORK prepared by prepare_work.sh
set -uo pipefail

TASK="$1"; ARM="$2"; LABEL="$3"
MODEL_TIER="${4:-Gemini 3.7 Flash (High)}"
BENCH=/Users/__blitzzz/Documents/GitHub/deep-swe-bench/tasks/$TASK
BASE=/Users/__blitzzz/Documents/GitHub/deep-swe-bench/tasks/$TASK/tests/config.json
RUNROOT=/tmp/deepswe_harness/runs/${LABEL}
WORK="$RUNROOT/work"
EVID="$RUNROOT"
mkdir -p "$WORK"

COMMIT=$(python3 -c "import json;print(json.load(open('$BASE'))['base_commit'])")
REPO_URL=$(python3 -c "
import re
t=open('/Users/__blitzzz/Documents/GitHub/deep-swe-bench/tasks/$TASK/task.toml').read()
print(re.search(r'repository_url = \"([^\"]+)\"',t).group(1))")

echo "[ab] task=$TASK arm=$ARM label=$LABEL commit=$COMMIT"

git config --global --add safe.directory "$WORK" 2>/dev/null || true
if [ ! -d "$WORK/.git" ]; then
  git clone --no-checkout --filter=blob:none "$REPO_URL" "$WORK" 2>&1 | tail -1
  git -C "$WORK" checkout "$COMMIT" 2>&1 | tail -1
fi
git -C "$WORK" status --short >/dev/null 2>&1 || exit 9

# --- arm switch (hot unplug, no shared-state mutation) --------------------
SAGE_FLAG=""
[ "$ARM" = "off" ] && SAGE_FLAG="--sage-off"   # per-spawn env gate; other threads unaffected

# --- install deps (needed before agent runs so it doesn't burn turns) -----
cd "$WORK"
PNPM=$(command -v pnpm || echo "$HOME/.local/share/pnpm/pnpm")
if command -v pnpm >/dev/null 2>&1; then
  pnpm install --frozen-lockfile > "$EVID/install.log" 2>&1 || pnpm install >> "$EVID/install.log" 2>&1 &
else
  echo "pnpm missing" >&2
fi

# --- dispatch to agy (orca split pane) -----------------------------------
S=~/.agents/skills/orca-swarm/scripts/orca-agy.sh
BRIEF="$EVID/brief.md"
cat > "$BRIEF" <<BRIEFEOF
# DeepSWE Task: $TASK

Work dir: $WORK
Instruction: READ the file $BENCH/instruction.md and implement it FULLY inside the work dir.

## Verification hard requirements (read carefully)
- Self-written tests are necessary but NOT sufficient. Graders compile the SQL of every feature and compare it BYTE-EXACT against expected strings across multiple dialects (postgres, mysql, mssql, sqlite).
- After implementing, write throwaway checks that compile each new SQL construct with EACH dialect adapter and eyeball the exact output string (spacing, parentheses, quoting). A single stray or missing space in emitted SQL fails the task.
- Re-check clause keywords against SQL-standard canonical spelling used by this repo's existing compiler output (grep DefaultQueryCompiler for how similar clauses are spelled, e.g. 'over (...)', existing 'group by' emission) BEFORE finalizing.
- Do not declare done until pnpm build + test:node:build pass AND your byte-exact SQL spot-checks pass.

Finish with a final line '計画通り: done'.
BRIEFEOF
$S spawn --topic "dswe-$LABEL" --task-file "$BRIEF" --worktree "path:$WORK" --split $SAGE_FLAG \
  --model "$MODEL_TIER" 2>&1 | tail -2

START_EPOCH=$(date +%s)
echo "[ab] dispatched at $START_EPOCH; waiting for completion..."
$S watch --topic "dswe-$LABEL" --timeout-ms 10800000 --poll-secs 30 --stall-secs 600
WATCH=$?
END_EPOCH=$(date +%s)
echo "[ab] watch exit=$WATCH elapsed=$((END_EPOCH-START_EPOCH))s"

COLLECT=$($S collect --topic "dswe-$LABEL" 2>/dev/null | head -30)
TRANSCRIPT=$($S status "dswe-$LABEL" 2>/dev/null | grep TRANSCRIPT= | cut -d= -f2)

# metrics
TURNS=$(python3 - "$TRANSCRIPT" <<'PY'
import json,sys
try:
    steps=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
    print(sum(1 for s in steps if s.get("type")=="PLANNER_RESPONSE"))
except Exception as e:
    print("ERR",e)
PY
)
echo "turns=$TURNS" >> "$EVID/metrics.txt"
echo "wall_secs=$((END_EPOCH-START_EPOCH)) watch_exit=$WATCH" >> "$EVID/metrics.txt"

# workspace diff -> model.patch
cd "$WORK"
git add -A >/dev/null 2>&1
git diff --binary "$COMMIT" HEAD > /dev/null 2>&1
git diff --cached --binary > "$EVID/model.patch" 2>/dev/null
# uncommitted work too
git stash create >/dev/null 2>&1 || true
git write-tree >/dev/null 2>&1 || true
git diff --binary "$(git rev-list HEAD -1)" > "$EVID/model-uncommitted.patch" 2>/dev/null || true

$S close --topic "dswe-$LABEL" >/dev/null 2>&1 || true
echo "[ab] evidence in $EVID"
