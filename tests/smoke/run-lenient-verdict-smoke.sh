#!/bin/bash
# tests/smoke/run-lenient-verdict-smoke.sh — v1.3.6 GROUP VERDICT-LENIENT smoke.
#
# Pins the lenient verdict parser end-to-end against the heading-style
# PROMOTION.md formats observed in the AI-trade Phase 2 v2.1 run:
#
#   ## Verdict\n### STABLE — ...               → PROMOTED
#   ## Verdict\n### CONDITIONAL — ...           → CONDITIONAL (no children)
#
# v1.3.5's strict `**Verdict: PROMOTED**` regex missed all of these and
# logged `promotion_verdict_missing` for every closed Phase 2 task —
# zero ablation children spawned across the whole run.

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

# --- Test 1: PROMOTED via `## Verdict\n### STABLE — ...` ---
PROJ1="$TMP/p1"
mkdir -p "$PROJ1/.cc-autopipe/memory" "$PROJ1/data/debug"
cat > "$PROJ1/backlog.md" <<'EOF'
- [x] [implement] [P1] vec_long_lgbm — model

## Done
EOF

# Note: post db6683c the file basename strips vec_long_/vec_ prefix.
cat > "$PROJ1/data/debug/CAND_lgbm_PROMOTION.md" <<'EOF'
# CAND vec_long_lgbm — PROMOTION

## Verdict

### STABLE — All criteria met. sum_fixed=+268.99%

## Acceptance
sum_fixed: +268.99%
regime_parity: 0.18
max_DD: -8.20%
DM_p_value: 0.003
DSR: 1.12

## Long-only verification
no shorts.
## Regime-stratified PnL
all 5 regimes positive.
## Statistical significance
DM p<0.01.
## Walk-forward stability
3 of 4.
## No-lookahead audit
clean.
EOF

log "PROMOTED via heading-style `## Verdict\\n### STABLE` → ablation children + leaderboard"
"$PY" - <<PY
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, "$REPO_ROOT/src/lib")
import promotion
project = Path("$PROJ1")
p = promotion.promotion_path(project, "vec_long_lgbm")
verdict = promotion.parse_verdict(p)
assert verdict == "PROMOTED", f"expected PROMOTED, got {verdict!r}"
ok_, missing = promotion.validate_v2_sections(p)
assert ok_, f"sections should validate, missing={missing}"
metrics = promotion.parse_metrics(p)
assert metrics["sum_fixed"] == 268.99
item = SimpleNamespace(id="vec_long_lgbm", priority=1)
promotion.on_promotion_success(project, item, metrics)
PY

BODY=$(cat "$PROJ1/backlog.md")
for SUFFIX in ab_drop_top ab_loss ab_seq ab_seed ab_eth; do
    echo "$BODY" | grep -q "vec_long_lgbm_${SUFFIX}" \
        || die "missing ablation child ${SUFFIX} after STABLE PROMOTED"
done
ok "5 ablation children spawned for STABLE verdict"

SPAWN=$(grep -c '"event":"ablation_children_spawned"' "$AGG" || true)
[ "$SPAWN" -eq 1 ] || die "expected 1 ablation_children_spawned event, got $SPAWN"
LB=$(grep -c '"event":"leaderboard_updated"' "$AGG" || true)
[ "$LB" -eq 1 ] || die "expected 1 leaderboard_updated event, got $LB"
ok "ablation_children_spawned + leaderboard_updated emitted"

# --- Test 2: CONDITIONAL → no children, no leaderboard ---
PROJ2="$TMP/p2"
mkdir -p "$PROJ2/.cc-autopipe/memory" "$PROJ2/data/debug"
cat > "$PROJ2/backlog.md" <<'EOF'
- [x] [implement] [P1] vec_long_dr_synth — partial pass
EOF
cat > "$PROJ2/data/debug/CAND_dr_synth_PROMOTION.md" <<'EOF'
# CAND vec_long_dr_synth — PROMOTION

## Verdict

### CONDITIONAL — Passes 3/4 PRD criteria; fails sum_fixed threshold

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

log "CONDITIONAL → no children, no leaderboard"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import promotion
project = Path("$PROJ2")
p = promotion.promotion_path(project, "vec_long_dr_synth")
v = promotion.parse_verdict(p)
assert v == "CONDITIONAL", f"expected CONDITIONAL, got {v!r}"
# CONDITIONAL must NOT call on_promotion_success — emulate cycle logic
import state as st
st.log_event(project, "promotion_conditional", task_id="vec_long_dr_synth")
PY

# Verify backlog unchanged — no ablation children appended.
BODY2=$(cat "$PROJ2/backlog.md")
echo "$BODY2" | grep -q "vec_long_dr_synth_ab_drop_top" \
    && die "CONDITIONAL must NOT spawn ablation children"
ok "CONDITIONAL did not mutate backlog"

# Verify event log: promotion_conditional present, no spawn/leaderboard
# events for this task.
COND=$(grep -c '"event":"promotion_conditional"' "$AGG" || true)
[ "$COND" -eq 1 ] || die "expected 1 promotion_conditional event, got $COND"
ok "promotion_conditional event emitted"

# Spawn count is from PROJ1 only (CONDITIONAL must not contribute).
SPAWN_AFTER=$(grep -c '"event":"ablation_children_spawned"' "$AGG" || true)
[ "$SPAWN_AFTER" -eq 1 ] || die "ablation_children_spawned increased on CONDITIONAL ($SPAWN_AFTER)"
LB_AFTER=$(grep -c '"event":"leaderboard_updated"' "$AGG" || true)
[ "$LB_AFTER" -eq 1 ] || die "leaderboard_updated increased on CONDITIONAL ($LB_AFTER)"
ok "no ablation/leaderboard side effects from CONDITIONAL"

# --- Test 3: REJECTED via `## Verdict: LONG_LOSES_MONEY` (inline) ---
PROJ3="$TMP/p3"
mkdir -p "$PROJ3/.cc-autopipe/memory" "$PROJ3/data/debug"
cat > "$PROJ3/backlog.md" <<'EOF'
- [x] [implement] [P0] vec_long_baseline — rejected
EOF
cat > "$PROJ3/data/debug/CAND_baseline_PROMOTION.md" <<'EOF'
## Verdict: LONG_LOSES_MONEY

## Long-only verification
n/a
EOF

log "Inline `## Verdict: LONG_LOSES_MONEY` → REJECTED"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import promotion
project = Path("$PROJ3")
p = promotion.promotion_path(project, "vec_long_baseline")
v = promotion.parse_verdict(p)
assert v == "REJECTED", f"expected REJECTED, got {v!r}"
PY
ok "LONG_LOSES_MONEY canonicalises to REJECTED"

printf '\033[32m===\033[0m PASS — v1.3.6 lenient-verdict smoke\n'
