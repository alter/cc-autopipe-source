#!/bin/bash
# tools/inject-429.sh
# Simulates StopFailure with rate_limit error for testing backoff logic.
#
# Usage:
#   bash tools/inject-429.sh /path/to/project
#
# Effect: invokes stop-failure.sh with synthetic 429 input, in the project's cwd.

set -euo pipefail

PROJECT_DIR="${1:-$(pwd)}"
HOOKS_DIR="${CC_AUTOPIPE_HOOKS_DIR:-$(pwd)/src/hooks}"

if [ ! -x "$HOOKS_DIR/stop-failure.sh" ]; then
    echo "ERROR: stop-failure.sh not found at $HOOKS_DIR" >&2
    exit 1
fi

cd "$PROJECT_DIR"

INPUT='{"session_id":"inject-test","error":"rate_limit","error_details":"429 Too Many Requests"}'

echo "[inject-429] Firing StopFailure in $PROJECT_DIR"
echo "$INPUT" | "$HOOKS_DIR/stop-failure.sh"

echo "[inject-429] state.json after injection:"
jq . < "$PROJECT_DIR/.cc-autopipe/state.json"
