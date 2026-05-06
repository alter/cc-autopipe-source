#!/bin/bash
# tests/smoke/v134/test_network_probe.sh — v1.3.4 Group R3 smoke.
#
# Real-CLI smoke: replace src/lib/transient.py with a PYTHONPATH-
# injected stub that returns False for the first 3 reachability
# probes, then True. The orchestrator must:
#   - emit network_probe_failed on the failed first probe
#   - sleep through the (overridden 1s) backoff
#   - emit network_probe_recovered on the eventually-True probe
#   - run the cycle normally afterwards
#   - NEVER increment consecutive_failures
#
# Also asserts: no `claude_invocation_transient` events fired (the
# probe path is independent of claude classification).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }
die() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

TMP=$(mktemp -d)

# Smoke must restore src/lib/transient.py on every exit path. The
# orchestrator loads transient via the runtime's hard-coded
# `sys.path.insert(0, src/lib)` so PYTHONPATH alone cannot override
# it; we physically swap the file and put it back on EXIT (incl. fail
# / SIGINT). Idempotent: trap fires once.
TRANSIENT_REAL="$REPO_ROOT/src/lib/transient.py"
TRANSIENT_BAK="$TMP/transient.real.bak"
cp "$TRANSIENT_REAL" "$TRANSIENT_BAK"
restore_transient() {
    if [ -f "$TRANSIENT_BAK" ]; then
        cp "$TRANSIENT_BAK" "$TRANSIENT_REAL"
    fi
    rm -rf "$TMP"
}
trap restore_transient EXIT
trap 'restore_transient; exit 130' INT TERM

# Drop the stub in place — the orchestrator subprocess will import it
# verbatim because src/lib is first on sys.path.
cp "$REPO_ROOT/tests/smoke/v134/conftest_stubs/transient.py" "$TRANSIENT_REAL"

UHOME="$TMP/uhome"
PROJ="$TMP/proj"
PROBE_COUNTER="$TMP/probe-counter"
mkdir -p "$UHOME/log" "$PROJ"

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_USER_HOME="$UHOME"
export CC_AUTOPIPE_QUOTA_DISABLED=1
# IMPORTANT: do NOT set CC_AUTOPIPE_NETWORK_PROBE_DISABLED — we want
# the gate to actually probe (against our stub).
unset CC_AUTOPIPE_NETWORK_PROBE_DISABLED || true
export CC_AUTOPIPE_NO_REDIRECT=1
# Collapse the backoff so the smoke runs in a few seconds, not 17min.
export CC_AUTOPIPE_NETWORK_PROBE_BACKOFF_OVERRIDE="1,1,1,1,1"
export CC_AUTOPIPE_PROBE_COUNTER_FILE="$PROBE_COUNTER"
export CC_AUTOPIPE_CLAUDE_BIN="$REPO_ROOT/tools/mock-claude.sh"
export CC_AUTOPIPE_MOCK_SCENARIO=success
export CC_AUTOPIPE_COOLDOWN_SEC=0
export CC_AUTOPIPE_IDLE_SLEEP_SEC=0

# 1. Initialise project via real CLI.
log "cc-autopipe init via real CLI"
bash "$REPO_ROOT/src/helpers/cc-autopipe" init "$PROJ" >/dev/null
[ -f "$PROJ/.cc-autopipe/state.json" ] || die "init did not create state.json"
ok "init seeded project"

# Passing verify.sh — we're testing the network gate, not verify.
cat > "$PROJ/.cc-autopipe/verify.sh" <<'VERIFY'
#!/bin/bash
printf '{"passed": true, "score": 0.95, "prd_complete": false}\n'
exit 0
VERIFY
chmod +x "$PROJ/.cc-autopipe/verify.sh"

# 2. Run one cycle. The stub fails the first 3 probes, succeeds on the
#    4th — the gate should recover during backoff and run claude.
log "cc-autopipe run --once with probe stub (False, False, False, True)"
echo "$PROJ" > "$UHOME/projects.list"
bash "$REPO_ROOT/src/helpers/cc-autopipe" run "$PROJ" --once >/dev/null 2>&1 || true

# 3. Assert events.
AGG="$UHOME/log/aggregate.jsonl"
[ -f "$AGG" ] || die "aggregate.jsonl missing at $AGG"

FAILED_COUNT=$(grep -c '"event":"network_probe_failed"' "$AGG" || true)
[ "$FAILED_COUNT" -ge 1 ] \
    || die "expected network_probe_failed event; aggregate:\n$(cat "$AGG")"
ok "network_probe_failed event emitted ($FAILED_COUNT occurrences)"

RECOVERED_COUNT=$(grep -c '"event":"network_probe_recovered"' "$AGG" || true)
[ "$RECOVERED_COUNT" -ge 1 ] \
    || die "expected network_probe_recovered event after stub flips True"
ok "network_probe_recovered event emitted ($RECOVERED_COUNT occurrences)"

# Probe stub also emits internet_up=false because is_internet_reachable
# is hard-coded false; verify the field landed in the failed event.
INTERNET_UP=$(grep '"event":"network_probe_failed"' "$AGG" | head -n 1 \
    | "$PY" -c "import sys, json; print(json.loads(sys.stdin.read())['internet_up'])")
[ "$INTERNET_UP" = "False" ] || die "internet_up field expected False, got $INTERNET_UP"
ok "internet_up=False captured in network_probe_failed event"

GIVING_UP_COUNT=$(grep -c '"event":"network_probe_giving_up"' "$AGG" || true)
[ "$GIVING_UP_COUNT" -eq 0 ] \
    || die "did NOT expect network_probe_giving_up (stub recovered); got $GIVING_UP_COUNT"
ok "no network_probe_giving_up (stub recovered before exhaustion)"

# 4. Verify state: gate did NOT increment failures.
FAILURES=$("$PY" -c "import json; s=json.load(open('$PROJ/.cc-autopipe/state.json')); print(s['consecutive_failures'])")
[ "$FAILURES" = "0" ] \
    || die "network probe deferral must NOT bump consecutive_failures, got $FAILURES"
ok "consecutive_failures=0 (gate did not poison structural counter)"

TRANSIENT_COUNT=$(grep -c '"event":"claude_invocation_transient"' "$AGG" || true)
[ "$TRANSIENT_COUNT" -eq 0 ] \
    || die "did NOT expect claude_invocation_transient; got $TRANSIENT_COUNT"
ok "no claude_invocation_transient (gate path is separate from classifier)"

printf '\033[32m===\033[0m PASS — v1.3.4 R9 network probe smoke\n'
