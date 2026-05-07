#!/bin/bash
# tests/smoke/run-phase-done-reopen-smoke.sh — v1.3.6 PHASE-DONE-RECOVERY smoke.
#
# Pins the auto-resume sweep that flips `phase=done` projects back to
# `active` when their backlog gains open `[ ]` tasks. Without this,
# Roman's planned 3-4 month autonomous absence requires a manual
# `cc-autopipe update-verify --prd-complete=false` (or state.json edit)
# every time the backlog cycles drained → reopened.

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
PROJ="$TMP/proj"
mkdir -p "$UHOME/log" "$PROJ/.cc-autopipe/memory"
echo "$PROJ" > "$UHOME/projects.list"
export CC_AUTOPIPE_USER_HOME="$UHOME"
AGG="$UHOME/log/aggregate.jsonl"

# --- Test 1: still-complete done project is left alone ---
cat > "$PROJ/backlog.md" <<'EOF'
- [x] [implement] [P1] vec_long_baseline — done

## Done
EOF

log "phase=done + backlog still complete → skipped (prd_still_complete)"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from orchestrator.recovery import sweep_done_projects
s = state.State.fresh("proj")
s.phase = "done"
s.prd_complete = True
s.prd_complete_detected = True
s.last_score = 0.95
s.last_passed = True
state.write("$PROJ", s)
revived = sweep_done_projects([Path("$PROJ")])
assert revived == 0, f"still-complete should not resume, got {revived}"
assert state.read("$PROJ").phase == "done"
PY

SKIP=$(grep -c '"event":"phase_done_resume_skipped"' "$AGG" || true)
[ "$SKIP" -ge 1 ] || die "expected phase_done_resume_skipped event"
grep -q '"reason":"prd_still_complete"' "$AGG" \
    || die "skip reason should be prd_still_complete"
ok "still-complete done left untouched, skip event logged"

# --- Test 2: operator appends new task → engine flips done → active ---
log "operator appends new `[ ]` task → phase_done → active"
cat >> "$PROJ/backlog.md" <<'EOF'

- [ ] [implement] [P1] vec_new_idea — operator-added Phase 3 candidate
EOF

"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from orchestrator.recovery import sweep_done_projects
revived = sweep_done_projects([Path("$PROJ")])
assert revived == 1, f"expected 1 resume, got {revived}"
s = state.read("$PROJ")
assert s.phase == "active", f"expected active, got {s.phase!r}"
assert s.prd_complete is False
assert s.prd_complete_detected is False
assert s.current_task is None
assert s.last_score is None
assert s.last_passed is None
PY

RESUME=$(grep -c '"event":"phase_done_to_active"' "$AGG" || true)
[ "$RESUME" -eq 1 ] || die "expected 1 phase_done_to_active event, got $RESUME"
grep -q '"reason":"backlog_reopened"' "$AGG" \
    || die "resume reason should be backlog_reopened"
grep -q '"open_tasks":1' "$AGG" \
    || die "open_tasks should be 1 (the new [ ] line)"
ok "phase_done → active, fresh state, resume event logged with open_tasks=1"

# --- Test 3: second sweep on now-active project does not re-trigger ---
log "second sweep on now-active project → no re-trigger"
PRE_LINES=$(wc -l < "$AGG")
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
from orchestrator.recovery import sweep_done_projects
revived = sweep_done_projects([Path("$PROJ")])
assert revived == 0, f"second sweep on active should be no-op, got {revived}"
PY
POST_LINES=$(wc -l < "$AGG")
[ "$PRE_LINES" -eq "$POST_LINES" ] \
    || die "second sweep emitted events ($PRE_LINES → $POST_LINES) — should be silent"
ok "second sweep silent — already-active project iterated past"

# --- Test 4: enforcement state outranks reopen ---
log "phase=done + backlog reopened + meta_reflect_pending → skipped"
PROJ2="$TMP/proj2"
mkdir -p "$PROJ2/.cc-autopipe/memory"
cat > "$PROJ2/backlog.md" <<'EOF'
- [ ] [implement] [P1] vec_new — operator-added
EOF
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from orchestrator.recovery import sweep_done_projects
s = state.State.fresh("proj2")
s.phase = "done"
s.meta_reflect_pending = True
state.write("$PROJ2", s)
revived = sweep_done_projects([Path("$PROJ2")])
assert revived == 0, f"meta_reflect should block, got {revived}"
assert state.read("$PROJ2").phase == "done"
PY
grep -q '"reason":"meta_reflect_in_progress"' "$AGG" \
    || die "skip reason should mention meta_reflect_in_progress"
ok "enforcement loop outranks reopen (meta_reflect_pending blocks)"

printf '\033[32m===\033[0m PASS — v1.3.6 phase-done-reopen smoke\n'
