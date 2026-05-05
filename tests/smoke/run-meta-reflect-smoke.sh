#!/bin/bash
# tests/smoke/run-meta-reflect-smoke.sh — v1.3 GROUP H end-to-end.

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
- [ ] [P1] vec_meta — proposed vector
EOF

# Test 1: trigger META_REFLECT after 3 verify failures.
log "trigger meta-reflection"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from orchestrator.reflection import trigger_meta_reflect

s = state.State.fresh("p")
s.current_task = state.CurrentTask(id="vec_meta", stage="stage_e_verdict")
s.consecutive_failures = 3
state.write("$PROJ", s)

action, target = trigger_meta_reflect("$PROJ", s, [{"error": "verify_failed"}] * 3)
assert action == "triggered", f"expected triggered, got {action}"
assert target.exists()
print(f"META_REFLECT written at {target.name}")

s2 = state.read("$PROJ")
assert s2.meta_reflect_pending is True
assert s2.meta_reflect_attempts == 1
assert s2.consecutive_failures == 0
PY
ok "META_REFLECT triggered, state updated"

# Test 2: simulate Claude writing META_DECISION skip → backlog marked.
log "apply meta-decision skip"
"$PY" - <<PY
import sys
sys.path.insert(0, "$REPO_ROOT/src")
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
from pathlib import Path
from orchestrator.reflection import detect_and_apply_decision

s = state.read("$PROJ")
target = Path(s.meta_reflect_target)
decision = target.parent / target.name.replace("META_REFLECT_", "META_DECISION_")
decision.write_text("decision: skip\nreason: structurally impossible\n")

applied = detect_and_apply_decision("$PROJ", state.read("$PROJ"))
assert applied is True

backlog = (Path("$PROJ") / "backlog.md").read_text()
assert "[~won't-fix]" in backlog, f"backlog: {backlog!r}"
print("backlog marked [~won't-fix]")

s2 = state.read("$PROJ")
assert s2.meta_reflect_pending is False
assert s2.current_task is None
PY
ok "decision applied, backlog updated, state cleared"

printf '\033[32m===\033[0m PASS — meta-reflect smoke\n'
