#!/bin/bash
# tests/smoke/v133/test_v132_backward_compat.sh — v1.3.3 P5.
#
# Critical: any v1.3.2 state.json on disk (schema_version=4, no
# pipeline_log_path / stale_after_sec / last_verdict_event_at fields)
# must read cleanly under v1.3.3. The next write persists v=5; behaviour
# is identical to v1.3.2 (no stale detection — both fields default null).

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
mkdir -p "$UHOME/log" "$PROJ/.cc-autopipe/memory"

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_USER_HOME="$UHOME"
export CC_AUTOPIPE_QUOTA_DISABLED=1

# Craft a v1.3.2 detached state — no pipeline_log_path, no stale_after_sec,
# no last_verdict_event_at, schema_version=4. Started 5 minutes ago,
# max_wait 4h, check_cmd 'true' (will succeed and return to active).
STARTED_AT=$(date -u -d "5 minutes ago" +"%Y-%m-%dT%H:%M:%SZ")
cat > "$PROJ/.cc-autopipe/state.json" <<JSON
{
  "schema_version": 4,
  "name": "proj",
  "phase": "detached",
  "iteration": 7,
  "session_id": null,
  "last_score": 0.82,
  "last_passed": false,
  "prd_complete": false,
  "consecutive_failures": 0,
  "last_cycle_started_at": "$STARTED_AT",
  "last_progress_at": "$STARTED_AT",
  "threshold": 0.85,
  "paused": null,
  "detached": {
    "reason": "v1.3.2 legacy detach",
    "started_at": "$STARTED_AT",
    "check_cmd": "true",
    "check_every_sec": 0,
    "max_wait_sec": 14400,
    "last_check_at": null,
    "checks_count": 2
  },
  "current_phase": 1,
  "phases_completed": [],
  "current_task": null
}
JSON
ok 'v1.3.2-style state.json seeded (schema_version=4, no liveness/verdict fields)'

echo "$PROJ" > "$UHOME/projects.list"

# 1. cc-autopipe status — exercises read path, must not crash.
log 'cc-autopipe status — read v=4 cleanly'
bash "$REPO_ROOT/src/helpers/cc-autopipe" status >/dev/null 2>&1 \
    || die 'cc-autopipe status crashed on v1.3.2 state'
ok 'status read v1.3.2 state without crashing'

# 2. cc-autopipe run --once should resume (check_cmd=true succeeds) and
#    write back the engine's current schema_version with liveness fields
#    = null. Test pins the migration BEHAVIOUR (write-back happens, new
#    fields get defaults), not the integer — v1.3.4 lifted the version
#    from 5 to 6 and a future hotfix may bump it again.
export CC_AUTOPIPE_CLAUDE_BIN="$REPO_ROOT/tools/mock-claude.sh"
export CC_AUTOPIPE_MOCK_SCENARIO=success
log 'cc-autopipe run --once'
bash "$REPO_ROOT/src/helpers/cc-autopipe" run "$PROJ" --once >/dev/null 2>&1 || true
ok 'run --once completed without crash'

# Discover the engine's current SCHEMA_VERSION rather than hard-coding it.
EXPECTED_SV=$("$PY" -c "import sys; sys.path.insert(0, '$REPO_ROOT/src/lib'); import state; print(state.SCHEMA_VERSION)")
SV=$("$PY" -c "import json; print(json.load(open('$PROJ/.cc-autopipe/state.json'))['schema_version'])")
[ "$SV" = "$EXPECTED_SV" ] || die "schema_version did not migrate to $EXPECTED_SV; got=$SV"
[ "$SV" -ge 5 ] || die "schema_version regressed below 5; got=$SV"
ok "schema_version migrated 4 → $SV (current engine SCHEMA_VERSION)"

# Liveness fields default null (no opt-in retroactively).
PLP=$("$PY" -c "import json; s=json.load(open('$PROJ/.cc-autopipe/state.json')); d=s.get('detached'); print('null' if d is None else (d.get('pipeline_log_path') or 'null'))")
[ "$PLP" = "null" ] || die "pipeline_log_path retroactively set: $PLP"
ok 'pipeline_log_path stays null (additive migration, no retroactive opt-in)'

LV=$("$PY" -c "import json; s=json.load(open('$PROJ/.cc-autopipe/state.json')); print(s.get('last_verdict_event_at'))")
[ "$LV" = "None" ] || die "last_verdict_event_at unexpected: $LV"
ok 'last_verdict_event_at stays null'

# 3. No stale-detection events fired — without flags, the engine doesn't
#    even attempt stale checks.
AGG="$UHOME/log/aggregate.jsonl"
if [ -f "$AGG" ]; then
    grep -q '"event":"detach_pipeline_stale"' "$AGG" \
        && die 'detach_pipeline_stale fired without --pipeline-log opt-in'
    grep -q '"event":"detach_pipeline_log_missing"' "$AGG" \
        && die 'detach_pipeline_log_missing fired without --pipeline-log opt-in'
fi
ok 'no liveness events fired for opt-out state (v1.3.2 parity)'

printf '\033[32m===\033[0m PASS — v1.3.3 P5 v1.3.2 backward compat\n'
