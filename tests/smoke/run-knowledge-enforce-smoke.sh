#!/bin/bash
# tests/smoke/run-knowledge-enforce-smoke.sh — v1.3 GROUP I end-to-end.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

UHOME="$TMP/uhome"
PROJ="$TMP/p"
mkdir -p "$UHOME/log" "$PROJ/.cc-autopipe/memory"
echo "$PROJ" > "$UHOME/projects.list"
export CC_AUTOPIPE_USER_HOME="$UHOME"

# Test 1: arm flag on verdict stage.
log "arm knowledge-update flag on verdict"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
import knowledge

assert knowledge.is_verdict_stage("stage_e_verdict")
assert knowledge.is_verdict_stage("PROMOTED")
assert not knowledge.is_verdict_stage("training")

s = state.State.fresh("p")
s.knowledge_update_pending = True
s.knowledge_baseline_mtime = 0.0
s.knowledge_pending_reason = "stage_e_verdict on vec_meta"
state.write("$PROJ", s)
PY
ok "verdict-stage detection + flag armed"

# Test 2: mandatory block injected.
log "mandatory block emitted while pending"
BLOCK=$("$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import session_start_helper
print(session_start_helper.build_knowledge_update_block("$PROJ"))
PY
)
echo "$BLOCK" | grep -q "MANDATORY KNOWLEDGE UPDATE" || die "block missing"
ok "mandatory block injected"

# Test 3: writing knowledge.md clears the flag (after Stop hook).
log "knowledge.md update clears flag"
echo "- new lesson - 2026-05-04" > "$PROJ/.cc-autopipe/knowledge.md"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import stop_helper
import state
cleared = stop_helper.maybe_clear_knowledge_update_flag("$PROJ")
assert cleared is True
s = state.read("$PROJ")
assert s.knowledge_update_pending is False
PY
ok "flag cleared after knowledge.md mtime advanced"

printf '\033[32m===\033[0m PASS — knowledge enforce smoke\n'
