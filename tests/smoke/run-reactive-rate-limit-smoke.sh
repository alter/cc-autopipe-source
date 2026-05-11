#!/bin/bash
# tests/smoke/run-reactive-rate-limit-smoke.sh — v1.5.0 end-to-end:
# stop-failure.sh parses retry-after from a synthetic 429 message,
# state.json pauses with resolved_via=parsed_message, no 5h_pre_check
# event fires (the preflight branch was removed in v1.5.0), and the
# project auto-resumes via _resume_paused_if_due after the parsed
# resume time elapses.
#
# Refs: PROMPT_v1.5.0.md GROUP REACTIVE-429-PARSE-FIRST + smoke S1.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }
die() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

if [ -x "$REPO_ROOT/.venv/bin/python3" ]; then
    PY="$REPO_ROOT/.venv/bin/python3"
else
    PY=python3
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

UHOME="$TMP/uhome"
PROJ="$TMP/proj"
mkdir -p "$UHOME"
mkdir -p "$PROJ"
(cd "$PROJ" && git init -q)

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_USER_HOME="$UHOME"
export CC_AUTOPIPE_QUOTA_DISABLED=1

bash "$REPO_ROOT/src/helpers/cc-autopipe" init "$PROJ" >/dev/null

log "synthesize a 429 stop-hook payload with a parseable ISO timestamp"
# Build an ISO timestamp 2 seconds in the future. stop-failure adds a
# +60s safety margin on top of the parsed timestamp, so the project
# resumes ~62 seconds after this point. Kept short to keep the smoke
# fast, while still verifying the parse-then-resume round trip.
RESUME_TARGET=$("$PY" -c "
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) + timedelta(seconds=2)).strftime('%Y-%m-%dT%H:%M:%SZ'))
")

PAYLOAD=$("$PY" -c "
import json, sys
print(json.dumps({
    'cwd': '$PROJ',
    'error': 'rate_limit',
    'error_details': 'Rate limit exceeded. Resets at $RESUME_TARGET',
}))
")

printf '%s' "$PAYLOAD" | bash "$REPO_ROOT/src/hooks/stop-failure.sh"

# Assertion 1: state.paused.resume_at matches the parsed timestamp +60s
# safety margin (±5s for clock drift between sample and assert).
PHASE=$("$PY" -c "
import json
print(json.load(open('$PROJ/.cc-autopipe/state.json'))['phase'])
")
[ "$PHASE" = "paused" ] || die "expected phase=paused, got $PHASE"

REASON=$("$PY" -c "
import json
print(json.load(open('$PROJ/.cc-autopipe/state.json'))['paused']['reason'])
")
[ "$REASON" = "rate_limit" ] || die "expected paused.reason=rate_limit, got $REASON"

ACTUAL_RESUME=$("$PY" -c "
import json
print(json.load(open('$PROJ/.cc-autopipe/state.json'))['paused']['resume_at'])
")
log "resume_at=$ACTUAL_RESUME (target=$RESUME_TARGET +60s safety)"

"$PY" - <<PY
from datetime import datetime, timezone, timedelta
def parse(s): return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
target = parse("$RESUME_TARGET") + timedelta(seconds=60)
actual = parse("$ACTUAL_RESUME")
delta = abs((actual - target).total_seconds())
assert delta < 5, f"resume_at off by {delta}s; got {actual}, expected ~{target}"
PY
ok "resume_at matches parsed-message + 60s safety margin"

# Assertion 2: aggregate event records resolved_via=parsed_message.
AGG="$UHOME/log/aggregate.jsonl"
[ -f "$AGG" ] || die "aggregate.jsonl missing"
VIA=$(tail -1 "$AGG" | "$PY" -c "
import json, sys
print(json.loads(sys.stdin.read()).get('resolved_via', ''))
")
[ "$VIA" = "parsed_message" ] || die "expected resolved_via=parsed_message, got '$VIA'"
ok "aggregate event resolved_via=parsed_message"

# Assertion 3: no paused event with reason=5h_pre_check (v1.5.0 removed
# the preflight branch entirely; only rate_limit + 7d_pre_check + disk
# reasons remain).
if grep -E '"reason":"5h_pre_check"|"5h_pre_check"' "$AGG" >/dev/null; then
    die "aggregate.jsonl contains 5h_pre_check — preflight branch should be gone in v1.5.0"
fi
ok "no 5h_pre_check events emitted"

# Assertion 4: sleep until just past resume_at + 60s, then run orchestrator
# one loop. State.phase should auto-flip back to "active".
log "sleeping until past resume_at, then driving one orchestrator loop"
NOW_SEC=$("$PY" -c "import time; print(int(time.time()))")
RESUME_SEC=$("$PY" -c "
from datetime import datetime, timezone
import calendar
dt = datetime.strptime('$ACTUAL_RESUME', '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
print(calendar.timegm(dt.timetuple()))
")
WAIT_SEC=$(( RESUME_SEC - NOW_SEC + 2 ))
# Cap defensive: target is +2s + 60s safety margin = ~62s. Allow up to
# 90s to absorb scheduling jitter without making a flaky test hang on
# unbounded sleeps.
if [ "$WAIT_SEC" -gt 0 ] && [ "$WAIT_SEC" -lt 90 ]; then
    sleep "$WAIT_SEC"
fi

CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$UHOME" \
CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_MAX_LOOPS=1 \
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
CC_AUTOPIPE_QUOTA_DISABLED=1 \
    "$PY" "$REPO_ROOT/src/orchestrator" >/dev/null 2>"$TMP/orch.err" \
    || die "orchestrator failed: $(cat "$TMP/orch.err")"

PHASE_AFTER=$("$PY" -c "
import json
print(json.load(open('$PROJ/.cc-autopipe/state.json'))['phase'])
")
[ "$PHASE_AFTER" = "active" ] || die "expected auto-resume to active, got $PHASE_AFTER"

PAUSED_AFTER=$("$PY" -c "
import json
print(json.load(open('$PROJ/.cc-autopipe/state.json'))['paused'])
")
[ "$PAUSED_AFTER" = "None" ] || die "expected paused=None after resume, got $PAUSED_AFTER"
ok "project auto-resumed via _resume_paused_if_due"

printf '\033[32m===\033[0m PASS — v1.5.0 REACTIVE-RATE-LIMIT smoke\n'
