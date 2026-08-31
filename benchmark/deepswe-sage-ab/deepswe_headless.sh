#!/usr/bin/env bash
# deepswe_headless.sh — run one DeepSWE task with headless `agy -p`, in a given
# sage arm, then grade it.
#
# Why this exists: deepswe_ab.sh declares RUN_MODE=headless-agy-p but only ever
# implements the orca split-pane path, which cannot adopt an unregistered /tmp
# worktree (spawns land in the active pane's worktree; see RESULTS-R3.md note 3).
# Rounds r3..r8 were therefore run from an ad-hoc script under /tmp, which has
# since been wiped along with every evidence dir. This is that script, committed.
#
# Usage: deepswe_headless.sh <task-id> <arm:off|on> <label> [model-tier]
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASKS_ROOT="${DEEPSWE_TASKS:-/Users/__blitzzz/Documents/GitHub/deep-swe-bench/tasks}"

TASK="${1:?task-id required}"; ARM="${2:?arm off|on required}"; LABEL="${3:?label required}"
MODEL_TIER="${4:-Gemini 3.7 Flash (Medium)}"
PRINT_TIMEOUT="${PRINT_TIMEOUT:-90m}"

BENCH="$TASKS_ROOT/$TASK"
CFG="$BENCH/tests/config.json"
RUNROOT="/tmp/deepswe_harness/runs/${LABEL}"
WORK="$RUNROOT/work"
EVID="$RUNROOT"
BRAIN="$HOME/.gemini/antigravity-cli/brain"

[ -f "$CFG" ] || { echo "FATAL: task config missing at $CFG" >&2; exit 1; }
case "$ARM" in off|on) ;; *) echo "FATAL: arm must be off|on, got '$ARM'" >&2; exit 1 ;; esac

mkdir -p "$WORK"
COMMIT=$(python3 -c "import json;print(json.load(open('$CFG'))['base_commit'])")
REPO_URL=$(python3 -c "
import tomllib
t=tomllib.load(open('$BENCH/task.toml','rb'))
print(t.get('metadata',{}).get('repository_url') or t.get('task',{}).get('repository_url',''))")

echo "[hl] task=$TASK arm=$ARM label=$LABEL commit=${COMMIT:0:10} repo=$REPO_URL"

git config --global --add safe.directory "$WORK" 2>/dev/null || true
if [ ! -d "$WORK/.git" ]; then
  git clone "$REPO_URL" "$WORK" 2>&1 | tail -1
  git -C "$WORK" checkout "$COMMIT" 2>&1 | tail -1
fi
git -C "$WORK" rev-parse HEAD >/dev/null 2>&1 || { echo "FATAL: clone failed" >&2; exit 9; }

# --- HERMETIC BASE (do not remove) --------------------------------------
# 113 of 117 task instruction.md files end with "work on this in a new branch
# from main and commit everything when you are done". A plain clone leaves `main`
# at UPSTREAM head, so an agent that obeys that line literally lands dozens of
# upstream commits ahead of base_commit — observed 2026-08-31: HEAD at
# origin/main, 55 commits / 570 files past base. The held-out test.patch and the
# f2p/p2p node-ids in config.json were derived AT base_commit, so grading such a
# tree is meaningless, and whether it happens varies run to run — which silently
# injects variance into every A/B arm.
#
# Fix the environment, not the prompt: make `main` BE base_commit and delete the
# remote so upstream commits are unreachable. The brief stays byte-identical and
# obeying the instruction becomes a no-op.
git -C "$WORK" checkout -B main "$COMMIT" >/dev/null 2>&1
git -C "$WORK" remote remove origin >/dev/null 2>&1 || true
git -C "$WORK" for-each-ref --format='%(refname)' refs/remotes 2>/dev/null \
  | while read -r r; do git -C "$WORK" update-ref -d "$r" 2>/dev/null || true; done
for b in $(git -C "$WORK" for-each-ref --format='%(refname:short)' refs/heads 2>/dev/null); do
  [ "$b" = "main" ] || git -C "$WORK" branch -D "$b" >/dev/null 2>&1 || true
done
# Assert the base is now the tip of main and nothing upstream is reachable.
MAIN_SHA=$(git -C "$WORK" rev-parse main 2>/dev/null)
if [ "$MAIN_SHA" != "$COMMIT" ]; then
  echo "FATAL: main is $MAIN_SHA, expected base $COMMIT — hermetic base not established" >&2
  exit 10
fi
# Scope the check to refs an agent would actually check out: branches and
# remotes. Tags are deliberately left intact — upstream tags do point past base,
# but removing them can break version-derivation in build tooling, and "branch
# from main" never resolves to a tag. The drift assertion after the run is the
# backstop if an agent finds some other route forward.
BRANCH_REFS=$(git -C "$WORK" for-each-ref --format='%(refname)' refs/heads refs/remotes 2>/dev/null)
REACHABLE=$(git -C "$WORK" rev-list --count $BRANCH_REFS --not "$COMMIT" 2>/dev/null || echo 0)
echo "[hl] hermetic base: main=${COMMIT:0:10}, branch-refs-past-base=$REACHABLE (must be 0)"
if [ "${REACHABLE:-0}" != "0" ]; then
  echo "FATAL: branch/remote refs still reach past base:" >&2
  echo "$BRANCH_REFS" >&2
  exit 10
fi

# Deps installed BEFORE the agent runs so it does not burn turns on setup.
cd "$WORK"
if [ -f pnpm-lock.yaml ] && command -v pnpm >/dev/null 2>&1; then
  pnpm install > "$EVID/install.log" 2>&1
elif [ -f package-lock.json ] && command -v npm >/dev/null 2>&1; then
  npm install > "$EVID/install.log" 2>&1
elif [ -f yarn.lock ] && command -v yarn >/dev/null 2>&1; then
  yarn install > "$EVID/install.log" 2>&1
fi
echo "[hl] deps installed (rc in install.log)"

# Brief: byte-identical across arms. The ONLY difference between arms is the
# AGY_SAGE_DISABLED env gate below.
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

# Snapshot brain conversations so we can identify THIS run's transcript after.
BEFORE="$EVID/brain-before.txt"
ls -1 "$BRAIN" 2>/dev/null | sort > "$BEFORE" || : > "$BEFORE"

START=$(date +%s)
AGY_VERSION=$(agy --version 2>/dev/null || echo unknown)
SAGE_SHA=$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)
SAGE_BRANCH=$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)
SAGE_DIRTY=$(git -C "$SCRIPT_DIR" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
# Only sage/ and hooks/ dirt changes the code under test — the live hooks are
# symlinked at those paths. Dirty benchmark tooling is noise, not a confound.
SAGE_DIRTY_CORE=$(git -C "$SCRIPT_DIR" status --porcelain -- sage hooks 2>/dev/null | wc -l | tr -d ' ')

# NOTE: heredoc is deliberately UNQUOTED so the "$VAR" values below interpolate,
# which means the script's own positionals interpolate too — never write $1/$2
# here expecting sys.argv. Use sys.argv[1] explicitly for the output path.
python3 - "$EVID/provenance.json" <<PY
import json, sys
json.dump({
  "task": "$TASK", "arm": "$ARM", "label": "$LABEL",
  "mode": "headless-agy-p", "model": "$MODEL_TIER",
  "agy_version": "$AGY_VERSION", "print_timeout": "$PRINT_TIMEOUT",
  "sage_sha": "$SAGE_SHA", "sage_branch": "$SAGE_BRANCH",
  "sage_dirty_files": int("$SAGE_DIRTY" or 0),
  "sage_dirty_core": int("$SAGE_DIRTY_CORE" or 0),
  "base_commit": "$COMMIT",
}, open(sys.argv[1], "w"), indent=2)
PY
echo "[hl] sage=$SAGE_BRANCH@${SAGE_SHA:0:7} dirty=$SAGE_DIRTY core_dirty=$SAGE_DIRTY_CORE agy=$AGY_VERSION"
if [ "$SAGE_DIRTY_CORE" != "0" ]; then
  echo "[hl] WARN: sage/ or hooks/ is dirty — the live hooks read the working tree, so this run" >&2
  echo "[hl]       does NOT correspond to $SAGE_BRANCH@${SAGE_SHA:0:7}. Provenance is unpinnable." >&2
fi

# --- the run ------------------------------------------------------------
# Arm gate: AGY_SAGE_DISABLED=1 hot-unplugs the hooks for THIS process only.
set -x
if [ "$ARM" = "off" ]; then
  AGY_SAGE_DISABLED=1 agy -p "$(cat "$BRIEF")" \
    --model "$MODEL_TIER" --print-timeout "$PRINT_TIMEOUT" \
    --dangerously-skip-permissions > "$EVID/stdout.txt" 2> "$EVID/stderr.txt"
else
  agy -p "$(cat "$BRIEF")" \
    --model "$MODEL_TIER" --print-timeout "$PRINT_TIMEOUT" \
    --dangerously-skip-permissions > "$EVID/stdout.txt" 2> "$EVID/stderr.txt"
fi
AGY_RC=$?
set +x
END=$(date +%s)
echo "$AGY_RC" > "$EVID/exit.code"
echo "[hl] agy exit=$AGY_RC elapsed=$((END-START))s"

# --- identify this run's transcript -------------------------------------
ls -1 "$BRAIN" 2>/dev/null | sort > "$EVID/brain-after.txt" || : > "$EVID/brain-after.txt"
CONV=$(comm -13 "$BEFORE" "$EVID/brain-after.txt" | head -1)
TRANSCRIPT="$BRAIN/$CONV/.system_generated/logs/transcript.jsonl"
echo "[hl] conv=$CONV"

TURNS=$(python3 - "$TRANSCRIPT" <<'PY'
import json, sys
try:
    steps = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
    print(sum(1 for s in steps if s.get("type") == "PLANNER_RESPONSE"))
except Exception:
    print("NA")
PY
)

{
  echo "turns=$TURNS"
  echo "wall_secs=$((END-START))"
  echo "agy_rc=$AGY_RC"
  echo "conv=$CONV"
  echo "mode=headless-agy-p model=$MODEL_TIER agy=$AGY_VERSION"
  echo "sage_branch=$SAGE_BRANCH sage_sha=$SAGE_SHA sage_dirty=$SAGE_DIRTY"
} >> "$EVID/metrics.txt"
echo "[hl] turns=$TURNS wall=$((END-START))s"

# --- capture the agent's diff (committed OR not; the brief allows either) ---
cd "$WORK"
git add -A >/dev/null 2>&1
git diff --cached --binary "$COMMIT" > "$EVID/model.patch" 2>/dev/null || true
echo "[hl] model.patch $(wc -l < "$EVID/model.patch" 2>/dev/null || echo 0) lines"

# Drift assertion: the graded tree must still descend from base with no upstream
# commits pulled in. A run that drifted is NOT comparable — say so loudly and
# record it rather than emitting a reward that looks legitimate.
DRIFT_FILES=$(git diff --name-only "$COMMIT" 2>/dev/null | wc -l | tr -d ' ')
git merge-base --is-ancestor "$COMMIT" HEAD 2>/dev/null && BASE_OK=1 || BASE_OK=0
if [ "$BASE_OK" != "1" ] || [ "${DRIFT_FILES:-0}" -gt 200 ]; then
  echo "INVALID: base_ancestor=$BASE_OK files_vs_base=$DRIFT_FILES (>200 implies upstream drift)" \
    | tee "$EVID/INVALID.txt" >&2
  echo "[hl] refusing to grade a drifted tree — see INVALID.txt" >&2
  exit 11
fi
echo "[hl] drift check OK (files_vs_base=$DRIFT_FILES, base is ancestor)"

python3 "$SCRIPT_DIR/grade.py" "$WORK" "$TASK" "$EVID"
echo "[hl] done — evidence in $EVID"
