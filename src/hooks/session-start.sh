#!/bin/bash
# session-start.sh — context summary injected at every claude -p invocation.
# Refs: SPEC.md §10.1
#
# Input:  stdin JSON from Claude Code (session_id, cwd, ...)
# Output: plain text to stdout (becomes part of Claude's context)
# Exit:   always 0 — failures here must not abort the session

set -u
# NOT -e: a transient jq/tail/grep failure must not skip the closing
# log_event call. Each step has its own fallback.

INPUT=$(cat || true)

# Prefer cwd from stdin (Claude Code documents passing it explicitly);
# fall back to the actual cwd.
PROJECT=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
if [ -z "${PROJECT:-}" ] || [ ! -d "$PROJECT" ]; then
    PROJECT=$(pwd)
fi

CCA="$PROJECT/.cc-autopipe"
STATE="$CCA/state.json"
CONFIG="$CCA/config.yaml"
BACKLOG="$PROJECT/backlog.md"
CHECKPOINT="$CCA/checkpoint.md"
FAILURES="$CCA/memory/failures.jsonl"

NAME=$(grep -E '^name:' "$CONFIG" 2>/dev/null | head -1 | awk -F'name:[ ]*' '{print $2}' | tr -d '"' || echo "")
[ -z "$NAME" ] && NAME=$(basename "$PROJECT")

PHASE=$(jq -r '.phase // "unknown"' "$STATE" 2>/dev/null || echo "unknown")
ITER=$(jq -r '.iteration // 0' "$STATE" 2>/dev/null || echo "0")
SCORE=$(jq -r '.last_score // "n/a"' "$STATE" 2>/dev/null || echo "n/a")
FAILCOUNT=$(jq -r '.consecutive_failures // 0' "$STATE" 2>/dev/null || echo "0")
OPEN=$(grep -c '^- \[ \]' "$BACKLOG" 2>/dev/null || true)
[ -z "$OPEN" ] && OPEN=0

cat <<EOF
=== cc-autopipe context ===
Project: $NAME
Phase: $PHASE | Iteration: $ITER | Last score: $SCORE | Consecutive failures: $FAILCOUNT
Open backlog tasks: $OPEN

EOF

if [ -f "$CHECKPOINT" ]; then
    echo "**RESUME:** Read .cc-autopipe/checkpoint.md FIRST and continue from there."
    echo
fi

if [ -f "$FAILURES" ]; then
    RECENT=$(tail -3 "$FAILURES" 2>/dev/null | jq -r '"  - \(.error): \(.details // .reason // "")"' 2>/dev/null || true)
    if [ -n "${RECENT:-}" ]; then
        echo "Recent failures (last 3):"
        printf '%s\n' "$RECENT"
        echo
    fi
fi

# v1.2 Bug A + C + D: inject context blocks (current_task + backlog
# top-3 + long-operation guidance). Helper exits 0 even on errors, so
# a transient failure here can never abort the session. The `all`
# subcommand composes whichever sub-blocks have content; empty
# sub-blocks (e.g. no backlog.md, no current_task) are omitted cleanly.
CC_AUTOPIPE_HOME="${CC_AUTOPIPE_HOME:-$HOME/cc-autopipe}"
V12_BLOCKS=$(python3 "$CC_AUTOPIPE_HOME/lib/session_start_helper.py" \
    all "$PROJECT" 2>/dev/null || true)
if [ -n "${V12_BLOCKS:-}" ]; then
    printf '%s\n\n' "$V12_BLOCKS"
fi

# Always log the hook fired — never let a failure here abort the session.
python3 "$CC_AUTOPIPE_HOME/lib/state.py" log-event "$PROJECT" hook_session_start \
    >/dev/null 2>&1 || true

exit 0
