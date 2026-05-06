#!/bin/bash
# tests/smoke/v134/test_transient_retry.sh — v1.3.4 Group R4 smoke.
#
# Real-CLI smoke: a mock-claude that returns "Server is temporarily
# limiting requests" + rc=1 for the first 2 invocations and exits 0
# on the 3rd must NOT bump consecutive_failures. The engine should
# emit two claude_invocation_transient events, sleep through the
# overridden backoff, and then complete a normal rc=0 cycle.
#
# Per acceptance criteria: NO Python heredoc — uses real CLI commands
# (cc-autopipe init, cc-autopipe run --once via the helpers/dispatcher)
# with state.json crafted via raw JSON heredoc into a file.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }
die() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

UHOME="$TMP/uhome"
PROJ="$TMP/proj"
COUNTER="$TMP/mock-counter"
mkdir -p "$UHOME/log" "$PROJ"

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_USER_HOME="$UHOME"
export CC_AUTOPIPE_QUOTA_DISABLED=1
export CC_AUTOPIPE_NETWORK_PROBE_DISABLED=1
export CC_AUTOPIPE_NO_REDIRECT=1
export CC_AUTOPIPE_TRANSIENT_BACKOFF_OVERRIDE="1,1,1,1,1"
export CC_AUTOPIPE_MOCK_TRANSIENT_THEN_OK=2
export CC_AUTOPIPE_MOCK_COUNTER_FILE="$COUNTER"
export CC_AUTOPIPE_CLAUDE_BIN="$REPO_ROOT/tools/mock-claude.sh"
export CC_AUTOPIPE_MOCK_SCENARIO=success
export CC_AUTOPIPE_COOLDOWN_SEC=0
export CC_AUTOPIPE_IDLE_SLEEP_SEC=0

# 1. Initialise project via real CLI.
log "cc-autopipe init via real CLI"
bash "$REPO_ROOT/src/helpers/cc-autopipe" init "$PROJ" >/dev/null
[ -f "$PROJ/.cc-autopipe/state.json" ] || die "init did not create state.json"
ok "init seeded project"

# Ensure verify.sh always passes — we are testing the transient-retry
# path, not verify scoring. Without this, the success cycle's verify
# might fail and bump consecutive_failures, which would mask whether
# transient handling itself is poisoning the counter.
cat > "$PROJ/.cc-autopipe/verify.sh" <<'VERIFY'
#!/bin/bash
printf '{"passed": true, "score": 0.95, "prd_complete": false}\n'
exit 0
VERIFY
chmod +x "$PROJ/.cc-autopipe/verify.sh"

# 2. Run three cycles. Cycle 1 + 2 are transient (mock exits 1 with
#    transient stderr); cycle 3 lets the mock fall through to the
#    success scenario which fires hooks and exits 0.
log "running 3 cycles via cc-autopipe-run"
echo "$PROJ" > "$UHOME/projects.list"
RC=0
for i in 1 2 3; do
    log "  cycle $i"
    bash "$REPO_ROOT/src/helpers/cc-autopipe" run "$PROJ" --once >/dev/null 2>&1 || RC=$?
done
log "loop exit rc=$RC"

# 3. Verify aggregate.jsonl events.
AGG="$UHOME/log/aggregate.jsonl"
[ -f "$AGG" ] || die "aggregate.jsonl missing at $AGG"
TRANSIENT_COUNT=$(grep -c '"event":"claude_invocation_transient"' "$AGG" || true)
[ "$TRANSIENT_COUNT" -eq 2 ] \
    || die "expected 2 claude_invocation_transient events, got $TRANSIENT_COUNT; aggregate:\n$(cat "$AGG")"
ok "2 claude_invocation_transient events emitted"

EXHAUSTED_COUNT=$(grep -c '"event":"claude_invocation_retry_exhausted"' "$AGG" || true)
[ "$EXHAUSTED_COUNT" -eq 0 ] \
    || die "did NOT expect retry_exhausted in this run; got $EXHAUSTED_COUNT"
ok "no retry_exhausted (under MAX_TRANSIENT_RETRIES)"

# 4. Verify state.json: counters reset after the successful cycle.
PHASE=$("$PY" -c "import json; s=json.load(open('$PROJ/.cc-autopipe/state.json')); print(s['phase'])")
TRANSIENT=$("$PY" -c "import json; s=json.load(open('$PROJ/.cc-autopipe/state.json')); print(s['consecutive_transient_failures'])")
FAILURES=$("$PY" -c "import json; s=json.load(open('$PROJ/.cc-autopipe/state.json')); print(s['consecutive_failures'])")
[ "$TRANSIENT" = "0" ] || die "consecutive_transient_failures should reset to 0, got $TRANSIENT"
[ "$FAILURES" = "0" ] || die "consecutive_failures should NEVER increment on transients, got $FAILURES"
ok "consecutive_transient_failures=0 after successful cycle"
ok "consecutive_failures=0 (transients did not poison structural counter)"
ok "phase=$PHASE"

printf '\033[32m===\033[0m PASS — v1.3.4 R8 transient retry smoke\n'
