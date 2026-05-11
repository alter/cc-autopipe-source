#!/bin/bash
# stop-failure.sh — handle API errors from claude -p subprocess.
# Refs: SPEC.md §10.4, §9.3, §15.2
#
# Input:  stdin JSON {error, error_details, ...}
# Output: state mutations, TG alert on rate_limit / failed
# Exit:   0
#
# 429 resume_at resolution (v1.5.0):
#   1. Parse retry-after from $DETAILS itself — ISO 8601 timestamp,
#      Retry-After header, or relative-time prose. Authoritative when
#      present: the 429 response IS the rate-limit signal, quota.py's
#      cache can lag by up to 60s.
#   2. Try quota.py — if it gives us five_hour.resets_at, use that
#      (with the §9.4 60s safety margin already applied by the engine
#      when the orchestrator's pre-flight kicks in).
#   3. Fall back to ratelimit.py register-429 (v1.5.0: flat 15min).
#   4. Last resort: 15min flat fallback (v1.5.0; was 1h pre-v1.5.0).

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

# v1.5.0: parse retry-after from the 429 message itself. Anthropic can
# surface the precise reset time via the response body or the
# Retry-After header; that's authoritative — quota.py's cache may lag
# by up to 60s. Order (prose AHEAD of bare-seconds header form so that
# "retry after 15 minutes" reads as 15min, not 15s):
#   1. ISO 8601 timestamp in error_details (e.g. "Resets at 2026-05-11T18:10:00Z")
#   2. Relative-time prose ("in 15 minutes", "retry after 600 seconds")
#   3. Retry-After / X-RateLimit-Reset header (seconds, no unit)
# All three feed into the existing RESUME_AT pipeline. DETAILS is passed
# via env var (not interpolated into a HEREDOC) to keep arbitrary shell
# metacharacters in the message safe.
PARSED_RESUME_AT=""
if [ "$ERROR" = "rate_limit" ] || [ "$ERROR" = "429" ] || [ "$ERROR" = "RATE_LIMIT" ]; then
    PARSED_RESUME_AT=$(DETAILS="$DETAILS" python3 -c "
import os, re, sys
from datetime import datetime, timedelta, timezone

text = os.environ.get('DETAILS', '') or ''

# 1. ISO 8601 timestamp.
m = re.search(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(Z|[+-]\d{2}:?\d{2})?', text)
if m:
    raw = m.group(1) + (m.group(2) or 'Z')
    raw = raw.replace('Z', '+00:00')
    try:
        dt = (datetime.fromisoformat(raw) + timedelta(seconds=60)).astimezone(timezone.utc)
        print(dt.strftime('%Y-%m-%dT%H:%M:%SZ'))
        sys.exit(0)
    except ValueError:
        pass

# 2. Relative-time prose ('retry after 15 minutes', 'in 600 seconds').
#    Runs ahead of the bare-seconds header form so 'retry after 15 minutes'
#    is read as 15min, not as Retry-After: 15.
m = re.search(r'(?:retry\s+after|in)\s+(\d+)\s+(second|minute|hour)s?', text, re.I)
if m:
    n = int(m.group(1))
    unit = m.group(2).lower()
    secs = n * {'second': 1, 'minute': 60, 'hour': 3600}[unit]
    dt = (datetime.now(timezone.utc) + timedelta(seconds=secs + 60)).astimezone(timezone.utc)
    print(dt.strftime('%Y-%m-%dT%H:%M:%SZ'))
    sys.exit(0)

# 3. Retry-After / X-RateLimit-Reset header (seconds, no unit).
m = re.search(r'(?:retry[-_]after|x[-_]ratelimit[-_]reset)[:\s]+(\d+)', text, re.I)
if m:
    secs = int(m.group(1)) + 60
    dt = (datetime.now(timezone.utc) + timedelta(seconds=secs)).astimezone(timezone.utc)
    print(dt.strftime('%Y-%m-%dT%H:%M:%SZ'))
    sys.exit(0)
" 2>/dev/null || echo "")
fi

case "$ERROR" in
    rate_limit|429|RATE_LIMIT)
        QUOTA_PY="$CC_AUTOPIPE_HOME/lib/quota.py"
        RATELIMIT_PY="$CC_AUTOPIPE_HOME/lib/ratelimit.py"

        RESUME_AT=""
        RESOLVE_VIA=""

        # v1.5.0: parsed retry-after wins over everything else.
        if [ -n "$PARSED_RESUME_AT" ]; then
            RESUME_AT="$PARSED_RESUME_AT"
            RESOLVE_VIA="parsed_message"
        fi

        # Try quota cache next.
        if [ -z "$RESUME_AT" ]; then
            QUOTA_JSON=$(python3 "$QUOTA_PY" read 2>/dev/null || true)
            QUOTA_RESETS_AT=""
            if [ -n "$QUOTA_JSON" ]; then
                QUOTA_RESETS_AT=$(printf '%s' "$QUOTA_JSON" | jq -r '.five_hour.resets_at // empty' 2>/dev/null || echo "")
            fi

            if [ -n "$QUOTA_RESETS_AT" ]; then
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
                if [ -n "$RESUME_AT" ]; then
                    RESOLVE_VIA="quota"
                fi
            fi
        fi

        if [ -z "$RESUME_AT" ]; then
            # Fall back to ratelimit. v1.5.0: flat 15min via FALLBACK_WAIT_SEC.
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
            # Last-resort fallback. v1.5.0: 15min flat (was 1h pre-v1.5.0).
            RESUME_AT=$(python3 -c "
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) + timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M:%SZ'))
")
            RESOLVE_VIA="fallback(15min)"
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
