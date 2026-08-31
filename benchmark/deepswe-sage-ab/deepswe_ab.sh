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
RUN_NONCE=$(python3 -c "import uuid; print(uuid.uuid4().hex[:12])")
S=~/.agents/skills/orca-swarm/scripts/orca-agy.sh
BRIEF="$EVID/brief.md"
cat > "$BRIEF" <<BRIEFEOF
# DeepSWE Task: $TASK (Run nonce: $RUN_NONCE)

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
SPAWN_WT=$(echo "$SPAWN_OUT" | grep '^SPAWNED' | sed -E 's/.*worktree=([^ ]+).*/\1/')
if [ "$SPAWN_WT" != "$WORK" ]; then
  echo "[ab] FATAL: spawned in wrong worktree: $SPAWN_WT (expected $WORK)" >&2
  $S close --topic "dswe-$LABEL" >/dev/null 2>&1 || true
  exit 8
fi

START_EPOCH=$(date +%s)
AGY_VERSION=$(agy --version 2>/dev/null || echo unknown)
REPO_SHA=$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)

echo "[ab] dispatched at $START_EPOCH (nonce: $RUN_NONCE); waiting for completion..."
$S watch --topic "dswe-$LABEL" --timeout-ms 10800000 --poll-secs 30 --stall-secs 600
WATCH=$?
END_EPOCH=$(date +%s)
echo "[ab] watch exit=$WATCH elapsed=$((END_EPOCH-START_EPOCH))s"

COLLECT=$($S collect --topic "dswe-$LABEL" 2>/dev/null | head -30)

# Exact transcript matching by run nonce, work dir, and start time
ATTR_INFO=$(python3 - "$RUN_NONCE" "$WORK" "$START_EPOCH" <<'PY'
import json, os, sys, glob

nonce, work_dir, start_t = sys.argv[1], sys.argv[2], float(sys.argv[3])
brain_dir = os.path.expanduser("~/.gemini/antigravity-cli/brain")
matches = []

for t_path in glob.glob(os.path.join(brain_dir, "*", ".system_generated", "logs", "transcript.jsonl")):
    try:
        mtime = os.path.getmtime(t_path)
        if mtime < start_t - 60:
            continue
        with open(t_path, "r", encoding="utf-8", errors="ignore") as f:
            head = "".join(f.readline() for _ in range(50))
        if nonce in head and work_dir in head:
            cid = t_path.split(os.sep)[-4]
            matches.append({"conv_id": cid, "transcript": t_path})
    except Exception:
        pass

if len(matches) == 1:
    print(json.dumps({"status": "VALID", "conv_id": matches[0]["conv_id"], "transcript": matches[0]["transcript"]}))
elif len(matches) == 0:
    print(json.dumps({"status": "INVALID_NO_MATCH", "conv_id": "", "transcript": ""}))
else:
    print(json.dumps({"status": "INVALID_MULTIPLE_MATCHES", "conv_id": "", "transcript": ""}))
PY
)

ATTR_STATUS=$(python3 -c "import json; print(json.loads('''$ATTR_INFO''')['status'])")
CONV_ID=$(python3 -c "import json; print(json.loads('''$ATTR_INFO''')['conv_id'])")
TRANSCRIPT=$(python3 -c "import json; print(json.loads('''$ATTR_INFO''')['transcript'])")

echo "[ab] attribution status=$ATTR_STATUS conv_id=$CONV_ID"

# metrics
TURNS=0
if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
  TURNS=$(python3 - "$TRANSCRIPT" <<'PY'
import json,sys
try:
    steps=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
    print(sum(1 for s in steps if s.get("type")=="PLANNER_RESPONSE"))
except Exception as e:
    print(0)
PY
)
fi

# workspace diff -> model.patch
cd "$WORK"
git add -A >/dev/null 2>&1
git diff --binary "$COMMIT" HEAD > /dev/null 2>&1
git diff --cached --binary > "$EVID/model.patch" 2>/dev/null
git stash create >/dev/null 2>&1 || true
PRE_GRADE_TREE=$(git write-tree 2>/dev/null || echo "unknown")
git diff --binary "$(git rev-list HEAD -1)" > "$EVID/model-uncommitted.patch" 2>/dev/null || true

# Isolated disposable copy for grading (agent tree is never altered by held-out patch)
GRADE_WORK="$RUNROOT/grade_work"
rm -rf "$GRADE_WORK"
cp -a "$WORK" "$GRADE_WORK"

# Grade run in disposable copy
python3 "$SCRIPT_DIR/grade.py" "$GRADE_WORK" "$TASK" "$EVID"
GRADE_EXIT=$?
rm -rf "$GRADE_WORK"

# Verify agent work tree remained byte-identical after grading
POST_GRADE_TREE=$(git -C "$WORK" write-tree 2>/dev/null || echo "unknown")
if [ "$PRE_GRADE_TREE" != "$POST_GRADE_TREE" ]; then
  echo "[ab] FATAL: grading mutated agent workspace ($PRE_GRADE_TREE != $POST_GRADE_TREE)" >&2
  ATTR_STATUS="INVALID_WORKSPACE_MUTATION"
fi

# Manifest generation
DIRTY_CORE=false
git -C "$SCRIPT_DIR" diff --quiet HEAD || DIRTY_CORE=true
TASK_HASH=$(python3 -c "import hashlib,os; p='$BENCH/instruction.md'; print(hashlib.sha256(open(p,'rb').read()).hexdigest() if os.path.exists(p) else 'missing')")
TEST_PATCH_HASH=$(python3 -c "import hashlib,os; p='$BENCH/tests/test.patch'; print(hashlib.sha256(open(p,'rb').read()).hexdigest() if os.path.exists(p) else 'missing')")

python3 - "$CONV_ID" "$TRANSCRIPT" "$MODEL_TIER" "$COMMIT" "$REPO_SHA" "$DIRTY_CORE" "$TASK_HASH" "$TEST_PATCH_HASH" "$PRE_GRADE_TREE" "$WATCH" "$GRADE_EXIT" "$ATTR_STATUS" "$EVID/manifest.json" <<'PY'
import json, sys

manifest = {
    "conversation_id": sys.argv[1],
    "transcript_path": sys.argv[2],
    "model_tier": sys.argv[3],
    "base_commit": sys.argv[4],
    "sage_revision": sys.argv[5],
    "dirty_core": sys.argv[6] == "true",
    "task_hash": sys.argv[7],
    "test_patch_hash": sys.argv[8],
    "workspace_fingerprint": sys.argv[9],
    "exit_codes": {
        "watch": int(sys.argv[10]),
        "grade": int(sys.argv[11]),
    },
    "validity_status": sys.argv[12],
}
with open(sys.argv[13], "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
PY

$S close --topic "dswe-$LABEL" >/dev/null 2>&1 || true
echo "[ab] evidence and results in $EVID (manifest validity: $ATTR_STATUS)"
