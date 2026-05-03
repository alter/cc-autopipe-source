#!/bin/bash
# tests/gates/batch-3-v12.sh — automated gate for v1.2 Batch 3 (C+D+F+G).
#
# Verifies:
#   A. Hygiene + lint
#   B. Pytest full suite
#   C. Stage smokes (skip via CC_AUTOPIPE_GATE_FAST=1)
#   D. SessionStart full block end-to-end (Bug C+D contents present)
#   E. backlog.parse_top_open priority sort
#   F. notify.notify_subprocess_failed_dedup dedup behaviour (dry_run)
#   G. rules.md template carries long-op discipline (Bug C)
#   H. orchestrator emits task_switched + stage_completed events
#      (light grep — full integration covered by per-cycle tests)
#   I. hello-fullstack-v1 + v12 regressions
#
# Refs: AGENTS-v1.2.md §7.

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

if [ -x "$REPO_ROOT/.venv/bin/pytest" ]; then
    PYTEST="$REPO_ROOT/.venv/bin/pytest"
    RUFF="$REPO_ROOT/.venv/bin/ruff"
    PY="$REPO_ROOT/.venv/bin/python3"
else
    PYTEST="pytest"
    RUFF="ruff"
    PY="python3"
fi

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"

FAIL=0
FAILED_ITEMS=()

check() {
    local desc="$1"; shift
    local out
    if ! out=$("$@" 2>&1); then
        printf 'GATE FAIL: %s\n' "$desc" >&2
        printf '%s\n' "$out" | tail -n 20 | sed 's/^/  | /' >&2
        FAIL=$((FAIL + 1))
        FAILED_ITEMS+=("$desc")
        return 1
    fi
    printf 'GATE OK:   %s\n' "$desc"
    return 0
}

check_sh() {
    local desc="$1"; shift
    check "$desc" sh -c "$*"
}

# A) Hygiene + lint
# shellcheck disable=SC2016
check_sh "working tree clean (excl. BATCH_HALT.md, .cc-autopipe/)" \
    'test -z "$(git status --porcelain | grep -vE "^\?\? (BATCH_HALT.md|\.cc-autopipe/)")"'
check "ruff check src tests tools" "$RUFF" check src tests tools
check "ruff format --check src tests tools" "$RUFF" format --check src tests tools
check_sh "shellcheck -x on bash files" \
    'find src tests tools -type f \( -name "*.sh" -o -path "*/helpers/*" \) ! -name "*.py" -print0 \
        | xargs -0 shellcheck -x'

# B) Pytest
check "pytest tests/ (full suite)" "$PYTEST" tests/ -q --tb=no

# C) Stage smokes
if [ "${CC_AUTOPIPE_GATE_FAST:-0}" = "1" ]; then
    printf 'GATE SKIP: full smoke runner (CC_AUTOPIPE_GATE_FAST=1)\n'
else
    check "tests/smoke/run-all-smokes.sh (13/13)" \
        bash "$REPO_ROOT/tests/smoke/run-all-smokes.sh"
fi

# D) SessionStart full block end-to-end
TMPDIR_GATE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_GATE"' EXIT
mkdir -p "$TMPDIR_GATE/proj/.cc-autopipe"
cat > "$TMPDIR_GATE/proj/backlog.md" <<'BL'
- [ ] [implement] [P0] task_alpha — first thing
- [ ] [implement] [P1] task_beta — second thing
- [ ] [implement] [P1] task_gamma — third thing
- [ ] [implement] [P2] task_delta — later
- [x] [implement] [P0] task_done — already finished
BL
cat > "$TMPDIR_GATE/proj/.cc-autopipe/state.json" <<'EOF'
{
  "schema_version": 3,
  "name": "test",
  "phase": "active",
  "iteration": 1,
  "current_task": {
    "id": "task_alpha",
    "started_at": "2026-05-03T10:00:00Z",
    "stage": "init",
    "stages_completed": [],
    "artifact_paths": [],
    "claude_notes": ""
  },
  "session_id": null,
  "last_score": null,
  "last_passed": null,
  "last_in_progress": false,
  "prd_complete": false,
  "consecutive_failures": 0,
  "consecutive_in_progress": 0,
  "last_cycle_started_at": null,
  "last_progress_at": null,
  "threshold": 0.85,
  "paused": null,
  "detached": null,
  "current_phase": 1,
  "phases_completed": [],
  "escalated_next_cycle": false,
  "successful_cycles_since_improver": 0,
  "improver_due": false
}
EOF
check_sh "session_start_helper 'all' contains current_task + backlog + long-op" \
    "OUT=\$($PY -c '
import sys
sys.path.insert(0, \"$REPO_ROOT/src/lib\")
from session_start_helper import build_full_block
print(build_full_block(\"$TMPDIR_GATE/proj\"))
') && \
    echo \"\$OUT\" | grep -q 'task_alpha' && \
    echo \"\$OUT\" | grep -q 'task_beta' && \
    echo \"\$OUT\" | grep -q 'Long operation guidance' && \
    echo \"\$OUT\" | grep -q 'CURRENT TASK'"

# E) backlog priority sort
check_sh "backlog.parse_top_open returns P0 first" \
    "$PY -c '
import sys
sys.path.insert(0, \"$REPO_ROOT/src/lib\")
from backlog import parse_top_open
top = parse_top_open(\"$TMPDIR_GATE/proj/backlog.md\", n=3)
ids = [it.id for it in top]
assert ids[0] == \"task_alpha\", ids
assert \"task_done\" not in ids
assert \"task_delta\" not in ids  # P2 outside top-3
'"

# F) notify dedup (dry_run)
check_sh "notify_subprocess_failed_dedup dedup honours window" \
    "$PY -c '
import sys, os, tempfile
sys.path.insert(0, \"$REPO_ROOT/src/lib\")
from notify import notify_subprocess_failed_dedup
with tempfile.TemporaryDirectory() as td:
    sd = os.path.join(td, \"sentinels\")
    os.makedirs(sd)
    a = notify_subprocess_failed_dedup(\"proj1\", 1, \"err\", sd, dedup_window=600, dry_run=True)
    assert a is True, \"first call should send\"
    b = notify_subprocess_failed_dedup(\"proj1\", 1, \"err\", sd, dedup_window=600, dry_run=True)
    assert b is False, \"second call within window should not send\"
'"

# G) rules.md template has long-op section
check_sh "rules.md template carries Long operation discipline" \
    "grep -q 'Long operation discipline' \"$REPO_ROOT/src/templates/.cc-autopipe/rules.md.example\""
check_sh "rules.md template mentions cc-autopipe-detach" \
    "grep -q 'cc-autopipe-detach' \"$REPO_ROOT/src/templates/.cc-autopipe/rules.md.example\""

# H) orchestrator carries task_switched + stage_completed event emitters
check_sh "orchestrator emits task_switched event" \
    "grep -q 'task_switched' \"$REPO_ROOT/src/orchestrator\""
check_sh "orchestrator emits stage_completed event" \
    "grep -q 'stage_completed' \"$REPO_ROOT/src/orchestrator\""
check_sh "orchestrator emits subprocess_alerted event" \
    "grep -q 'subprocess_alerted' \"$REPO_ROOT/src/orchestrator\""

# I) Regressions
check "hello-fullstack-v1 regression" \
    bash "$REPO_ROOT/tests/regression/hello-fullstack-v1.sh"
check "hello-fullstack-v12 regression" \
    bash "$REPO_ROOT/tests/regression/hello-fullstack-v12.sh"

echo
if [ $FAIL -gt 0 ]; then
    printf 'BATCH 3 v1.2 GATE FAILED: %d check(s) failed\n' "$FAIL" >&2
    for item in "${FAILED_ITEMS[@]}"; do
        printf '  - %s\n' "$item" >&2
    done
    exit 1
fi
echo "BATCH 3 v1.2 GATE PASSED."
exit 0
