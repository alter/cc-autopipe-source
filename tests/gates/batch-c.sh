#!/bin/bash
# tests/gates/batch-c.sh — automated gate for v1.0 Batch c (Stages K/L).
#
# shellcheck disable=SC2016
# (Single-quoted strings are deliberate — they are passed verbatim to
# `sh -c` via check_sh and must not expand in this shell.)
#
# Extends batch-b.sh with Stage K/L smokes + subsystem checks.
#
# Refs: AGENTS-v1.md §2.1, §3.3

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
# A) Source-tree hygiene
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
# D) v1.0 smokes — batch-b carry-over plus batch-c additions
# ---------------------------------------------------------------------------

check "v1.0 stage-h smoke (DETACHED)"           bash tests/smoke/stage-h.sh
check "v1.0 stage-i smoke (R/R subagents)"      bash tests/smoke/stage-i.sh
check "v1.0 stage-j smoke (phase split)"        bash tests/smoke/stage-j.sh
check "v1.0 stage-k smoke (quota_monitor)"      bash tests/smoke/stage-k.sh
check "v1.0 stage-l smoke (auto-escalation)"    bash tests/smoke/stage-l.sh

# ---------------------------------------------------------------------------
# E) Pytest
# ---------------------------------------------------------------------------

check "pytest tests/ (full suite)" "$PYTEST" tests/ -q --tb=no

# ---------------------------------------------------------------------------
# F) Doctor (offline)
# ---------------------------------------------------------------------------

check "cc-autopipe doctor --offline" "$DISPATCHER" doctor --offline

# ---------------------------------------------------------------------------
# G) Batch c-specific surface
# ---------------------------------------------------------------------------

# Stage K: quota_monitor module + orchestrator wiring + override env var.
check_sh "quota_monitor module imports" \
    "python3 -c 'import sys; sys.path.insert(0, \"src/lib\"); import quota_monitor; assert hasattr(quota_monitor, \"QuotaMonitor\") and hasattr(quota_monitor, \"check_once\")'"
check_sh "orchestrator carries quota_monitor wiring" \
    "grep -q 'QuotaMonitor' src/orchestrator"
check_sh "orchestrator exposes monitor_interval env override" \
    "grep -q 'CC_AUTOPIPE_QUOTA_MONITOR_INTERVAL_SEC' src/orchestrator"

# Stage L: config schema + state field + orchestrator branch.
check_sh "config.yaml template carries auto_escalation block" \
    "grep -q 'auto_escalation:' src/templates/.cc-autopipe/config.yaml"
check_sh "state.py exposes escalated_next_cycle" \
    "grep -q 'escalated_next_cycle' src/lib/state.py"
check_sh "orchestrator carries _read_config_auto_escalation" \
    "grep -q '_read_config_auto_escalation' src/orchestrator"
check_sh "orchestrator carries escalated_to_opus event" \
    "grep -q 'escalated_to_opus' src/orchestrator"
check_sh "resume.py clears escalated_next_cycle" \
    "grep -q 'escalated_next_cycle = False' src/cli/resume.py"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
if [ $FAIL -gt 0 ]; then
    printf 'BATCH c GATE FAILED: %d check(s) failed\n' "$FAIL" >&2
    for item in "${FAILED_ITEMS[@]}"; do
        printf '  - %s\n' "$item" >&2
    done
    exit 1
fi

echo "BATCH c GATE PASSED."
exit 0
