#!/bin/bash
# tests/smoke/run-multi-prefix-filename-smoke.sh — v1.4.0 MULTI-PREFIX-
# STRIP end-to-end smoke.
#
# Three filename conventions co-exist in AI-trade Phase 3:
#   - CAND_p3_la_*    (canonical, vec_-stripped)
#   - CAND_meta_*     (phase-stripped, no `p3_` prefix)
#   - CAND_lv_* / CAND_nn_* (also phase-stripped)
# Engine probes a candidate chain so all three resolve.
#
# Two task IDs are validated in the same run:
#   - vec_p3_la_test_two   → file at CAND_p3_la_test_two_*  (Form 1)
#   - vec_p3_meta_test_one → file at CAND_meta_test_one_*   (Form 2)
# Both must produce promotion_validated events; neither may emit
# promotion_verdict_missing.
#
# Refs: PROMPT_v1.4.0.md GROUP MULTI-PREFIX-STRIP + smoke S2.

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
- [x] [implement] [P2] vec_p3_meta_test_one — Phase 3 meta task (no p3_ in filename)
- [x] [implement] [P2] vec_p3_la_test_two — Phase 3 LA task (canonical filename)

## Done
EOF

# Form 2 — phase-stripped filename (no `p3_` prefix). This is the form
# 6+ AI-trade meta tasks were emitting under v1.3.13, all silently
# dropped as promotion_verdict_missing.
cat > "$PROJ/data/debug/CAND_meta_test_one_PROMOTION.md" <<'EOF'
# PROMOTION: vec_p3_meta_test_one

## Metrics for leaderboard
- **verdict**: PROMOTED
- **auc**: 0.81
- **sharpe**: 1.2
- **dm_p_value**: 0.04
EOF

# Form 1 — canonical (vec_-stripped) filename. Same convention used by
# Phase 3 LA tasks in production.
cat > "$PROJ/data/debug/CAND_p3_la_test_two_PROMOTION.md" <<'EOF'
# PROMOTION: vec_p3_la_test_two

## Metrics for leaderboard
- **verdict**: PROMOTED
- **auc**: 0.78
- **sharpe**: 1.05
- **dm_p_value**: 0.07
EOF

log "multi-prefix retroactive validation"
"$PY" tools/retroactive_promotion_validate.py "$PROJ" --prefix vec_p3_ >/dev/null

for tid in vec_p3_meta_test_one vec_p3_la_test_two; do
    n=$(grep -c "\"event\":\"promotion_validated\".*${tid}" "$AGG" || true)
    [ "$n" -ge 1 ] || die "expected promotion_validated for ${tid}, got $n"
    ok "promotion_validated event emitted for ${tid}"

    miss=$(grep -c "\"event\":\"promotion_verdict_missing\".*${tid}" "$AGG" || true)
    [ "$miss" -eq 0 ] || die "${tid} must NOT emit promotion_verdict_missing (got $miss)"
done
ok "no promotion_verdict_missing for either task"

[ -f "$LB" ] || die "LEADERBOARD.md not created"
for tid in vec_p3_meta_test_one vec_p3_la_test_two; do
    grep -q "${tid}" "$LB" || die "LEADERBOARD.md missing ${tid}"
done
ok "both rows present in LEADERBOARD.md"

# Independent confirmation via promotion_path() — Form 2 file should
# resolve via the candidate probe.
"$PY" - <<PY
import sys
sys.path.insert(0, "src/lib")
import promotion
from pathlib import Path
proj = Path("$PROJ")
p1 = promotion.promotion_path(proj, "vec_p3_meta_test_one")
assert p1.name == "CAND_meta_test_one_PROMOTION.md", f"unexpected: {p1.name}"
assert p1.exists(), f"resolved path does not exist: {p1}"
p2 = promotion.promotion_path(proj, "vec_p3_la_test_two")
assert p2.name == "CAND_p3_la_test_two_PROMOTION.md", f"unexpected: {p2.name}"
assert p2.exists(), f"resolved path does not exist: {p2}"
PY
ok "promotion_path() resolves both Form 1 and Form 2 filenames"

printf '\033[32m===\033[0m PASS — v1.4.0 MULTI-PREFIX-FILENAME smoke\n'
