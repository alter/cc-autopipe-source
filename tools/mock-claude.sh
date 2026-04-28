#!/bin/bash
# tools/mock-claude.sh
# Fake `claude` binary for testing hooks without burning real MAX quota.
#
# Usage: simulates claude -p invocation by firing hooks with synthetic input.
#
# Reads tools/mock-claude.config.json for canned scenarios.
# Hooks are read from .claude/settings.json or CC_AUTOPIPE_HOOKS_DIR.
#
# Modes:
#   --scenario success         Run SessionStart -> 1 PreToolUse(Bash) -> Stop
#   --scenario verify-fail     Same but Stop hook receives verify-fail signal
#   --scenario rate-limit      Run SessionStart -> StopFailure with rate_limit
#   --scenario block-secret    Run SessionStart -> PreToolUse(Bash) blocking secrets path
#
# Exit codes: 0 = scenario success, non-zero = scenario asserted failure

set -euo pipefail

SCENARIO="${1:-success}"
PROJECT_DIR="${2:-$(pwd)}"
SESSION_ID="mock-session-$(date +%s)"

HOOKS_DIR="${CC_AUTOPIPE_HOOKS_DIR:-$PROJECT_DIR/.claude/hooks}"
if [ ! -d "$HOOKS_DIR" ]; then
    # Look for hooks in settings.json
    if [ -f "$PROJECT_DIR/.claude/settings.json" ]; then
        # Extract one hook path to find HOOKS_DIR
        FIRST_HOOK=$(jq -r '.hooks.SessionStart[0].hooks[0].command // empty' "$PROJECT_DIR/.claude/settings.json")
        if [ -n "$FIRST_HOOK" ]; then
            HOOKS_DIR=$(dirname "$FIRST_HOOK")
        fi
    fi
fi

if [ ! -d "$HOOKS_DIR" ]; then
    echo "ERROR: cannot find hooks dir. Set CC_AUTOPIPE_HOOKS_DIR." >&2
    exit 1
fi

run_hook() {
    local hook_name=$1
    local input_json=$2
    local hook_script="$HOOKS_DIR/$hook_name.sh"
    
    if [ ! -x "$hook_script" ]; then
        echo "[mock-claude] hook not found or not executable: $hook_script" >&2
        return 1
    fi
    
    echo "[mock-claude] firing hook: $hook_name" >&2
    echo "$input_json" | "$hook_script"
}

case "$SCENARIO" in
    success)
        run_hook session-start "{\"session_id\":\"$SESSION_ID\",\"cwd\":\"$PROJECT_DIR\"}"
        run_hook pre-tool-use "{\"session_id\":\"$SESSION_ID\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"echo hello\"}}"
        run_hook stop "{\"session_id\":\"$SESSION_ID\",\"cwd\":\"$PROJECT_DIR\"}"
        ;;
    
    verify-fail)
        run_hook session-start "{\"session_id\":\"$SESSION_ID\",\"cwd\":\"$PROJECT_DIR\"}"
        # Stop hook will run verify.sh which is expected to fail
        run_hook stop "{\"session_id\":\"$SESSION_ID\",\"cwd\":\"$PROJECT_DIR\"}"
        ;;
    
    rate-limit)
        run_hook session-start "{\"session_id\":\"$SESSION_ID\",\"cwd\":\"$PROJECT_DIR\"}"
        run_hook stop-failure "{\"session_id\":\"$SESSION_ID\",\"error\":\"rate_limit\",\"error_details\":\"429 Too Many Requests\"}"
        ;;
    
    block-secret)
        run_hook session-start "{\"session_id\":\"$SESSION_ID\",\"cwd\":\"$PROJECT_DIR\"}"
        # This SHOULD be blocked by pre-tool-use hook
        run_hook pre-tool-use "{\"session_id\":\"$SESSION_ID\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"cat ~/.cc-autopipe/secrets.env\"}}"
        local rc=$?
        if [ $rc -eq 2 ]; then
            echo "[mock-claude] PreToolUse correctly blocked secret access (exit 2)" >&2
            exit 0
        else
            echo "[mock-claude] EXPECTED block (exit 2), got $rc" >&2
            exit 1
        fi
        ;;
    
    long-bash)
        run_hook session-start "{\"session_id\":\"$SESSION_ID\",\"cwd\":\"$PROJECT_DIR\"}"
        run_hook pre-tool-use "{\"session_id\":\"$SESSION_ID\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"pip install some-large-package\"}}"
        local rc=$?
        if [ $rc -eq 2 ]; then
            echo "[mock-claude] PreToolUse correctly blocked long-op (exit 2)" >&2
            exit 0
        fi
        exit 1
        ;;
    
    *)
        echo "Unknown scenario: $SCENARIO" >&2
        echo "Available: success, verify-fail, rate-limit, block-secret, long-bash" >&2
        exit 1
        ;;
esac
