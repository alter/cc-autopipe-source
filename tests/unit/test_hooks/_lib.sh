#!/bin/bash
# tests/unit/test_hooks/_lib.sh — tiny test harness for bash hook tests.
# Sourced by each test_*.sh under tests/unit/test_hooks/.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SRC="$REPO_ROOT/src"
HOOKS="$SRC/hooks"
DISPATCHER="$SRC/helpers/cc-autopipe"

PASS=0
FAIL=0
FAILURES=()

# fresh_project: makes an isolated CC_AUTOPIPE_USER_HOME + cc-autopipe-init'd
# project under a unique tmpdir. Exports SCRATCH, USER_HOME, PROJECT.
fresh_project() {
    SCRATCH=$(mktemp -d)
    USER_HOME="$SCRATCH/uhome"
    PROJECT="$SCRATCH/proj"
    export SCRATCH USER_HOME PROJECT
    mkdir -p "$PROJECT"
    (cd "$PROJECT" && git init -q)
    CC_AUTOPIPE_HOME="$SRC" CC_AUTOPIPE_USER_HOME="$USER_HOME" \
        bash "$DISPATCHER" init "$PROJECT" >/dev/null
}

cleanup_project() {
    [ -n "${SCRATCH:-}" ] && rm -rf "$SCRATCH"
    SCRATCH=""
}

# run_hook <hook_name> <input_json>
# Sets these globals (read by callers): HOOK_OUT, HOOK_RC, HOOK_ERR.
# QUOTA_DISABLED=1 keeps stop-failure's quota path from accidentally
# hitting api.anthropic.com on a host whose Keychain has live creds.
run_hook() {
    local hook=$1
    local input=$2
    set +e
    # shellcheck disable=SC2034
    HOOK_OUT=$(printf '%s' "$input" | \
        CC_AUTOPIPE_HOME="$SRC" \
        CC_AUTOPIPE_USER_HOME="$USER_HOME" \
        CC_AUTOPIPE_QUOTA_DISABLED=1 \
        bash "$HOOKS/$hook.sh" 2>"$SCRATCH/hook.stderr")
    # shellcheck disable=SC2034
    HOOK_RC=$?
    set -e
    # shellcheck disable=SC2034
    HOOK_ERR=$(cat "$SCRATCH/hook.stderr" 2>/dev/null || true)
}

assert_eq() {
    local desc=$1 expected=$2 actual=$3
    if [ "$expected" = "$actual" ]; then
        printf '  \033[32mPASS\033[0m %s\n' "$desc"
        PASS=$((PASS + 1))
    else
        printf '  \033[31mFAIL\033[0m %s — expected %q, got %q\n' "$desc" "$expected" "$actual"
        FAILURES+=("$desc")
        FAIL=$((FAIL + 1))
    fi
}

assert_contains() {
    local desc=$1 needle=$2 hay=$3
    if printf '%s' "$hay" | grep -qF -- "$needle"; then
        printf '  \033[32mPASS\033[0m %s\n' "$desc"
        PASS=$((PASS + 1))
    else
        printf '  \033[31mFAIL\033[0m %s — needle %q not in haystack\n' "$desc" "$needle"
        FAILURES+=("$desc")
        FAIL=$((FAIL + 1))
    fi
}

assert_not_contains() {
    local desc=$1 needle=$2 hay=$3
    if printf '%s' "$hay" | grep -qF -- "$needle"; then
        printf '  \033[31mFAIL\033[0m %s — unexpectedly found %q\n' "$desc" "$needle"
        FAILURES+=("$desc")
        FAIL=$((FAIL + 1))
    else
        printf '  \033[32mPASS\033[0m %s\n' "$desc"
        PASS=$((PASS + 1))
    fi
}

assert_jq() {
    local desc=$1 file=$2 jq_filter=$3 expected=$4
    local actual
    actual=$(jq -r "$jq_filter" "$file" 2>/dev/null || echo "<jq-error>")
    assert_eq "$desc" "$expected" "$actual"
}

print_summary() {
    local label=$1
    echo
    if [ $FAIL -eq 0 ]; then
        printf '%s: \033[32m%d/%d PASS\033[0m\n' "$label" "$PASS" "$((PASS + FAIL))"
    else
        printf '%s: \033[31m%d/%d FAIL\033[0m\n' "$label" "$FAIL" "$((PASS + FAIL))"
        for f in "${FAILURES[@]}"; do
            printf '    - %s\n' "$f"
        done
    fi
}
