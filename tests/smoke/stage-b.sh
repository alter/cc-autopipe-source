#!/bin/bash
# tests/smoke/stage-b.sh — Stage B DoD validation end-to-end.
# Refs: AGENTS.md §2 Stage B
#
# Exits non-zero on first failure. Prints a one-line PASS/FAIL summary.

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

# 1. Lint everything new in Stage B.
log "ruff check + format-check"
"$RUFF" check src tests || die "ruff check failed"
"$RUFF" format --check src tests || die "ruff format dirty"
ok "ruff clean"

log "shellcheck on bash files (incl. dispatcher)"
SHELL_FILES=$(find src tests tools -type f \( -name '*.sh' -o -path '*/helpers/*' \) ! -name '*.py')
# shellcheck disable=SC2086
shellcheck $SHELL_FILES || die "shellcheck failed"
ok "shellcheck clean ($(echo "$SHELL_FILES" | wc -l | tr -d ' ') files)"

# 2. Pytest unit + integration.
log "pytest tests/unit tests/integration"
"$PYTEST" tests/unit tests/integration -q || die "pytest failed"
ok "all unit + integration tests pass"

# 3. End-to-end: init a fresh project, status, orchestrator one loop.
log "end-to-end: init → status → orchestrator (one loop)"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
PROJECT="$SCRATCH/demo-project"
USER_HOME="$SCRATCH/uhome"
mkdir -p "$PROJECT" "$USER_HOME"
(cd "$PROJECT" && git init -q)

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_USER_HOME="$USER_HOME"
DISPATCHER="$REPO_ROOT/src/helpers/cc-autopipe"

# 3a. init creates skeleton.
"$DISPATCHER" init "$PROJECT" >/dev/null || die "init failed"
[ -f "$PROJECT/.cc-autopipe/state.json" ]    || die "state.json not created"
[ -f "$PROJECT/.cc-autopipe/config.yaml" ]   || die "config.yaml not created"
[ -f "$PROJECT/.cc-autopipe/verify.sh" ]     || die "verify.sh not created"
[ -x "$PROJECT/.cc-autopipe/verify.sh" ]     || die "verify.sh not executable"
[ -f "$PROJECT/.claude/settings.json" ]      || die ".claude/settings.json not created"
grep -q "$REPO_ROOT/src/hooks" "$PROJECT/.claude/settings.json" || \
    die "settings.json missing absolute hook path"
[ -f "$USER_HOME/projects.list" ]            || die "projects.list not created"
grep -qF "$PROJECT" "$USER_HOME/projects.list" || die "project not registered"
grep -q '\.cc-autopipe/state\.json' "$PROJECT/.gitignore" || die "gitignore not updated"
ok "init produced full skeleton with absolute paths"

# 3b. init refuses without --force.
set +e
"$DISPATCHER" init "$PROJECT" >/dev/null 2>&1
RC=$?
set -e
[ "$RC" = "1" ] || die "expected rc=1 on second init without --force, got $RC"
ok "init refuses non-empty .cc-autopipe/ without --force"

# 3c. --force succeeds.
"$DISPATCHER" init --force "$PROJECT" >/dev/null || die "init --force failed"
ok "init --force overwrites cleanly"

# 3d. status (human + JSON).
"$DISPATCHER" status | grep -q "demo-project" || die "status missing project row"
"$DISPATCHER" status | grep -qE 'ACTIVE\s+0' || die "status row missing ACTIVE 0"
"$DISPATCHER" status --json > "$SCRATCH/status.json" || die "status --json failed"
"$PY" -c "
import json
d = json.load(open('$SCRATCH/status.json'))
assert d['orchestrator']['running'] is False
assert d['quota']['available'] is False
assert len(d['projects']) == 1
p = d['projects'][0]
assert p['name'] == 'demo-project', p
assert p['phase'] == 'ACTIVE', p
assert p['iteration'] == 0, p
" || die "status --json produced unexpected document"
ok "status (human + JSON) renders projects from state.json"

# 3e. orchestrator one loop, no claude spawn.
CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_MAX_LOOPS=2 \
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
    "$DISPATCHER" start 2>"$SCRATCH/orch.stderr" >/dev/null \
    || die "orchestrator failed: $(cat "$SCRATCH/orch.stderr")"

grep -q "shutdown gracefully" "$SCRATCH/orch.stderr" \
    || die "orchestrator did not log graceful shutdown"

# state.json should now reflect 2 cycles.
ITER=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['iteration'])
")
[ "$ITER" = "2" ] || die "expected iteration=2 after 2 loops, got $ITER"

# aggregate.jsonl should have 2 cycle_start events (Stage C event names).
COUNT=$(grep -c '"event":"cycle_start"' "$USER_HOME/log/aggregate.jsonl" || true)
[ "$COUNT" = "2" ] || die "expected 2 cycle_start events in aggregate.jsonl, got $COUNT"
ok "orchestrator ran 2 loops, logged cycle_start events"

# 3f. SIGTERM during a long sleep exits within 5s.
log "SIGTERM smoke test"
unset CC_AUTOPIPE_MAX_LOOPS
CC_AUTOPIPE_COOLDOWN_SEC=10 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=10 \
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
    "$DISPATCHER" start &
ORCH_PID=$!
sleep 1.0
kill -TERM "$ORCH_PID"
WAITED=0
while kill -0 "$ORCH_PID" 2>/dev/null; do
    sleep 0.5
    WAITED=$((WAITED + 1))
    [ "$WAITED" -gt 10 ] && die "orchestrator did not exit within 5s of SIGTERM"
done
wait "$ORCH_PID" 2>/dev/null || true
ok "SIGTERM exits within ${WAITED}/10 half-seconds"

echo
echo "Stage B: OK"
