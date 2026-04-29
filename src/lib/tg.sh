#!/bin/bash
# tg.sh — fire-and-forget Telegram notification.
# Usage: tg.sh "message text"
# Always exits 0 — never blocks the pipeline on TG failure.
# Refs: SPEC.md §6.5

set -u
# Note: -e and pipefail intentionally NOT set. We must not abort
# on missing secrets, missing curl, network errors, etc.

SECRETS_FILE="${CC_AUTOPIPE_SECRETS_FILE:-$HOME/.cc-autopipe/secrets.env}"

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

curl -s -X POST \
    --max-time 3 \
    "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TG_CHAT_ID}" \
    --data-urlencode "text=${MSG}" \
    -d "disable_web_page_preview=true" \
    > /dev/null 2>&1 || true

exit 0
