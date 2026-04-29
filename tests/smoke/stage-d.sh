#!/bin/bash
# tests/smoke/stage-d.sh — Stage D DoD validation end-to-end.
# Refs: AGENTS.md §2 Stage D, SPEC.md §8.3, §8.4

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

# 2. Pytest unit + integration (includes locking + recovery).
log "pytest tests/unit tests/integration"
"$PYTEST" tests/unit tests/integration -q || die "pytest failed"
ok "all unit + integration tests pass"

# 3. Hook unit tests.
log "tests/unit/test_hooks/ (bash harness)"
for t in tests/unit/test_hooks/test_*.sh; do
    bash "$t" >/dev/null || die "$t failed"
done
ok "all 4 hook test files pass"

# 4. Singleton: second start exits with rc=1.
log "second start exits with 'already running'"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
USER_HOME="$SCRATCH/uhome"
mkdir -p "$USER_HOME"

CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$USER_HOME" \
CC_AUTOPIPE_COOLDOWN_SEC=10 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=10 \
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
    "$PY" "$REPO_ROOT/src/orchestrator" >"$SCRATCH/orch1.out" 2>"$SCRATCH/orch1.err" &
ORCH1_PID=$!
# Wait until first orchestrator has acquired the lock.
DEADLINE=$(( $(date +%s) + 5 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    if [ -f "$USER_HOME/orchestrator.pid" ]; then
        break
    fi
    sleep 0.1
done
[ -f "$USER_HOME/orchestrator.pid" ] || die "first orchestrator never wrote pidfile"

set +e
CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$USER_HOME" \
CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
CC_AUTOPIPE_MAX_LOOPS=1 \
    "$PY" "$REPO_ROOT/src/orchestrator" 2>"$SCRATCH/orch2.err"
RC=$?
set -e
[ "$RC" = "1" ] || die "second orchestrator expected rc=1, got $RC"
grep -q "already running" "$SCRATCH/orch2.err" \
    || die "second orchestrator did not log 'already running'"
ok "second start rejected with rc=1 + 'already running' log"

# Tear down the first orchestrator.
kill -TERM "$ORCH1_PID" 2>/dev/null || true
wait "$ORCH1_PID" 2>/dev/null || true

# 5. kill -9 recovery: lock auto-released, restart succeeds <5s.
log "kill -9 mid-run → restart succeeds within 5s"
rm -f "$USER_HOME/orchestrator.pid"
CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$USER_HOME" \
CC_AUTOPIPE_COOLDOWN_SEC=10 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=10 \
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
    "$PY" "$REPO_ROOT/src/orchestrator" >/dev/null 2>"$SCRATCH/orch3.err" &
ORCH3_PID=$!
DEADLINE=$(( $(date +%s) + 5 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    if [ -f "$USER_HOME/orchestrator.pid" ]; then
        break
    fi
    sleep 0.1
done
kill -9 "$ORCH3_PID"
wait "$ORCH3_PID" 2>/dev/null || true

START=$(date +%s)
set +e
CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$USER_HOME" \
CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
CC_AUTOPIPE_MAX_LOOPS=1 \
    "$PY" "$REPO_ROOT/src/orchestrator" >/dev/null 2>"$SCRATCH/orch4.err"
RC=$?
set -e
ELAPSED=$(( $(date +%s) - START ))
[ "$RC" = "0" ] || die "post-kill restart expected rc=0, got $RC. stderr: $(cat "$SCRATCH/orch4.err")"
[ "$ELAPSED" -lt 5 ] || die "recovery took ${ELAPSED}s, expected <5s"
ok "kill -9 recovery in ${ELAPSED}s"

# 6. Per-project lock: cycle holds it, releases after.
log "per-project lock acquired then released across cycle"
PROJECT="$SCRATCH/proj"
(cd / && git init -q "$PROJECT")  # avoid cwd dependency
CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$USER_HOME" \
    bash "$REPO_ROOT/src/helpers/cc-autopipe" init "$PROJECT" >/dev/null
cat > "$PROJECT/.cc-autopipe/verify.sh" <<'EOF'
#!/bin/bash
echo '{"passed":true,"score":0.9,"prd_complete":false,"details":{}}'
EOF
chmod +x "$PROJECT/.cc-autopipe/verify.sh"

# Pre-populate quota cache so this cycle never hits the real endpoint
# on hosts with live Keychain creds (Q12 fix).
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
CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_CLAUDE_BIN="$REPO_ROOT/tools/mock-claude.sh" \
CC_AUTOPIPE_HOOKS_DIR="$REPO_ROOT/src/hooks" \
CC_AUTOPIPE_CYCLE_TIMEOUT_SEC=30 \
CC_AUTOPIPE_MAX_LOOPS=1 \
    "$PY" "$REPO_ROOT/src/orchestrator" >/dev/null 2>"$SCRATCH/orch5.err" \
    || die "cycle failed: $(cat "$SCRATCH/orch5.err")"

# After cycle, lock_status reports not-held.
HELD=$("$PY" -c "
import sys
sys.path.insert(0, '$REPO_ROOT/src/lib')
import locking
from pathlib import Path
print(locking.lock_status(Path('$PROJECT/.cc-autopipe/lock'))['held'])
")
[ "$HELD" = "False" ] || die "per-project lock still held after cycle: $HELD"
ok "per-project lock released after cycle"

# 7. status now reports orchestrator info while one is live.
log "status renders live orchestrator info"
CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
CC_AUTOPIPE_USER_HOME="$USER_HOME" \
CC_AUTOPIPE_COOLDOWN_SEC=10 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=10 \
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
    "$PY" "$REPO_ROOT/src/orchestrator" >/dev/null 2>&1 &
ORCH_PID=$!
sleep 1.0
JSON=$(CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
       CC_AUTOPIPE_USER_HOME="$USER_HOME" \
       bash "$REPO_ROOT/src/helpers/cc-autopipe" status --json)
RUNNING=$(echo "$JSON" | "$PY" -c "import json,sys; print(json.load(sys.stdin)['orchestrator']['running'])")
[ "$RUNNING" = "True" ] || die "status didn't report orchestrator running: $JSON"

HUMAN=$(CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
        CC_AUTOPIPE_USER_HOME="$USER_HOME" \
        bash "$REPO_ROOT/src/helpers/cc-autopipe" status)
echo "$HUMAN" | grep -qE 'Orchestrator: running.*PID' \
    || die "human status missing 'Orchestrator: running': $HUMAN"
echo "$HUMAN" | grep -qE 'uptime' || die "human status missing uptime field: $HUMAN"
ok "status reports running orchestrator with PID + uptime"

kill -TERM "$ORCH_PID" 2>/dev/null || true
wait "$ORCH_PID" 2>/dev/null || true

echo
echo "Stage D: OK"
