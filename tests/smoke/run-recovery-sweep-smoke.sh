#!/bin/bash
# tests/smoke/run-recovery-sweep-smoke.sh — v1.3.1 B3-FIX end-to-end.
# Pins the recovery sweep: failed-with-stale-activity revives, active
# projects are untouched, and the lock awareness skips locked projects.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

UHOME="$TMP/uhome"
P_FAIL="$TMP/failed-old"
P_ACTIVE="$TMP/active"
mkdir -p "$UHOME/log"
mkdir -p "$P_FAIL/.cc-autopipe/memory" "$P_ACTIVE/.cc-autopipe/memory"
{ echo "$P_FAIL"; echo "$P_ACTIVE"; } > "$UHOME/projects.list"

export CC_AUTOPIPE_USER_HOME="$UHOME"

# Test 1: sweep revives stale-failed, leaves active alone.
log "sweep revives stale-failed, leaves active untouched"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from pathlib import Path
from datetime import datetime, timedelta, timezone
from orchestrator.recovery import auto_recover_failed_projects

s = state.State.fresh("failed-old")
s.phase = "failed"
s.last_activity_at = (datetime.now(timezone.utc) - timedelta(minutes=70)).strftime("%Y-%m-%dT%H:%M:%SZ")
state.write("$P_FAIL", s)

s2 = state.State.fresh("active")
s2.phase = "active"
state.write("$P_ACTIVE", s2)

revived = auto_recover_failed_projects([Path("$P_FAIL"), Path("$P_ACTIVE")])
assert revived == 1, f"expected 1, got {revived}"
assert state.read("$P_FAIL").phase == "active", "failed project not revived"
assert state.read("$P_ACTIVE").phase == "active", "active was clobbered"
print("sweep revival + isolation OK")
PY
ok "sweep revived 1 project, active untouched"

# Test 2: idempotency — calling sweep twice gives same revival count
# (project stays active after first revive, no double-bump).
log "second sweep is no-op (project already active)"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from pathlib import Path
from orchestrator.recovery import auto_recover_failed_projects
from datetime import datetime, timedelta, timezone

# Re-fail one project so we can revive again, then sweep twice in a row.
s = state.State.fresh("failed-old")
s.phase = "failed"
s.last_activity_at = (datetime.now(timezone.utc) - timedelta(minutes=70)).strftime("%Y-%m-%dT%H:%M:%SZ")
state.write("$P_FAIL", s)

first = auto_recover_failed_projects([Path("$P_FAIL")])
second = auto_recover_failed_projects([Path("$P_FAIL")])
assert first == 1, first
assert second == 0, "second sweep should be no-op"
print("idempotent OK")
PY
ok "idempotency — second consecutive sweep is no-op"

# Test 3: held per-project lock blocks recovery (race protection).
log "held lock blocks recovery"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state, locking
from pathlib import Path
from datetime import datetime, timedelta, timezone
from orchestrator.recovery import maybe_auto_recover

s = state.State.fresh("failed-old")
s.phase = "failed"
s.last_activity_at = (datetime.now(timezone.utc) - timedelta(minutes=70)).strftime("%Y-%m-%dT%H:%M:%SZ")
state.write("$P_FAIL", s)

# Hold the lock — recovery must skip rather than clobber state.
held = locking.acquire_project(Path("$P_FAIL"))
assert held is not None
try:
    revived = maybe_auto_recover("$P_FAIL")
    assert revived is False, "recovery raced on held lock!"
    assert state.read("$P_FAIL").phase == "failed"
    print("lock-aware skip OK")
finally:
    held.release()
PY
ok "held per-project lock blocks recovery"

printf '\033[32m===\033[0m PASS — recovery-sweep smoke (B3-FIX safety nets pinned)\n'
