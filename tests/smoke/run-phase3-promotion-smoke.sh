#!/bin/bash
# tests/smoke/run-phase3-promotion-smoke.sh — v1.3.13 PHASE3 + NEUTRAL smoke.
#
# Exercises end-to-end Phase 3 retroactive validation against AI-trade-
# style PROMOTION.md formats:
#   1. PROMOTED + AUC/Sharpe/DM → promotion_validated, LEADERBOARD.md
#      with composite > 0 (Phase 3 formula applied because sum_fixed is
#      absent)
#   2. **Status**: NEUTRAL → promotion_conditional (NOT
#      promotion_verdict_unrecognized), no leaderboard append
#
# Refs: PROMPT_v1.3.13-hotfix.md.

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
export CC_AUTOPIPE_USER_HOME="$UHOME"

AGG="$UHOME/log/aggregate.jsonl"
LB="$PROJ/data/debug/LEADERBOARD.md"

# --- Test 1: PROMOTED Phase 3 task with AUC + Sharpe + DM ---
cat > "$PROJ/backlog.md" <<'EOF'
- [x] [implement] [P2] vec_p3_test_auc_model — phase 3 model

## Done
EOF

cat > "$PROJ/data/debug/CAND_p3_test_auc_model_PROMOTION.md" <<'EOF'
## Verdict

### PROMOTED

**AUC**: 0.873
**Sharpe ratio**: 1.45
**DM p-value**: 0.031
EOF

log "Phase 3 PROMOTED — AUC/Sharpe/DM → promotion_validated + non-zero composite"
"$PY" tools/retroactive_promotion_validate.py "$PROJ" --prefix vec_p3_ >/dev/null

VALIDATED=$(grep -c '"event":"promotion_validated"' "$AGG" || true)
[ "$VALIDATED" -ge 1 ] || die "expected promotion_validated event, got $VALIDATED"
ok "promotion_validated event emitted"

[ -f "$LB" ] || die "LEADERBOARD.md not created"
grep -q 'vec_p3_test_auc_model' "$LB" \
    || die "LEADERBOARD.md missing vec_p3_test_auc_model row"
ok "LEADERBOARD.md row present"

# Composite column = 3rd cell after `| <rank> | <task_id> |`. Expect
# 0.6 * ((0.873-0.5)*2) + 0.3 * min(1.45/3, 1) + 0.1 * max(0, 1-0.031*10)
#   = 0.6 * 0.746 + 0.3 * 0.4833... + 0.1 * 0.69
#   ≈ 0.4476 + 0.145 + 0.069 = 0.6616
COMP=$(grep 'vec_p3_test_auc_model' "$LB" \
    | awk -F'|' '{gsub(/^ +| +$/, "", $4); print $4}')
log "  composite cell: $COMP"
"$PY" - <<PY
v = float("${COMP}")
expected = round(0.6*(0.746) + 0.3*(1.45/3.0) + 0.1*(1.0 - 0.031*10), 4)
# Allow tiny floating drift; the underlying _composite rounds to 4 dp so
# v should equal expected exactly.
assert abs(v - expected) < 1e-6, f"composite={v} expected={expected}"
assert v > 0.0, f"composite should be > 0, got {v}"
PY
ok "composite uses Phase 3 formula (≈ 0.6616), strictly > 0"

# --- Test 2: **Status**: NEUTRAL → promotion_conditional, no leaderboard append ---
cat >> "$PROJ/backlog.md" <<'EOF'
- [x] [implement] [P2] vec_p3_test_neutral — DA information ceiling
EOF

cat > "$PROJ/data/debug/CAND_p3_test_neutral_PROMOTION.md" <<'EOF'
**Status**: NEUTRAL
EOF

PREV_LB_HASH=$(sha256sum "$LB" | awk '{print $1}')

log "**Status**: NEUTRAL → promotion_conditional, no leaderboard append"
"$PY" tools/retroactive_promotion_validate.py "$PROJ" --prefix vec_p3_ >/dev/null

COND=$(grep -c '"event":"promotion_conditional".*vec_p3_test_neutral' "$AGG" || true)
[ "$COND" -ge 1 ] || die "expected promotion_conditional for vec_p3_test_neutral, got $COND"
ok "promotion_conditional event emitted for NEUTRAL"

UNREC=$(grep -c '"event":"promotion_verdict_unrecognized".*vec_p3_test_neutral' "$AGG" || true)
[ "$UNREC" -eq 0 ] || die "NEUTRAL must not log promotion_verdict_unrecognized (got $UNREC)"
ok "no promotion_verdict_unrecognized event"

CURR_LB_HASH=$(sha256sum "$LB" | awk '{print $1}')
[ "$PREV_LB_HASH" = "$CURR_LB_HASH" ] \
    || die "LEADERBOARD.md must not be rewritten for CONDITIONAL verdict"
ok "LEADERBOARD.md unchanged (no append for CONDITIONAL)"

# --- Test 3: --reprocess re-runs already-validated tasks ---
# Reduce AUC and Sharpe in the PROMOTION.md, re-run with --reprocess,
# confirm the leaderboard entry's composite changes.
cat > "$PROJ/data/debug/CAND_p3_test_auc_model_PROMOTION.md" <<'EOF'
## Verdict

### PROMOTED

**AUC**: 0.700
**Sharpe ratio**: 0.50
**DM p-value**: 0.099
EOF

log "--reprocess re-scores an already-validated task"
"$PY" tools/retroactive_promotion_validate.py "$PROJ" --prefix vec_p3_ --reprocess >/dev/null

NEW_COMP=$(grep 'vec_p3_test_auc_model' "$LB" \
    | awk -F'|' '{gsub(/^ +| +$/, "", $4); print $4}')
"$PY" - <<PY
v = float("${NEW_COMP}")
old = float("${COMP}")
expected = round(0.6*((0.7-0.5)*2) + 0.3*(0.5/3.0) + 0.1*(1.0 - 0.099*10), 4)
assert abs(v - expected) < 1e-6, f"new composite={v} expected={expected}"
assert v < old, f"composite should drop after lowering AUC/Sharpe; old={old}, new={v}"
PY
ok "--reprocess overwrote prior composite with corrected (lower) value"

printf '\033[32m===\033[0m PASS — v1.3.13 PHASE3-PROMOTION smoke\n'
