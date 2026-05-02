#!/bin/bash
# tests/regression/hello-fullstack-v1.sh — minimal mocked-claude regression
#
# Goal: verify the engine pipeline (init → orchestrator preflight → claude
# subprocess → hooks → state.json → aggregate.jsonl) is intact, without
# burning real Claude quota. Uses /usr/bin/true (or a one-liner stub) as
# claude_bin so a single `cc-autopipe run --once` exercises:
#   - init scaffolding
#   - quota cache short-circuit (pre-populated)
#   - orchestrator pre-flight + cycle dispatch
#   - state.json create + atomic write
#   - aggregate.jsonl event logging
#
# What this regression does NOT do:
#   - Run real Claude
#   - Validate verify.sh contract end-to-end (project verify is a stub)
#   - Validate session_id round-trip (would need a richer mock)
#
# Refs: AGENTS-v1.2.md §8, user request 2026-05-02 ("hello-fullstack-v1.sh
# минимум: setup tmp project, init, run --once mocked, assert state.json
# + no errors in aggregate.jsonl").
#
# Exit: 0 on success, non-zero on any assertion failure.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

log()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32mOK \033[0m %s\n' "$*"; }
die()  { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

if [ -x "$REPO_ROOT/.venv/bin/python3" ]; then
    PY="$REPO_ROOT/.venv/bin/python3"
else
    PY="python3"
fi

DISPATCHER="$REPO_ROOT/src/helpers/cc-autopipe"
[ -x "$DISPATCHER" ] || die "cc-autopipe dispatcher missing at $DISPATCHER"

# ---------------------------------------------------------------------------
# Scratch project setup
# ---------------------------------------------------------------------------

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
PROJECT="$SCRATCH/hello-fullstack"
USER_HOME="$SCRATCH/uhome"
mkdir -p "$PROJECT" "$USER_HOME"
(cd "$PROJECT" && git init -q)

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_USER_HOME="$USER_HOME"

# 1. Scaffold project.
log "cc-autopipe init"
"$DISPATCHER" init "$PROJECT" >/dev/null || die "init failed"
[ -d "$PROJECT/.cc-autopipe" ] || die "init did not create .cc-autopipe/"
[ -f "$PROJECT/.cc-autopipe/config.yaml" ] \
    || die "init did not seed config.yaml"
ok "init scaffold present"

# 2. Pre-populate quota cache so run --once skips api.anthropic.com.
log "seed quota cache"
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
ok "quota cache seeded (5h=5%, 7d=10%)"

# 3. Run a single cycle with /usr/bin/true as claude_bin.
log "cc-autopipe run --once with mocked claude_bin"
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
CC_AUTOPIPE_CYCLE_TIMEOUT_SEC=30 \
    "$DISPATCHER" run "$PROJECT" --once \
        >"$SCRATCH/run.out" 2>"$SCRATCH/run.err" \
    || die "run --once failed: $(cat "$SCRATCH/run.err")"
ok "run --once exited 0"

# 4. Assert state.json exists and has the expected fields.
log "state.json fields"
STATE="$PROJECT/.cc-autopipe/state.json"
[ -f "$STATE" ] || die "state.json missing after run --once"

"$PY" -c "
import json, sys
data = json.load(open('$STATE'))
required = ['schema_version', 'name', 'phase', 'iteration', 'session_id',
            'last_score', 'last_passed', 'prd_complete',
            'consecutive_failures', 'threshold']
missing = [k for k in required if k not in data]
if missing:
    sys.exit(f'state.json missing required keys: {missing}')
if data['iteration'] != 1:
    sys.exit(f'expected iteration=1 after one cycle, got {data[\"iteration\"]}')
if data['phase'] not in ('active', 'paused', 'failed', 'done'):
    sys.exit(f'unexpected phase: {data[\"phase\"]}')
print('state.json keys + iteration OK')
" || die "state.json assertions failed"
ok "state.json has required v1.0 fields, iteration=1"

# 5. Assert aggregate.jsonl exists and contains a cycle_start event.
log "aggregate.jsonl"
AGG="$USER_HOME/log/aggregate.jsonl"
[ -f "$AGG" ] || die "aggregate.jsonl missing"
grep -q '"event":"cycle_start"' "$AGG" \
    || die "aggregate.jsonl missing cycle_start event"
ok "aggregate.jsonl has cycle_start event"

# 6. No critical-error events in aggregate.jsonl.
# We tolerate quota-related "info" events but reject anything that looks
# like a hard engine fault. /usr/bin/true exits 0 so claude_subprocess_failed
# should not appear; if it does, the engine pipeline is broken.
log "no critical errors in aggregate.jsonl"
if grep -E '"event":"(claude_subprocess_failed|engine_fault|state_unrecoverable)"' "$AGG"; then
    cat "$AGG" >&2
    die "aggregate.jsonl contains hard-failure events"
fi
ok "no hard-failure events recorded"

echo
echo "hello-fullstack-v1 regression: OK"
