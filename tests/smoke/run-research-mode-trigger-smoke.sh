#!/bin/bash
# tests/smoke/run-research-mode-trigger-smoke.sh — v1.3.2 TRIGGER-SMOKES.
#
# Synthetic end-to-end validation of the research_mode lifecycle.
# Production has never activated this path — first activation will
# happen during 14-day autonomy when AI-trade actually exhausts its
# baseline backlog. This smoke surfaces ordering / quarantine bugs
# BEFORE Roman is offline.
#
# Lifecycle exercised:
#   1. backlog.md with all tasks closed → detect_prd_complete=True
#   2. activate_research_mode → flags + plan target set
#   3. SessionStart injection: build_research_mode_block has the
#      MANDATORY block
#   4. backlog mutated WITHOUT plan → entries quarantined,
#      research_plan_violation logged
#   5. plan filed → flag clears via validate_research_plan

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

# Backlog: all closed/in-progress, no `- [ ]` open tasks.
cat > "$PROJ/backlog.md" <<'EOF'
- [x] [P0] vec_meta — promoted
- [x] [P1] vec_tbm — promoted
- [~won't-fix] [P2] vec_dead — won't-fix
EOF

# Test 1: detect_prd_complete returns True.
log "PRD complete detection on all-closed backlog"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
from orchestrator.research import detect_prd_complete
assert detect_prd_complete("$PROJ") is True, "PRD must read complete"
print("detect_prd_complete=True")
PY
ok 'no "- [ ]" lines → PRD complete'

# Test 2: maybe_activate_after_cycle activates research mode.
log "research_mode activation via maybe_activate_after_cycle"
"$PY" - <<PY
import json, sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from pathlib import Path
import orchestrator.research as r

# Force quota gate open (test env has no real quota cache).
r._quota_seven_day_pct = lambda: 0.30

s = state.State.fresh("p")
state.write("$PROJ", s)

result = r.maybe_activate_after_cycle(Path("$PROJ"), s)
assert result == "active", f"expected active, got {result}"

s2 = state.read("$PROJ")
assert s2.prd_complete_detected is True
assert s2.research_mode_active is True
assert s2.research_plan_required is True
target = s2.research_plan_target
assert target and "RESEARCH_PLAN_" in target, target
assert "data/debug" in target.replace("\\\\", "/")

# Aggregate events.
agg = Path("$UHOME/log/aggregate.jsonl").read_text().splitlines()
events = [json.loads(ln) for ln in agg if ln.strip()]
assert any(e["event"] == "prd_complete" for e in events)
assert any(e["event"] == "research_mode_active" for e in events)
print(f"plan target: {Path(target).name}")
PY
ok "research_mode_active=True, plan target points at data/debug/RESEARCH_PLAN_*.md"

# Test 3: SessionStart injection has MANDATORY-style content.
log "research_mode mandatory block injected"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import session_start_helper
# build_research_mode_block lives in research module per the v1.3 design.
from orchestrator.research import build_research_mode_block
block = build_research_mode_block("$PROJ")
assert block, "research_mode block was empty while flag pending"
upper = block.upper()
assert "RESEARCH" in upper, "block missing RESEARCH wording"
assert len(block) > 50, f"suspicious tiny block: {block!r}"
print(f"block length: {len(block)}")
PY
ok "research_mode injection block has RESEARCH wording"

# Test 4: backlog mutation WITHOUT plan → quarantine.
log "violation: backlog additions quarantined when no plan filed"
echo "- [ ] [P1] vec_speculative_1 — proposed without plan" >> "$PROJ/backlog.md"
echo "- [ ] [P2] vec_speculative_2 — proposed without plan" >> "$PROJ/backlog.md"
"$PY" - <<PY
import json, sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from pathlib import Path
from orchestrator.research import validate_research_plan

s = state.read("$PROJ")
out = validate_research_plan(
    "$PROJ", s, "2026-05-05T20:00:00Z", pre_open_lines=[]
)
assert out == "violation", f"expected violation, got {out}"

# Check quarantine file.
quar_files = list(Path("$PROJ/.cc-autopipe").glob("UNVALIDATED_BACKLOG_*.md"))
assert len(quar_files) == 1, f"expected 1 quarantine, got {quar_files}"
qtext = quar_files[0].read_text()
assert "vec_speculative_1" in qtext
assert "vec_speculative_2" in qtext

# Backlog stripped.
backlog = Path("$PROJ/backlog.md").read_text()
assert "vec_speculative_1" not in backlog
assert "vec_speculative_2" not in backlog

# Aggregate event.
agg = Path("$UHOME/log/aggregate.jsonl").read_text().splitlines()
events = [json.loads(ln) for ln in agg if ln.strip()]
viol = [e for e in events if e["event"] == "research_plan_violation"]
assert len(viol) == 1
assert viol[0]["quarantined_count"] == 2
print("quarantined 2 unvalidated entries")
PY
ok "violation path: 2 entries quarantined to UNVALIDATED_BACKLOG_*.md"

# Test 5: file the plan → flag clears, research_plan_filed event.
log "filing the plan clears research_plan_required"
"$PY" - <<PY
import json, sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from pathlib import Path
from orchestrator.research import validate_research_plan

s = state.read("$PROJ")
target = Path(s.research_plan_target)
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(
    "# Research plan — vec family exhaustion review\n\n"
    "## Hypothesis\nvec_meta family exhausted; explore vec_skew.\n\n"
    "## Candidates\n- vec_skew_5d\n- vec_skew_20d\n"
)

out = validate_research_plan(
    "$PROJ", s, "2026-05-05T21:00:00Z", pre_open_lines=[]
)
assert out == "filed", f"expected filed, got {out}"

s2 = state.read("$PROJ")
assert s2.research_plan_required is False, "flag must clear"
assert s2.research_mode_active is True, "research_mode stays until backlog gains entries"

agg = Path("$UHOME/log/aggregate.jsonl").read_text().splitlines()
events = [json.loads(ln) for ln in agg if ln.strip()]
filed = [e for e in events if e["event"] == "research_plan_filed"]
assert len(filed) == 1
print("plan filed; research_plan_required=False")
PY
ok "research_plan_filed event, flag cleared"

printf '\033[32m===\033[0m PASS — research-mode-trigger smoke (full lifecycle pinned)\n'
