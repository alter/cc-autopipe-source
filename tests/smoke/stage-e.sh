#!/bin/bash
# tests/smoke/stage-e.sh — Stage E DoD validation end-to-end.
# Refs: AGENTS.md §2 Stage E, SPEC.md §6.3, §6.4, §9

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }
die() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

if [ -x "$REPO_ROOT/.venv/bin/pytest" ]; then
    PYTEST="$REPO_ROOT/.venv/bin/pytest"
    RUFF="$REPO_ROOT/.venv/bin/ruff"
    PY="$REPO_ROOT/.venv/bin/python3"
else
    PYTEST="pytest"
    RUFF="ruff"
    PY="python3"
fi

# 1. Lint.
log "ruff check + format-check"
"$RUFF" check src tests || die "ruff check failed"
"$RUFF" format --check src tests || die "ruff format dirty"
ok "ruff clean"

log "shellcheck on bash files"
SHELL_FILES=$(find src tests tools -type f \( -name '*.sh' -o -path '*/helpers/*' \) ! -name '*.py')
# shellcheck disable=SC2086
shellcheck -x $SHELL_FILES || die "shellcheck failed"
ok "shellcheck clean ($(echo "$SHELL_FILES" | wc -l | tr -d ' ') files)"

# 2. Pytest unit + integration (includes ratelimit, quota, pre-flight).
log "pytest tests/unit tests/integration"
"$PYTEST" tests/unit tests/integration -q || die "pytest failed"
ok "all unit + integration tests pass"

# 3. Hook unit tests.
log "tests/unit/test_hooks/ (bash harness)"
for t in tests/unit/test_hooks/test_*.sh; do
    bash "$t" >/dev/null || die "$t failed"
done
ok "all 4 hook test files pass"

# 4. End-to-end: pre-populate quota cache, observe pre-flight pause.
# v1.5.0: 5h pre-check removed entirely. 5h saturation alone must NOT
# pause the project — the engine relies on Claude CLI's 429 response
# (reactive path in stop-failure.sh) instead.
log "5h saturation alone does NOT pause (v1.5.0 reactive policy)"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
USER_HOME="$SCRATCH/uhome"
PROJECT="$SCRATCH/proj"
mkdir -p "$USER_HOME"
mkdir -p "$PROJECT"
(cd "$PROJECT" && git init -q)
CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$USER_HOME" \
    bash "$REPO_ROOT/src/helpers/cc-autopipe" init "$PROJECT" >/dev/null

# Build a quota cache with 5h utilization at 100% and 7d safely low.
"$PY" -c "
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
five_resets = (datetime.now(timezone.utc) + timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M:%SZ')
seven_resets = (datetime.now(timezone.utc) + timedelta(days=6)).strftime('%Y-%m-%dT%H:%M:%SZ')
Path('$USER_HOME/quota-cache.json').write_text(json.dumps({
    'five_hour':  {'utilization': 1.00, 'resets_at': five_resets},
    'seven_day':  {'utilization': 0.30, 'resets_at': seven_resets},
}))
"

CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$USER_HOME" \
CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_MAX_LOOPS=1 \
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
    "$PY" "$REPO_ROOT/src/orchestrator" >/dev/null 2>"$SCRATCH/orch.err" \
    || die "orchestrator failed: $(cat "$SCRATCH/orch.err")"

PHASE=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['phase'])
")
[ "$PHASE" = "active" ] || die "expected phase=active with 5h saturation in v1.5.0, got $PHASE"
ok "5h-only saturation does not pause (v1.5.0 reactive)"

# 5. End-to-end: 7d threshold pauses + sentinel created.
log "pre-flight pauses ALL projects at >=98% 7d with TG dedup (v1.5.0)"
"$PY" -c "
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
five_resets = (datetime.now(timezone.utc) + timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M:%SZ')
seven_resets = (datetime.now(timezone.utc) + timedelta(days=6)).strftime('%Y-%m-%dT%H:%M:%SZ')
Path('$USER_HOME/quota-cache.json').write_text(json.dumps({
    'five_hour':  {'utilization': 0.30, 'resets_at': five_resets},
    'seven_day':  {'utilization': 0.99, 'resets_at': seven_resets},
}))
"

# Reset project to active (the previous step did not pause, but be safe).
"$PY" -c "
import json
from pathlib import Path
sf = Path('$PROJECT/.cc-autopipe/state.json')
s = json.loads(sf.read_text())
s['phase'] = 'active'
s['paused'] = None
sf.write_text(json.dumps(s))
"
rm -f "$USER_HOME/7d-tg.last"

CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$USER_HOME" \
CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_MAX_LOOPS=1 \
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
    "$PY" "$REPO_ROOT/src/orchestrator" >/dev/null 2>>"$SCRATCH/orch.err"

REASON=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['paused']['reason'])
")
[ "$REASON" = "7d_pre_check" ] || die "expected 7d_pre_check, got $REASON"
[ -f "$USER_HOME/7d-tg.last" ] || die "7d-tg.last sentinel not created"
ok "pre-flight 7d pause + TG sentinel verified"

# 6. stop-failure with quota cache uses precise resets_at.
log "stop-failure uses quota.five_hour.resets_at when present"
# Use the same 4h-from-now cache; quota has 30% utilization but stop-failure
# still consumes resets_at (hits when claude actually returned 429 mid-cycle).
echo '{"cwd":"'"$PROJECT"'","error":"rate_limit"}' | \
    CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
    CC_AUTOPIPE_USER_HOME="$USER_HOME" \
    bash "$REPO_ROOT/src/hooks/stop-failure.sh" >/dev/null

VIA=$(tail -1 "$USER_HOME/log/aggregate.jsonl" | "$PY" -c "
import json, sys
d = json.loads(sys.stdin.read())
print(d.get('resolved_via', ''))
")
[ "$VIA" = "quota" ] || die "expected resolved_via=quota, got '$VIA'"
ok "stop-failure quota path verified"

# 7. stop-failure without quota cache falls back to ladder.
log "stop-failure falls back to ladder when quota unavailable"
rm -f "$USER_HOME/quota-cache.json"
echo '{"cwd":"'"$PROJECT"'","error":"rate_limit"}' | \
    CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
    CC_AUTOPIPE_USER_HOME="$USER_HOME" \
    CC_AUTOPIPE_QUOTA_DISABLED=1 \
    bash "$REPO_ROOT/src/hooks/stop-failure.sh" >/dev/null

VIA=$(tail -1 "$USER_HOME/log/aggregate.jsonl" | "$PY" -c "
import json, sys
d = json.loads(sys.stdin.read())
v = d.get('resolved_via', '')
print('ladder' if v.startswith('ladder') else v)
")
[ "$VIA" = "ladder" ] || die "expected resolved_via=ladder, got '$VIA'"
ok "stop-failure ladder fallback verified"

# 8. status renders quota when cache is present.
log "status renders quota line"
"$PY" -c "
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
Path('$USER_HOME/quota-cache.json').write_text(json.dumps({
    'five_hour':  {'utilization': 0.42, 'resets_at': '2026-04-29T18:00:00Z'},
    'seven_day':  {'utilization': 0.13, 'resets_at': '2026-05-06T00:00:00Z'},
}))
"
CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$USER_HOME" \
    bash "$REPO_ROOT/src/helpers/cc-autopipe" status > "$SCRATCH/status.out"
grep -q "5h quota: 42%" "$SCRATCH/status.out" \
    || die "status missing 5h quota line: $(cat "$SCRATCH/status.out")"
grep -q "7d quota: 13%" "$SCRATCH/status.out" \
    || die "status missing 7d quota line"
ok "status quota render verified"

echo
echo "Stage E: OK"
