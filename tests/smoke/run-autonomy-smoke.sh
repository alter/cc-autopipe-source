#!/bin/bash
# tests/smoke/run-autonomy-smoke.sh — v1.3 GROUP B end-to-end.
# Verifies activity-based stuck detection and auto-recovery flow.
#
# Exits non-zero on first failure. Mocked claude (no real API calls).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }
die() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

UHOME="$TMP/uhome"
PROJ="$TMP/p"
mkdir -p "$UHOME/log" "$PROJ/.cc-autopipe/memory"
touch "$UHOME/projects.list"
echo "$PROJ" >> "$UHOME/projects.list"

export CC_AUTOPIPE_USER_HOME="$UHOME"

# Test 1: stuck detection — last_activity_at older than 60min → fail.
log "stuck detection on stale activity"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from datetime import datetime, timedelta, timezone
from orchestrator.recovery import evaluate_stuck

s = state.State.fresh("p")
s.last_activity_at = (datetime.now(timezone.utc) - timedelta(minutes=75)).strftime("%Y-%m-%dT%H:%M:%SZ")
verdict = evaluate_stuck(s)
assert verdict == "fail", f"expected fail, got {verdict}"
print("stuck=fail OK")
PY
ok "stuck detection — fail at 75min"

# Test 2: warn band 30-60min.
log "stuck warn band"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from datetime import datetime, timedelta, timezone
from orchestrator.recovery import evaluate_stuck
s = state.State.fresh("p")
s.last_activity_at = (datetime.now(timezone.utc) - timedelta(minutes=35)).strftime("%Y-%m-%dT%H:%M:%SZ")
assert evaluate_stuck(s) == "warn"
print("warn OK")
PY
ok "warn band — 35min"

# Test 3: auto-recovery revives a >1h failed project.
log "auto-recovery revival"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from datetime import datetime, timedelta, timezone
from orchestrator.recovery import maybe_auto_recover

s = state.State.fresh("p")
s.phase = "failed"
s.last_activity_at = (datetime.now(timezone.utc) - timedelta(minutes=75)).strftime("%Y-%m-%dT%H:%M:%SZ")
state.write("$PROJ", s)
revived = maybe_auto_recover("$PROJ")
assert revived is True, "expected revive"
s2 = state.read("$PROJ")
assert s2.phase == "active", f"expected active, got {s2.phase}"
assert s2.recovery_attempts == 1
print("revived OK")
PY
ok "auto-recovery revived stale failed project"

printf '\033[32m===\033[0m PASS — autonomy smoke\n'
