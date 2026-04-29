#!/bin/bash
# stop-failure.sh — handle API errors from claude -p subprocess.
# Refs: SPEC.md §10.4, §9.3, §15.2
#
# Input:  stdin JSON {error, error_details, ...}
# Output: state mutations, TG alert on rate_limit / failed
# Exit:   0
#
# v0.5 Stage C: 429 / rate_limit → state.phase = "paused" with
# resume_at = now + 1h (conservative fallback). Stage E will replace
# the 1h fallback with quota.py + ratelimit.py ladder.

set -u

INPUT=$(cat || true)

PROJECT=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
if [ -z "${PROJECT:-}" ] || [ ! -d "$PROJECT" ]; then
    PROJECT=$(pwd)
fi

CC_AUTOPIPE_HOME="${CC_AUTOPIPE_HOME:-$HOME/cc-autopipe}"
STATE_PY="$CC_AUTOPIPE_HOME/lib/state.py"
TG_SH="$CC_AUTOPIPE_HOME/lib/tg.sh"

ERROR=$(printf '%s' "$INPUT" | jq -r '.error // empty' 2>/dev/null)
DETAILS=$(printf '%s' "$INPUT" | jq -r '.error_details // empty' 2>/dev/null)

PROJECT_NAME=$(basename "$PROJECT")

case "$ERROR" in
    rate_limit|429|RATE_LIMIT)
        # Conservative 1h pause. Stage E will read quota.py first and
        # use the exact resets_at when available, falling back to the
        # ladder otherwise. SPEC §9.4 mandates a 60s safety margin —
        # 1h naturally includes that and avoids hammering the API.
        # TODO(v0.5-stage-E): replace 1h fallback with quota+ladder per §9.3
        RESUME_AT=$(python3 -c "
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'))
")
        python3 "$STATE_PY" set-paused "$PROJECT" "$RESUME_AT" "rate_limit" \
            >/dev/null 2>&1 || true
        python3 "$STATE_PY" log-event "$PROJECT" paused \
            "reason=rate_limit" "resume_at=$RESUME_AT" >/dev/null 2>&1 || true
        bash "$TG_SH" "[$PROJECT_NAME] 429, resume at $RESUME_AT" || true
        ;;

    "" )
        # No error field present — treat as transient. Increment failures
        # so consecutive_failures still ticks toward the cap, but don't
        # alert TG (would be noisy if Claude exits 0 with empty input).
        python3 "$STATE_PY" inc-failures "$PROJECT" >/dev/null 2>&1 || true
        python3 "$STATE_PY" log-event "$PROJECT" stop_failure_unknown \
            >/dev/null 2>&1 || true
        ;;

    *)
        # Any other error: bump failures, alert TG, let orchestrator's
        # 3-failure cap take it to FAILED if it persists (SPEC §6.1).
        python3 "$STATE_PY" inc-failures "$PROJECT" >/dev/null 2>&1 || true
        python3 "$STATE_PY" log-event "$PROJECT" stop_failure \
            "error=$ERROR" >/dev/null 2>&1 || true
        bash "$TG_SH" "[$PROJECT_NAME] stop_failure: $ERROR ($DETAILS)" || true
        ;;
esac

exit 0
