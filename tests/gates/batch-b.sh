#!/bin/bash
# tests/gates/batch-b.sh — automated gate for v1.0 Batch b (Stages H/I/J).
#
# shellcheck disable=SC2016
# (Single-quoted strings are deliberate — they are passed verbatim to
# `sh -c` via check_sh and must not expand in this shell.)
#
# Extends batch-a.sh with the new Stage H/I/J smokes and a few
# subsystem-specific surface checks. Same set+e + collect-failures
# pattern so a failed run reports every failed item on one screen.
#
# Usage:
#   bash tests/gates/batch-b.sh
#
# Refs: AGENTS-v1.md §2.1, §3.2

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

if [ -x "$REPO_ROOT/.venv/bin/pytest" ]; then
    PYTEST="$REPO_ROOT/.venv/bin/pytest"
    RUFF="$REPO_ROOT/.venv/bin/ruff"
else
    PYTEST="pytest"
    RUFF="ruff"
fi

DISPATCHER="$REPO_ROOT/src/helpers/cc-autopipe"
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
# A) Source-tree hygiene (same as batch-a)
# ---------------------------------------------------------------------------

check_sh "working tree clean" \
    'test -z "$(git status --porcelain | grep -v "^?? PROMPT-v1.md$")"'
check_sh "OPEN_QUESTIONS.md has no Status: blocked entries" \
    '! grep -qE "^\*\*Status:\*\*[[:space:]]*blocked" OPEN_QUESTIONS.md'
check_sh "no orphan TODO version markers" \
    "! grep -REn --exclude-dir=gates 'TODO\\((v0\\.5\\.1|v1\\.0)\\)' src/ tests/ tools/ \
        | grep -vE 'OPEN_QUESTIONS|issue#'"

# ---------------------------------------------------------------------------
# B) Lint
# ---------------------------------------------------------------------------

check "ruff check src tests tools" "$RUFF" check src tests tools
check "ruff format --check src tests tools" "$RUFF" format --check src tests tools
check_sh "shellcheck on bash files" \
    'find src tests tools -type f \( -name "*.sh" -o -path "*/helpers/*" \) ! -name "*.py" -print0 \
        | xargs -0 shellcheck -x'

# ---------------------------------------------------------------------------
# C) Regression: all v0.5 stage smokes still pass
# ---------------------------------------------------------------------------

for stage in a b c d e f; do
    check "v0.5 stage-$stage smoke" bash "tests/smoke/stage-$stage.sh"
done

# ---------------------------------------------------------------------------
# D) Batch b new smokes
# ---------------------------------------------------------------------------

check "v1.0 stage-h smoke (DETACHED)"           bash tests/smoke/stage-h.sh
check "v1.0 stage-i smoke (R/R subagents)"      bash tests/smoke/stage-i.sh
check "v1.0 stage-j smoke (phase split)"        bash tests/smoke/stage-j.sh

# ---------------------------------------------------------------------------
# E) Pytest
# ---------------------------------------------------------------------------

check "pytest tests/ (full suite)" "$PYTEST" tests/ -q --tb=no

# ---------------------------------------------------------------------------
# F) Doctor (offline)
# ---------------------------------------------------------------------------

check "cc-autopipe doctor --offline" "$DISPATCHER" doctor --offline

# ---------------------------------------------------------------------------
# G) Batch b-specific surface
# ---------------------------------------------------------------------------

# Stage H: cc-autopipe-detach helper exists, executable, dispatcher wired.
check_sh "cc-autopipe-detach helper executable" \
    'test -x src/helpers/cc-autopipe-detach'
check_sh "cc-autopipe detach --help via dispatcher" \
    "$DISPATCHER detach --help | grep -q -- '--check-cmd'"
check_sh "state.py CLI exposes set-detached" \
    "python3 src/lib/state.py set-detached --help 2>&1 | grep -q -- '--check-cmd'"

# Stage I: agents.json template carries researcher + reporter.
check_sh "agents.json template carries researcher + reporter" \
    "python3 -c 'import json; d=json.load(open(\"src/templates/.cc-autopipe/agents.json\")); assert \"researcher\" in d and \"reporter\" in d'"

# Stage J: PRD phase parser importable + state CLI exposes complete-phase.
check_sh "src/lib/prd.py imports cleanly" \
    "python3 -c 'import sys; sys.path.insert(0, \"src/lib\"); import prd; assert callable(prd.parse_phases)'"
check_sh "state.py CLI exposes complete-phase" \
    "python3 src/lib/state.py complete-phase --help 2>&1 | grep -q complete-phase || python3 src/lib/state.py complete-phase 2>&1 | grep -q complete-phase"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
if [ $FAIL -gt 0 ]; then
    printf 'BATCH b GATE FAILED: %d check(s) failed\n' "$FAIL" >&2
    for item in "${FAILED_ITEMS[@]}"; do
        printf '  - %s\n' "$item" >&2
    done
    exit 1
fi

echo "BATCH b GATE PASSED."
exit 0
