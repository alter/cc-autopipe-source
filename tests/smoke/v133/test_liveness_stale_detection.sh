#!/bin/bash
# tests/smoke/v133/test_liveness_stale_detection.sh — v1.3.3 Group L P1.
#
# Real-CLI smoke: detached project with stale pipeline.log gets force-
# resumed via detach_pipeline_stale instead of waiting full max_wait_sec.
#
# Per PROMPT-v1.3.3 acceptance criteria: NO Python heredoc. Uses
# `cc-autopipe init`, `cc-autopipe run --once` with mock-claude, and
# direct state.json crafting (raw JSON via heredoc into a file is "real
# state.json on disk" — distinct from Python module heredoc patterns).

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

UHOME="$TMP/uhome"
PROJ="$TMP/proj"
mkdir -p "$UHOME/log" "$PROJ"

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_USER_HOME="$UHOME"
export CC_AUTOPIPE_QUOTA_DISABLED=1

# 1. Initialise project via real CLI.
log "cc-autopipe init via real CLI"
bash "$REPO_ROOT/src/helpers/cc-autopipe" init "$PROJ" >/dev/null
[ -f "$PROJ/.cc-autopipe/state.json" ] || die "init did not create state.json"
ok 'init seeded project'

# 2. Create pipeline.log with mtime 1 hour ago.
PIPELINE_LOG="$TMP/pipeline.log"
echo "started" > "$PIPELINE_LOG"
touch -d "1 hour ago" "$PIPELINE_LOG"
ok "stale pipeline.log seeded (mtime 1h ago)"

# 3. Craft a detached state directly. Started 1 hour ago, max_wait 12h
#    so timeout doesn't fire; check_cmd always rc=1; pipeline_log + stale
#    300s configured.
STARTED_AT=$(date -u -d "1 hour ago" +"%Y-%m-%dT%H:%M:%SZ")
cat > "$PROJ/.cc-autopipe/state.json" <<JSON
{
  "schema_version": 5,
  "name": "proj",
  "phase": "detached",
  "iteration": 0,
  "session_id": null,
  "last_score": null,
  "last_passed": null,
  "prd_complete": false,
  "consecutive_failures": 0,
  "last_cycle_started_at": null,
  "last_progress_at": "$STARTED_AT",
  "threshold": 0.85,
  "paused": null,
  "detached": {
    "reason": "training",
    "started_at": "$STARTED_AT",
    "check_cmd": "false",
    "check_every_sec": 0,
    "max_wait_sec": 43200,
    "last_check_at": null,
    "checks_count": 0,
    "pipeline_log_path": "$PIPELINE_LOG",
    "stale_after_sec": 300
  },
  "current_phase": 1,
  "phases_completed": [],
  "current_task": null
}
JSON
ok 'detached state seeded with pipeline_log + stale_after_sec=300'

# 4. Run one orchestrator cycle with mock-claude. Stale check runs first;
#    detected stale → emits detach_pipeline_stale and transitions to active.
log "cc-autopipe run --once (mock-claude)"
echo "$PROJ" > "$UHOME/projects.list"
export CC_AUTOPIPE_CLAUDE_BIN="$REPO_ROOT/tools/mock-claude.sh"
export CC_AUTOPIPE_MOCK_SCENARIO=success
bash "$REPO_ROOT/src/helpers/cc-autopipe" run "$PROJ" --once >/dev/null 2>&1 || true

# 5. Assert detach_pipeline_stale event in aggregate.jsonl.
AGG="$UHOME/log/aggregate.jsonl"
[ -f "$AGG" ] || die "aggregate.jsonl missing at $AGG"
STALE_LINE=$(grep '"event":"detach_pipeline_stale"' "$AGG" || true)
[ -n "$STALE_LINE" ] || die "expected detach_pipeline_stale event; aggregate:\n$(cat "$AGG")"
ok 'detach_pipeline_stale event emitted'

LOG_AGE=$(echo "$STALE_LINE" | "$PY" -c "import sys, json; e = json.loads(sys.stdin.read()); print(e['log_age_sec'])")
[ "$LOG_AGE" -ge 3000 ] || die "log_age_sec too small: $LOG_AGE (expected >=3000)"
ok "log_age_sec=$LOG_AGE >= 3000"

# 6. Verify state transitioned and resume reason set.
PHASE=$("$PY" -c "import json; s=json.load(open('$PROJ/.cc-autopipe/state.json')); print(s['phase'])")
# last_detach_resume_reason is consumed by _build_prompt in the same
# cycle (cleared post-bake), so we only verify the phase moved off
# detached and the stale event landed in aggregate above.
[ "$PHASE" != "detached" ] || die "phase still detached after stale resume; got=$PHASE"
ok "phase advanced past detached (got=$PHASE)"

printf '\033[32m===\033[0m PASS — v1.3.3 P1 liveness stale detection\n'
