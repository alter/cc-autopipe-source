#!/bin/bash
# tests/smoke/run-sentinel-race-smoke.sh — v1.3.8 GROUP SENTINEL-RACE-FIX
# + RECOVERY-SWEEP-SENTINEL-TIMEOUT smoke.
#
# Pins five v1.3.8 invariants end-to-end:
#
#   1. Arm with cycle_start baseline (NOT current_mtime). Baseline must
#      be ≤ pre-cycle knowledge.md mtime so a same-cycle Claude append
#      is detectable. Verifies the v1.3.6 race fix.
#
#   2. Idempotency: pending=True + new fresh PROMOTION → arming is
#      skipped (no re-arm); event
#      `knowledge_sentinel_arm_skipped_already_armed` is emitted.
#
#   3. Clear-on-mtime-advance: knowledge.md grew past baseline →
#      detector clears pending AND resets baseline_mtime to None
#      (so the next arming starts fresh, not on a stale baseline).
#      Event `knowledge_updated_detected` carries baseline_was +
#      current_mtime fields for race diagnostics.
#
#   4. Subsequent fresh PROMOTION after detector cleared → arms again
#      with a NEW baseline. Verifies the cycle is repeatable end-to-end.
#
#   5. Stuck-state escape hatch: pending=True + last_activity 5h ago +
#      no mtime advance past baseline → recovery sweep emits
#      `sentinel_force_cleared` and recovers phase=active. Replaces the
#      v1.3.2 infinite skip loop observed in AI-trade Phase 2 v2.0
#      production (4h+ stuck on `phase=failed,
#      knowledge_update_pending=True`).

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
mkdir -p "$UHOME/log"
export CC_AUTOPIPE_USER_HOME="$UHOME"
AGG="$UHOME/log/aggregate.jsonl"

PROJ="$TMP/p1"
mkdir -p "$PROJ/.cc-autopipe/memory" "$PROJ/data/debug"

# --- Test 1: Arm with cycle_start baseline (not current_mtime) -----------
log "Test 1: arm-with-cycle-start-baseline + idempotent re-arm"
"$PY" - <<PY
import os, sys, time, json
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
from orchestrator import cycle
import promotion as promotion_lib
import state
import stop_helper

project = Path("$PROJ")

# Pre-cycle knowledge.md (older than cycle_start by 30s).
k = project / ".cc-autopipe" / "knowledge.md"
k.write_text("# knowledge\n", encoding="utf-8")
predates = time.time() - 30.0
os.utime(k, (predates, predates))

# Fresh PROMOTION inside the cycle.
p_promo = promotion_lib.promotion_path(project, "vec_long_lgbm")
p_promo.parent.mkdir(parents=True, exist_ok=True)
p_promo.write_text("## Verdict\n\n### STABLE — model ready\n", encoding="utf-8")

s = state.State.fresh(project.name)
cycle_start = (datetime.now(timezone.utc) - timedelta(seconds=5))
s.last_cycle_started_at = cycle_start.strftime("%Y-%m-%dT%H:%M:%SZ")
state.write(project, s)

# First arm.
armed = cycle._maybe_arm_sentinel_via_promotion(project, "vec_long_lgbm", s)
assert armed is True, "first arm must succeed (pending was False)"

s1 = state.read(project)
assert s1.knowledge_update_pending is True
assert s1.knowledge_baseline_mtime is not None
# v1.3.8 fix: baseline must be ≤ predates (pre-cycle mtime). v1.3.6 bug
# would have stamped current_mtime here, leaving the detector unable to
# fire even on a same-cycle Claude append.
assert s1.knowledge_baseline_mtime <= predates + 0.001, (
    f"baseline {s1.knowledge_baseline_mtime} must be ≤ pre-cycle "
    f"mtime {predates} (v1.3.6 race fix)"
)

# Second arm attempt with same fresh PROMOTION → idempotent skip.
armed2 = cycle._maybe_arm_sentinel_via_promotion(project, "vec_long_lgbm", s1)
assert armed2 is False, "second arm must skip (already pending)"
s2 = state.read(project)
# Baseline UNCHANGED (the whole point of idempotency).
assert s2.knowledge_baseline_mtime == s1.knowledge_baseline_mtime, (
    f"baseline must not be re-stamped on already-armed re-attempt: "
    f"{s2.knowledge_baseline_mtime} vs {s1.knowledge_baseline_mtime}"
)
PY

# Verify event trail.
ARM_COUNT=$(grep -c '"event":"knowledge_sentinel_armed_via_promotion"' "$AGG" || true)
[ "$ARM_COUNT" -eq 1 ] || die "expected 1 arm event, got $ARM_COUNT"
SKIP_COUNT=$(grep -c '"event":"knowledge_sentinel_arm_skipped_already_armed"' "$AGG" || true)
[ "$SKIP_COUNT" -eq 1 ] || die "expected 1 skip event, got $SKIP_COUNT"
ok "arm event with pre-cycle baseline + idempotent skip event"

# --- Test 2: Clear on mtime advance + reset baseline ---------------------
log "Test 2: knowledge.md advances past baseline → detector clears + resets"
"$PY" - <<PY
import os, sys, time
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state, stop_helper

project = Path("$PROJ")
k = project / ".cc-autopipe" / "knowledge.md"
k.write_text("# knowledge\n\n## Architectures\n- new lesson\n", encoding="utf-8")
new_mtime = time.time() + 1.0
os.utime(k, (new_mtime, new_mtime))

cleared = stop_helper.maybe_clear_knowledge_update_flag(project)
assert cleared is True, "detector must clear when mtime > baseline"

s = state.read(project)
assert s.knowledge_update_pending is False
# v1.3 + v1.3.8: baseline reset to None so next arm starts fresh.
assert s.knowledge_baseline_mtime is None, (
    f"baseline must be None after clear, got {s.knowledge_baseline_mtime}"
)
PY

DETECT_COUNT=$(grep -c '"event":"knowledge_updated_detected"' "$AGG" || true)
[ "$DETECT_COUNT" -eq 1 ] || die "expected 1 detect event, got $DETECT_COUNT"
# v1.3.8 enriches the detect event with baseline_was + current_mtime.
grep '"event":"knowledge_updated_detected"' "$AGG" | grep -q '"baseline_was"' \
    || die "knowledge_updated_detected must include baseline_was field"
grep '"event":"knowledge_updated_detected"' "$AGG" | grep -q '"current_mtime"' \
    || die "knowledge_updated_detected must include current_mtime field"
ok "detector cleared pending + reset baseline; event includes diagnostics"

# --- Test 3: Re-arm after clear works with fresh baseline ----------------
log "Test 3: cycle 2 — fresh PROMOTION after clear → arms again"
"$PY" - <<PY
import os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
from orchestrator import cycle
import promotion as promotion_lib, state

project = Path("$PROJ")
# Refresh PROMOTION mtime so it's fresh again for the next arm window.
p_promo = promotion_lib.promotion_path(project, "vec_long_lgbm")
p_promo.write_text("## Verdict\n\n### STABLE — second pass\n", encoding="utf-8")

s = state.read(project)
# Stamp new cycle_start for cycle 2.
cycle_start = (datetime.now(timezone.utc))
s.last_cycle_started_at = cycle_start.strftime("%Y-%m-%dT%H:%M:%SZ")
state.write(project, s)

armed = cycle._maybe_arm_sentinel_via_promotion(project, "vec_long_lgbm", s)
assert armed is True, "cycle-2 arm must succeed (cleared, fresh PROMOTION)"

s2 = state.read(project)
assert s2.knowledge_update_pending is True
assert s2.knowledge_baseline_mtime is not None
assert s2.knowledge_baseline_mtime > 0
PY

ARM_COUNT2=$(grep -c '"event":"knowledge_sentinel_armed_via_promotion"' "$AGG" || true)
[ "$ARM_COUNT2" -eq 2 ] || die "expected 2 arm events after cycle 2, got $ARM_COUNT2"
ok "cycle 2 armed with fresh baseline_mtime (cycle is repeatable)"

# --- Test 4: Stuck-state escape hatch ------------------------------------
log "Test 4: pending=True + last_activity 5h ago + no advance → force-clear"
PROJ2="$TMP/p2"
mkdir -p "$PROJ2/.cc-autopipe/memory"

"$PY" - <<PY
import os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
from orchestrator import recovery
import state

project = Path("$PROJ2")
k = project / ".cc-autopipe" / "knowledge.md"
k.write_text("# k\n", encoding="utf-8")
baseline = k.stat().st_mtime  # pretend v1.3.6 race state — baseline = current

s = state.State.fresh(project.name)
s.phase = "failed"
s.knowledge_update_pending = True
s.knowledge_baseline_mtime = baseline
s.knowledge_pending_reason = "stage_e_verdict on vec_long_x"
five_h_ago = datetime.now(timezone.utc) - timedelta(hours=5)
s.last_activity_at = five_h_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
state.write(project, s)

result = recovery.maybe_auto_recover(project)
assert result is True, "stuck sentinel >4h with no advance → must recover"

s2 = state.read(project)
assert s2.phase == "active", f"phase should be active, got {s2.phase!r}"
assert s2.knowledge_update_pending is False
assert s2.knowledge_baseline_mtime is None
PY

FORCE_COUNT=$(grep -c '"event":"sentinel_force_cleared"' "$AGG" || true)
[ "$FORCE_COUNT" -eq 1 ] || die "expected 1 sentinel_force_cleared, got $FORCE_COUNT"
RECOVER_COUNT=$(grep -c '"event":"auto_recovery_attempted"' "$AGG" || true)
[ "$RECOVER_COUNT" -eq 1 ] || die "expected 1 auto_recovery_attempted, got $RECOVER_COUNT"
# Recovery event records the sentinel_stuck reason for trace.
grep '"event":"auto_recovery_attempted"' "$AGG" | grep -q 'sentinel_stuck_force_clear' \
    || die "auto_recovery_attempted must record recover_reason=sentinel_stuck_force_clear"
ok "sentinel_force_cleared fired + phase recovered from stuck-deadlock state"

# --- Test 5: 2h-stuck still blocks (threshold respected) -----------------
log "Test 5: pending + 2h activity (below 4h threshold) → standard skip"
PROJ3="$TMP/p3"
mkdir -p "$PROJ3/.cc-autopipe/memory"

"$PY" - <<PY
import os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
from orchestrator import recovery
import state

project = Path("$PROJ3")
k = project / ".cc-autopipe" / "knowledge.md"
k.write_text("# k\n", encoding="utf-8")
baseline = k.stat().st_mtime

s = state.State.fresh(project.name)
s.phase = "failed"
s.knowledge_update_pending = True
s.knowledge_baseline_mtime = baseline
two_h_ago = datetime.now(timezone.utc) - timedelta(hours=2)
s.last_activity_at = two_h_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
state.write(project, s)

result = recovery.maybe_auto_recover(project)
assert result is False, "2h-stuck must NOT trigger force-clear (below 4h)"
assert state.read(project).phase == "failed"
PY

# Skip event count: 2h project should add a knowledge_update_in_progress skip.
SKIP_COUNT=$(grep -c '"reason":"knowledge_update_in_progress"' "$AGG" || true)
[ "$SKIP_COUNT" -ge 1 ] \
    || die "expected ≥1 knowledge_update_in_progress skip, got $SKIP_COUNT"
ok "2h-stuck respects 4h threshold (still skipped, sentinel preserved)"

printf '\033[32m===\033[0m PASS — v1.3.8 sentinel-race smoke\n'
