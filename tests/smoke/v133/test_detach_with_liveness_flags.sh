#!/bin/bash
# tests/smoke/v133/test_detach_with_liveness_flags.sh — v1.3.3 L P4.
#
# End-to-end: real cc-autopipe-detach call with --pipeline-log +
# --stale-after-sec, then run-once cycles drive the detached state
# machine. Two paths:
#   Path A: pipeline keeps writing → check_cmd succeeds → detach_resumed
#   Path B: pipeline killed early → log mtime stalls → detach_pipeline_stale
#
# This is the most expensive smoke (~30s wall) because we need real
# subprocess heartbeat behavior, but it's the one that pins the full
# Group L wiring against the actual CLI surface.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }
die() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

setup_project() {
    local proj="$1" uhome="$2"
    mkdir -p "$uhome/log" "$proj"
    bash "$REPO_ROOT/src/helpers/cc-autopipe" init "$proj" >/dev/null
    echo "$proj" > "$uhome/projects.list"
}

# -----------------------------------------------------------------------
# Path B (run first — short and decisive). Worker writes to log every
# 0.2s for 2s, then exits without creating done.flag. After ~5s the log
# mtime is stale (relative to a 3s threshold). cc-autopipe run --once
# should emit detach_pipeline_stale.
# -----------------------------------------------------------------------

UHOME_B="$TMP/uhome_b"
PROJ_B="$TMP/proj_b"
PIPELINE_B="$TMP/pipeline_b.log"
export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_QUOTA_DISABLED=1

CC_AUTOPIPE_USER_HOME="$UHOME_B" setup_project "$PROJ_B" "$UHOME_B"
ok "Path B project initialised"

cat > "$TMP/worker_b.sh" <<EOF
#!/bin/bash
for _ in 1 2 3 4 5 6 7 8 9 10; do
    date +%s.%N >> "$PIPELINE_B"
    sleep 0.2
done
# Then exit WITHOUT creating done.flag — pipeline 'died'.
EOF
chmod +x "$TMP/worker_b.sh"

bash "$TMP/worker_b.sh" &
WORKER_PID=$!

# Detach with check_cmd that won't succeed (done.flag never appears),
# pipeline_log present, stale_after_sec=3 → after ~5s log goes stale.
log 'cc-autopipe-detach with --pipeline-log + --stale-after-sec=3'
CC_AUTOPIPE_USER_HOME="$UHOME_B" \
    bash "$REPO_ROOT/src/helpers/cc-autopipe-detach" \
    --reason "path-B" \
    --check-cmd "test -f $PROJ_B/done.flag" \
    --check-every 1 \
    --max-wait 600 \
    --pipeline-log "$PIPELINE_B" \
    --stale-after-sec 3 \
    --project "$PROJ_B" >/dev/null

# Wait for worker to finish writing (2s) + give 4s of stale gap.
wait $WORKER_PID 2>/dev/null || true
sleep 4
ok 'worker exited; pipeline log stale ~4s old'

# Run one orchestrator cycle. Stale check should fire.
export CC_AUTOPIPE_CLAUDE_BIN="$REPO_ROOT/tools/mock-claude.sh"
export CC_AUTOPIPE_MOCK_SCENARIO=success
log 'cc-autopipe run --once (Path B — expect detach_pipeline_stale)'
CC_AUTOPIPE_USER_HOME="$UHOME_B" \
    bash "$REPO_ROOT/src/helpers/cc-autopipe" run "$PROJ_B" --once >/dev/null 2>&1 || true

AGG_B="$UHOME_B/log/aggregate.jsonl"
[ -f "$AGG_B" ] || die "Path B aggregate.jsonl missing"
grep -q '"event":"detach_pipeline_stale"' "$AGG_B" \
    || die "Path B: detach_pipeline_stale not emitted; aggregate:\n$(cat "$AGG_B")"
ok 'Path B: detach_pipeline_stale event emitted'

# -----------------------------------------------------------------------
# Path A: worker keeps writing the log AND creates done.flag. check_cmd
# transitions to success normally (detach_resumed, not stale).
# -----------------------------------------------------------------------

UHOME_A="$TMP/uhome_a"
PROJ_A="$TMP/proj_a"
PIPELINE_A="$TMP/pipeline_a.log"

CC_AUTOPIPE_USER_HOME="$UHOME_A" setup_project "$PROJ_A" "$UHOME_A"
ok "Path A project initialised"

cat > "$TMP/worker_a.sh" <<EOF
#!/bin/bash
for _ in \$(seq 1 6); do
    date +%s.%N >> "$PIPELINE_A"
    sleep 0.5
done
touch "$PROJ_A/done.flag"
EOF
chmod +x "$TMP/worker_a.sh"

bash "$TMP/worker_a.sh" &
WORKER_A_PID=$!

log 'Path A: cc-autopipe-detach with active worker'
CC_AUTOPIPE_USER_HOME="$UHOME_A" \
    bash "$REPO_ROOT/src/helpers/cc-autopipe-detach" \
    --reason "path-A" \
    --check-cmd "test -f $PROJ_A/done.flag" \
    --check-every 0 \
    --max-wait 600 \
    --pipeline-log "$PIPELINE_A" \
    --stale-after-sec 30 \
    --project "$PROJ_A" >/dev/null

# Wait for worker to finish (writes done.flag).
wait $WORKER_A_PID 2>/dev/null || true
[ -f "$PROJ_A/done.flag" ] || die "Path A worker did not create done.flag"
ok 'Path A: worker created done.flag'

log 'cc-autopipe run --once (Path A — expect detach_resumed)'
CC_AUTOPIPE_USER_HOME="$UHOME_A" \
    bash "$REPO_ROOT/src/helpers/cc-autopipe" run "$PROJ_A" --once >/dev/null 2>&1 || true

AGG_A="$UHOME_A/log/aggregate.jsonl"
grep -q '"event":"detach_resumed"' "$AGG_A" \
    || die "Path A: detach_resumed not emitted; aggregate:\n$(cat "$AGG_A")"
grep -q '"event":"detach_pipeline_stale"' "$AGG_A" \
    && die "Path A: stale event fired but pipeline was alive"
ok 'Path A: detach_resumed (no stale event)'

# Verify state.detached carried the liveness flags through (sanity).
PIPELINE_LOG=$("$PY" -c "import json; s=json.load(open('$PROJ_A/.cc-autopipe/state.json')); d=s.get('detached'); print(d['pipeline_log_path'] if d else 'phase_was_active')")
# After detach_resumed, detached=null, so we accept the post-resume state.
[ "$PIPELINE_LOG" = "phase_was_active" ] || \
    [ "$PIPELINE_LOG" = "$PIPELINE_A" ] || \
    die "Path A pipeline_log_path unexpected: $PIPELINE_LOG"
ok 'Path A: detached fields cleaned up post-resume'

printf '\033[32m===\033[0m PASS — v1.3.3 P4 detach with liveness flags\n'
