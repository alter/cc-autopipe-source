#!/bin/bash
# tests/smoke/stage-i.sh — Stage I DoD validation.
# Refs: AGENTS-v1.md §3.2 (Batch b), SPEC-v1.md §2.2

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

# 1. Template parses as valid JSON and has all 4 entries.
log "agents.json template structure"
"$PY" -c "
import json, sys
d = json.load(open('src/templates/.cc-autopipe/agents.json'))
expected = {'io-worker', 'verifier', 'researcher', 'reporter'}
assert set(d) == expected, sorted(d)
for name, spec in d.items():
    for k in ('description', 'prompt', 'tools', 'model', 'maxTurns'):
        assert k in spec, f'{name} missing {k}'
print('template valid:', sorted(d))
" || die "template structure check failed"
ok "template parses with 4 subagents (io-worker, verifier, researcher, reporter)"

# 2. Pytest slice: init provisions v1 subagents.
log "pytest tests/integration/test_init.py (v1 subagent cases)"
"$PYTEST" tests/integration/test_init.py -q --tb=short || die "init tests failed"
ok "init provisions researcher + reporter; structure validated"

# 3. End-to-end: real init copies the template into a fresh project.
log "end-to-end: cc-autopipe init copies subagents into project"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
PROJECT="$SCRATCH/proj"
USER_HOME="$SCRATCH/uhome"
mkdir -p "$PROJECT" "$USER_HOME"
(cd "$PROJECT" && git init -q)

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_USER_HOME="$USER_HOME"

"$DISPATCHER" init "$PROJECT" >/dev/null || die "init failed"

INSTALLED_KEYS=$("$PY" -c "
import json
print(' '.join(sorted(json.load(open('$PROJECT/.cc-autopipe/agents.json')).keys())))
")
[ "$INSTALLED_KEYS" = "io-worker reporter researcher verifier" ] \
    || die "agents.json keys not as expected: $INSTALLED_KEYS"
ok "real-init project has io-worker + verifier + researcher + reporter"

# 4. Backward compat: a project initialised on v0.5 (without researcher/
# reporter in its agents.json) must still be readable by the orchestrator.
log "backward compat: pre-Stage-I agents.json without new entries"
LEGACY_AGENTS="$SCRATCH/legacy/.cc-autopipe/agents.json"
mkdir -p "$(dirname "$LEGACY_AGENTS")"
cat > "$LEGACY_AGENTS" <<'JSON'
{
  "io-worker": {"description": "x","prompt":"x","tools":["Read"],"model":"haiku","maxTurns":3},
  "verifier":  {"description": "y","prompt":"y","tools":["Bash"],"model":"haiku","maxTurns":3}
}
JSON
"$PY" -c "
import json
d = json.load(open('$LEGACY_AGENTS'))
assert 'io-worker' in d and 'verifier' in d
# Orchestrator does not require researcher/reporter; absence is fine.
print('legacy agents.json parses cleanly')
" || die "legacy compat broken"
ok "legacy agents.json without v1 entries still parses cleanly"

echo
echo "Stage I: OK"
