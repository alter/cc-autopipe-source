#!/bin/bash
# tests/smoke/stage-l.sh — Stage L DoD validation end-to-end.
# Refs: AGENTS-v1.md §3.3, SPEC-v1.md §2.5
#
# Exercises the auto-escalation path:
#   3 mock failures → 4th cycle uses opus + --effort xhigh + reminder
# Plus the lint + pytest slice for fast regression detection.

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
MOCK_CLAUDE="$REPO_ROOT/tools/mock-claude.sh"

# 1. Lint slice.
log "ruff + shellcheck on Stage L surfaces"
"$REPO_ROOT/.venv/bin/ruff" check src/orchestrator src/cli/resume.py \
    || die "ruff failed"
ok "lint clean"

# 2. Unit + integration coverage.
log "pytest tests/integration/test_orchestrator_escalation.py"
"$PYTEST" tests/integration/test_orchestrator_escalation.py -q --tb=short \
    || die "pytest failed"
ok "12 escalation tests pass"

# 3. End-to-end: 3 failed cycles → 4th cycle uses opus model in cmd args.
# We capture mock-claude.sh's argv via its DUMP_INPUT facility — when
# CC_AUTOPIPE_MOCK_DUMP_ARGV is set, mock-claude writes its argv to
# the named file. Then we grep for "claude-opus-4-7" + "--effort".
log "end-to-end: 3 mock failures → 4th cycle uses opus + --effort"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
PROJECT="$SCRATCH/proj"
USER_HOME="$SCRATCH/uhome"
mkdir -p "$PROJECT" "$USER_HOME"
(cd "$PROJECT" && git init -q)

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_USER_HOME="$USER_HOME"

"$DISPATCHER" init "$PROJECT" >/dev/null || die "init failed"

# Always-failing verify so consecutive_failures climbs by 1 each cycle.
cat > "$PROJECT/.cc-autopipe/verify.sh" <<'SH'
#!/bin/bash
echo '{"passed":false,"score":0.1,"prd_complete":false,"details":{}}'
SH
chmod +x "$PROJECT/.cc-autopipe/verify.sh"

# Pre-populate quota cache so the orchestrator doesn't try a live fetch.
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

# Wrap mock-claude with a tiny logger. Record each invocation's MODEL
# arg only (not the full prompt — the prompt contains newlines and would
# inflate the log file by hundreds of "lines"). One line per invocation:
# "INVOKE: <model_or_NONE> | --effort <effort_or_NONE>"
ARGV_LOG="$SCRATCH/claude-argv.log"
WRAPPER="$SCRATCH/claude-wrapper.sh"
cat > "$WRAPPER" <<'EOF'
#!/bin/bash
MODEL="NONE"
EFFORT="NONE"
i=1
for arg in "$@"; do
    if [ "$arg" = "--model" ]; then
        next=$((i + 1))
        MODEL="${!next}"
    fi
    if [ "$arg" = "--effort" ]; then
        next=$((i + 1))
        EFFORT="${!next}"
    fi
    i=$((i + 1))
done
printf 'INVOKE: model=%s effort=%s\n' "$MODEL" "$EFFORT" >> "ARGV_LOG_PLACEHOLDER"
exec "MOCK_CLAUDE_PLACEHOLDER" "$@"
EOF
sed -i.bak "s|ARGV_LOG_PLACEHOLDER|$ARGV_LOG|; s|MOCK_CLAUDE_PLACEHOLDER|$MOCK_CLAUDE|" "$WRAPPER"
rm -f "$WRAPPER.bak"
chmod +x "$WRAPPER"

CC_AUTOPIPE_CLAUDE_BIN="$WRAPPER" \
CC_AUTOPIPE_HOOKS_DIR="$REPO_ROOT/src/hooks" \
CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_MAX_LOOPS=4 \
CC_AUTOPIPE_QUOTA_DISABLED=1 \
CC_AUTOPIPE_CYCLE_TIMEOUT_SEC=30 \
    "$PY" "$REPO_ROOT/src/orchestrator" >"$SCRATCH/orch.out" 2>"$SCRATCH/orch.err" \
    || true  # orchestrator may exit nonzero after FAIL transition

# Argv log should have 4 entries: 3 sonnet + 1 opus.
COUNT=$(wc -l < "$ARGV_LOG" | tr -d ' ')
[ "$COUNT" -ge 4 ] || die "expected at least 4 claude invocations, got $COUNT
$(cat "$ARGV_LOG")"
ok "$COUNT claude invocations recorded"

# Cycle 1-3: should use sonnet (default), no effort.
SONNET_LINES=$(head -n 3 "$ARGV_LOG" | grep -c "claude-opus" || true)
[ "$SONNET_LINES" = "0" ] || die "first 3 cycles unexpectedly used opus
$(head -n 3 "$ARGV_LOG")"
ok "first 3 cycles used default model (no opus)"

# Cycle 4 (escalated): opus + --effort xhigh.
LAST_LINE=$(sed -n '4p' "$ARGV_LOG")
echo "$LAST_LINE" | grep -q "model=claude-opus-4-7" \
    || die "cycle 4 missing opus model. line: $LAST_LINE"
echo "$LAST_LINE" | grep -q "effort=xhigh" \
    || die "cycle 4 missing effort=xhigh. line: $LAST_LINE"
ok "cycle 4 escalated to opus + effort=xhigh"

# Aggregate log carries escalated_to_opus event.
AGG=$("$PY" -c "
import json
from pathlib import Path
events = [json.loads(l) for l in Path('$USER_HOME/log/aggregate.jsonl').read_text().splitlines() if l.strip()]
print(','.join(e['event'] for e in events))
")
echo "$AGG" | grep -q "escalated_to_opus" || die "no escalated_to_opus event in aggregate"
echo "$AGG" | grep -q "failed" || die "no failed event in aggregate"
ok "aggregate.jsonl carries escalated_to_opus + failed events"

echo
echo "Stage L: OK"
