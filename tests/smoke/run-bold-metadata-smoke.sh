#!/bin/bash
# tests/smoke/run-bold-metadata-smoke.sh — v1.3.9 GROUP BOLD-METADATA-VERDICT
# tier-4 inline `**Field**: KEYWORD` smoke.
#
# Pins three v1.3.9 invariants end-to-end against the AI-trade Phase 2
# v2.1 compact bold-metadata PROMOTION report shape:
#
#     # CAND_<name>_PROMOTION
#     **Status**: PASS ✓
#     **Note**: <short summary>
#
#   1. Bold-metadata PROMOTED → tier 4 resolves PROMOTED →
#      on_promotion_success → ablation children + leaderboard append
#      (was `promotion_verdict_unrecognized` in v1.3.8 — production
#      logged 31 such events in 12 hours, silently dropping all
#      measurement-task promotions).
#
#   2. Bold-metadata REJECTED → tier 4 resolves REJECTED → no children
#      and no leaderboard append. Symmetric REJECTED handling.
#
#   3. Bold-metadata with non-verdict value (`**Status**: in_progress`)
#      → tier 4 returns None → engine logs
#      `promotion_verdict_unrecognized` instead of misclassifying. The
#      tier must be conservative — it parses verdict-vocabulary keywords
#      only (PASS/FAIL/PROMOTED/REJECTED/etc.), not arbitrary status text.

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

# --- Test 1: Bold-metadata PROMOTED → ablation children + leaderboard --
PROJ1="$TMP/p1"
mkdir -p "$PROJ1/.cc-autopipe/memory" "$PROJ1/data/debug"
cat > "$PROJ1/backlog.md" <<'EOF'
- [x] [implement] [P1] vec_long_test_bold — bold-metadata measurement task

## Done
EOF

# Compact bold-metadata PROMOTION shape — NO `## Verdict` heading, NO
# `## Acceptance` heading, just an inline `**Status**: PASS ✓` line.
# Path convention: promotion_path("vec_long_test_bold") strips the
# "vec_long_" prefix → CAND_test_bold_PROMOTION.md.
cat > "$PROJ1/data/debug/CAND_test_bold_PROMOTION.md" <<'EOF'
# CAND_test_bold_PROMOTION
**Status**: PASS ✓
**Note**: Test bold-metadata format. Champion: M6_synth_v3 (1557).

---
*eval_test_bold.py | 23s*
EOF

log "Test 1: bold-metadata PROMOTED → ablation children + leaderboard"
"$PY" - <<PY
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, "$REPO_ROOT/src/lib")
import promotion, state
project = Path("$PROJ1")
p = promotion.promotion_path(project, "vec_long_test_bold")
verdict = promotion.parse_verdict(p)
assert verdict == "PROMOTED", f"tier-4 should resolve PROMOTED, got {verdict!r}"
# Measurement task — relaxed v2 validation (no strategy prefix).
ok_, missing = promotion.validate_v2_sections(p, task_id="vec_long_test_bold")
assert ok_, f"measurement task should pass relaxed validation, missing={missing}"
metrics = promotion.parse_metrics(p)
item = SimpleNamespace(id="vec_long_test_bold", priority=1)
promotion.on_promotion_success(project, item, metrics)
state.log_event(project, "promotion_validated", task_id="vec_long_test_bold")
PY

BODY=$(cat "$PROJ1/backlog.md")
for SUFFIX in ab_drop_top ab_loss ab_seq ab_seed ab_eth; do
    echo "$BODY" | grep -q "vec_long_test_bold_${SUFFIX}" \
        || die "missing ablation child ${SUFFIX} after bold-metadata PROMOTED"
done
ok "5 ablation children spawned for bold-metadata PROMOTED verdict"

VAL=$(grep -c '"event":"promotion_validated"' "$AGG" || true)
[ "$VAL" -eq 1 ] || die "expected 1 promotion_validated event, got $VAL"
SPAWN=$(grep -c '"event":"ablation_children_spawned"' "$AGG" || true)
[ "$SPAWN" -eq 1 ] || die "expected 1 ablation_children_spawned event, got $SPAWN"
LB=$(grep -c '"event":"leaderboard_updated"' "$AGG" || true)
[ "$LB" -eq 1 ] || die "expected 1 leaderboard_updated event, got $LB"
ok "promotion_validated + ablation_children_spawned + leaderboard_updated emitted"

# v1.3.8 regression marker: tier 4 must clear the v1.3.8
# `promotion_verdict_unrecognized` count (31 in 12h of production).
UNREC=$(grep -c '"event":"promotion_verdict_unrecognized"' "$AGG" || true)
[ "$UNREC" -eq 0 ] \
    || die "tier-4 should have resolved verdict; got $UNREC promotion_verdict_unrecognized events"
ok "no promotion_verdict_unrecognized (v1.3.8 regression cleared)"

# --- Test 2: Bold-metadata REJECTED → no children, no leaderboard ------
PROJ2="$TMP/p2"
mkdir -p "$PROJ2/.cc-autopipe/memory" "$PROJ2/data/debug"
cat > "$PROJ2/backlog.md" <<'EOF'
- [x] [implement] [P1] vec_long_test_bold_fail — bold-metadata FAIL variant

## Done
EOF

cat > "$PROJ2/data/debug/CAND_test_bold_fail_PROMOTION.md" <<'EOF'
# CAND_test_bold_fail_PROMOTION
**Status**: FAIL ✗
**Note**: Regression vs baseline; -3.2pp sum_fixed.
EOF

log "Test 2: bold-metadata REJECTED → no children, no leaderboard"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import promotion, state
project = Path("$PROJ2")
p = promotion.promotion_path(project, "vec_long_test_bold_fail")
v = promotion.parse_verdict(p)
assert v == "REJECTED", f"tier-4 should resolve REJECTED, got {v!r}"
state.log_event(project, "promotion_rejected", task_id="vec_long_test_bold_fail")
PY

BODY2=$(cat "$PROJ2/backlog.md")
echo "$BODY2" | grep -q "vec_long_test_bold_fail_ab_" \
    && die "REJECTED must NOT spawn ablation children"
ok "REJECTED did not mutate backlog (no ablation children)"

REJ=$(grep -c '"event":"promotion_rejected"' "$AGG" || true)
[ "$REJ" -ge 1 ] || die "expected ≥1 promotion_rejected event, got $REJ"
SPAWN_AFTER=$(grep -c '"event":"ablation_children_spawned"' "$AGG" || true)
[ "$SPAWN_AFTER" -eq 1 ] \
    || die "REJECTED leaked ablation_children_spawned ($SPAWN_AFTER vs 1 expected)"
ok "promotion_rejected emitted; no ablation/leaderboard side effects"

# --- Test 3: Bold-metadata in-progress → unrecognized -------------------
PROJ3="$TMP/p3"
mkdir -p "$PROJ3/.cc-autopipe/memory" "$PROJ3/data/debug"

cat > "$PROJ3/data/debug/CAND_test_bold_inprogress_PROMOTION.md" <<'EOF'
# CAND_test_bold_inprogress_PROMOTION
**Status**: in_progress
**Note**: Run still executing.
EOF

log "Test 3: bold-metadata in_progress → tier-4 returns None (conservative)"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import promotion, state
project = Path("$PROJ3")
p = promotion.promotion_path(project, "vec_long_test_bold_inprogress")
v = promotion.parse_verdict(p)
assert v is None, f"tier-4 must NOT misclassify non-verdict status, got {v!r}"
state.log_event(
    project,
    "promotion_verdict_unrecognized",
    task_id="vec_long_test_bold_inprogress",
)
PY

UNREC2=$(grep -c '"event":"promotion_verdict_unrecognized"' "$AGG" || true)
[ "$UNREC2" -ge 1 ] \
    || die "expected ≥1 promotion_verdict_unrecognized for in_progress, got $UNREC2"
ok "in_progress non-verdict → promotion_verdict_unrecognized (no misclassification)"

printf '\033[32m===\033[0m PASS — v1.3.9 bold-metadata-verdict smoke\n'
