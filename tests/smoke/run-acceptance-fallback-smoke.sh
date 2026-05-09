#!/bin/bash
# tests/smoke/run-acceptance-fallback-smoke.sh — v1.3.7 GROUP
# ACCEPTANCE-FALLBACK + STUCK-WITH-PROGRESS smoke.
#
# Pins three v1.3.7 invariants end-to-end:
#
#   1. PROMOTION.md with `## Acceptance\n\nCriteria met. Pipeline-ready.`
#      and the v2.0 section set BUT no Verdict heading anywhere → tier 3
#      fallback resolves PROMOTED → on_promotion_success → ablation
#      children + leaderboard append (was None / promotion_verdict_
#      unrecognized in v1.3.6).
#
#   2. PROMOTION.md with `## Acceptance\n\nCriteria not met.` (no Verdict
#      heading) → tier 3 fallback resolves REJECTED → no children, no
#      leaderboard append.
#
#   3. Stuck timestamp + cycle that closed 4 backlog `[x]` and wrote
#      4 PROMOTION files with mock-claude rc=1 → engine emits
#      `stuck_check_skipped_progress_detected` (single combined event)
#      and keeps phase=active. Reproduces the AI-trade Phase 2 v2.0
#      iteration=24 scenario that v1.3.6 silently failed.

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

# --- Test 1: Acceptance-only PROMOTED ----------------------------------
PROJ1="$TMP/p1"
mkdir -p "$PROJ1/.cc-autopipe/memory" "$PROJ1/data/debug"
cat > "$PROJ1/backlog.md" <<'EOF'
- [x] [implement] [P1] vec_long_stat_dm_test — DM test pipeline-ready

## Done
EOF

# Acceptance-only PROMOTION: NO `## Verdict` heading anywhere. Tier 3
# matches `## Acceptance` + "Criteria met"/PASS keyword. Includes the
# v2.0 section set so validate_v2_sections passes and the cycle's path
# fires on_promotion_success (vs. quarantine_invalid).
#
# Path: promotion_path("vec_long_stat_dm_test") strips the "vec_long_"
# prefix → CAND_stat_dm_test_PROMOTION.md. This is the engine's basename
# convention (see promotion._promotion_basename).
cat > "$PROJ1/data/debug/CAND_stat_dm_test_PROMOTION.md" <<'EOF'
# CAND vec_long_stat_dm_test — PROMOTION

## Long-only verification
no shorts.
## Regime-stratified PnL
all 5 regimes positive.
## Statistical significance
DM p<0.01.
## Walk-forward stability
3 of 4 windows positive.
## No-lookahead audit
clean.

## Acceptance

Criteria met. Pipeline-ready implementation.
sum_fixed: +268.99%
regime_parity: 0.18
max_DD: -8.20%
DM_p_value: 0.003
DSR: 1.12
EOF

log "Test 1: Acceptance-only PROMOTED → ablation children + leaderboard"
"$PY" - <<PY
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, "$REPO_ROOT/src/lib")
import promotion, state
project = Path("$PROJ1")
p = promotion.promotion_path(project, "vec_long_stat_dm_test")
verdict = promotion.parse_verdict(p)
assert verdict == "PROMOTED", f"tier-3 should resolve PROMOTED, got {verdict!r}"
ok_, missing = promotion.validate_v2_sections(p)
assert ok_, f"v2.0 sections should validate, missing={missing}"
metrics = promotion.parse_metrics(p)
item = SimpleNamespace(id="vec_long_stat_dm_test", priority=1)
promotion.on_promotion_success(project, item, metrics)
state.log_event(project, "promotion_validated", task_id="vec_long_stat_dm_test")
PY

BODY=$(cat "$PROJ1/backlog.md")
for SUFFIX in ab_drop_top ab_loss ab_seq ab_seed ab_eth; do
    echo "$BODY" | grep -q "vec_long_stat_dm_test_${SUFFIX}" \
        || die "missing ablation child ${SUFFIX} after Acceptance-PROMOTED"
done
ok "5 ablation children spawned for Acceptance-fallback PROMOTED verdict"

VAL=$(grep -c '"event":"promotion_validated"' "$AGG" || true)
[ "$VAL" -eq 1 ] || die "expected 1 promotion_validated event, got $VAL"
SPAWN=$(grep -c '"event":"ablation_children_spawned"' "$AGG" || true)
[ "$SPAWN" -eq 1 ] || die "expected 1 ablation_children_spawned event, got $SPAWN"
LB=$(grep -c '"event":"leaderboard_updated"' "$AGG" || true)
[ "$LB" -eq 1 ] || die "expected 1 leaderboard_updated event, got $LB"
ok "promotion_validated + ablation_children_spawned + leaderboard_updated emitted"

# Verify no `promotion_verdict_unrecognized` (v1.3.6 regression marker).
UNREC=$(grep -c '"event":"promotion_verdict_unrecognized"' "$AGG" || true)
[ "$UNREC" -eq 0 ] \
    || die "tier-3 should have resolved verdict; got $UNREC promotion_verdict_unrecognized events"
ok "no promotion_verdict_unrecognized (v1.3.6 regression cleared)"

# --- Test 2: Acceptance-only REJECTED ----------------------------------
PROJ2="$TMP/p2"
mkdir -p "$PROJ2/.cc-autopipe/memory" "$PROJ2/data/debug"
cat > "$PROJ2/backlog.md" <<'EOF'
- [x] [implement] [P1] vec_long_dm_negative — measurement task

## Done
EOF

cat > "$PROJ2/data/debug/CAND_dm_negative_PROMOTION.md" <<'EOF'
# CAND vec_long_dm_negative — PROMOTION

## Acceptance

Criteria not met — too few OOS periods for power.
EOF

log "Test 2: Acceptance-only REJECTED → no children, no leaderboard"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import promotion, state
project = Path("$PROJ2")
p = promotion.promotion_path(project, "vec_long_dm_negative")
v = promotion.parse_verdict(p)
assert v == "REJECTED", f"tier-3 should resolve REJECTED, got {v!r}"
state.log_event(project, "promotion_rejected", task_id="vec_long_dm_negative")
PY

BODY2=$(cat "$PROJ2/backlog.md")
echo "$BODY2" | grep -q "vec_long_dm_negative_ab_" \
    && die "REJECTED must NOT spawn ablation children"
ok "REJECTED did not mutate backlog (no ablation children)"

REJ=$(grep -c '"event":"promotion_rejected"' "$AGG" || true)
[ "$REJ" -ge 1 ] || die "expected ≥1 promotion_rejected event, got $REJ"
SPAWN_AFTER=$(grep -c '"event":"ablation_children_spawned"' "$AGG" || true)
[ "$SPAWN_AFTER" -eq 1 ] \
    || die "REJECTED leaked ablation_children_spawned ($SPAWN_AFTER vs 1 expected)"
ok "promotion_rejected emitted; no ablation/leaderboard side effects"

# --- Test 3: Stuck timestamp + 4 in-cycle closures (rc=1) skips fail ---
log "Test 3: stuck timestamp + 4 closed tasks + rc=1 → skip event, phase=active"
"$PY" - <<PY
import sys, importlib, json
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
cycle_mod = importlib.import_module("orchestrator.cycle")

import os
os.environ["CC_AUTOPIPE_QUOTA_DISABLED"] = "1"
os.environ["CC_AUTOPIPE_NETWORK_PROBE_DISABLED"] = "1"

project = Path("$TMP/p3")
(project / ".cc-autopipe" / "memory").mkdir(parents=True, exist_ok=True)

# Backlog with 4 open vec_long_* tasks; mock-claude will mark all four [x].
(project / "backlog.md").write_text(
    "- [ ] [implement] [P1] vec_long_a — \n"
    "- [ ] [implement] [P1] vec_long_b — \n"
    "- [ ] [implement] [P1] vec_long_c — \n"
    "- [ ] [implement] [P1] vec_long_d — \n"
)

s = state.State.fresh(project.name)
s.phase = "active"
s.iteration = 23
stale = datetime.now(timezone.utc) - timedelta(minutes=65)
s.last_activity_at = stale.strftime("%Y-%m-%dT%H:%M:%SZ")
state.write(project, s)

# Reproduce the AI-trade WALK_FILE_LIMIT miss: stub the legacy activity
# probe to is_active=False so the v1.3.7 gate is the only signal.
def _fake_activity(*a, **kw):
    return {
        "has_running_processes": False,
        "recent_artifact_changes": [],
        "stage_changed": False,
        "last_artifact_mtime": None,
        "process_pids": [],
        "is_active": False,
    }
cycle_mod.activity_lib.detect_activity = _fake_activity

# Mock claude: write 4 PROMOTION files + close 4 backlog tasks, rc=1.
def _claude(project_path, cmd, timeout):
    debug = project_path / "data" / "debug"
    debug.mkdir(parents=True, exist_ok=True)
    # Engine basename convention strips the vec_long_ prefix to just the
    # suffix (see promotion._promotion_basename).
    for tid in ("a", "b", "c", "d"):
        (debug / f"CAND_{tid}_PROMOTION.md").write_text(
            "## Acceptance\n\nCriteria met.\n"
        )
    (project_path / "backlog.md").write_text(
        "- [x] [implement] [P1] vec_long_a — \n"
        "- [x] [implement] [P1] vec_long_b — \n"
        "- [x] [implement] [P1] vec_long_c — \n"
        "- [x] [implement] [P1] vec_long_d — \n"
    )
    return 1, "", ""
cycle_mod._run_claude = _claude
cycle_mod._notify_tg = lambda *a, **kw: None

cycle_mod.process_project(project)

s2 = state.read(project)
assert s2.phase == "active", f"phase must remain active, got {s2.phase!r}"

agg = Path("$AGG").read_text(encoding="utf-8") if Path("$AGG").exists() else ""
events = [json.loads(ln) for ln in agg.splitlines() if ln.strip()]
proj_events = [e for e in events if e.get("project") == project.name]
skip = [e for e in proj_events if e["event"] == "stuck_check_skipped_progress_detected"]
assert len(skip) == 1, f"expected 1 skip event, got {len(skip)}: {proj_events}"
assert skip[0]["new_promotions"] == 4, f"expected 4 new_promotions, got {skip[0]}"
assert skip[0]["backlog_x_delta"] == 4, f"expected backlog_x_delta=4, got {skip[0]}"
assert "stuck_failed" not in {e["event"] for e in proj_events}, (
    "stuck_failed must NOT fire when filesystem evidence shows progress"
)
PY
ok "stuck_check_skipped_progress_detected fired with new_promotions=4 + backlog_x_delta=4"
ok "stuck_failed suppressed; phase=active preserved across rc=1 cycle"

printf '\033[32m===\033[0m PASS — v1.3.7 acceptance-fallback smoke\n'
