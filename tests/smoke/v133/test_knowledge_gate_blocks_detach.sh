#!/bin/bash
# tests/smoke/v133/test_knowledge_gate_blocks_detach.sh — v1.3.3 N P2.
#
# End-to-end real-CLI verification of the knowledge enforcement gate:
#   1. Project with a recorded verdict + missing/stale knowledge.md
#   2. cc-autopipe-detach exits 3, stderr says BLOCKED, state unmutated
#   3. Append knowledge entry, retry — exit 0, phase=detached,
#      last_verdict_event_at reset to null

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }
die() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

UHOME="$TMP/uhome"
PROJ="$TMP/proj"
mkdir -p "$UHOME/log" "$PROJ"

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_USER_HOME="$UHOME"
export CC_AUTOPIPE_QUOTA_DISABLED=1

log 'cc-autopipe init'
bash "$REPO_ROOT/src/helpers/cc-autopipe" init "$PROJ" >/dev/null
ok 'project initialised'

# init seeded knowledge.md — for the "missing" case, remove it.
rm -f "$PROJ/.cc-autopipe/knowledge.md"

# Stamp a verdict event 60 seconds in the past.
VERDICT_TS=$(date -u -d "60 seconds ago" +"%Y-%m-%dT%H:%M:%SZ")
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
s = state.read("$PROJ")
s.last_verdict_event_at = "$VERDICT_TS"
s.last_verdict_task_id = "vec_smoke"
state.write("$PROJ", s)
PY
ok "verdict stamped at $VERDICT_TS"

# 2. cc-autopipe-detach must exit 3 with BLOCKED.
log 'cc-autopipe-detach (knowledge.md missing) — expect exit 3'
set +e
OUT=$(bash "$REPO_ROOT/src/helpers/cc-autopipe-detach" \
    --reason gate-test \
    --check-cmd "true" \
    --project "$PROJ" 2>&1)
RC=$?
set -e
[ "$RC" -eq 3 ] || die "expected rc=3, got rc=$RC; out:\n$OUT"
echo "$OUT" | grep -q "BLOCKED" || die "stderr missing BLOCKED; out:\n$OUT"
ok 'rc=3 BLOCKED on missing knowledge.md'

# State must NOT have flipped to detached.
PHASE=$("$PY" -c "import json; print(json.load(open('$PROJ/.cc-autopipe/state.json'))['phase'])")
[ "$PHASE" != "detached" ] || die "state mutated despite gate failure; phase=$PHASE"
ok "state intact (phase=$PHASE)"

# 3. Append a knowledge entry — mtime > verdict — retry, expect rc=0.
log 'append knowledge.md entry'
cat > "$PROJ/.cc-autopipe/knowledge.md" <<'EOF'
# Project Knowledge

## 2026-05-06 vec_smoke — REJECT
- Lesson: gate test entry to advance mtime past verdict timestamp.
EOF
ok 'knowledge.md appended'

set +e
OUT=$(bash "$REPO_ROOT/src/helpers/cc-autopipe-detach" \
    --reason gate-test \
    --check-cmd "true" \
    --project "$PROJ" 2>&1)
RC=$?
set -e
[ "$RC" -eq 0 ] || die "expected rc=0 after append, got rc=$RC; out:\n$OUT"
ok 'rc=0 after knowledge update'

PHASE=$("$PY" -c "import json; print(json.load(open('$PROJ/.cc-autopipe/state.json'))['phase'])")
[ "$PHASE" = "detached" ] || die "expected phase=detached, got $PHASE"
ok "phase=detached"

LV=$("$PY" -c "import json; s=json.load(open('$PROJ/.cc-autopipe/state.json')); print(s.get('last_verdict_event_at'))")
[ "$LV" = "None" ] || die "last_verdict_event_at not reset; got=$LV"
ok 'last_verdict_event_at reset to null after successful detach'

# 4. Stale-but-present case: knowledge.md older than verdict → exit 3.
log 'stale knowledge.md older than fresh verdict → exit 3'
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
s = state.read("$PROJ")
s.last_verdict_event_at = "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
s.last_verdict_task_id = "vec_stale"
s.phase = "active"
s.detached = None
state.write("$PROJ", s)
PY
# Backdate knowledge.md to 1 hour ago.
touch -d "1 hour ago" "$PROJ/.cc-autopipe/knowledge.md"

set +e
OUT=$(bash "$REPO_ROOT/src/helpers/cc-autopipe-detach" \
    --reason gate-test \
    --check-cmd "true" \
    --project "$PROJ" 2>&1)
RC=$?
set -e
[ "$RC" -eq 3 ] || die "expected rc=3 on stale knowledge, got rc=$RC; out:\n$OUT"
echo "$OUT" | grep -q "older than last verdict" || die "stderr missing 'older than last verdict'"
ok 'stale knowledge.md → exit 3 with explicit reason'

printf '\033[32m===\033[0m PASS — v1.3.3 P2 knowledge gate blocks detach\n'
