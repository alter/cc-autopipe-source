#!/bin/bash
# compat.sh — cross-platform shim for date, stat, flock differences.
# Sourced by other scripts; defines CC_AUTOPIPE_OS, CC_AUTOPIPE_DATE_FLAVOR,
# CC_AUTOPIPE_STAT_FLAVOR, and helper functions.
# Refs: SPEC.md §6.6, OPEN_QUESTIONS.md Q8

set -eu

# Coarse OS label, primarily for callers' own branching needs.
case "$(uname -s)" in
    Darwin) CC_AUTOPIPE_OS="macos" ;;
    Linux)  CC_AUTOPIPE_OS="linux" ;;
    *)      CC_AUTOPIPE_OS="unknown" ;;
esac
export CC_AUTOPIPE_OS

# Feature-detect date/stat flavour rather than dispatch on uname.
# Reason: macOS users frequently install GNU coreutils via brew and put
# it ahead of /bin on PATH, which means `date` may be GNU even though
# uname says Darwin. Detection is cheap and avoids a class of bugs.
if date --version >/dev/null 2>&1; then
    CC_AUTOPIPE_DATE_FLAVOR="gnu"
else
    CC_AUTOPIPE_DATE_FLAVOR="bsd"
fi
export CC_AUTOPIPE_DATE_FLAVOR

if stat --version >/dev/null 2>&1; then
    CC_AUTOPIPE_STAT_FLAVOR="gnu"
else
    CC_AUTOPIPE_STAT_FLAVOR="bsd"
fi
export CC_AUTOPIPE_STAT_FLAVOR

# date_from_epoch <epoch_seconds>
# Prints UTC ISO 8601 ("YYYY-MM-DDTHH:MM:SSZ").
date_from_epoch() {
    local ts=$1
    if [ "$CC_AUTOPIPE_DATE_FLAVOR" = "gnu" ]; then
        date -u -d "@$ts" +"%Y-%m-%dT%H:%M:%SZ"
    else
        date -u -r "$ts" +"%Y-%m-%dT%H:%M:%SZ"
    fi
}

# file_mtime <path>
# Prints file modification time as epoch seconds.
file_mtime() {
    local path=$1
    if [ "$CC_AUTOPIPE_STAT_FLAVOR" = "gnu" ]; then
        stat -c %Y "$path"
    else
        stat -f %m "$path"
    fi
}

# now_iso
# Prints current UTC time as ISO 8601.
now_iso() {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

# now_epoch
# Prints current time as epoch seconds.
now_epoch() {
    date -u +"%s"
}
