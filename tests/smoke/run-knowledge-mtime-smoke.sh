#!/bin/bash
# tests/smoke/run-knowledge-mtime-smoke.sh — v1.3.2 TRIGGER-SMOKES.
#
# Synthetic end-to-end validation of the knowledge.md update enforcement
# loop. Production has never activated this either: AI-trade run never
# completed a verdict stage that triggered the arm. First activation
# during 14-day autonomy will surface any latent bug.
#
# Lifecycle exercised:
#   1. arm flag (mirrors cycle.py:222-234 — verdict stage detection)
#   2. SessionStart injection: build_knowledge_update_block has the
#      MANDATORY block while pending
#   3. mtime DOESN'T advance → flag stays armed
#   4. mtime advances (Claude appends a lesson) → flag clears,
#      knowledge_updated_detected event logged

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
mkdir -p "$UHOME/log" "$PROJ/.cc-autopipe/memory"
echo "$PROJ" > "$UHOME/projects.list"
export CC_AUTOPIPE_USER_HOME="$UHOME"

# Seed: empty knowledge.md (mtime=now). CURRENT_TASK with vec_meta /
# stage_e_verdict. consecutive_failures=0; phase=active.
echo "" > "$PROJ/.cc-autopipe/knowledge.md"
sleep 1  # ensure later mtime advances are detectable

# Test 1: arm the flag (mirrors cycle.py post-stage_e_verdict path).
log "arm knowledge_update_pending on verdict-stage transition"
"$PY" - <<PY
import json, sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state, knowledge
from pathlib import Path

assert knowledge.is_verdict_stage("stage_e_verdict")
mtime = knowledge.get_mtime_or_zero(Path("$PROJ"))
assert mtime > 0, f"knowledge.md mtime not readable, got {mtime}"

s = state.State.fresh("p")
s.current_task = state.CurrentTask(
    id="vec_meta", stage="stage_e_verdict",
    stages_completed=["stage_a", "stage_b", "stage_e_verdict"],
)
s.knowledge_update_pending = True
s.knowledge_baseline_mtime = mtime
s.knowledge_pending_reason = "stage_e_verdict on vec_meta"
state.write("$PROJ", s)

# Mirror the cycle.py log_event call so aggregate is realistic.
state.log_event("$PROJ", "knowledge_update_required",
                stage="stage_e_verdict", task_id="vec_meta")

agg = Path("$UHOME/log/aggregate.jsonl").read_text().splitlines()
events = [json.loads(ln) for ln in agg if ln.strip()]
required = [e for e in events if e["event"] == "knowledge_update_required"]
assert len(required) == 1
print(f"flag armed; baseline mtime {mtime}")
PY
ok "knowledge_update_pending=True, baseline mtime captured, knowledge_update_required logged"

# Test 2: mandatory block emitted while pending.
log "MANDATORY KNOWLEDGE UPDATE block injected"
BLOCK=$("$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
from session_start_helper import build_knowledge_update_block
print(build_knowledge_update_block("$PROJ"))
PY
)
echo "$BLOCK" | grep -q "MANDATORY KNOWLEDGE UPDATE" \
    || die "block missing 'MANDATORY KNOWLEDGE UPDATE': $BLOCK"
ok "build_knowledge_update_block returns MANDATORY-style content"

# Test 3: cycle runs, knowledge.md NOT touched → flag stays.
log "flag persists when knowledge.md mtime unchanged"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from stop_helper import maybe_clear_knowledge_update_flag

cleared = maybe_clear_knowledge_update_flag("$PROJ")
assert cleared is False, "flag should not clear without mtime advance"
s = state.read("$PROJ")
assert s.knowledge_update_pending is True, "flag must remain armed"
print("flag persists across no-op cycle")
PY
ok "flag stays armed when knowledge.md mtime unchanged"

# Test 4: Claude appends lesson → mtime advances → flag clears.
log "knowledge.md mtime advance clears flag"
sleep 1  # guarantee mtime increases past baseline
echo "- Test lesson — 2026-05-05 — verdict stage finishing requires knowledge.md update" \
    >> "$PROJ/.cc-autopipe/knowledge.md"

"$PY" - <<PY
import json, sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state, knowledge
from pathlib import Path
from stop_helper import maybe_clear_knowledge_update_flag

s = state.read("$PROJ")
new_mtime = knowledge.get_mtime_or_zero(Path("$PROJ"))
assert new_mtime > s.knowledge_baseline_mtime, \
    f"mtime didn't advance: baseline={s.knowledge_baseline_mtime}, now={new_mtime}"

cleared = maybe_clear_knowledge_update_flag("$PROJ")
assert cleared is True, "flag should clear after mtime advances"

s2 = state.read("$PROJ")
assert s2.knowledge_update_pending is False, "flag must clear"
assert s2.knowledge_baseline_mtime is None
assert s2.knowledge_pending_reason is None

agg = Path("$UHOME/log/aggregate.jsonl").read_text().splitlines()
events = [json.loads(ln) for ln in agg if ln.strip()]
detected = [e for e in events if e["event"] == "knowledge_updated_detected"]
assert len(detected) == 1
print("flag cleared; knowledge_updated_detected logged")
PY
ok "knowledge_update_pending=False, knowledge_updated_detected event logged"

printf '\033[32m===\033[0m PASS — knowledge-mtime smoke (full lifecycle pinned)\n'
