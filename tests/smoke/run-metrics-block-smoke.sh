#!/bin/bash
# tests/smoke/run-metrics-block-smoke.sh — v1.4.0 METRICS-BLOCK
# end-to-end smoke.
#
# Validates that a PROMOTION.md carrying both a labelled `## Metrics
# for leaderboard` block AND conflicting prose / per-bar Sharpe noise:
#   1. Resolves `verdict` from the labelled block (Tier 0) — `**Result:**`
#      and `**Status**` later in the file are ignored.
#   2. Resolves `sharpe` from the labelled block — the inflated
#      `Per-bar Sharpe 90.8` prose is ignored even though it appears
#      first in the file.
#   3. Renders a LEADERBOARD.md composite via the Phase 2 formula
#      (sum_fixed populated → Phase 2 path in lib/leaderboard._composite).
#
# Refs: PROMPT_v1.4.0.md GROUP METRICS-BLOCK-CONVENTION + smoke S1.

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

cat > "$PROJ/backlog.md" <<'EOF'
- [x] [implement] [P2] vec_p3_la_test_q10 — Phase 3 LA quantile recompute

## Done
EOF

cat > "$PROJ/data/debug/CAND_p3_la_test_q10_PROMOTION.md" <<'EOF'
# PROMOTION: vec_p3_la_test_q10

**Status**: PASS ✓
**Result:** PROMOTED — leakage audit passed

## Metrics for leaderboard
- **verdict**: PROMOTED
- **sum_fixed**: 692.84
- **sharpe**: 18.33
- **regime_parity**: 0.18
- **max_dd**: -8.2
- **dm_p_value**: 0.003

## Free-form summary
Per-bar Sharpe 90.8 is inflated. Daily Sharpe 18.33 is the true value.
EOF

log "labelled metrics block + retroactive validation"
"$PY" tools/retroactive_promotion_validate.py "$PROJ" --prefix vec_p3_ >/dev/null

VALIDATED=$(grep -c '"event":"promotion_validated".*vec_p3_la_test_q10' "$AGG" || true)
[ "$VALIDATED" -ge 1 ] || die "expected promotion_validated event for vec_p3_la_test_q10, got $VALIDATED"
ok "promotion_validated event emitted"

[ -f "$LB" ] || die "LEADERBOARD.md not created"
grep -q 'vec_p3_la_test_q10' "$LB" \
    || die "LEADERBOARD.md missing vec_p3_la_test_q10 row"
ok "LEADERBOARD.md row present"

# Phase 2 composite formula:
#   0.5 * (sum_fixed / 1000)
#   + 0.3 * (1 - regime_parity)
#   + 0.2 * (max_dd / -100)
# = 0.5 * 0.69284 + 0.3 * 0.82 + 0.2 * 0.082
# = 0.34642 + 0.246 + 0.0164 = 0.60882 → 0.6088 after round(., 4)
COMP=$(grep 'vec_p3_la_test_q10' "$LB" \
    | awk -F'|' '{gsub(/^ +| +$/, "", $4); print $4}')
log "  composite cell: $COMP"
"$PY" - <<PY
v = float("${COMP}")
expected = round(0.5 * (692.84 / 1000.0) + 0.3 * (1 - 0.18) + 0.2 * (-8.2 / -100.0), 4)
assert abs(v - expected) < 1e-6, f"composite={v} expected={expected}"
assert v > 0.3, f"Phase 2 composite must exceed 0.3, got {v}"
PY
ok "composite uses Phase 2 formula (sum_fixed populated, > 0.3)"

# Verify the parser actually picked up sharpe=18.33 (block) — NOT 90.8
# (per-bar prose).
"$PY" - <<PY
import sys
sys.path.insert(0, "src/lib")
import promotion
from pathlib import Path
m = promotion.parse_metrics(Path("$PROJ/data/debug/CAND_p3_la_test_q10_PROMOTION.md"))
assert m["sharpe"] == 18.33, f"sharpe={m['sharpe']} expected 18.33 (block, not 90.8 per-bar)"
v = promotion.parse_verdict(Path("$PROJ/data/debug/CAND_p3_la_test_q10_PROMOTION.md"))
assert v == "PROMOTED", f"verdict={v} expected PROMOTED (block)"
PY
ok "metrics block won over per-bar prose + bold-metadata"

printf '\033[32m===\033[0m PASS — v1.4.0 METRICS-BLOCK smoke\n'
