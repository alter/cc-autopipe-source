#!/bin/bash
# tests/smoke/stage-h.sh — Stage H DoD validation end-to-end.
# Refs: AGENTS-v1.md §3.2 (Batch b), SPEC-v1.md §2.1
#
# Mirrors the Stage F smoke pattern (init → orchestrator interaction →
# state assertions) for the DETACHED long-op flow.

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

DISPATCHER="$REPO_ROOT/src/helpers/cc-autopipe"
DETACH_HELPER="$REPO_ROOT/src/helpers/cc-autopipe-detach"

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"

# 1. Lint slice (cheap rerun, ensures Stage H additions are clean).
log "ruff + shellcheck on Stage H surfaces"
"$REPO_ROOT/.venv/bin/ruff" check src/lib/state.py src/orchestrator \
    || die "ruff check failed"
shellcheck src/helpers/cc-autopipe-detach src/hooks/pre-tool-use.sh \
    || die "shellcheck failed"
ok "lint clean"

# 2. Unit + integration coverage for state.py + orchestrator detached.
log "pytest tests/unit/test_state.py tests/integration/test_orchestrator_detached.py"
"$PYTEST" tests/unit/test_state.py tests/integration/test_orchestrator_detached.py \
    -q --tb=short || die "pytest failed"
ok "schema-v2 + detached integration tests pass"

# 3. Hook unit slice — pre-tool-use rule 7 added in Stage H.
log "tests/unit/test_hooks/test_pre_tool_use.sh"
bash tests/unit/test_hooks/test_pre_tool_use.sh >/dev/null \
    || die "pre-tool-use unit tests failed"
ok "pre-tool-use 37 cases (rule 7 included) pass"

# 4. cc-autopipe detach is wired in the dispatcher.
log "cc-autopipe detach --help via dispatcher"
"$DISPATCHER" detach --help | grep -q -- '--check-cmd' \
    || die "dispatcher does not surface detach --check-cmd"
ok "dispatcher exposes 'detach' subcommand"

# 5. End-to-end: init project, transition to detached, verify state.json.
log "end-to-end: init → detach → state.phase=detached"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
PROJECT="$SCRATCH/long-op"
USER_HOME="$SCRATCH/uhome"
mkdir -p "$PROJECT" "$USER_HOME"
(cd "$PROJECT" && git init -q)

export CC_AUTOPIPE_USER_HOME="$USER_HOME"

"$DISPATCHER" init "$PROJECT" >/dev/null || die "init failed"

# Pre-populate quota cache so any subsequent run --once never hits live api.
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

bash "$DETACH_HELPER" \
    --project "$PROJECT" \
    --reason "stage-h smoke" \
    --check-cmd "test -f $SCRATCH/done" \
    --check-every 0 \
    --max-wait 300 \
    >/dev/null || die "cc-autopipe-detach exited non-zero"

PHASE=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['phase'])
")
[ "$PHASE" = "detached" ] || die "expected phase=detached, got $PHASE"
ok "cc-autopipe-detach transitioned project to phase=detached"

DETACHED_REASON=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['detached']['reason'])
")
[ "$DETACHED_REASON" = "stage-h smoke" ] || die "reason mismatch: $DETACHED_REASON"
ok "detached.reason persisted"

# 6. Trigger the orchestrator: with check_cmd failing (no $SCRATCH/done file)
# the project should stay DETACHED and bump checks_count.
log "orchestrator pass with failing check_cmd → still detached"
echo "$PROJECT" > "$USER_HOME/projects.list"
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_MAX_LOOPS=1 \
CC_AUTOPIPE_QUOTA_DISABLED=1 \
    "$PY" "$REPO_ROOT/src/orchestrator" >"$SCRATCH/orch.out" 2>"$SCRATCH/orch.err" \
    || die "orchestrator exited non-zero: $(cat "$SCRATCH/orch.err")"

PHASE=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['phase'])
")
CHECKS=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['detached']['checks_count'])
")
[ "$PHASE" = "detached" ] || die "expected phase=detached after failing check, got $PHASE"
[ "$CHECKS" = "1" ] || die "expected checks_count=1, got $CHECKS"
ok "orchestrator polled, check_cmd failed, stayed DETACHED with checks_count=1"

# 7. Now satisfy the check_cmd and run again — orchestrator transitions
# to ACTIVE, falls through to a normal cycle (iteration bumps to 1).
log "orchestrator pass with passing check_cmd → ACTIVE + cycle runs"
touch "$SCRATCH/done"
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_MAX_LOOPS=1 \
CC_AUTOPIPE_QUOTA_DISABLED=1 \
    "$PY" "$REPO_ROOT/src/orchestrator" >>"$SCRATCH/orch.out" 2>>"$SCRATCH/orch.err" \
    || die "orchestrator exited non-zero: $(cat "$SCRATCH/orch.err")"

PHASE=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['phase'])
")
ITER=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['iteration'])
")
DETACHED_AFTER=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['detached'])
")
[ "$PHASE" = "active" ] || die "expected phase=active after success, got $PHASE"
[ "$ITER" = "1" ] || die "expected iteration=1 after fall-through, got $ITER"
[ "$DETACHED_AFTER" = "None" ] || die "expected detached=None after resume, got $DETACHED_AFTER"
ok "orchestrator transitioned DETACHED→ACTIVE and ran a cycle (iter=1)"

# 8. Aggregate log carries detach_resumed + cycle_start.
AGG=$("$PY" -c "
import json
from pathlib import Path
log = Path('$USER_HOME/log/aggregate.jsonl')
events = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
print(','.join(e['event'] for e in events))
")
echo "$AGG" | grep -q "detach_check_failed" || die "missing detach_check_failed event"
echo "$AGG" | grep -q "detach_resumed"      || die "missing detach_resumed event"
echo "$AGG" | grep -q "cycle_start"         || die "missing post-resume cycle_start"
ok "aggregate.jsonl carries detach_check_failed + detach_resumed + cycle_start"

echo
echo "Stage H: OK"
