#!/bin/bash
# tools/mock-claude.sh — fake `claude` binary for hook + orchestrator tests.
# Refs: AGENTS.md §3.2, §13 (no real claude during build)
#
# Two invocation styles:
#
# A. Scenario style (direct hook tests):
#       mock-claude.sh <scenario> [<project_dir>]
#    Scenarios: success | verify-fail | rate-limit | block-secret | long-bash
#
# B. Popen style (orchestrator spawns this as if it were claude):
#       mock-claude.sh -p PROMPT --max-turns N --model M ... [--resume ID]
#    Recognized by leading `-p`. Behaviour controlled by env vars:
#       CC_AUTOPIPE_MOCK_SCENARIO   one of the scenarios above (default: success)
#       CC_AUTOPIPE_MOCK_SLEEP_SEC  delay before exit (test wall-clock timeouts)
#       CC_AUTOPIPE_MOCK_EXIT_RC    final exit code (default: 0)
#       CC_AUTOPIPE_MOCK_DUMP_INPUT path to dump stdin JSON for Stop hook to
#                                   $path so tests can audit session_id
#
# Hooks dir is resolved from CC_AUTOPIPE_HOOKS_DIR (preferred) or by
# parsing the project's .claude/settings.json.

set -euo pipefail

# ---------------------------------------------------------------------------
# Argument detection
# ---------------------------------------------------------------------------

STYLE="scenario"
SCENARIO="success"
PROJECT_DIR="$(pwd)"
RESUME_ID=""

# Popen detection: any arg list that starts with `-` (e.g. `-p`,
# `--resume`, `--max-turns`) is the orchestrator invoking us as if
# we were `claude`. Walk the args to find -p/--resume; ignore the rest.
HAS_PRINT=0
HAS_VERBOSE=0
HAS_STREAM_JSON=0
if [ $# -gt 0 ] && [ "${1:0:1}" = "-" ]; then
    STYLE="popen"
    SCENARIO="${CC_AUTOPIPE_MOCK_SCENARIO:-success}"
    while [ $# -gt 0 ]; do
        case "$1" in
            -p|--print)
                HAS_PRINT=1
                shift
                shift || true  # drop the prompt
                ;;
            --resume)
                shift
                RESUME_ID="${1:-}"
                shift || true
                ;;
            --resume=*)
                RESUME_ID="${1#--resume=}"
                shift
                ;;
            --verbose)
                HAS_VERBOSE=1
                shift
                ;;
            --output-format)
                shift
                if [ "${1:-}" = "stream-json" ]; then
                    HAS_STREAM_JSON=1
                fi
                shift || true
                ;;
            --output-format=stream-json)
                HAS_STREAM_JSON=1
                shift
                ;;
            *) shift ;;
        esac
    done

    # Mirror real claude 2.1.123+ validation: `-p` + stream-json without
    # --verbose is rejected. Without this check the mock silently
    # accepts a flag combination that fails in production.
    if [ "$HAS_PRINT" = "1" ] && [ "$HAS_STREAM_JSON" = "1" ] \
            && [ "$HAS_VERBOSE" = "0" ]; then
        echo "Error: When using --print, --output-format=stream-json requires --verbose" >&2
        exit 1
    fi
elif [ $# -gt 0 ]; then
    SCENARIO="$1"
    PROJECT_DIR="${2:-$(pwd)}"
fi

SESSION_ID="mock-session-$(date +%s)-$$"

# v1.3.4 R8: transient-then-OK mode for real-CLI smoke. When
# CC_AUTOPIPE_MOCK_TRANSIENT_THEN_OK=N is set, the first N invocations
# exit 1 with a transient stderr signature ("Server is temporarily
# limiting requests"); invocation N+1 onwards behaves normally. The
# counter is persisted to CC_AUTOPIPE_MOCK_COUNTER_FILE (defaults to
# /tmp/cc-autopipe-mock-counter.$$ which dies with the test).
if [ -n "${CC_AUTOPIPE_MOCK_TRANSIENT_THEN_OK:-}" ] && [ "$STYLE" = "popen" ]; then
    COUNTER_FILE="${CC_AUTOPIPE_MOCK_COUNTER_FILE:-/tmp/cc-autopipe-mock-counter.$$}"
    current=0
    [ -f "$COUNTER_FILE" ] && current=$(cat "$COUNTER_FILE")
    current=$((current + 1))
    echo "$current" > "$COUNTER_FILE"
    if [ "$current" -le "$CC_AUTOPIPE_MOCK_TRANSIENT_THEN_OK" ]; then
        echo "Error: Server is temporarily limiting requests" >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Hooks directory resolution
# ---------------------------------------------------------------------------

HOOKS_DIR="${CC_AUTOPIPE_HOOKS_DIR:-}"
if [ -z "$HOOKS_DIR" ] && [ -f "$PROJECT_DIR/.claude/settings.json" ]; then
    FIRST_HOOK=$(jq -r '.hooks.SessionStart[0].hooks[0].command // empty' \
        "$PROJECT_DIR/.claude/settings.json")
    [ -n "$FIRST_HOOK" ] && HOOKS_DIR=$(dirname "$FIRST_HOOK")
fi
if [ -z "$HOOKS_DIR" ] || [ ! -d "$HOOKS_DIR" ]; then
    echo "[mock-claude] cannot find hooks dir; set CC_AUTOPIPE_HOOKS_DIR" >&2
    exit 1
fi

run_hook() {
    local hook_name=$1
    local input_json=$2
    local hook_script="$HOOKS_DIR/$hook_name.sh"
    if [ ! -x "$hook_script" ]; then
        echo "[mock-claude] hook missing: $hook_script" >&2
        return 1
    fi
    echo "[mock-claude] firing hook: $hook_name" >&2
    printf '%s' "$input_json" | "$hook_script"
}

# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

dump_stop_input() {
    local input=$1
    [ -n "${CC_AUTOPIPE_MOCK_DUMP_INPUT:-}" ] || return 0
    printf '%s' "$input" > "$CC_AUTOPIPE_MOCK_DUMP_INPUT"
}

scenario_success() {
    run_hook session-start "$(jq -nc \
        --arg sid "$SESSION_ID" --arg cwd "$PROJECT_DIR" \
        '{session_id:$sid,cwd:$cwd}')"
    run_hook pre-tool-use "$(jq -nc \
        --arg sid "$SESSION_ID" --arg cwd "$PROJECT_DIR" \
        '{session_id:$sid,cwd:$cwd,tool_name:"Bash",tool_input:{command:"echo hello"}}')"
    local stop_input
    stop_input=$(jq -nc \
        --arg sid "$SESSION_ID" --arg cwd "$PROJECT_DIR" \
        '{session_id:$sid,cwd:$cwd}')
    dump_stop_input "$stop_input"
    run_hook stop "$stop_input"
}

scenario_verify_fail() {
    run_hook session-start "$(jq -nc \
        --arg sid "$SESSION_ID" --arg cwd "$PROJECT_DIR" \
        '{session_id:$sid,cwd:$cwd}')"
    local stop_input
    stop_input=$(jq -nc \
        --arg sid "$SESSION_ID" --arg cwd "$PROJECT_DIR" \
        '{session_id:$sid,cwd:$cwd}')
    dump_stop_input "$stop_input"
    run_hook stop "$stop_input"
}

scenario_rate_limit() {
    run_hook session-start "$(jq -nc \
        --arg sid "$SESSION_ID" --arg cwd "$PROJECT_DIR" \
        '{session_id:$sid,cwd:$cwd}')"
    run_hook stop-failure "$(jq -nc \
        --arg sid "$SESSION_ID" --arg cwd "$PROJECT_DIR" \
        '{session_id:$sid,cwd:$cwd,error:"rate_limit",error_details:"429 Too Many Requests"}')"
}

scenario_block_secret() {
    run_hook session-start "$(jq -nc \
        --arg sid "$SESSION_ID" --arg cwd "$PROJECT_DIR" \
        '{session_id:$sid,cwd:$cwd}')"
    set +e
    run_hook pre-tool-use "$(jq -nc \
        --arg sid "$SESSION_ID" --arg cwd "$PROJECT_DIR" \
        '{session_id:$sid,cwd:$cwd,tool_name:"Bash",tool_input:{command:"cat ~/.cc-autopipe/secrets.env"}}')"
    rc=$?
    set -e
    if [ $rc -eq 2 ]; then
        echo "[mock-claude] PreToolUse correctly blocked secret access (exit 2)" >&2
        return 0
    fi
    echo "[mock-claude] EXPECTED block (exit 2), got $rc" >&2
    return 1
}

scenario_long_bash() {
    run_hook session-start "$(jq -nc \
        --arg sid "$SESSION_ID" --arg cwd "$PROJECT_DIR" \
        '{session_id:$sid,cwd:$cwd}')"
    set +e
    run_hook pre-tool-use "$(jq -nc \
        --arg sid "$SESSION_ID" --arg cwd "$PROJECT_DIR" \
        '{session_id:$sid,cwd:$cwd,tool_name:"Bash",tool_input:{command:"pip install some-large-package"}}')"
    rc=$?
    set -e
    if [ $rc -eq 2 ]; then
        echo "[mock-claude] PreToolUse correctly blocked long-op (exit 2)" >&2
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

if [ "$STYLE" = "popen" ]; then
    if [ -n "$RESUME_ID" ]; then
        echo "[mock-claude] resuming session $RESUME_ID" >&2
    fi
    if [ -n "${CC_AUTOPIPE_MOCK_SLEEP_SEC:-}" ]; then
        sleep "$CC_AUTOPIPE_MOCK_SLEEP_SEC"
    fi
fi

case "$SCENARIO" in
    success)      scenario_success ;;
    verify-fail)  scenario_verify_fail ;;
    rate-limit)   scenario_rate_limit ;;
    block-secret) scenario_block_secret ;;
    long-bash)    scenario_long_bash ;;
    *)
        echo "[mock-claude] unknown scenario: $SCENARIO" >&2
        echo "Available: success, verify-fail, rate-limit, block-secret, long-bash" >&2
        exit 1
        ;;
esac

exit "${CC_AUTOPIPE_MOCK_EXIT_RC:-0}"
