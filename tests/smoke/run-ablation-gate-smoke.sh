#!/bin/bash
# tests/smoke/run-ablation-gate-smoke.sh — v1.5.1 ABLATION-VERDICT-GATE.
#
# Synthetic end-to-end: drop a CAND_*_PROMOTION.md with verdict=NEUTRAL,
# drive parse_metrics + on_promotion_success (the same path cycle.py
# takes), and verify NO ablation children are appended to backlog.md.
# Then repeat with verdict=PROMOTED and verify exactly 5 children land.
#
# This is the post-mortem regression guard for AI-trade Phase 4
# (2026-05-11/12): hundreds of legitimate NEUTRAL verdicts each spawned
# 5 children → backlog grew to ~38K orphan `_ab_` entries → engine
# burned --max-turns reopening stale work.
#
# Refs: PROMPT_v1.5.1.md GROUP ABLATION-VERDICT-GATE.

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

# ----- Step 1: NEUTRAL verdict → ablation SKIPPED -----
PROJ_N="$TMP/p_neutral"
mkdir -p "$PROJ_N/.cc-autopipe/memory" "$PROJ_N/data/debug"
echo "$PROJ_N" > "$UHOME/projects.list"

cat > "$PROJ_N/backlog.md" <<'EOF'
- [x] [implement] [P1] vec_long_neutral_v1 — model

## Done
EOF

# A v1.4.0-shaped PROMOTION report carrying the labelled metrics block
# AND the v2.0 sections required by validate_v2_sections. The labelled
# `**verdict**: NEUTRAL` is the authoritative source; parse_verdict
# canonicalises NEUTRAL → CONDITIONAL via CANONICAL_MAP, and
# parse_metrics propagates that into metrics["verdict"]. The gate sees
# verdict != "PROMOTED" → ablation skipped.
cat > "$PROJ_N/data/debug/CAND_neutral_v1_PROMOTION.md" <<'EOF'
# CAND vec_long_neutral_v1 — PROMOTION

## Verdict

NEUTRAL — no exploitable edge in held-out window.

## Metrics for leaderboard
- **verdict**: NEUTRAL
- **sum_fixed**: 0.0
- **regime_parity**: 0.0
- **max_dd**: -3.2
- **dm_p_value**: 0.50
- **dsr**: 0.0

## Acceptance
sum_fixed: 0.0%
regime_parity: 0.0
max_DD: -3.20%
DM_p_value: 0.50
DSR: 0.0

## Long-only verification
no shorts.
## Regime-stratified PnL
no edge detected.
## Statistical significance
DM p=0.50.
## Walk-forward stability
unstable.
## No-lookahead audit
clean.
EOF

log "NEUTRAL verdict → on_promotion_success skips ablation spawn"
"$PY" - <<PY
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, "$REPO_ROOT/src/lib")
import promotion
project = Path("$PROJ_N")
p = promotion.promotion_path(project, "vec_long_neutral_v1")
metrics = promotion.parse_metrics(p)
assert metrics["verdict"] == "CONDITIONAL", \
    f"NEUTRAL should canonicalise to CONDITIONAL via CANONICAL_MAP, got {metrics['verdict']!r}"
item = SimpleNamespace(id="vec_long_neutral_v1", priority=1)
promotion.on_promotion_success(project, item, metrics)
PY

BACKLOG_N=$(cat "$PROJ_N/backlog.md")
if echo "$BACKLOG_N" | grep -q '_ab_'; then
    die "NEUTRAL verdict mutated backlog: $(echo "$BACKLOG_N" | grep '_ab_')"
fi
ok "NEUTRAL: backlog UNCHANGED (no _ab_ entries)"

AGG="$UHOME/log/aggregate.jsonl"
[ -f "$AGG" ] || die "aggregate.jsonl missing"

SKIPPED_COUNT=$(grep -c '"event":"ablation_skipped_non_promoted"' "$AGG" || true)
[ "$SKIPPED_COUNT" -eq 1 ] \
    || die "expected 1 ablation_skipped_non_promoted event, got $SKIPPED_COUNT"
ok "NEUTRAL: ablation_skipped_non_promoted event emitted"

# The skipped event must carry verdict=CONDITIONAL (NEUTRAL canonicalised).
grep '"event":"ablation_skipped_non_promoted"' "$AGG" \
    | grep -q '"verdict":"CONDITIONAL"' \
    || die "skipped event missing verdict=CONDITIONAL payload"
ok "NEUTRAL: skipped event payload verdict=CONDITIONAL"

# No spawned event for the NEUTRAL path.
SPAWN_COUNT_N=$(grep -c '"event":"ablation_children_spawned"' "$AGG" || true)
[ "$SPAWN_COUNT_N" -eq 0 ] \
    || die "NEUTRAL must not spawn; got $SPAWN_COUNT_N spawned events"
ok "NEUTRAL: no ablation_children_spawned events"

# ----- Step 2: PROMOTED verdict → exactly 5 ablation children -----
PROJ_P="$TMP/p_promoted"
mkdir -p "$PROJ_P/.cc-autopipe/memory" "$PROJ_P/data/debug"
# Append second project to projects.list so the same aggregate.jsonl
# captures both lifecycles under one CC_AUTOPIPE_USER_HOME.
echo "$PROJ_P" >> "$UHOME/projects.list"

cat > "$PROJ_P/backlog.md" <<'EOF'
- [x] [implement] [P1] vec_long_promoted_v1 — model

## Done
EOF

cat > "$PROJ_P/data/debug/CAND_promoted_v1_PROMOTION.md" <<'EOF'
# CAND vec_long_promoted_v1 — PROMOTION

## Verdict

PROMOTED — robust edge across regimes.

## Metrics for leaderboard
- **verdict**: PROMOTED
- **sum_fixed**: 245.5
- **regime_parity**: 0.18
- **max_dd**: -8.2
- **dm_p_value**: 0.003
- **dsr**: 1.12

## Acceptance
sum_fixed: +245.5%
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

log "PROMOTED verdict → on_promotion_success spawns 5 ablation children"
"$PY" - <<PY
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, "$REPO_ROOT/src/lib")
import promotion
project = Path("$PROJ_P")
p = promotion.promotion_path(project, "vec_long_promoted_v1")
metrics = promotion.parse_metrics(p)
assert metrics["verdict"] == "PROMOTED", \
    f"labelled-block PROMOTED should propagate verdict; got {metrics['verdict']!r}"
item = SimpleNamespace(id="vec_long_promoted_v1", priority=1)
promotion.on_promotion_success(project, item, metrics)
PY

BACKLOG_P=$(cat "$PROJ_P/backlog.md")
for SUFFIX in ab_drop_top ab_loss ab_seq ab_seed ab_eth; do
    echo "$BACKLOG_P" | grep -q "vec_long_promoted_v1_${SUFFIX}" \
        || die "missing PROMOTED ablation child ${SUFFIX}"
done
AB_COUNT=$(echo "$BACKLOG_P" | grep -c '_ab_' || true)
[ "$AB_COUNT" -eq 5 ] \
    || die "expected exactly 5 _ab_ children, got $AB_COUNT"
ok "PROMOTED: exactly 5 ablation children appended"

SPAWN_COUNT=$(grep -c '"event":"ablation_children_spawned"' "$AGG" || true)
[ "$SPAWN_COUNT" -eq 1 ] \
    || die "expected 1 ablation_children_spawned event, got $SPAWN_COUNT"
ok "PROMOTED: ablation_children_spawned event emitted"

# After the PROMOTED run, skipped count must still be 1 (no NEUTRAL re-fire).
SKIPPED_COUNT_P=$(grep -c '"event":"ablation_skipped_non_promoted"' "$AGG" || true)
[ "$SKIPPED_COUNT_P" -eq 1 ] \
    || die "skipped event count must stay 1 across PROMOTED run, got $SKIPPED_COUNT_P"
ok "skipped event count unchanged after PROMOTED run"

printf '\033[32m===\033[0m PASS — v1.5.1 ABLATION-VERDICT-GATE smoke\n'
