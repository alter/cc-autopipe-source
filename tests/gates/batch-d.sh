#!/bin/bash
# tests/gates/batch-d.sh — automated gate for v1.0 Batch d (Stages M/N).
#
# shellcheck disable=SC2016
# (Single-quoted strings are deliberate — they are passed verbatim to
# `sh -c` via check_sh and must not expand in this shell.)
#
# Extends batch-c.sh with the new Stage M/N smokes + subsystem checks.
#
# Refs: AGENTS-v1.md §2.1, §3.4

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
# D) v1.0 smokes (full set after Batch d)
# ---------------------------------------------------------------------------

for stage in h i j k l m n; do
    check "v1.0 stage-$stage smoke" bash "tests/smoke/stage-$stage.sh"
done

# ---------------------------------------------------------------------------
# E) Pytest
# ---------------------------------------------------------------------------

check "pytest tests/ (full suite)" "$PYTEST" tests/ -q --tb=no

# ---------------------------------------------------------------------------
# F) Doctor (offline)
# ---------------------------------------------------------------------------

check "cc-autopipe doctor --offline" "$DISPATCHER" doctor --offline

# ---------------------------------------------------------------------------
# G) Batch d-specific surface
# ---------------------------------------------------------------------------

# Stage M: templates + service module + dispatcher.
check_sh "src/init/cc-autopipe.service.template exists" \
    'test -f src/init/cc-autopipe.service.template'
check_sh "src/init/com.cc-autopipe.plist.template exists" \
    'test -f src/init/com.cc-autopipe.plist.template'
check_sh "src/cli/service.py exposes 4 subcommands" \
    "python3 src/cli/service.py --help 2>&1 | grep -E 'install-systemd|install-launchd|uninstall-systemd|uninstall-launchd' | wc -l | grep -q 4"
check_sh "dispatcher --help lists install-systemd" \
    "$DISPATCHER --help | grep -q install-systemd"
check_sh "dispatcher --help lists install-launchd" \
    "$DISPATCHER --help | grep -q install-launchd"

# Stage N: improver subagent in template + orchestrator wiring.
check_sh "agents.json template carries improver subagent" \
    "python3 -c 'import json; d=json.load(open(\"src/templates/.cc-autopipe/agents.json\")); assert \"improver\" in d'"
check_sh "config.yaml template carries improver block" \
    "grep -q '^improver:' src/templates/.cc-autopipe/config.yaml"
check_sh "state.py exposes successful_cycles_since_improver field" \
    "grep -q 'successful_cycles_since_improver' src/lib/state.py"
check_sh "state.py exposes improver_due field" \
    "grep -q 'improver_due' src/lib/state.py"
check_sh "orchestrator carries _read_config_improver" \
    "grep -q '_read_config_improver' src/orchestrator"
check_sh "orchestrator carries improver_trigger_due event" \
    "grep -q 'improver_trigger_due' src/orchestrator"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
if [ $FAIL -gt 0 ]; then
    printf 'BATCH d GATE FAILED: %d check(s) failed\n' "$FAIL" >&2
    for item in "${FAILED_ITEMS[@]}"; do
        printf '  - %s\n' "$item" >&2
    done
    exit 1
fi

echo "BATCH d GATE PASSED."
exit 0
