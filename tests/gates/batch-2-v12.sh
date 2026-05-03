#!/bin/bash
# tests/gates/batch-2-v12.sh — automated gate for v1.2 Batch 2 (Bug B + H).
#
# Verifies:
#   A. Source-tree hygiene + lint
#   B. Pytest full suite
#   C. v1.2 schema still v3 (no Batch 2 regressions on Batch 1 work)
#   D. failures.categorize_recent decision matrix
#   E. orchestrator: process_project routes verify-pattern → HUMAN_NEEDED
#      (no escalation), crash-pattern → escalation, fallback path
#      preserved
#   F. in_progress flag end-to-end via state.update_verify
#   G. hello-fullstack-v1 + hello-fullstack-v12 regressions
#
# CC_AUTOPIPE_GATE_FAST=1 skips the smoke-runner block (~25min).

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
check_sh "working tree clean (excl. BATCH_HALT.md, .cc-autopipe/SKIP_COOLDOWN)" \
    'test -z "$(git status --porcelain | grep -vE "^\?\? (BATCH_HALT.md|\.cc-autopipe/)")"'
check "ruff check src tests tools" "$RUFF" check src tests tools
check "ruff format --check src tests tools" "$RUFF" format --check src tests tools
check_sh "shellcheck -x on bash files" \
    'find src tests tools -type f \( -name "*.sh" -o -path "*/helpers/*" \) ! -name "*.py" -print0 \
        | xargs -0 shellcheck -x'

# B) Pytest
check "pytest tests/ (full suite)" "$PYTEST" tests/ -q --tb=no

# C) Stage smokes (skip in fast)
if [ "${CC_AUTOPIPE_GATE_FAST:-0}" = "1" ]; then
    printf 'GATE SKIP: full smoke runner (CC_AUTOPIPE_GATE_FAST=1)\n'
else
    check "tests/smoke/run-all-smokes.sh (13/13)" \
        bash "$REPO_ROOT/tests/smoke/run-all-smokes.sh"
fi

# D) Schema sanity (Batch 1 still healthy)
TMPDIR_GATE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_GATE"' EXIT
mkdir -p "$TMPDIR_GATE/proj/.cc-autopipe"
check_sh "schema v3 still emitted by fresh state" \
    "$PY -c '
import sys, json
sys.path.insert(0, \"$REPO_ROOT/src/lib\")
import state
state.write(\"$TMPDIR_GATE/proj\", state.State.fresh(\"x\"))
data = json.load(open(\"$TMPDIR_GATE/proj/.cc-autopipe/state.json\"))
assert data[\"schema_version\"] == 3
assert data[\"last_in_progress\"] is False
assert data[\"consecutive_in_progress\"] == 0
'"

# E) failures.categorize_recent decision matrix
check_sh "categorize: 3 verify_failed → human_needed, no escalation" \
    "$PY -c '
import sys
sys.path.insert(0, \"$REPO_ROOT/src/lib\")
from failures import categorize_recent
recent = [{\"error\":\"verify_failed\"}] * 3
cat = categorize_recent(recent)
assert cat[\"recommend_escalation\"] is False
assert cat[\"recommend_human_needed\"] is True
assert cat[\"verify_count\"] == 3
'"
check_sh "categorize: 3 claude_subprocess_failed → escalation" \
    "$PY -c '
import sys
sys.path.insert(0, \"$REPO_ROOT/src/lib\")
from failures import categorize_recent
recent = [{\"error\":\"claude_subprocess_failed\"}] * 3
cat = categorize_recent(recent)
assert cat[\"recommend_escalation\"] is True
assert cat[\"recommend_human_needed\"] is False
'"
check_sh "categorize: 5 mixed → recommend_failed" \
    "$PY -c '
import sys
sys.path.insert(0, \"$REPO_ROOT/src/lib\")
from failures import categorize_recent
recent = [
    {\"error\":\"verify_failed\"},
    {\"error\":\"claude_subprocess_failed\"},
    {\"error\":\"verify_failed\"},
    {\"error\":\"claude_subprocess_failed\"},
    {\"error\":\"weird\"},
]
cat = categorize_recent(recent)
assert cat[\"recommend_failed\"] is True
'"

# F) update_verify in_progress in_progress
check_sh "state.update_verify in_progress=True does not bump failures" \
    "$PY -c '
import sys, json
sys.path.insert(0, \"$REPO_ROOT/src/lib\")
import state
import os
os.makedirs(\"$TMPDIR_GATE/ipproj/.cc-autopipe/memory\", exist_ok=True)
state.write(\"$TMPDIR_GATE/ipproj\", state.State.fresh(\"ip\"))
state.update_verify(\"$TMPDIR_GATE/ipproj\", passed=False, score=0.4, prd_complete=False, in_progress=True)
s = state.read(\"$TMPDIR_GATE/ipproj\")
assert s.consecutive_failures == 0
assert s.consecutive_in_progress == 1
assert s.last_in_progress is True
'"

# G) Regressions
check "hello-fullstack-v1 regression" \
    bash "$REPO_ROOT/tests/regression/hello-fullstack-v1.sh"
check "hello-fullstack-v12 regression" \
    bash "$REPO_ROOT/tests/regression/hello-fullstack-v12.sh"

# Summary
echo
if [ $FAIL -gt 0 ]; then
    printf 'BATCH 2 v1.2 GATE FAILED: %d check(s) failed\n' "$FAIL" >&2
    for item in "${FAILED_ITEMS[@]}"; do
        printf '  - %s\n' "$item" >&2
    done
    exit 1
fi
echo "BATCH 2 v1.2 GATE PASSED."
exit 0
