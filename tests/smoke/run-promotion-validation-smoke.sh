#!/bin/bash
# tests/smoke/run-promotion-validation-smoke.sh — v1.3.5 PROMOTION-PARSER smoke.
#
# Synthetic end-to-end validation of the v2.0 PROMOTION.md parsing +
# ablation auto-spawn + quarantine flow. Production has never run
# this — first activation will be the first vec_long_* promotion in
# AI-trade Phase 2.
#
# Lifecycle exercised:
#   1. PROMOTED + full v2.0 sections → ablation children spawned,
#      leaderboard append fired, knowledge sentinel armed
#   2. PROMOTED + missing section → backlog reverted [x]→[~],
#      UNVALIDATED_PROMOTION_<id>.md written
#   3. REJECTED → log only, no children, no leaderboard append
#   4. Atomic write: no leftover .tmp files

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
mkdir -p "$UHOME/log" "$PROJ/.cc-autopipe/memory" "$PROJ/data/debug"
echo "$PROJ" > "$UHOME/projects.list"
export CC_AUTOPIPE_USER_HOME="$UHOME"

# --- Test 1: PROMOTED + full sections → ablation children spawned ---
cat > "$PROJ/backlog.md" <<'EOF'
- [x] [implement] [P1] vec_long_lgbm — model

## Done
EOF

cat > "$PROJ/data/debug/CAND_lgbm_PROMOTION.md" <<'EOF'
# CAND vec_long_lgbm — PROMOTION

**Verdict: PROMOTED**

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

log "PROMOTED + full sections → 5 ablation children + leaderboard append"
"$PY" - <<PY
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, "$REPO_ROOT/src/lib")
import promotion
project = Path("$PROJ")
p = promotion.promotion_path(project, "vec_long_lgbm")
assert promotion.parse_verdict(p) == "PROMOTED"
ok_, missing = promotion.validate_v2_sections(p)
assert ok_, f"expected all sections present, missing={missing}"
metrics = promotion.parse_metrics(p)
assert metrics["sum_fixed"] == 268.99
item = SimpleNamespace(id="vec_long_lgbm", priority=1)
promotion.on_promotion_success(project, item, metrics)
PY

BACKLOG_BODY=$(cat "$PROJ/backlog.md")
for SUFFIX in ab_drop_top ab_loss ab_seq ab_seed ab_eth; do
    echo "$BACKLOG_BODY" | grep -q "vec_long_lgbm_${SUFFIX}" \
        || die "missing ablation child ${SUFFIX}"
done
ok "5 ablation children present in backlog.md"

P2_COUNT=$(echo "$BACKLOG_BODY" | grep -c '\[P2\]' || true)
[ "$P2_COUNT" -ge 5 ] \
    || die "expected ≥5 [P2] children (parent=P1 → P2), got $P2_COUNT"
ok "ablation child priority is parent+1 (P1→P2)"

# Insertion point: BEFORE Done section.
AB_LINE=$(grep -n vec_long_lgbm_ab_drop_top "$PROJ/backlog.md" | head -1 | cut -d: -f1)
DONE_LINE=$(grep -n '## Done' "$PROJ/backlog.md" | head -1 | cut -d: -f1)
[ "$AB_LINE" -lt "$DONE_LINE" ] \
    || die "ablation children must be inserted BEFORE ## Done"
ok "ablation children inserted before ## Done"

AGG="$UHOME/log/aggregate.jsonl"
[ -f "$AGG" ] || die "aggregate.jsonl missing"
SPAWN_COUNT=$(grep -c '"event":"ablation_children_spawned"' "$AGG" || true)
[ "$SPAWN_COUNT" -eq 1 ] \
    || die "expected 1 ablation_children_spawned event, got $SPAWN_COUNT"
ok "ablation_children_spawned event emitted"

LB_COUNT=$(grep -c '"event":"leaderboard_updated"' "$AGG" || true)
[ "$LB_COUNT" -eq 1 ] \
    || die "expected 1 leaderboard_updated event, got $LB_COUNT"
ok "leaderboard_updated event emitted"

# Verify no leftover .tmp files.
TMP_LEAKS=$(find "$PROJ" -name "*.tmp" -type f 2>/dev/null | wc -l)
[ "$TMP_LEAKS" = "0" ] || die "leftover .tmp files: $(find "$PROJ" -name "*.tmp")"
ok "no leftover .tmp files (atomic writes)"

# --- Test 2: PROMOTED + missing section → quarantine ---
PROJ2="$TMP/p2"
mkdir -p "$PROJ2/.cc-autopipe/memory" "$PROJ2/data/debug"
cat > "$PROJ2/backlog.md" <<'EOF'
- [x] [implement] [P1] vec_long_lgbm — model
EOF

cat > "$PROJ2/data/debug/CAND_lgbm_PROMOTION.md" <<'EOF'
**Verdict: PROMOTED**

## Long-only verification
yes
## Regime-stratified PnL
yes
## Statistical significance
yes
## No-lookahead audit
yes
EOF

log "PROMOTED + missing 'Walk-forward stability' → quarantine"
"$PY" - <<PY
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, "$REPO_ROOT/src/lib")
import promotion
project = Path("$PROJ2")
p = promotion.promotion_path(project, "vec_long_lgbm")
ok_, missing = promotion.validate_v2_sections(p)
assert not ok_
assert "Walk-forward stability" in missing
item = SimpleNamespace(id="vec_long_lgbm", priority=1)
promotion.quarantine_invalid(project, item, missing)
PY

grep -q '\[~\] \[implement\] \[P1\] vec_long_lgbm' "$PROJ2/backlog.md" \
    || die "backlog [x] should be reverted to [~]\n$(cat "$PROJ2/backlog.md")"
ok "backlog reverted [x]→[~]"

# v1.4.1 QUARANTINE-FILENAME-CONSISTENCY: marker uses Form 1
# basename (`_promotion_basename` strips `vec_`), so the file lands
# at UNVALIDATED_PROMOTION_long_lgbm.md, not _vec_long_lgbm.md.
[ -f "$PROJ2/data/debug/UNVALIDATED_PROMOTION_long_lgbm.md" ] \
    || die "UNVALIDATED_PROMOTION_long_lgbm.md (Form 1 basename) missing"
ok "UNVALIDATED_PROMOTION_<basename>.md quarantine marker written"

INVALID_COUNT=$(grep -c '"event":"promotion_invalid"' "$AGG" || true)
[ "$INVALID_COUNT" -eq 1 ] \
    || die "expected 1 promotion_invalid event, got $INVALID_COUNT"
ok "promotion_invalid event emitted"

# --- Test 3: REJECTED → log only ---
PROJ3="$TMP/p3"
mkdir -p "$PROJ3/.cc-autopipe/memory" "$PROJ3/data/debug"
cat > "$PROJ3/backlog.md" <<'EOF'
- [x] [implement] [P0] vec_long_xgb — rejected model
EOF
cat > "$PROJ3/data/debug/CAND_xgb_PROMOTION.md" <<'EOF'
**Verdict: REJECTED**

(no v2.0 sections necessary for rejected verdicts)
EOF

log "REJECTED → no children, no leaderboard append"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import promotion
project = Path("$PROJ3")
p = promotion.promotion_path(project, "vec_long_xgb")
v = promotion.parse_verdict(p)
assert v == "REJECTED", f"got {v!r}"
PY

# REJECTED is informational; on_promotion_success is NOT called.
# Verify backlog unchanged (no ablation children, no [P1] swap).
grep -q '\[x\] \[implement\] \[P0\] vec_long_xgb' "$PROJ3/backlog.md" \
    || die "backlog should be unchanged for REJECTED"
[ ! -d "$PROJ3/data/debug/ARCHIVE" ] \
    || die "no leaderboard archive should exist for REJECTED"
ok "REJECTED handled without children or leaderboard mutation"

printf '\033[32m===\033[0m PASS — v1.3.5 PROMOTION-PARSER smoke\n'
