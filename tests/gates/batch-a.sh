#!/bin/bash
# tests/gates/batch-a.sh — automated gate for v0.5.1 (Batch a).
#
# shellcheck disable=SC2016
# (Single-quoted strings here are deliberate — they are passed verbatim
# to `sh -c` via check_sh and must not expand in this shell.)
#
# Runs every check from AGENTS-v1.md §2.1 plus Batch a-specific items
# (cc-autopipe stop subcommand surface). Exits 0 only when ALL checks
# pass — on failure, surfaces every failed item by name so the human
# (or the next agent session reading BATCH_HALT.md) can triage quickly.
#
# Usage:
#   bash tests/gates/batch-a.sh
#
# Refs: AGENTS-v1.md §2 (gate criteria), §2.3 (gate failure handling)

# Note: NOT `set -e` — we want to keep running every check so the
# operator sees the full picture in one pass.
set +e

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

# Pick venv tools when present, fall back to PATH otherwise.
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
        # Tail the output so the operator gets a hint without 1MB of noise.
        printf '%s\n' "$out" | tail -n 20 | sed 's/^/  | /' >&2
        FAIL=$((FAIL + 1))
        FAILED_ITEMS+=("$desc")
        return 1
    fi
    printf 'GATE OK:   %s\n' "$desc"
    return 0
}

check_sh() {
    # Convenience wrapper for inline shell expressions. Single quotes
    # in the call sites are intentional — they're sh -c payloads.
    # shellcheck disable=SC2016
    local desc="$1"; shift
    check "$desc" sh -c "$*"
}

# ---------------------------------------------------------------------------
# A) Source-tree hygiene
# ---------------------------------------------------------------------------

check_sh "working tree clean (no uncommitted changes outside untracked PROMPT-v1.md)" \
    'test -z "$(git status --porcelain | grep -v "^?? PROMPT-v1.md$")"'

check_sh "OPEN_QUESTIONS.md has no Status: blocked entries" \
    '! grep -qE "^\*\*Status:\*\*[[:space:]]*blocked" OPEN_QUESTIONS.md'

# Don't allow stray TODO(v0.5.1) / TODO(v1.0) markers without an
# OPEN_QUESTIONS or issue reference per AGENTS.md §6.5.
check_sh "no orphan TODO(v0.5.1) / TODO(v1.0) markers" \
    "! grep -REn 'TODO\\((v0\\.5\\.1|v1\\.0)\\)[^.]*$' src/ tests/ tools/ \
        | grep -vE 'OPEN_QUESTIONS|issue'"

# ---------------------------------------------------------------------------
# B) Lint
# ---------------------------------------------------------------------------

check "ruff check src tests tools" "$RUFF" check src tests tools
check "ruff format --check src tests tools" "$RUFF" format --check src tests tools
check_sh "shellcheck on bash files" \
    'find src tests tools -type f \( -name "*.sh" -o -path "*/helpers/*" \) ! -name "*.py" -print0 \
        | xargs -0 shellcheck -x'

# ---------------------------------------------------------------------------
# C) v0.5 stage smokes — must still pass after every batch (regression guard).
# ---------------------------------------------------------------------------

for stage in a b c d e f; do
    check "v0.5 stage-$stage smoke" bash "tests/smoke/stage-$stage.sh"
done

# ---------------------------------------------------------------------------
# D) pytest
# ---------------------------------------------------------------------------

check "pytest tests/ (unit + integration)" "$PYTEST" tests/ -q --tb=no

# ---------------------------------------------------------------------------
# E) Doctor (offline) — confirms install is sane on the build host.
# ---------------------------------------------------------------------------

check "cc-autopipe doctor --offline" "$DISPATCHER" doctor --offline

# ---------------------------------------------------------------------------
# F) Batch a-specific surface: cc-autopipe stop must be wired and documented.
# ---------------------------------------------------------------------------

check_sh "cc-autopipe --help lists stop" \
    "$DISPATCHER --help | grep -qE '^\\s+stop\\b'"
check_sh "cc-autopipe stop --help responds with usage + --timeout" \
    "$DISPATCHER stop --help | grep -q -- '--timeout'"
check_sh "cc-autopipe stop is idempotent (rc=0 with no orchestrator)" \
    'tmp=$(mktemp -d); CC_AUTOPIPE_USER_HOME="$tmp" '"$DISPATCHER"' stop >/dev/null 2>&1; \
     rc=$?; rm -rf "$tmp"; test $rc -eq 0'

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
if [ $FAIL -gt 0 ]; then
    printf 'BATCH a GATE FAILED: %d check(s) failed\n' "$FAIL" >&2
    for item in "${FAILED_ITEMS[@]}"; do
        printf '  - %s\n' "$item" >&2
    done
    exit 1
fi

echo "BATCH a GATE PASSED."
exit 0
