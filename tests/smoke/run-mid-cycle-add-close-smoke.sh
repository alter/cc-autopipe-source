#!/bin/bash
# tests/smoke/run-mid-cycle-add-close-smoke.sh — v1.3.10 MID-CYCLE-ADD-CLOSE
#
# Pins the _post_cycle_delta_scan invariant: vec_long_* tasks added AND
# closed within the same cycle (not in the pre-cycle snapshot) are caught by
# the delta-scan path and tagged origin=post_cycle_delta on emitted events.
# The pre-cycle path (v1.3.5+) handles tasks open at cycle start; both paths
# must coexist without double-emitting.

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

# ---------------------------------------------------------------------------
# Test 1: pre-cycle + mid-cycle paths coexist
# ---------------------------------------------------------------------------

PROJ1="$TMP/p1"
mkdir -p "$PROJ1/.cc-autopipe/memory" "$PROJ1/data/debug"

# Initial backlog: one pre-cycle open task only.
cat > "$PROJ1/backlog.md" <<'EOF'
- [ ] [implement] [P0] vec_long_pre_existing — pre-cycle open task

## Done
EOF

log "Test 1: snapshot pre_open ids"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import backlog as backlog_lib
project = Path("$PROJ1")
pre_open = [
    it for it in backlog_lib.parse_open_tasks(project / "backlog.md")
    if it.id.startswith("vec_long_") and it.task_type == "implement"
]
(project / "pre_open_ids.txt").write_text(
    "\n".join(p.id for p in pre_open), encoding="utf-8"
)
assert len(pre_open) == 1, f"expected 1 pre_open task, got {len(pre_open)}"
PY
ok "pre_open snapshot written (1 task)"

log "Test 1: mock-claude mid-cycle mutations"
# Simulate Claude's in-cycle work: close pre_existing AND add+close mid_cycle_added.
cat > "$PROJ1/backlog.md" <<'EOF'
- [x] [implement] [P0] vec_long_pre_existing — pre-cycle open task
- [x] [implement] [P0] vec_long_mid_cycle_added — meta-task created same cycle

## Done
EOF

# PROMOTION.md for pre_existing — full v2 sections + metrics.
cat > "$PROJ1/data/debug/CAND_pre_existing_PROMOTION.md" <<'EOF'
## Verdict

### ✅ PROMOTED — strategy validated

## Long-only verification
All long-only checks pass. sum_fixed: +120.0%

## Regime-stratified PnL
regime_parity: 0.15

## Statistical significance
DM_p_value: 0.005, DSR: 1.20

## Walk-forward stability
Stable across all windows. max_DD: -7.5%

## No-lookahead audit
No lookahead detected.
EOF

# PROMOTION.md for mid_cycle_added — heading-style verdict (tier 1 recognisable).
cat > "$PROJ1/data/debug/CAND_mid_cycle_added_PROMOTION.md" <<'EOF'
## Verdict

### ✅ PROMOTED — meta-task complete

## Long-only verification
yes
## Regime-stratified PnL
yes
## Statistical significance
yes
## Walk-forward stability
yes
## No-lookahead audit
yes
EOF

log "Test 1: drive pre-cycle path + _post_cycle_delta_scan"
"$PY" - <<PY
import json, sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
from orchestrator import cycle
import backlog as backlog_lib
import promotion as promotion_lib
import state

project = Path("$PROJ1")

# Rebuild pre_open snapshot from the txt file we wrote earlier.
pre_ids = [
    ln.strip() for ln in
    (project / "pre_open_ids.txt").read_text(encoding="utf-8").splitlines()
    if ln.strip()
]
pre_open = [
    backlog_lib.BacklogItem(
        status="x", priority=0, id=tid, description="",
        tags=["[implement]", "[P0]"],
        raw_line=f"- [x] [implement] [P0] {tid} — pre",
    )
    for tid in pre_ids
]

# --- Pre-cycle path: reproduce exactly what cycle.py does for tasks that
# were open at cycle start and are now [x].
for pre_item in pre_open:
    p_path = promotion_lib.promotion_path(project, pre_item.id)
    verdict = promotion_lib.parse_verdict(p_path)
    if verdict == "PROMOTED":
        state.log_event(project, "promotion_validated_attempt", task_id=pre_item.id)
        ok, missing = promotion_lib.validate_v2_sections(p_path, task_id=pre_item.id)
        state.log_event(
            project, "promotion_v2_sections_check",
            task_id=pre_item.id, all_present=ok, missing=",".join(missing),
            strict=promotion_lib.requires_full_v2_validation(pre_item.id),
        )
        if ok:
            metrics = promotion_lib.parse_metrics(p_path)
            promotion_lib.on_promotion_success(project, pre_item, metrics)
            state.log_event(
                project, "promotion_validated", task_id=pre_item.id,
                **{k: v for k, v in metrics.items() if v is not None},
            )

# --- Delta-scan path: under test.
cycle._post_cycle_delta_scan(project, pre_open)
PY
ok "pre-cycle path + _post_cycle_delta_scan completed"

# --- Assertions ---

log "Test 1: assert 2x promotion_validated_attempt events"
ATTEMPT_COUNT=$(grep -c '"event":"promotion_validated_attempt"' "$AGG" || true)
[ "$ATTEMPT_COUNT" -eq 2 ] \
    || die "expected 2 promotion_validated_attempt events, got $ATTEMPT_COUNT"
ok "2 promotion_validated_attempt events"

log "Test 1: assert pre_existing has NO origin=post_cycle_delta on its attempt"
"$PY" - <<PY
import json, sys
path = "$AGG"
want_id = "vec_long_pre_existing"
matches = []
for ln in open(path):
    try:
        e = json.loads(ln)
    except Exception:
        continue
    if e.get("event") == "promotion_validated_attempt" and e.get("task_id") == want_id:
        matches.append(e)
assert len(matches) == 1, f"expected 1 attempt for {want_id}, got {len(matches)}"
assert matches[0].get("origin") is None, (
    f"pre-cycle path must NOT set origin, got origin={matches[0].get('origin')!r}"
)
PY
ok "vec_long_pre_existing has no origin on promotion_validated_attempt (pre-cycle path)"

log "Test 1: assert mid_cycle_added has origin=post_cycle_delta on its attempt"
"$PY" - <<PY
import json, sys
path = "$AGG"
want_id = "vec_long_mid_cycle_added"
matches = []
for ln in open(path):
    try:
        e = json.loads(ln)
    except Exception:
        continue
    if e.get("event") == "promotion_validated_attempt" and e.get("task_id") == want_id:
        matches.append(e)
assert len(matches) == 1, f"expected 1 attempt for {want_id}, got {len(matches)}"
assert matches[0].get("origin") == "post_cycle_delta", (
    f"origin must be post_cycle_delta, got {matches[0].get('origin')!r}"
)
PY
ok "vec_long_mid_cycle_added has origin=post_cycle_delta on promotion_validated_attempt"

log "Test 1: assert mid_cycle_added has >=1 event with origin=post_cycle_delta"
"$PY" - <<PY
import json, sys
path = "$AGG"
want_id = "vec_long_mid_cycle_added"
delta_events = []
for ln in open(path):
    try:
        e = json.loads(ln)
    except Exception:
        continue
    if e.get("task_id") == want_id and e.get("origin") == "post_cycle_delta":
        delta_events.append(e)
assert len(delta_events) >= 1, (
    f"expected >=1 post_cycle_delta events for {want_id}, got {len(delta_events)}"
)
PY
ok "mid_cycle_added has >=1 origin=post_cycle_delta events"

log "Test 1: assert 2x ablation_children_spawned events, each count=5"
SPAWN_COUNT=$(grep -c '"event":"ablation_children_spawned"' "$AGG" || true)
[ "$SPAWN_COUNT" -eq 2 ] \
    || die "expected 2 ablation_children_spawned events, got $SPAWN_COUNT"
"$PY" - <<PY
import json, sys
path = "$AGG"
spawned = []
for ln in open(path):
    try:
        e = json.loads(ln)
    except Exception:
        continue
    if e.get("event") == "ablation_children_spawned":
        spawned.append(e)
for ev in spawned:
    cnt = ev.get("count")
    assert cnt == 5, f"ablation_children_spawned count must be 5, got {cnt!r} in {ev}"
PY
ok "2 ablation_children_spawned events, each count=5"

log "Test 1: assert 10 ablation child lines in backlog"
BODY=$(cat "$PROJ1/backlog.md")
for SUFFIX in ab_drop_top ab_loss ab_seq ab_seed ab_eth; do
    echo "$BODY" | grep -q "vec_long_pre_existing_${SUFFIX}" \
        || die "missing ablation child vec_long_pre_existing_${SUFFIX}"
    echo "$BODY" | grep -q "vec_long_mid_cycle_added_${SUFFIX}" \
        || die "missing ablation child vec_long_mid_cycle_added_${SUFFIX}"
done
ok "10 ablation child lines present in backlog (5 per parent)"

log "Test 1: assert LEADERBOARD.md exists and contains both task ids"
LB="$PROJ1/data/debug/LEADERBOARD.md"
[ -f "$LB" ] || die "LEADERBOARD.md does not exist at $LB"
grep -q "vec_long_pre_existing" "$LB" \
    || die "LEADERBOARD.md missing vec_long_pre_existing"
grep -q "vec_long_mid_cycle_added" "$LB" \
    || die "LEADERBOARD.md missing vec_long_mid_cycle_added"
ok "LEADERBOARD.md exists and contains both task ids"

# ---------------------------------------------------------------------------
# Test 2: variant — pre_open empty, unrecognised verdict mid-cycle task
# ---------------------------------------------------------------------------

PROJ2="$TMP/p2"
mkdir -p "$PROJ2/.cc-autopipe/memory" "$PROJ2/data/debug"

cat > "$PROJ2/backlog.md" <<'EOF'
- [x] [implement] [P0] vec_long_unparseable_added — variant

## Done
EOF

cat > "$PROJ2/data/debug/CAND_unparseable_added_PROMOTION.md" <<'EOF'
**Note**: no verdict here
EOF

log "Test 2: variant — empty pre_open, unrecognised verdict → delta-scan logs unrecognized+missing"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
from orchestrator import cycle

project = Path("$PROJ2")
cycle._post_cycle_delta_scan(project, [])
PY
ok "_post_cycle_delta_scan completed for variant project"

log "Test 2: assert promotion_verdict_unrecognized with origin=post_cycle_delta"
"$PY" - <<PY
import json, sys
path = "$AGG"
want_id = "vec_long_unparseable_added"
unrec = []
for ln in open(path):
    try:
        e = json.loads(ln)
    except Exception:
        continue
    if e.get("event") == "promotion_verdict_unrecognized" and e.get("task_id") == want_id:
        unrec.append(e)
assert len(unrec) >= 1, f"expected >=1 promotion_verdict_unrecognized for {want_id}, got {len(unrec)}"
for ev in unrec:
    assert ev.get("origin") == "post_cycle_delta", (
        f"origin must be post_cycle_delta, got {ev.get('origin')!r}"
    )
PY
ok "promotion_verdict_unrecognized with origin=post_cycle_delta for unparseable_added"

log "Test 2: assert promotion_verdict_missing with origin=post_cycle_delta"
"$PY" - <<PY
import json, sys
path = "$AGG"
want_id = "vec_long_unparseable_added"
missing_evs = []
for ln in open(path):
    try:
        e = json.loads(ln)
    except Exception:
        continue
    if e.get("event") == "promotion_verdict_missing" and e.get("task_id") == want_id:
        missing_evs.append(e)
assert len(missing_evs) >= 1, f"expected >=1 promotion_verdict_missing for {want_id}, got {len(missing_evs)}"
for ev in missing_evs:
    assert ev.get("origin") == "post_cycle_delta", (
        f"origin must be post_cycle_delta, got {ev.get('origin')!r}"
    )
PY
ok "promotion_verdict_missing with origin=post_cycle_delta for unparseable_added"

log "Test 2: assert no ablation_children_spawned for unparseable_added"
"$PY" - <<PY
import json, sys
path = "$AGG"
want_id = "vec_long_unparseable_added"
spawned = []
for ln in open(path):
    try:
        e = json.loads(ln)
    except Exception:
        continue
    if e.get("event") == "ablation_children_spawned" and e.get("task_id") == want_id:
        spawned.append(e)
assert len(spawned) == 0, f"unrecognised verdict must NOT spawn ablation children, got {len(spawned)}"
PY
ok "no ablation_children_spawned for unparseable_added (unrecognised verdict)"

printf '\033[32m===\033[0m PASS — v1.3.10 mid-cycle-add-close smoke\n'
