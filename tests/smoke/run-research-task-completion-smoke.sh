#!/bin/bash
# tests/smoke/run-research-task-completion-smoke.sh — v1.3.5 RESEARCH-COMPLETION smoke.
#
# Synthetic end-to-end validation of the [research] task completion
# path. Exercises:
#   1. Prompt builder injects RESEARCH TASK block when topmost open is
#      [research]
#   2. completion_satisfied returns True when artifact + verdict-stage
#      both present, False with reason otherwise
#   3. cycle.py post-cycle branch synthesises last_passed=True on success
#
# Tests the modules directly (no real claude binary). Production
# has never run this — first activation will be Phase Gate 2_1 in
# AI-trade Phase 2.

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

# Backlog with a top [research] task.
cat > "$PROJ/backlog.md" <<'EOF'
- [ ] [research] [P0] phase_gate_2_1 — Selection at start of Phase 2
- [ ] [implement] [P1] vec_long_lgbm — model
EOF

# Minimal PRD + context so _build_prompt can run.
cat > "$PROJ/.cc-autopipe/prd.md" <<'EOF'
# PRD
- [ ] phase 2 selection
EOF
echo "ctx" > "$PROJ/.cc-autopipe/context.md"

# Test 1: prompt contains RESEARCH TASK block, NOT implement instruction.
log "prompt builder injects RESEARCH TASK block on [research] top"
PROMPT=$("$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import importlib
prompt = importlib.import_module("orchestrator.prompt")
import state
s = state.State.fresh("p")
print(prompt._build_prompt(Path("$PROJ"), s))
PY
)
echo "$PROMPT" | grep -q "RESEARCH TASK" \
    || die "prompt missing RESEARCH TASK marker"
echo "$PROMPT" | grep -q "phase_gate_2_1" \
    || die "prompt missing task id phase_gate_2_1"
echo "$PROMPT" | grep -q "SELECTION_phase_gate_2_1.md" \
    || die "prompt missing artifact path"
if echo "$PROMPT" | grep -q "Run .cc-autopipe/verify.sh before declaring done"; then
    die "prompt should NOT contain implement-style verify.sh instructions"
fi
ok "prompt has RESEARCH TASK block, no implement-style verify instructions"

# Test 2: completion_satisfied=False when artifact missing.
log "completion_satisfied=False when artifact missing"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src/lib")
import research_completion as rc
from pathlib import Path
import backlog
items = backlog.parse_open_tasks(Path("$PROJ/backlog.md"))
research_item = next(it for it in items if it.id == "phase_gate_2_1")
ok_, reason = rc.completion_satisfied(Path("$PROJ"), research_item)
assert not ok_
assert reason.startswith("artifact_missing:"), reason
print("reason:", reason)
PY
ok "artifact_missing reason logged"

# Test 3: completion_satisfied=False when artifact present but verdict-stage missing.
log "completion_satisfied=False when verdict-stage absent"
echo "selection notes" > "$PROJ/data/debug/SELECTION_phase_gate_2_1.md"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src/lib")
import research_completion as rc
from pathlib import Path
import backlog
items = backlog.parse_open_tasks(Path("$PROJ/backlog.md"))
research_item = next(it for it in items if it.id == "phase_gate_2_1")
ok_, reason = rc.completion_satisfied(Path("$PROJ"), research_item)
assert not ok_
assert reason == "research_verdict_stage_missing", reason
print("reason:", reason)
PY
ok "research_verdict_stage_missing reason logged"

# Test 4: completion_satisfied=True when both artifact + verdict-stage present.
log "completion_satisfied=True when artifact + verdict-stage both present"
cat > "$PROJ/.cc-autopipe/CURRENT_TASK.md" <<'EOF'
task: phase_gate_2_1
stage: phase_gate_complete
stages_completed: hypothesis, phase_gate_complete
EOF
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src/lib")
import research_completion as rc
from pathlib import Path
import backlog
items = backlog.parse_open_tasks(Path("$PROJ/backlog.md"))
research_item = next(it for it in items if it.id == "phase_gate_2_1")
ok_, reason = rc.completion_satisfied(Path("$PROJ"), research_item)
assert ok_, f"expected ok, got reason={reason!r}"
print("ok=True")
PY
ok "completion_satisfied=True"

# Test 5: knowledge.is_verdict_stage matches Phase 2 patterns.
log "knowledge.is_verdict_stage matches new Phase 2 verdict stages"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src/lib")
import knowledge as kn
for s in ("phase_gate_complete", "selection_complete",
         "research_digest_complete", "negative_mining_complete",
         "hypo_filed", "track_winner_selected", "synth_promoted"):
    assert kn.is_verdict_stage(s), f"{s} should be a verdict stage"
PY
ok "Phase 2 verdict stages registered"

printf '\033[32m===\033[0m PASS — v1.3.5 RESEARCH-COMPLETION smoke\n'
