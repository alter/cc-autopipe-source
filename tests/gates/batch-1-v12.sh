#!/bin/bash
# tests/gates/batch-1-v12.sh — automated gate for v1.2 Batch 1 (Bug A + E).
#
# Verifies:
#   A. Source-tree hygiene (clean working tree, no stray markers).
#   B. Lint (ruff check / format-check, shellcheck -x).
#   C. All v1.0 stage smokes still pass — runner-driven via
#      tests/smoke/run-all-smokes.sh (sequential, log per-stage).
#   D. Pytest full suite.
#   E. v1.2 schema migration: v2 state.json → schema_v3 with v1.2
#      defaults; iteration preserved through migration.
#   F. CURRENT_TASK.md parsing: minimal example → expected dict.
#   G. CURRENT_TASK.md → state.current_task wiring (stop_helper).
#   H. session_start_helper emits the current_task block.
#   I. hello-fullstack-v1 + hello-fullstack-v12 regressions green.
#
# The full smoke runner takes ~25min in foreground. To save wall time
# during gate runs, set CC_AUTOPIPE_GATE_FAST=1 to skip the full smoke
# run and trust the individual stage smokes verified by the v1.0 final
# gate. The fast mode still runs hygiene/lint/pytest/regression/
# v1.2-specific blocks.
#
# Refs: AGENTS-v1.2.md §5 (Batch 1 gate), SPEC-v1.2.md Bug A.

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

# ---------------------------------------------------------------------------
# A) Source-tree hygiene
# ---------------------------------------------------------------------------

# BATCH_HALT.md and .cc-autopipe/ runtime state are working artifacts
# during a build, so we exclude them from the clean-tree check.
# shellcheck disable=SC2016
check_sh "working tree clean (excl. BATCH_HALT.md, .cc-autopipe/)" \
    'test -z "$(git status --porcelain | grep -vE "^\?\? (BATCH_HALT.md|\.cc-autopipe/)")"'
check_sh "OPEN_QUESTIONS.md has no Status: blocked entries" \
    '! grep -qE "^\*\*Status:\*\*[[:space:]]*blocked" OPEN_QUESTIONS.md'

# ---------------------------------------------------------------------------
# B) Lint
# ---------------------------------------------------------------------------

check "ruff check src tests tools" "$RUFF" check src tests tools
check "ruff format --check src tests tools" "$RUFF" format --check src tests tools
check_sh "shellcheck -x on bash files" \
    'find src tests tools -type f \( -name "*.sh" -o -path "*/helpers/*" \) ! -name "*.py" -print0 \
        | xargs -0 shellcheck -x'

# ---------------------------------------------------------------------------
# C) Stage smokes
# ---------------------------------------------------------------------------

if [ "${CC_AUTOPIPE_GATE_FAST:-0}" = "1" ]; then
    printf 'GATE SKIP: full smoke runner (CC_AUTOPIPE_GATE_FAST=1)\n'
    printf '           individual smokes already verified per v1.0 final.\n'
else
    check "tests/smoke/run-all-smokes.sh (13/13)" \
        bash "$REPO_ROOT/tests/smoke/run-all-smokes.sh"
fi

# ---------------------------------------------------------------------------
# D) Pytest
# ---------------------------------------------------------------------------

check "pytest tests/ (full suite)" "$PYTEST" tests/ -q --tb=no

# ---------------------------------------------------------------------------
# E) v1.2 schema migration smoke (v2 → v3)
# ---------------------------------------------------------------------------

TMPDIR_GATE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_GATE"' EXIT

cat > "$TMPDIR_GATE/state-v2.json" <<'EOF'
{
  "schema_version": 2,
  "name": "test",
  "phase": "active",
  "iteration": 5,
  "session_id": "abc-123",
  "last_score": 0.5,
  "last_passed": false,
  "prd_complete": false,
  "consecutive_failures": 1,
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

mkdir -p "$TMPDIR_GATE/proj/.cc-autopipe"
mv "$TMPDIR_GATE/state-v2.json" "$TMPDIR_GATE/proj/.cc-autopipe/state.json"

check_sh "schema v2 → v3 read+write migration" \
    "$PY -c '
import sys, json
sys.path.insert(0, \"$REPO_ROOT/src/lib\")
import state
s = state.read(\"$TMPDIR_GATE/proj\")
assert s.schema_version == 3, f\"expected v3, got {s.schema_version}\"
assert s.current_task is None, \"v2 file must migrate to current_task=None\"
assert s.iteration == 5, \"iteration must persist through migration\"
assert s.session_id == \"abc-123\"
assert s.last_in_progress is False
assert s.consecutive_in_progress == 0
state.write(\"$TMPDIR_GATE/proj\", s)
data = json.load(open(\"$TMPDIR_GATE/proj/.cc-autopipe/state.json\"))
assert data[\"schema_version\"] == 3
assert \"current_task\" in data and data[\"current_task\"] is None
assert \"last_in_progress\" in data
assert \"consecutive_in_progress\" in data
'"

# ---------------------------------------------------------------------------
# F) CURRENT_TASK.md parsing smoke
# ---------------------------------------------------------------------------

cat > "$TMPDIR_GATE/proj/.cc-autopipe/CURRENT_TASK.md" <<'EOF'
task: cand_imbloss_v2
stage: training
artifact: data/models/exp_cand_imbloss_v2/
notes: SwingLoss with class_balance_beta=0.999
EOF

check_sh "CURRENT_TASK.md parse → expected dict" \
    "$PY -c '
import sys
sys.path.insert(0, \"$REPO_ROOT/src/lib\")
from current_task import parse_file
ct = parse_file(\"$TMPDIR_GATE/proj/.cc-autopipe/CURRENT_TASK.md\")
assert ct[\"id\"] == \"cand_imbloss_v2\", ct
assert ct[\"stage\"] == \"training\", ct
assert \"data/models/exp_cand_imbloss_v2/\" in ct[\"artifact_paths\"]
assert \"SwingLoss\" in ct[\"claude_notes\"]
'"

# ---------------------------------------------------------------------------
# G) Stop hook helper wiring
# ---------------------------------------------------------------------------

check_sh "stop_helper sync_current_task_from_md projects file → state" \
    "$PY -c '
import sys, json
sys.path.insert(0, \"$REPO_ROOT/src/lib\")
import stop_helper, state
changed = stop_helper.sync_current_task_from_md(\"$TMPDIR_GATE/proj\")
assert changed is True
s = state.read(\"$TMPDIR_GATE/proj\")
assert s.current_task is not None
assert s.current_task.id == \"cand_imbloss_v2\"
assert s.current_task.stage == \"training\"
'"

# ---------------------------------------------------------------------------
# H) SessionStart helper block
# ---------------------------------------------------------------------------

check_sh "session_start_helper emits current_task block with id+stage" \
    "$PY -c '
import sys
sys.path.insert(0, \"$REPO_ROOT/src/lib\")
import session_start_helper
block = session_start_helper.build_current_task_block(\"$TMPDIR_GATE/proj\")
assert \"=== Current task ===\" in block
assert \"Task: cand_imbloss_v2\" in block
assert \"Stage: training\" in block
assert \"data/models/exp_cand_imbloss_v2/\" in block
'"

# ---------------------------------------------------------------------------
# I) hello-fullstack regressions
# ---------------------------------------------------------------------------

check "hello-fullstack-v1 regression" \
    bash "$REPO_ROOT/tests/regression/hello-fullstack-v1.sh"
check "hello-fullstack-v12 regression" \
    bash "$REPO_ROOT/tests/regression/hello-fullstack-v12.sh"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
if [ $FAIL -gt 0 ]; then
    printf 'BATCH 1 v1.2 GATE FAILED: %d check(s) failed\n' "$FAIL" >&2
    for item in "${FAILED_ITEMS[@]}"; do
        printf '  - %s\n' "$item" >&2
    done
    exit 1
fi

echo "BATCH 1 v1.2 GATE PASSED."
exit 0
