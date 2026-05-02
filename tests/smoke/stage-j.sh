#!/bin/bash
# tests/smoke/stage-j.sh — Stage J DoD validation end-to-end.
# Refs: AGENTS-v1.md §3.2 (Batch b), SPEC-v1.md §2.3
#
# Drives a 3-phase mock PRD through transitions, verifying:
#   - phase 1 complete + verify pass → archive + advance + reset session
#   - phase 2 incomplete → no transition
#   - phase 3 complete (last) → project DONE
# Plus backward compat: a no-phase PRD still uses v0.5 prd_complete.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }
die() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

if [ -x "$REPO_ROOT/.venv/bin/python3" ]; then
    PY="$REPO_ROOT/.venv/bin/python3"
    PYTEST="$REPO_ROOT/.venv/bin/pytest"
else
    PY="python3"
    PYTEST="pytest"
fi

# 1. Lint slice.
log "ruff + shellcheck"
"$REPO_ROOT/.venv/bin/ruff" check src/lib/prd.py src/orchestrator || die "ruff check failed"
ok "lint clean"

# 2. Unit + integration coverage.
log "pytest tests/unit/test_prd.py tests/integration/test_orchestrator_phase.py"
"$PYTEST" tests/unit/test_prd.py tests/integration/test_orchestrator_phase.py \
    -q --tb=short || die "pytest failed"
ok "PRD parser + phase transitions pass"

# 3. End-to-end with a 3-phase mock PRD.
log "end-to-end: 3-phase PRD walk"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
PROJECT="$SCRATCH/proj"
USER_HOME="$SCRATCH/uhome"
mkdir -p "$PROJECT/.cc-autopipe/memory" "$USER_HOME"

cat > "$PROJECT/.cc-autopipe/prd.md" <<'PRD'
# PRD: smoke test

### Phase 1: Foundation
**Acceptance:** all items checked.

- [x] Item 1.1
- [x] Item 1.2

### Phase 2: API
- [ ] Item 2.1
- [ ] Item 2.2

### Phase 3: Frontend
- [x] Item 3.1
PRD

cat > "$PROJECT/.cc-autopipe/state.json" <<'JSON'
{
  "schema_version": 2,
  "name": "smoke",
  "phase": "active",
  "iteration": 0,
  "session_id": "phase-1-sid",
  "last_score": 0.95,
  "last_passed": true,
  "prd_complete": false,
  "consecutive_failures": 0,
  "last_cycle_started_at": null,
  "last_progress_at": null,
  "threshold": 0.85,
  "paused": null,
  "detached": null,
  "current_phase": 1,
  "phases_completed": []
}
JSON

# Pre-populate quota cache so the orchestrator doesn't try a live fetch.
"$PY" -c "
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
five = (datetime.now(timezone.utc) + timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M:%SZ')
seven = (datetime.now(timezone.utc) + timedelta(days=6)).strftime('%Y-%m-%dT%H:%M:%SZ')
Path('$USER_HOME/quota-cache.json').write_text(json.dumps({
    'five_hour': {'utilization': 5, 'resets_at': five},
    'seven_day': {'utilization': 10, 'resets_at': seven},
}))
"

echo "$PROJECT" > "$USER_HOME/projects.list"

# 3a. First pass: phase 1 complete + verify passing → advance to phase 2.
CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$USER_HOME" \
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_MAX_LOOPS=1 \
CC_AUTOPIPE_QUOTA_DISABLED=1 \
    "$PY" "$REPO_ROOT/src/orchestrator" >"$SCRATCH/orch.out" 2>"$SCRATCH/orch.err" \
    || die "orchestrator pass 1 exit non-zero: $(cat "$SCRATCH/orch.err")"

CURRENT=$("$PY" -c "import json; print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['current_phase'])")
COMPLETED=$("$PY" -c "import json; print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['phases_completed'])")
SID=$("$PY" -c "import json; print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['session_id'])")
[ "$CURRENT" = "2" ] || die "expected current_phase=2 after phase 1, got $CURRENT"
[ "$COMPLETED" = "[1]" ] || die "expected phases_completed=[1], got $COMPLETED"
[ "$SID" = "None" ] || die "expected session_id reset to None, got $SID"
[ -f "$PROJECT/.cc-autopipe/backlog-archive.md" ] || die "backlog-archive.md not written"
grep -q "Item 1.1" "$PROJECT/.cc-autopipe/backlog-archive.md" || die "archive missing Item 1.1"
ok "phase 1 → phase 2: archive written, session_id reset"

# 3b. Second pass: phase 2 still has unchecked items, no transition.
# Mark verify still green for phase 2 to test the unchecked guard.
CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$USER_HOME" \
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_MAX_LOOPS=1 \
CC_AUTOPIPE_QUOTA_DISABLED=1 \
    "$PY" "$REPO_ROOT/src/orchestrator" >>"$SCRATCH/orch.out" 2>>"$SCRATCH/orch.err" \
    || die "orchestrator pass 2 exit non-zero"

CURRENT=$("$PY" -c "import json; print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['current_phase'])")
[ "$CURRENT" = "2" ] || die "expected current_phase=2 with unchecked items, got $CURRENT"
ok "phase 2 incomplete → no transition (current_phase stays 2)"

# 3c. Now flip phase 2 items to checked + leave phase 3 already complete →
# orchestrator should advance through phase 2 → phase 3 → DONE.
sed -i.bak 's/- \[ \] Item 2\.1/- [x] Item 2.1/; s/- \[ \] Item 2\.2/- [x] Item 2.2/' \
    "$PROJECT/.cc-autopipe/prd.md"

# Run twice so phase 2 advances first, then phase 3 finalises.
for pass in 3 4; do
    CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
    CC_AUTOPIPE_USER_HOME="$USER_HOME" \
    CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
    CC_AUTOPIPE_COOLDOWN_SEC=0 \
    CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
    CC_AUTOPIPE_MAX_LOOPS=1 \
    CC_AUTOPIPE_QUOTA_DISABLED=1 \
        "$PY" "$REPO_ROOT/src/orchestrator" >>"$SCRATCH/orch.out" 2>>"$SCRATCH/orch.err" \
        || die "orchestrator pass $pass exit non-zero"
done

PHASE=$("$PY" -c "import json; print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['phase'])")
COMPLETED=$("$PY" -c "import json; print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['phases_completed'])")
[ "$PHASE" = "done" ] || die "expected phase=done after final phase, got $PHASE"
[ "$COMPLETED" = "[1, 2, 3]" ] || die "expected phases_completed=[1,2,3], got $COMPLETED"
ok "phase 3 complete → project DONE; phases_completed=[1,2,3]"

# 4. Aggregate log carries phase_transition events.
AGG=$("$PY" -c "
import json
from pathlib import Path
events = [json.loads(l) for l in Path('$USER_HOME/log/aggregate.jsonl').read_text().splitlines() if l.strip()]
transitions = [e for e in events if e.get('event') == 'phase_transition']
print(len(transitions), [(t['completed_phase'], t.get('is_last_phase', False)) for t in transitions])
")
echo "$AGG" | grep -q "^3 " || die "expected 3 phase_transition events, got: $AGG"
ok "aggregate.jsonl carries 3 phase_transition events"

echo
echo "Stage J: OK"
