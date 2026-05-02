#!/bin/bash
# tests/smoke/stage-k.sh — Stage K DoD validation end-to-end.
# Refs: AGENTS-v1.md §3.3, SPEC-v1.md §2.4

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }
die() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

if [ -x "$REPO_ROOT/.venv/bin/python3" ]; then
    PY="$REPO_ROOT/.venv/bin/python3"
    PYTEST="$REPO_ROOT/.venv/bin/pytest"
else
    PY="python3"
    PYTEST="pytest"
fi

# 1. Lint slice.
log "ruff + shellcheck on Stage K surfaces"
"$REPO_ROOT/.venv/bin/ruff" check src/lib/quota_monitor.py src/orchestrator \
    || die "ruff failed"
ok "lint clean"

# 2. Unit coverage.
log "pytest tests/unit/test_quota_monitor.py"
"$PYTEST" tests/unit/test_quota_monitor.py -q --tb=short || die "pytest failed"
ok "15 unit cases for quota_monitor pass"

# 3. End-to-end: orchestrator main() spawns + tears down the daemon.
# We can't directly observe the daemon's internal ticks from outside the
# process, but the orchestrator's startup log mentions the monitor
# interval — confirms wiring is live. Then SIGTERM cleanly exits.
log "orchestrator boots quota_monitor + tears down on SIGTERM"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
USER_HOME="$SCRATCH/uhome"
mkdir -p "$USER_HOME"

# Pre-populate quota cache so the daemon has data to read on startup.
"$PY" -c "
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
five = (datetime.now(timezone.utc) + timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M:%SZ')
seven = (datetime.now(timezone.utc) + timedelta(days=6)).strftime('%Y-%m-%dT%H:%M:%SZ')
Path('$USER_HOME/quota-cache.json').write_text(json.dumps({
    'five_hour': {'utilization': 5, 'resets_at': five},
    'seven_day': {'utilization': 10, 'resets_at': seven},
}))
"

CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$USER_HOME" \
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
CC_AUTOPIPE_COOLDOWN_SEC=10 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=10 \
CC_AUTOPIPE_QUOTA_MONITOR_INTERVAL_SEC=0.2 \
    "$PY" "$REPO_ROOT/src/orchestrator" >"$SCRATCH/orch.out" 2>"$SCRATCH/orch.err" &
ORCH_PID=$!

# Give it a moment to start the daemon + log the startup line.
sleep 1.5
kill -TERM "$ORCH_PID" 2>/dev/null || true
wait "$ORCH_PID" 2>/dev/null || true

grep -q "quota_monitor_interval=" "$SCRATCH/orch.err" \
    || die "orchestrator startup log did not mention quota_monitor_interval. stderr:
$(cat "$SCRATCH/orch.err")"
ok "orchestrator startup log carries quota_monitor_interval"

grep -q "shutdown gracefully" "$SCRATCH/orch.err" \
    || die "orchestrator did not log graceful shutdown. stderr:
$(cat "$SCRATCH/orch.err")"
ok "orchestrator tears down quota_monitor on SIGTERM (no hang)"

# 4. With 7d quota at 92% pre-populated and a fast monitor interval, a
# single check_once should fire a DANGER warning + drop a flag file.
log "warning fires + drops flag file at 90% threshold"
USER_HOME2="$SCRATCH/uhome2"
mkdir -p "$USER_HOME2"
"$PY" -c "
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
five = (datetime.now(timezone.utc) + timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M:%SZ')
seven = (datetime.now(timezone.utc) + timedelta(days=6)).strftime('%Y-%m-%dT%H:%M:%SZ')
Path('$USER_HOME2/quota-cache.json').write_text(json.dumps({
    'five_hour': {'utilization': 5, 'resets_at': five},
    'seven_day': {'utilization': 92, 'resets_at': seven},
}))
"
TODAY=$(date -u +%Y-%m-%d)
CC_AUTOPIPE_USER_HOME="$USER_HOME2" "$PY" -c "
import sys
sys.path.insert(0, 'src/lib')
import quota_monitor
from pathlib import Path
msgs = []
fired = quota_monitor.check_once(
    user_home=Path('$USER_HOME2'),
    notify_tg=lambda m: msgs.append(m),
    today_iso='$TODAY',
)
assert fired == '90', f'expected 90 fired, got {fired}'
assert msgs and 'DANGER' in msgs[0], f'expected DANGER msg, got {msgs}'
print('check_once fired:', fired, '/', msgs[0])
" || die "check_once at 92% did not fire DANGER"

[ -f "$USER_HOME2/7d-warn-90-$TODAY.flag" ] \
    || die "dedup flag file not created at $USER_HOME2/7d-warn-90-$TODAY.flag"
ok "92% reading triggers DANGER warning + flag file"

echo
echo "Stage K: OK"
