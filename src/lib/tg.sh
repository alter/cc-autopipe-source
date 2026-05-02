#!/bin/bash
# tg.sh — fire-and-forget Telegram notification.
# Usage: tg.sh "message text"
# Always exits 0 — never blocks the pipeline on TG failure.
# Refs: SPEC.md §6.5

set -u
# Note: -e and pipefail intentionally NOT set. We must not abort
# on missing secrets, missing curl, network errors, etc.

# Secrets path resolution (most-specific wins):
#   1. $CC_AUTOPIPE_SECRETS_FILE         — explicit override (tests, install)
#   2. $CC_AUTOPIPE_USER_HOME/secrets.env — follows isolated user_home
#   3. $HOME/.cc-autopipe/secrets.env    — production default
# Resolving via user_home (#2) means any caller that already isolates state
# (tests, sandboxes) automatically isolates secrets too — no additional env
# var needed. Without #2, pytest subprocesses inherited only USER_HOME and
# silently fired real TG against the real secrets.env (Q20 / 2026-05-02).
SECRETS_FILE="${CC_AUTOPIPE_SECRETS_FILE:-${CC_AUTOPIPE_USER_HOME:-$HOME/.cc-autopipe}/secrets.env}"

# shellcheck disable=SC1090
[ -r "$SECRETS_FILE" ] && . "$SECRETS_FILE" 2>/dev/null

if [ -z "${TG_BOT_TOKEN:-}" ] || [ -z "${TG_CHAT_ID:-}" ]; then
    exit 0
fi

MSG="${1:-}"
[ -z "$MSG" ] && exit 0

# Truncate very long messages to fit Telegram's 4096-char limit comfortably.
if [ ${#MSG} -gt 3000 ]; then
    MSG="${MSG:0:2900}... [truncated]"
fi

# Debug instrumentation (Q20). When CC_AUTOPIPE_TG_TRACE=/path/file is set,
# log every invocation that reaches the actual curl. Used by test runners to
# verify no real TG fires during pytest. Intentionally placed AFTER the
# no-creds early exit so the log only records would-be-real sends.
if [ -n "${CC_AUTOPIPE_TG_TRACE:-}" ]; then
    printf '%s\tsecrets=%s\tmsg_len=%d\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "$SECRETS_FILE" \
        "${#MSG}" >> "$CC_AUTOPIPE_TG_TRACE" 2>/dev/null || true
fi

curl -s -X POST \
    --max-time 3 \
    "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TG_CHAT_ID}" \
    --data-urlencode "text=${MSG}" \
    -d "disable_web_page_preview=true" \
    > /dev/null 2>&1 || true

exit 0
