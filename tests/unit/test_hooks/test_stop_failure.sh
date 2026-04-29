#!/bin/bash
# tests/unit/test_hooks/test_stop_failure.sh — 429/error handling.
# Refs: SPEC.md §10.4, §9.3, §15.2

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=tests/unit/test_hooks/_lib.sh
. "$SCRIPT_DIR/_lib.sh"

echo "== test_stop_failure.sh =="

stop_failure_input() {
    local error=$1 details=${2:-}
    jq -nc --arg cwd "$PROJECT" --arg err "$error" --arg det "$details" '
        {cwd:$cwd, error:$err, error_details:$det}
    '
}

# --- rate_limit → PAUSED with resume_at ~1h in the future ---
fresh_project
run_hook stop-failure "$(stop_failure_input rate_limit "429 Too Many Requests")"
assert_eq "rc=0 on rate_limit" 0 "$HOOK_RC"
assert_jq "phase=paused" "$PROJECT/.cc-autopipe/state.json" .phase "paused"
assert_jq "paused.reason=rate_limit" "$PROJECT/.cc-autopipe/state.json" .paused.reason "rate_limit"

# resume_at must parse as ISO 8601 and be roughly 1h in the future
RESUME_AT=$(jq -r .paused.resume_at "$PROJECT/.cc-autopipe/state.json")
NOW_EPOCH=$(date -u +%s)
# GNU date -d works regardless of OS (we depend on coreutils per Q9 anyway)
RESUME_EPOCH=$(date -u -d "$RESUME_AT" +%s 2>/dev/null || \
    date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$RESUME_AT" +%s 2>/dev/null || echo 0)
DELTA=$(( RESUME_EPOCH - NOW_EPOCH ))
if [ "$DELTA" -gt 3500 ] && [ "$DELTA" -lt 3700 ]; then
    echo "  PASS resume_at is ~1h in future ($DELTA seconds)"
    PASS=$((PASS + 1))
else
    echo "  FAIL resume_at delta out of range: $DELTA seconds"
    FAIL=$((FAIL + 1))
fi

# §15.2: paused goes to aggregate.jsonl
assert_contains "aggregate.jsonl has paused event" '"event":"paused"' \
    "$(cat "$USER_HOME/log/aggregate.jsonl")"
assert_contains "aggregate event records reason" '"reason":"rate_limit"' \
    "$(cat "$USER_HOME/log/aggregate.jsonl")"
cleanup_project

# --- 429 alias ---
fresh_project
run_hook stop-failure "$(stop_failure_input 429 "rate limit")"
assert_eq "rc=0 on 429" 0 "$HOOK_RC"
assert_jq "phase=paused (429)" "$PROJECT/.cc-autopipe/state.json" .phase "paused"
cleanup_project

# --- RATE_LIMIT alias ---
fresh_project
run_hook stop-failure "$(stop_failure_input RATE_LIMIT "")"
assert_eq "rc=0 on RATE_LIMIT" 0 "$HOOK_RC"
assert_jq "phase=paused (RATE_LIMIT)" "$PROJECT/.cc-autopipe/state.json" .phase "paused"
cleanup_project

# --- other error → consecutive_failures bumped, NOT PAUSED ---
fresh_project
run_hook stop-failure "$(stop_failure_input unexpected_eof "connection reset")"
assert_eq "rc=0 on other error" 0 "$HOOK_RC"
assert_jq "phase remains active" "$PROJECT/.cc-autopipe/state.json" .phase "active"
assert_jq "consecutive_failures=1" "$PROJECT/.cc-autopipe/state.json" .consecutive_failures 1
assert_jq "no paused block" "$PROJECT/.cc-autopipe/state.json" .paused "null"
assert_contains "aggregate.jsonl has stop_failure event" '"event":"stop_failure"' \
    "$(cat "$USER_HOME/log/aggregate.jsonl")"
cleanup_project

# --- repeated other-errors accumulate ---
fresh_project
run_hook stop-failure "$(stop_failure_input network_error "")"
run_hook stop-failure "$(stop_failure_input network_error "")"
run_hook stop-failure "$(stop_failure_input network_error "")"
assert_jq "3 errors → consecutive_failures=3" \
    "$PROJECT/.cc-autopipe/state.json" .consecutive_failures 3
cleanup_project

# --- empty error field → bump failures but no TG noise ---
fresh_project
echo '{"cwd":"'"$PROJECT"'"}' > "$SCRATCH/in.json"
# shellcheck disable=SC2002  # cat-into-pipe avoids stdin redirection in run_hook
run_hook stop-failure "$(cat "$SCRATCH/in.json")"
assert_eq "rc=0 on empty error" 0 "$HOOK_RC"
assert_jq "consecutive_failures=1 on empty error" \
    "$PROJECT/.cc-autopipe/state.json" .consecutive_failures 1
assert_contains "aggregate has stop_failure_unknown event" \
    '"event":"stop_failure_unknown"' \
    "$(cat "$USER_HOME/log/aggregate.jsonl")"
cleanup_project

# --- TG send is fire-and-forget: hook exits 0 even with bogus creds ---
fresh_project
SECRETS="$SCRATCH/secrets.env"
cat > "$SECRETS" <<'EOF'
TG_BOT_TOKEN=fake-token-not-real
TG_CHAT_ID=0
EOF
set +e
echo "{\"cwd\":\"$PROJECT\",\"error\":\"rate_limit\"}" | \
    CC_AUTOPIPE_HOME="$SRC" \
    CC_AUTOPIPE_USER_HOME="$USER_HOME" \
    CC_AUTOPIPE_SECRETS_FILE="$SECRETS" \
    bash "$HOOKS/stop-failure.sh" >/dev/null 2>&1
RC=$?
set -e
assert_eq "rc=0 even with bogus TG creds" 0 "$RC"
cleanup_project

print_summary "test_stop_failure"
exit "$FAIL"
