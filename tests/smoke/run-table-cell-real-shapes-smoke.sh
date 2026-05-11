#!/bin/bash
# tests/smoke/run-table-cell-real-shapes-smoke.sh — v1.4.1 TABLE-CELL-
# HARDENING end-to-end smoke.
#
# Two PROMOTION.md files mirror the real AI-trade Phase 3 shapes that
# v1.4.0's inline `float(raw.rstrip('%').lstrip('+'))` silently dropped:
#
#   1. Phase 3 LA table with a Unicode-minus (U+2212) Δ row beneath the
#      first data row. v1.4.0 still extracted the first data row's
#      numbers (Unicode minus is in the Δ row only), but a small
#      drift in row ordering would have hit the bug. The smoke pins
#      the contract that this exact Phase 3 LA shape produces a
#      PROMOTED verdict with sum_fixed populated and a Phase 2
#      composite > 0.3.
#
#   2. Phase 3 NN table with bold-cell data rows (`**0.78762**`).
#      Verdict is REJECTED via the v1.4.0 RESULT-OVER-STATUS path
#      (`**Result:** REJECTED` wins over `**Status**: PASS ✓`).
#      Asserts the validator emits promotion_rejected and does NOT
#      append to LEADERBOARD.md, and no promotion_verdict_unrecognized
#      fires for either file.
#
# Refs: PROMPT_v1.4.1-hotfix.md GROUP TABLE-CELL-HARDENING + smoke S1.

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
- [x] [implement] [P2] vec_p3_la_smoke_unicode — Phase 3 LA: Unicode-minus Δ row
- [x] [implement] [P2] vec_p3_nn_smoke_bold — Phase 3 NN: bold-cell data row

## Done
EOF

# Form 1 (canonical) filename — vec_-stripped. Phase 3 LA shape with
# Unicode minus in the delta row. No labelled metrics block — forces
# the table fallback to fill sum_fixed and the daily-Sharpe regex to
# capture sharpe from the table header line.
cat > "$PROJ/data/debug/CAND_p3_la_smoke_unicode_PROMOTION.md" <<'EOF'
# PROMOTION: vec_p3_la_smoke_unicode

**Status**: PASS ✓
**Result:** PROMOTED — no leakage detected

| Method | sf | Sharpe(daily) |
|--------|-----|---------------|
| Original | 692.84 | 18.33 |
| Δ | −3.32 | −0.01 |
EOF

# Form 1 (canonical) filename. Phase 3 NN shape with bold-cell data
# rows. **Result:** REJECTED is the verdict-semantic primary; the
# preceding `**Status**: PASS ✓` is overridden by RESULT-OVER-STATUS
# (v1.4.0). No labelled block — table is the only metrics source.
cat > "$PROJ/data/debug/CAND_p3_nn_smoke_bold_PROMOTION.md" <<'EOF'
# PROMOTION: vec_p3_nn_smoke_bold

**Status**: PASS ✓
**Result:** REJECTED — model below baseline

| Model | AUC | DM p | Status |
|-------|-----|------|--------|
| TFT | 0.86000 | 0.000 | PROMOTED |
| **CfC** | **0.78762** | **1.000** | **REJECTED** |
EOF

log "retroactive validation against real Phase 3 shapes"
"$PY" tools/retroactive_promotion_validate.py "$PROJ" --prefix vec_p3_ >/dev/null

# Assertion 1: LA file produces promotion_validated.
n=$(grep -c '"event":"promotion_validated".*vec_p3_la_smoke_unicode' "$AGG" || true)
[ "$n" -ge 1 ] || die "expected promotion_validated for vec_p3_la_smoke_unicode, got $n"
ok "promotion_validated event emitted for vec_p3_la_smoke_unicode"

# Assertion 2: NN file produces promotion_rejected (Result-over-Status).
n=$(grep -c '"event":"promotion_rejected".*vec_p3_nn_smoke_bold' "$AGG" || true)
[ "$n" -ge 1 ] || die "expected promotion_rejected for vec_p3_nn_smoke_bold, got $n"
ok "promotion_rejected event emitted for vec_p3_nn_smoke_bold (Result-over-Status)"

# Assertion 3: no promotion_verdict_unrecognized for either task.
for tid in vec_p3_la_smoke_unicode vec_p3_nn_smoke_bold; do
    n=$(grep -c "\"event\":\"promotion_verdict_unrecognized\".*${tid}" "$AGG" || true)
    [ "$n" -eq 0 ] || die "${tid} unexpectedly emitted promotion_verdict_unrecognized ($n)"
done
ok "no promotion_verdict_unrecognized for either task"

# Assertion 4: LEADERBOARD.md contains LA row, NOT NN row.
[ -f "$LB" ] || die "LEADERBOARD.md not created"
grep -q 'vec_p3_la_smoke_unicode' "$LB" \
    || die "LEADERBOARD.md missing vec_p3_la_smoke_unicode row"
ok "vec_p3_la_smoke_unicode row present in LEADERBOARD.md"

if grep -q 'vec_p3_nn_smoke_bold' "$LB"; then
    die "vec_p3_nn_smoke_bold MUST NOT be appended (REJECTED verdict)"
fi
ok "vec_p3_nn_smoke_bold correctly absent from LEADERBOARD.md (REJECTED)"

# Assertion 5: LA composite > 0.3 (Phase 2 formula via sum_fixed from
# the table fallback). Cell 4 in the markdown table is composite.
COMP=$(grep 'vec_p3_la_smoke_unicode' "$LB" \
    | awk -F'|' '{gsub(/^ +| +$/, "", $4); print $4}')
log "  composite cell: $COMP"
"$PY" - <<PY
v = float("${COMP}")
# Phase 2 formula with sum_fixed=692.84, regime_parity=None, max_dd=None:
#   0.5 * 0.69284 + 0.3 * 0.0 + 0.2 * 0.0 = 0.34642
expected = round(0.5 * (692.84 / 1000.0), 4)
assert abs(v - expected) < 1e-6, f"composite={v} expected={expected}"
assert v > 0.3, f"Phase 2 composite must exceed 0.3, got {v}"
PY
ok "Phase 2 composite > 0.3 from table-fallback sum_fixed"

# Assertion 6: independent confirmation — table parser extracts the
# FIRST data row, not the Δ row beneath it. The Unicode-minus row is
# tolerated (would have ValueError'd under v1.4.0 inline float() if
# row ordering had landed it first).
"$PY" - <<PY
import sys
sys.path.insert(0, "src/lib")
import promotion
from pathlib import Path

la = Path("$PROJ/data/debug/CAND_p3_la_smoke_unicode_PROMOTION.md")
m = promotion.parse_metrics(la)
assert m["sum_fixed"] == 692.84, f"LA sum_fixed={m['sum_fixed']} expected 692.84"
assert m["sharpe"] == 18.33, f"LA sharpe={m['sharpe']} expected 18.33"
v = promotion.parse_verdict(la)
assert v == "PROMOTED", f"LA verdict={v} expected PROMOTED"

nn = Path("$PROJ/data/debug/CAND_p3_nn_smoke_bold_PROMOTION.md")
v = promotion.parse_verdict(nn)
assert v == "REJECTED", f"NN verdict={v} expected REJECTED"

# Direct table-parser exercise: NN file's first data row contains
# unboldened cells; AUC + DM-p extract cleanly. The bold-cell second
# row is tolerated (would crash under v1.4.0 inline coercion if the
# table had only the bold row).
table = promotion._parse_table_metrics(nn.read_text(encoding="utf-8"))
assert table.get("auc") == 0.86, f"NN auc={table.get('auc')} expected 0.86"
PY
ok "parser extracts table values cleanly across both Phase 3 shapes"

printf '\033[32m===\033[0m PASS — v1.4.1 TABLE-CELL-REAL-SHAPES smoke\n'
