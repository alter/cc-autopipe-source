#!/bin/bash
# tests/smoke/run-research-plan-smoke.sh — v1.3 GROUP D end-to-end.

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

cat > "$PROJ/backlog.md" <<'EOF'
- [x] [P0] vec_meta — done
- [~] [P1] vec_tbm — in progress
EOF

# Test 1: PRD complete detection.
log "PRD complete detection"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
from orchestrator.research import detect_prd_complete
assert detect_prd_complete("$PROJ") is True
print("prd_complete=True OK")
PY
ok "detect_prd_complete returns True with no [ ] tasks"

# Test 2: research mode activates with quota gate ok.
log "activate research mode"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from orchestrator.research import activate_research_mode
import orchestrator.research as r
r._quota_seven_day_pct = lambda: 0.30  # quota fine

s = state.State.fresh("p")
state.write("$PROJ", s)
result = activate_research_mode("$PROJ", s)
assert result == "active", f"got {result}"
s2 = state.read("$PROJ")
assert s2.research_mode_active is True
assert s2.research_plan_required is True
print(f"plan target: {s2.research_plan_target}")
PY
ok "research mode active, plan target written"

# Test 3: backlog mutated without plan → quarantined.
log "backlog without plan quarantined"
echo "- [ ] [P1] vec_new — proposed without plan" >> "$PROJ/backlog.md"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from orchestrator.research import validate_research_plan
s = state.read("$PROJ")
out = validate_research_plan("$PROJ", s, "2026-05-04T17:00:00Z", pre_open_lines=[])
assert out == "violation", f"got {out}"
PY
QUAR=$(ls "$PROJ"/.cc-autopipe/UNVALIDATED_BACKLOG_*.md 2>/dev/null | head -1)
[ -n "$QUAR" ] || die "no quarantine file"
grep -q "vec_new" "$QUAR" || die "quarantine missing entry"
grep -q "vec_new" "$PROJ/backlog.md" && die "vec_new still in backlog"
ok "backlog additions quarantined without plan"

# Test 4: plan written → flag clears.
log "research plan filed clears flag"
"$PY" - <<PY
import sys, pathlib
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from orchestrator.research import validate_research_plan

s = state.read("$PROJ")
target = pathlib.Path(s.research_plan_target)
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text("# Research plan\nclusters: vec_meta family exhausted\n")

out = validate_research_plan("$PROJ", s, "2026-05-04T18:00:00Z", pre_open_lines=[])
assert out == "filed", f"got {out}"
assert state.read("$PROJ").research_plan_required is False
PY
ok "research_plan_required cleared after plan filed"

printf '\033[32m===\033[0m PASS — research plan smoke\n'
