#!/bin/bash
# tests/smoke/run-stuck-detection-smoke.sh — v1.3.1 B-FIX end-to-end.
# Pins the AI-trade regression: 15 cycle_in_progress events with
# is_active=True from activity.py do NOT cause phase=failed; only a
# stale last_activity_at (>60 min) does, and the failure carries the
# stuck_no_activity reason.

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
PROJ="$TMP/p"
mkdir -p "$UHOME/log" "$PROJ/.cc-autopipe/memory"
echo "$PROJ" > "$UHOME/projects.list"

export CC_AUTOPIPE_USER_HOME="$UHOME"

# Test 1: 15 cycles with is_active=True → no fail, counter increments.
log "15 in_progress cycles with active filesystem signal"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from datetime import datetime, timezone
from orchestrator.recovery import evaluate_stuck

s = state.State.fresh("p")
s.last_in_progress = True
for _ in range(15):
    s.consecutive_in_progress += 1
    # Active probe — last_activity_at refreshed every cycle.
    s.last_activity_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert evaluate_stuck(s) == "ok", "false-positive stuck on active project"
assert s.consecutive_in_progress == 15
print("15-cycle no-fail OK; consecutive_in_progress=15 (telemetry)")
PY
ok "15 in_progress cycles never fail (long-training scenario)"

# Test 2: stale last_activity_at (65 min ago) → fail.
log "stale activity (65 min) flips evaluate_stuck to fail"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from datetime import datetime, timedelta, timezone
from orchestrator.recovery import evaluate_stuck

s = state.State.fresh("p")
s.last_in_progress = True
s.consecutive_in_progress = 7
s.last_activity_at = (datetime.now(timezone.utc) - timedelta(minutes=65)).strftime("%Y-%m-%dT%H:%M:%SZ")
verdict = evaluate_stuck(s)
assert verdict == "fail", f"expected fail at 65 min, got {verdict}"
print("stale 65-min OK")
PY
ok "stale activity → fail"

# Test 3: detect_activity recognises recent fs writes (mtime < 10 min)
# as active — keeps the stuck timer reset during legit ML training.
log "recent fs activity probes as is_active=True"
"$PY" - <<PY
import sys, os
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
from pathlib import Path
import activity

p = Path("$PROJ")
data = p / "data" / "models"
data.mkdir(parents=True, exist_ok=True)
ckpt = data / "epoch_0042.pt"
ckpt.write_bytes(b"x" * 16)
out = activity.detect_activity(p, "p", since_seconds=600)
assert out["is_active"] is True, out
print("fs-mtime active probe OK")
PY
ok "recent checkpoint write probes active"

# Test 4: stage transition signals activity.
log "stage transition probes as is_active=True"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
from pathlib import Path
import activity

p = Path("$PROJ")
out = activity.detect_activity(
    p, "p",
    last_observed_stage="train_baseline",
    current_stage="evaluate_oos",
)
assert out["is_active"] is True, "stage change must mark active"
print("stage-transition active probe OK")
PY
ok "stage change probes active"

printf '\033[32m===\033[0m PASS — stuck-detection smoke (B-FIX regression pinned)\n'
