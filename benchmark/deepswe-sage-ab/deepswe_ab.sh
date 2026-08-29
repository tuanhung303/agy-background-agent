#!/usr/bin/env bash
# deepswe_ab.sh — run one DeepSWE task with agy in a given sage arm, grade it.
#
# Usage: deepswe_ab.sh <task-id> <arm:off|on> <label> [model]
# Example: deepswe_ab.sh koota-deferred-mutation-buffer off koota-arm1-off
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TASK="$1"; ARM="$2"; LABEL="$3"
MODEL_TIER="${4:-Gemini 3.7 Flash (High)}"
RUN_MODE="${RUN_MODE:-headless-agy-p}"
BENCH=/Users/__blitzzz/Documents/GitHub/deep-swe-bench/tasks/$TASK
BASE=/Users/__blitzzz/Documents/GitHub/deep-swe-bench/tasks/$TASK/tests/config.json
RUNROOT=/tmp/deepswe_harness/runs/${LABEL}
WORK="$RUNROOT/work"
EVID="$RUNROOT"
mkdir -p "$WORK"

if [ ! -f "$BASE" ]; then
  echo "Error: task config missing at $BASE" >&2
  exit 1
fi

COMMIT=$(python3 -c "import json;print(json.load(open('$BASE'))['base_commit'])")
REPO_URL=$(python3 -c "
import tomllib
t=tomllib.load(open('/Users/__blitzzz/Documents/GitHub/deep-swe-bench/tasks/$TASK/task.toml', 'rb'))
print(t.get('metadata', {}).get('repository_url') or t.get('task', {}).get('repository_url', ''))")

echo "[ab] task=$TASK arm=$ARM label=$LABEL commit=$COMMIT repo=$REPO_URL"

git config --global --add safe.directory "$WORK" 2>/dev/null || true
if [ ! -d "$WORK/.git" ]; then
  git clone "$REPO_URL" "$WORK" 2>&1 | tail -1
  git -C "$WORK" checkout "$COMMIT" 2>&1 | tail -1
fi
git -C "$WORK" status --short >/dev/null 2>&1 || exit 9

# --- arm switch (hot unplug, no shared-state mutation) --------------------
SAGE_FLAG=""
[ "$ARM" = "off" ] && SAGE_FLAG="--sage-off"   # per-spawn env gate; other threads unaffected

# --- install deps (needed before agent runs so it doesn't burn turns) -----
cd "$WORK"
if [ -f "pnpm-lock.yaml" ] && command -v pnpm >/dev/null 2>&1; then
  pnpm install > "$EVID/install.log" 2>&1
elif [ -f "package-lock.json" ] && command -v npm >/dev/null 2>&1; then
  npm install > "$EVID/install.log" 2>&1
elif [ -f "yarn.lock" ] && command -v yarn >/dev/null 2>&1; then
  yarn install > "$EVID/install.log" 2>&1
fi

# --- dispatch to agy (orca split pane) -----------------------------------
S=~/.agents/skills/orca-swarm/scripts/orca-agy.sh
BRIEF="$EVID/brief.md"
cat > "$BRIEF" <<BRIEFEOF
# DeepSWE Task: $TASK

Work dir: $WORK
Instruction: Read the task specification in $BENCH/instruction.md and implement the complete feature in the work dir.

## Verification requirements
- Run and verify all existing and new tests in the repo before concluding.
- Self-written tests are necessary but verify against the actual project test runner and compiler (e.g. vitest, jest, tsc).
- Ensure no regressions across unchanged functionality and ensure type-check passes.

Finish with a final line '計画通り: done'.
BRIEFEOF

SPAWN_OUT=$($S spawn --topic "dswe-$LABEL" --task-file "$BRIEF" --worktree "path:$WORK" $SAGE_FLAG \
  --model "$MODEL_TIER" 2>&1)
echo "$SPAWN_OUT" | tail -2
# Fail fast if the pane landed in the wrong worktree (--split splits the ACTIVE
# pane and ignores --worktree; observed 2026-08-29 spawning in seeda).
SPAWN_WT=$(echo "$SPAWN_OUT" | grep '^SPAWNED' | sed -E 's/.*worktree=([^ ]+).*/\1/')
if [ "$SPAWN_WT" != "$WORK" ]; then
  echo "[ab] FATAL: spawned in wrong worktree: $SPAWN_WT (expected $WORK)" >&2
  $S close --topic "dswe-$LABEL" >/dev/null 2>&1 || true
  exit 8
fi

START_EPOCH=$(date +%s)
AGY_VERSION=$(agy --version 2>/dev/null || echo unknown)
REPO_SHA=$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)
python3 - "$AGY_VERSION" "$RUN_MODE" "$MODEL_TIER" "$REPO_SHA" "$TASK" "$EVID/provenance.json" <<'PY'
import json, sys
data = {
    "agy_version": sys.argv[1],
    "mode": sys.argv[2],
    "model": sys.argv[3],
    "repo_sha": sys.argv[4],
    "task": sys.argv[5],
}
with open(sys.argv[6], "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
PY
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
echo "agy_version=$AGY_VERSION mode=$RUN_MODE model=$MODEL_TIER repo_sha=$REPO_SHA task=$TASK" >> "$EVID/metrics.txt"

# workspace diff -> model.patch
cd "$WORK"
git add -A >/dev/null 2>&1
git diff --binary "$COMMIT" HEAD > /dev/null 2>&1
git diff --cached --binary > "$EVID/model.patch" 2>/dev/null
git stash create >/dev/null 2>&1 || true
git write-tree >/dev/null 2>&1 || true
git diff --binary "$(git rev-list HEAD -1)" > "$EVID/model-uncommitted.patch" 2>/dev/null || true

# Grade run with grade.py
python3 "$SCRIPT_DIR/grade.py" "$WORK" "$TASK" "$EVID"

$S close --topic "dswe-$LABEL" >/dev/null 2>&1 || true
echo "[ab] evidence and results in $EVID (agy=$AGY_VERSION mode=$RUN_MODE model=$MODEL_TIER repo_sha=$REPO_SHA)"
