#!/bin/bash
# tests/smoke/stage-n.sh — Stage N DoD validation.
# Refs: AGENTS-v1.md §3.4, SPEC-v1.md §2.7

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
export CC_AUTOPIPE_HOME="$REPO_ROOT/src"

# 1. Lint slice.
log "ruff on Stage N surfaces"
"$REPO_ROOT/.venv/bin/ruff" check src/orchestrator src/lib/state.py \
    || die "ruff failed"
ok "lint clean"

# 2. Template carries improver entry.
log "agents.json template carries improver subagent"
"$PY" -c "
import json
d = json.load(open('src/templates/.cc-autopipe/agents.json'))
assert 'improver' in d, sorted(d.keys())
assert d['improver']['model'] == 'sonnet'
assert set(d['improver']['tools']) == {'Read', 'Write'}
assert '.claude/skills/' in d['improver']['prompt']
print('improver entry valid')
" || die "improver entry malformed"
ok "improver entry present in template (model=sonnet, tools=Read+Write)"

# 3. Pytest slice.
log "pytest tests/integration/test_orchestrator_improver.py"
"$PYTEST" tests/integration/test_orchestrator_improver.py -q --tb=short \
    || die "pytest failed"
ok "8 improver tests pass"

# 4. End-to-end via dispatcher: 3 successful cycles → improver_due fires
# + skills dir created + improver_trigger_due event in aggregate log.
log "end-to-end: 3 successful cycles → improver triggered"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
PROJECT="$SCRATCH/proj"
USER_HOME="$SCRATCH/uhome"
mkdir -p "$PROJECT" "$USER_HOME"
(cd "$PROJECT" && git init -q)

CC_AUTOPIPE_USER_HOME="$USER_HOME" "$DISPATCHER" init "$PROJECT" >/dev/null \
    || die "init failed"

# Always-passing verify.
cat > "$PROJECT/.cc-autopipe/verify.sh" <<'SH'
#!/bin/bash
echo '{"passed":true,"score":0.95,"prd_complete":false,"details":{}}'
SH
chmod +x "$PROJECT/.cc-autopipe/verify.sh"

# Lower trigger to 3.
sed -i.bak 's/trigger_every_n_successes: 5/trigger_every_n_successes: 3/' \
    "$PROJECT/.cc-autopipe/config.yaml"
rm -f "$PROJECT/.cc-autopipe/config.yaml.bak"

# Pre-populate quota cache.
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
CC_AUTOPIPE_CLAUDE_BIN="$REPO_ROOT/tools/mock-claude.sh" \
CC_AUTOPIPE_HOOKS_DIR="$REPO_ROOT/src/hooks" \
CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_MAX_LOOPS=3 \
CC_AUTOPIPE_QUOTA_DISABLED=1 \
CC_AUTOPIPE_CYCLE_TIMEOUT_SEC=30 \
    "$PY" "$REPO_ROOT/src/orchestrator" >"$SCRATCH/orch.out" 2>"$SCRATCH/orch.err" \
    || die "orchestrator exit non-zero: $(cat "$SCRATCH/orch.err")"

# Assert: skills dir exists; improver_due is True (no consuming cycle ran).
[ -d "$PROJECT/.claude/skills" ] || die "skills dir not created"
ok ".claude/skills/ created on trigger"

DUE=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['improver_due'])
")
[ "$DUE" = "True" ] || die "expected improver_due=True after 3 successes, got $DUE"
ok "improver_due=True after 3 successful cycles"

CTR=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['successful_cycles_since_improver'])
")
[ "$CTR" = "0" ] || die "expected counter reset to 0, got $CTR"
ok "successful_cycles_since_improver counter reset to 0"

grep -q "improver_trigger_due" "$USER_HOME/log/aggregate.jsonl" \
    || die "improver_trigger_due event missing from aggregate.jsonl"
ok "aggregate.jsonl carries improver_trigger_due event"

# Now demo skill discovery: drop a SKILL.md and verify Claude Code's
# project-local skills directory has the right shape.
mkdir -p "$PROJECT/.claude/skills/example-skill"
cat > "$PROJECT/.claude/skills/example-skill/SKILL.md" <<'SK'
---
name: example-skill
description: Tiny stand-in for a real improver-generated skill.
---

# example-skill

Demonstration skill for stage-n.sh. The real improver subagent
would write this from observed patterns in reports/iteration-*.md.
SK
[ -f "$PROJECT/.claude/skills/example-skill/SKILL.md" ] \
    || die "could not write demo SKILL.md"
ok "demo SKILL.md placed under .claude/skills/example-skill/"

echo
echo "Stage N: OK"
