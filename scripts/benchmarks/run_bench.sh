#!/bin/bash
# run_bench.sh - one-command Orca benchmark wave for the stop-audit hook.
#
# Usage:
#   scripts/run_bench.sh <scenario>            # scenario = scripts/bench_scenarios/<name>.md
#   SCENARIO=parallelize_serial TIMEOUT=900 scripts/run_bench.sh
#
# Does: overlay backup/write (bench thresholds) -> Orca tab spawn (cd into WORKDIR)
# -> tracked dispatch + manual send -> poll for recap/idle -> evidence collection
# (audit-log slice, state v2 fields, independent test rerun) -> overlay restore,
# tab close, task completion.
set -uo pipefail

SCENARIO="${1:-${SCENARIO:-parallelize_serial}}"
WORKDIR="${WORKDIR:-/Users/__blitzzz/Documents/GitHub/blitzzz-hermes/tmp}"
MODEL="${MODEL:-Gemini 3.7 Flash (Medium)}"
TIMEOUT="${TIMEOUT:-900}"
INTERVAL="${INTERVAL:-6}"
MIN_TOOLS="${MIN_TOOLS:-8}"
WT_ID="${WT_ID:-66f17015-a501-4b67-8e50-9dc38bb4ebf4::/Users/__blitzzz/Documents/GitHub/agy-background-agent}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCEN_FILE="$(cd "$(dirname "$0")" && pwd)/bench_scenarios/${SCENARIO}.md"
OVERLAY="$HOME/.config/agy/sage.env"
AUDIT_LOG="/tmp/agy_sage.log"
[ -f "$AUDIT_LOG" ] || AUDIT_LOG="/tmp/agy_stop_audit.log"
STAMP="$(date +%Y%m%d_%H%M%S)"
EVID="/tmp/bench_${SCENARIO}_${STAMP}"
mkdir -p "$EVID"

[ -f "$SCEN_FILE" ] || { echo "scenario not found: $SCEN_FILE"; exit 1; }
PROMPT="$(awk '/^```$/{f=!f; next} f' "$SCEN_FILE" | head -40)"
[ -n "$PROMPT" ] || { echo "no fenced prompt block in $SCEN_FILE"; exit 1; }

OVERLAY_BAK=""
if [ -f "$OVERLAY" ]; then OVERLAY_BAK="$OVERLAY.bak.$STAMP"; cp "$OVERLAY" "$OVERLAY_BAK"; fi
cat > "$OVERLAY" <<EOF
AGY_SAGE_TOOL_INTERVAL=$INTERVAL
AGY_ADVISOR_TOOL_INTERVAL=$INTERVAL
AGY_STOP_AUDIT_MIN_TOOLS=$MIN_TOOLS
AGY_STOP_AUDIT_MIN_DURATION=120
EOF
restore_overlay() {
  if [ -n "$OVERLAY_BAK" ]; then mv "$OVERLAY_BAK" "$OVERLAY"; else rm -f "$OVERLAY"; fi
}
trap restore_overlay EXIT

echo "[bench] scenario=$SCENARIO workdir=$WORKDIR interval=$INTERVAL min_tools=$MIN_TOOLS"
HANDLE=$(orca terminal create --worktree "$WT_ID" --title "swarm:bench-${SCENARIO}:agy" \
  --command "cd '$WORKDIR' && agy --model '$MODEL'" --json 2>/dev/null | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['result']['terminal']['handle'])")
[ -n "$HANDLE" ] || { echo "spawn failed"; exit 1; }
orca terminal wait --terminal "$HANDLE" --for tui-idle --timeout-ms 60000 --json >/dev/null 2>&1

RUN_ID=$(orca orchestration run-create --objective "bench $SCENARIO $STAMP" --json 2>/dev/null | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['result']['run']['id'])")
TASK_ID=$(orca orchestration task-create --spec "$PROMPT" --task-title "bench $SCENARIO" \
  --display-name "bench-$SCENARIO" --run "$RUN_ID" --json 2>/dev/null | \
  python3 -c "import json,sys; r=json.load(sys.stdin)['result']; print(r.get('taskId') or r.get('task',{}).get('id'))")
orca orchestration dispatch --task "$TASK_ID" --to "$HANDLE" --run "$RUN_ID" --json >/dev/null 2>&1 || true
MARKER="$(printf '%s' "$PROMPT" | tr '\n' ' ' | awk '{print $1, $2, $3}')"
LANDED=0
for ATTEMPT in 1 2 3; do
  sleep $((ATTEMPT * 4))
  orca terminal send --terminal "$HANDLE" --text "$PROMPT" --enter --json >/dev/null 2>&1
  for _ in 1 2 3 4 5; do
    sleep 4
    if orca terminal read --terminal "$HANDLE" --json 2>/dev/null | grep -qF "$MARKER"; then
      LANDED=1; break 2
    fi
  done
  echo "[bench] prompt not visible after attempt $ATTEMPT; resending..."
done
if [ "$LANDED" = "1" ]; then
  echo "[bench] dispatched task=$TASK_ID handle=$HANDLE (prompt confirmed on attempt ${ATTEMPT}); polling (timeout ${TIMEOUT}s)..."
else
  echo "[bench] WARN: prompt never appeared in tab after 3 attempts; continuing to poll (evidence will show the dead tab)"
fi

ELAPSED=0
DONE=0
STABLE=0
QUIESCENT=0
PREV_ART=""
PREV_TAIL=""
art_sig() { find "$WORKDIR" -type f -exec stat -f "%m %N" {} + 2>/dev/null | sort | md5 -q; }
ART0="$(art_sig)"
while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
  sleep 30; ELAPSED=$((ELAPSED + 30))
  TAIL_TXT=$(orca terminal read --terminal "$HANDLE" --json 2>/dev/null | \
    python3 -c "import json,sys; print('\n'.join(json.load(sys.stdin).get('result',{}).get('terminal',{}).get('tail') or []))" 2>/dev/null || echo "")
  case "$TAIL_TXT" in
    *"※ recap:"*) DONE=1; echo "[bench] recap observed at ${ELAPSED}s"; break;;
  esac
  ART="$(art_sig)"; TAIL_SIG="$(printf '%s' "$TAIL_TXT" | md5 -q)"
  if [ "$ART" = "$PREV_ART" ] && [ "$TAIL_SIG" = "$PREV_TAIL" ]; then
    STABLE=$((STABLE + 1))
  else
    STABLE=0
  fi
  PREV_ART="$ART"; PREV_TAIL="$TAIL_SIG"
  if [ "$STABLE" -ge 4 ]; then
    DONE=1; QUIESCENT=1; echo "[bench] quiescent (fs+tail stable x4) at ${ELAPSED}s"; break
  fi
  echo "[bench] ${ELAPSED}s working... (stable=${STABLE})"
done
[ "$DONE" = "1" ] || echo "[bench] timeout after ${TIMEOUT}s; collecting whatever exists"
if [ "$QUIESCENT" = "1" ] && [ "$(art_sig)" = "$ART0" ]; then
  echo "[bench] WARN: quiesced with ZERO workdir artifacts — likely permission wedge or planning-only loop"
fi
printf '%s\n' "$TAIL_TXT" > "$EVID/final_tail.txt"

sleep 3
grep -E "$(date +%Y-%m-%d)" "$AUDIT_LOG" 2>/dev/null | tail -400 > "$EVID/audit_log_tail.txt"
echo "--- key audit events ---"
grep -E "prompt mode|triggered steer|watchout emitted|deduplicated|passed \(healthy\)|circuit breaker|Recap recorded|New turn detected|Exit .*(below interval|healthy)" "$EVID/audit_log_tail.txt" | tail -20
NEWEST_STATE=$(ls -t /tmp/agy_sage_*.json /tmp/agy_advisor_*.json 2>/dev/null | head -1)
[ -n "$NEWEST_STATE" ] && python3 -c "
import json
d = json.load(open('$NEWEST_STATE'))
print('--- state ($NEWEST_STATE) ---')
print({k: d.get(k) for k in ('sage_status','sage_holds','sage_advice_counts','sage_error_streak','advisor_status','advisor_holds','advisor_advice_counts','advisor_error_streak','recap_emitted','last_verified_tools')})
" | tee "$EVID/state.json.txt"
python3 -c "
import os, subprocess, sys
try:
    cmd = [sys.executable, '-m', 'pytest', '-q', '$ROOT/tests', '--timeout=80', '-x', '--no-header', '-p', 'no:cacheprovider']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, cwd='$ROOT')
    print(f'Test returncode: {r.returncode}')
    print((r.stdout or '')[-300:]); print((r.stderr or '')[-150:])
except subprocess.TimeoutExpired:
    print('test rerun capped at 90s (probe-heavy suites); per-leg PASS status lives in SUMMARY.md')
" | tee "$EVID/tests.txt"

orca orchestration task-update --id "$TASK_ID" --status completed --json >/dev/null 2>&1 || true
orca terminal close --terminal "$HANDLE" --json >/dev/null 2>&1 || true
echo "[bench] evidence in $EVID ; overlay restored, tab closed."
