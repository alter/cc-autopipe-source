#!/bin/bash
# tests/smoke/stage-f.sh — Stage F DoD validation end-to-end.
# Refs: AGENTS.md §2 Stage F, SPEC.md §12

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
"$RUFF" check src tests tools || die "ruff check failed"
"$RUFF" format --check src tests tools || die "ruff format dirty"
ok "ruff clean"

log "shellcheck on bash files"
find src tests tools -type f \( -name '*.sh' -o -path '*/helpers/*' \) ! -name '*.py' -print0 \
    | xargs -0 shellcheck -x || die "shellcheck failed"
ok "shellcheck clean"

# 2. Pytest unit + integration (includes test_cli.py).
log "pytest tests/unit tests/integration"
"$PYTEST" tests/unit tests/integration -q || die "pytest failed"
ok "all unit + integration tests pass"

# 3. Hook unit tests.
log "tests/unit/test_hooks/ (bash harness)"
for t in tests/unit/test_hooks/test_*.sh; do
    bash "$t" >/dev/null || die "$t failed"
done
ok "all 4 hook test files pass"

# 4. --help discoverability for every Stage F surface.
log "--help on each Stage F command"
DISPATCHER="$REPO_ROOT/src/helpers/cc-autopipe"
HELP=$("$DISPATCHER" --help)
for sub in resume run tail doctor checkpoint block; do
    echo "$HELP" | grep -qE "^\s+$sub\b" || die "--help missing $sub"
done
ok "dispatcher --help lists all Stage F commands"

CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
    "$DISPATCHER" resume --help >/dev/null || die "resume --help failed"
CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
    "$DISPATCHER" run --help >/dev/null || die "run --help failed"
CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
    "$DISPATCHER" tail --help >/dev/null || die "tail --help failed"
CC_AUTOPIPE_HOME="$REPO_ROOT/src" \
    "$DISPATCHER" doctor --help >/dev/null || die "doctor --help failed"
bash "$REPO_ROOT/src/helpers/cc-autopipe-checkpoint" --help >/dev/null \
    || die "checkpoint --help failed"
bash "$REPO_ROOT/src/helpers/cc-autopipe-block" --help >/dev/null \
    || die "block --help failed"
ok "all 6 Stage F commands respond to --help"

# 5. End-to-end scenario: init → run --once → checkpoint → block → resume.
log "end-to-end: init → run --once → checkpoint → block → resume"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
PROJECT="$SCRATCH/demo"
USER_HOME="$SCRATCH/uhome"
mkdir -p "$PROJECT" "$USER_HOME"
(cd "$PROJECT" && git init -q)

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_USER_HOME="$USER_HOME"

# 5a. init.
"$DISPATCHER" init "$PROJECT" >/dev/null || die "init failed"
ok "init created skeleton"

# Pre-populate quota cache so run --once never hits api.anthropic.com.
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

# 5b. run --once with /usr/bin/true as claude → cycle increments iteration.
CC_AUTOPIPE_CLAUDE_BIN=/usr/bin/true \
CC_AUTOPIPE_CYCLE_TIMEOUT_SEC=30 \
    "$DISPATCHER" run "$PROJECT" --once >/dev/null 2>"$SCRATCH/run.err" \
    || die "run --once failed: $(cat "$SCRATCH/run.err")"
ITER=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['iteration'])
")
[ "$ITER" = "1" ] || die "expected iteration=1 after run --once, got $ITER"
ok "run --once incremented iteration to 1"

# 5c. checkpoint helper writes file.
echo "stopped at task 3, need to backfill fixture" \
    | bash "$REPO_ROOT/src/helpers/cc-autopipe-checkpoint" --project "$PROJECT" \
    >/dev/null
[ -f "$PROJECT/.cc-autopipe/checkpoint.md" ] || die "checkpoint.md not created"
grep -q "task 3" "$PROJECT/.cc-autopipe/checkpoint.md" \
    || die "checkpoint body missing"
ok "checkpoint helper writes .cc-autopipe/checkpoint.md"

# 5d. block helper marks failed + creates HUMAN_NEEDED.md.
bash "$REPO_ROOT/src/helpers/cc-autopipe-block" \
    --project "$PROJECT" "verifier missing pytest" >/dev/null \
    || die "block helper failed"
PHASE=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['phase'])
")
[ "$PHASE" = "failed" ] || die "expected phase=failed after block, got $PHASE"
[ -f "$PROJECT/.cc-autopipe/HUMAN_NEEDED.md" ] || die "HUMAN_NEEDED.md not created"
grep -q "verifier missing pytest" "$PROJECT/.cc-autopipe/HUMAN_NEEDED.md" \
    || die "HUMAN_NEEDED.md missing reason text"
ok "block helper transitioned to failed + wrote HUMAN_NEEDED.md"

# 5e. resume clears failed, removes HUMAN_NEEDED.md.
"$DISPATCHER" resume "$PROJECT" >/dev/null || die "resume failed"
PHASE=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['phase'])
")
[ "$PHASE" = "active" ] || die "expected phase=active after resume, got $PHASE"
[ ! -f "$PROJECT/.cc-autopipe/HUMAN_NEEDED.md" ] \
    || die "HUMAN_NEEDED.md still present after resume"
ok "resume cleared failed phase + removed HUMAN_NEEDED.md"

# 6. tail --no-follow prints recent events.
log "tail --no-follow surfaces aggregate.jsonl events"
TAIL_OUT=$("$DISPATCHER" tail --no-follow -n 50)
echo "$TAIL_OUT" | grep -q "cycle_start" || die "tail missing cycle_start"
echo "$TAIL_OUT" | grep -q "blocked"     || die "tail missing blocked event"
echo "$TAIL_OUT" | grep -q "resume"      || die "tail missing resume event"
ok "tail surfaces cycle_start / blocked / resume events"

# 7. doctor --offline reports green-or-warn.
log "doctor --offline prints checklist"
DOCTOR_OUT=$("$DISPATCHER" doctor --offline 2>&1 || true)
echo "$DOCTOR_OUT" | grep -q "claude binary" || die "doctor missing claude check"
echo "$DOCTOR_OUT" | grep -q "python3"        || die "doctor missing python check"
echo "$DOCTOR_OUT" | grep -q "hooks"          || die "doctor missing hooks check"
echo "$DOCTOR_OUT" | grep -q "skipped (--offline)" \
    || die "doctor didn't honour --offline"
ok "doctor --offline prints all expected checks"

# 8. doctor --json yields valid JSON with summary block.
DOCTOR_JSON=$("$DISPATCHER" doctor --offline --json 2>/dev/null || true)
echo "$DOCTOR_JSON" | "$PY" -c "
import json, sys
doc = json.load(sys.stdin)
assert 'checks' in doc and 'summary' in doc, doc
assert isinstance(doc['checks'], list) and doc['checks'], doc
" || die "doctor --json malformed"
ok "doctor --json valid"

echo
echo "Stage F: OK"
