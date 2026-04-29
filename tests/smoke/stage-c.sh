#!/bin/bash
# tests/smoke/stage-c.sh — Stage C DoD validation end-to-end.
# Refs: AGENTS.md §2 Stage C

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

# 2. Pytest unit + integration.
log "pytest tests/unit tests/integration"
"$PYTEST" tests/unit tests/integration -q || die "pytest failed"
ok "all unit + integration tests pass"

# 3. Hook unit tests (bash harness).
log "tests/unit/test_hooks/ (bash harness)"
for t in tests/unit/test_hooks/test_*.sh; do
    bash "$t" >/dev/null || die "$t failed"
done
ok "all 4 hook test files pass"

# 4. End-to-end: orchestrator + mock-claude + real hooks → DONE.
log "end-to-end: orchestrator → mock-claude → hooks → DONE"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
PROJECT="$SCRATCH/demo"
USER_HOME="$SCRATCH/uhome"
mkdir -p "$PROJECT"
(cd "$PROJECT" && git init -q)

export CC_AUTOPIPE_HOME="$REPO_ROOT/src"
export CC_AUTOPIPE_USER_HOME="$USER_HOME"
DISPATCHER="$REPO_ROOT/src/helpers/cc-autopipe"

"$DISPATCHER" init "$PROJECT" >/dev/null || die "init failed"

# verify.sh that returns prd_complete=true on iteration 1 → orchestrator
# should transition phase to DONE.
cat > "$PROJECT/.cc-autopipe/verify.sh" <<'EOF'
#!/bin/bash
echo '{"passed":true,"score":0.95,"prd_complete":true,"details":{}}'
EOF
chmod +x "$PROJECT/.cc-autopipe/verify.sh"

CC_AUTOPIPE_COOLDOWN_SEC=0 \
CC_AUTOPIPE_IDLE_SLEEP_SEC=0 \
CC_AUTOPIPE_MAX_LOOPS=1 \
CC_AUTOPIPE_CLAUDE_BIN="$REPO_ROOT/tools/mock-claude.sh" \
CC_AUTOPIPE_HOOKS_DIR="$REPO_ROOT/src/hooks" \
CC_AUTOPIPE_CYCLE_TIMEOUT_SEC=30 \
    "$DISPATCHER" start 2>"$SCRATCH/orch.stderr" >/dev/null \
    || die "orchestrator failed: $(cat "$SCRATCH/orch.stderr")"

PHASE=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json'))['phase'])
")
[ "$PHASE" = "done" ] || die "expected phase=done, got $PHASE"
ok "orchestrator + claude + hooks pipeline reached DONE"

# 5. PreToolUse blocks all 6 §10.2 rules end-to-end.
log "pre-tool-use blocks all 6 rules (real hook against scenario inputs)"
HOOK="$REPO_ROOT/src/hooks/pre-tool-use.sh"
check_block() {
    local desc=$1 input=$2
    set +e
    echo "$input" | bash "$HOOK" >/dev/null 2>&1
    local rc=$?
    set -e
    [ "$rc" = "2" ] || die "$desc: expected rc=2, got $rc"
}
PROJ_JSON="$PROJECT"
check_block "rule1 secrets"      "$(jq -nc --arg cwd "$PROJ_JSON" '{tool_name:"Bash",cwd:$cwd,tool_input:{command:"cat ~/.cc-autopipe/secrets.env"}}')"
check_block "rule2 destructive"  "$(jq -nc --arg cwd "$PROJ_JSON" '{tool_name:"Bash",cwd:$cwd,tool_input:{command:"rm -rf /"}}')"
check_block "rule3 long-op"      "$(jq -nc --arg cwd "$PROJ_JSON" '{tool_name:"Bash",cwd:$cwd,tool_input:{command:"pip install foo"}}')"
check_block "rule4 state.json"   "$(jq -nc --arg cwd "$PROJ_JSON" '{tool_name:"Write",cwd:$cwd,tool_input:{file_path:".cc-autopipe/state.json",content:"{}"}}')"
check_block "rule5 secret content" "$(jq -nc --arg cwd "$PROJ_JSON" '{tool_name:"Write",cwd:$cwd,tool_input:{file_path:"src/foo.py",content:"sk-ant-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}}')"
check_block "rule6 settings.json" "$(jq -nc --arg cwd "$PROJ_JSON" '{tool_name:"Write",cwd:$cwd,tool_input:{file_path:".claude/settings.json",content:"{}"}}')"
ok "all 6 PreToolUse rules block correctly end-to-end"

# 6. stop-failure handles 429 transition.
log "stop-failure transitions on rate_limit"
# Reset to active first so the test sees the transition.
"$PY" "$REPO_ROOT/src/lib/state.py" set-paused "$PROJECT" \
    "$(date -u -d "@0" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u -r 0 +"%Y-%m-%dT%H:%M:%SZ")" \
    "test_setup" >/dev/null || true
# Use the real stop-failure hook directly.
echo "{\"cwd\":\"$PROJECT\",\"error\":\"rate_limit\"}" | \
    bash "$REPO_ROOT/src/hooks/stop-failure.sh" >/dev/null
PAUSED_REASON=$("$PY" -c "
import json
print(json.load(open('$PROJECT/.cc-autopipe/state.json')).get('paused', {}).get('reason'))
")
[ "$PAUSED_REASON" = "rate_limit" ] || die "expected paused.reason=rate_limit, got $PAUSED_REASON"
ok "stop-failure 429 → PAUSED"

echo
echo "Stage C: OK"
