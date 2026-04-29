#!/bin/bash
# stop-failure.sh — handle API errors from claude -p subprocess.
# Refs: SPEC.md §10.4, §9.3, §15.2
#
# Input:  stdin JSON {error, error_details, ...}
# Output: state mutations, TG alert on rate_limit / failed
# Exit:   0
#
# 429 resume_at resolution per SPEC §9.3:
#   1. Try quota.py — if it gives us five_hour.resets_at, use that
#      (with the §9.4 60s safety margin already applied by the engine
#      when the orchestrator's pre-flight kicks in).
#   2. Fall back to ratelimit.py register-429 ladder (5/15/60 min).
#   3. As a last resort if both are unavailable, default to now+1h.

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
        QUOTA_PY="$CC_AUTOPIPE_HOME/lib/quota.py"
        RATELIMIT_PY="$CC_AUTOPIPE_HOME/lib/ratelimit.py"

        # Try quota first per SPEC §9.3.
        QUOTA_JSON=$(python3 "$QUOTA_PY" read 2>/dev/null || true)
        RESUME_AT=""
        QUOTA_RESETS_AT=""
        if [ -n "$QUOTA_JSON" ]; then
            QUOTA_RESETS_AT=$(printf '%s' "$QUOTA_JSON" | jq -r '.five_hour.resets_at // empty' 2>/dev/null || echo "")
        fi

        if [ -n "$QUOTA_RESETS_AT" ]; then
            # Convert quota's resets_at to canonical "...Z" (UTC) with
            # the SPEC §9.4 60s safety margin. Python parses both
            # Z-suffix and +00:00 offset forms; keep timezone UTC the
            # whole way so the printed Z suffix is honest.
            RESUME_AT=$(python3 -c "
import sys
from datetime import datetime, timedelta, timezone
raw = '$QUOTA_RESETS_AT'.replace('Z', '+00:00')
try:
    dt = (datetime.fromisoformat(raw) + timedelta(seconds=60)).astimezone(timezone.utc)
    print(dt.strftime('%Y-%m-%dT%H:%M:%SZ'))
except Exception:
    sys.exit(1)
" 2>/dev/null || echo "")
            RESOLVE_VIA="quota"
        fi

        if [ -z "$RESUME_AT" ]; then
            # Fall back to ratelimit ladder.
            WAIT_SEC=$(python3 "$RATELIMIT_PY" register-429 2>/dev/null || echo "")
            if [ -n "$WAIT_SEC" ]; then
                RESUME_AT=$(python3 -c "
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) + timedelta(seconds=$WAIT_SEC)).strftime('%Y-%m-%dT%H:%M:%SZ'))
" 2>/dev/null || echo "")
                RESOLVE_VIA="ladder($WAIT_SEC s)"
            fi
        fi

        if [ -z "$RESUME_AT" ]; then
            # Last-resort 1h fallback (matches Stage C's behaviour when
            # both quota and ratelimit are completely unavailable).
            RESUME_AT=$(python3 -c "
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'))
")
            RESOLVE_VIA="fallback(1h)"
        fi

        python3 "$STATE_PY" set-paused "$PROJECT" "$RESUME_AT" "rate_limit" \
            >/dev/null 2>&1 || true
        python3 "$STATE_PY" log-event "$PROJECT" paused \
            "reason=rate_limit" "resume_at=$RESUME_AT" "resolved_via=$RESOLVE_VIA" \
            >/dev/null 2>&1 || true
        bash "$TG_SH" "[$PROJECT_NAME] 429, resume at $RESUME_AT (via $RESOLVE_VIA)" || true
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
