#!/bin/bash
# tests/regression/hello-fullstack-v12.sh — v1.2 regression
#
# Inherits the full v1 regression (engine pipeline must still work),
# then layers the v1.2-specific assertions:
#   - state.schema_version == 3 after first cycle
#   - state.current_task field present (may be null until Bug A lands)
#   - state.last_in_progress + consecutive_in_progress fields present
#
# Pre-Batch 1: this script is EXPECTED to fail at the schema_v3 check
# (engine still writes schema_v2). After Batch 1 lands → green. After
# every subsequent batch (per AGENTS-v1.2.md §8) → must still be green.
#
# Refs: AGENTS-v1.2.md §8, SPEC-v1.2.md "Cross-cutting / Updated state
# schema (v3)", user request 2026-05-02 ("v12 наследует v1, добавляет
# schema_version==3 + current_task field").
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

# 1. Run the v1 base — engine pipeline must remain intact.
log "v1 regression base"
bash "$REPO_ROOT/tests/regression/hello-fullstack-v1.sh" >/dev/null 2>&1 \
    || die "hello-fullstack-v1 regression failed (engine pipeline broken)"
ok "v1 regression passes"

# 2. Re-run a single cycle ourselves so we can inspect state.json.
# (The v1 script cleans up its own scratch dir on exit; we need a fresh
# scratch we control so the assertions below see the post-cycle state.)
log "v1.2 cycle for schema assertions"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
PROJECT="$SCRATCH/hello-fullstack"
USER_HOME="$SCRATCH/uhome"
mkdir -p "$PROJECT" "$USER_HOME"
(cd "$PROJECT" && git init -q)

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_USER_HOME="$USER_HOME"

DISPATCHER="$REPO_ROOT/src/helpers/cc-autopipe"
"$DISPATCHER" init "$PROJECT" >/dev/null || die "init failed"

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

CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
CC_AUTOPIPE_CYCLE_TIMEOUT_SEC=30 \
    "$DISPATCHER" run "$PROJECT" --once >/dev/null 2>"$SCRATCH/run.err" \
    || die "run --once failed: $(cat "$SCRATCH/run.err")"

STATE="$PROJECT/.cc-autopipe/state.json"
[ -f "$STATE" ] || die "state.json missing after cycle"
ok "post-cycle state.json present"

# 3. v1.2 schema assertions.
log "schema_version == 3"
"$PY" -c "
import json, sys
data = json.load(open('$STATE'))
sv = data.get('schema_version')
if sv != 3:
    sys.exit(f'expected schema_version=3, got {sv}. v1.2 build is incomplete.')
print('schema_version=3 OK')
" || die "schema_version assertion failed (likely Bug A not yet implemented)"
ok "state.schema_version == 3"

log "current_task field present"
"$PY" -c "
import json, sys
data = json.load(open('$STATE'))
if 'current_task' not in data:
    sys.exit('state.json missing current_task field')
ct = data['current_task']
if ct is not None and not isinstance(ct, dict):
    sys.exit(f'current_task must be dict or null, got {type(ct).__name__}')
if isinstance(ct, dict):
    required = ['id', 'started_at', 'stage', 'stages_completed',
                'artifact_paths', 'claude_notes']
    missing = [k for k in required if k not in ct]
    if missing:
        sys.exit(f'current_task missing keys: {missing}')
print('current_task OK')
" || die "current_task assertion failed"
ok "state.current_task field present (null or dict with required keys)"

log "in_progress fields present"
"$PY" -c "
import json, sys
data = json.load(open('$STATE'))
for k in ('last_in_progress', 'consecutive_in_progress'):
    if k not in data:
        sys.exit(f'state.json missing {k}')
if not isinstance(data['last_in_progress'], bool):
    sys.exit(f'last_in_progress must be bool, got {type(data[\"last_in_progress\"]).__name__}')
if not isinstance(data['consecutive_in_progress'], int):
    sys.exit(f'consecutive_in_progress must be int, got {type(data[\"consecutive_in_progress\"]).__name__}')
print('in_progress fields OK')
" || die "in_progress assertion failed (Bug B not yet implemented)"
ok "state.last_in_progress + consecutive_in_progress present"

echo
echo "hello-fullstack-v12 regression: OK"
