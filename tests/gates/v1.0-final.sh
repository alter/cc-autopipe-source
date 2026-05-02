#!/bin/bash
# tests/gates/v1.0-final.sh — final cross-batch validator for v1.0.
#
# shellcheck disable=SC2016
# (Single-quoted strings are deliberate — they are passed verbatim to
# `sh -c` via check_sh and must not expand in this shell.)
#
# Comprehensive end-of-build gate per AGENTS-v1.md §7. Runs every
# discipline check from batch-a..d in one pass plus v1.0-spanning
# acceptance items from SPEC-v1.md §3.
#
# Refs: AGENTS-v1.md §7 (v1.0 acceptance criteria), SPEC-v1.md §3
#
# Usage:
#   bash tests/gates/v1.0-final.sh

set +e

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

if [ -x "$REPO_ROOT/.venv/bin/pytest" ]; then
    PYTEST="$REPO_ROOT/.venv/bin/pytest"
    RUFF="$REPO_ROOT/.venv/bin/ruff"
else
    PYTEST="pytest"
    RUFF="ruff"
fi

DISPATCHER="$REPO_ROOT/src/helpers/cc-autopipe"
export CC_AUTOPIPE_HOME="$REPO_ROOT/src"

FAIL=0
FAILED_ITEMS=()

check() {
    local desc="$1"; shift
    local out
    if ! out=$("$@" 2>&1); then
        printf 'GATE FAIL: %s\n' "$desc" >&2
        printf '%s\n' "$out" | tail -n 25 | sed 's/^/  | /' >&2
        FAIL=$((FAIL + 1))
        FAILED_ITEMS+=("$desc")
        return 1
    fi
    printf 'GATE OK:   %s\n' "$desc"
    return 0
}

check_sh() {
    local desc="$1"; shift
    check "$desc" sh -c "$*"
}

# ---------------------------------------------------------------------------
# A) Source-tree hygiene
# ---------------------------------------------------------------------------

check_sh "[hygiene] working tree clean" \
    'test -z "$(git status --porcelain | grep -v "^?? PROMPT-v1.md$")"'
check_sh "[hygiene] OPEN_QUESTIONS.md has no Status: blocked entries" \
    '! grep -qE "^\*\*Status:\*\*[[:space:]]*blocked" OPEN_QUESTIONS.md'
check_sh "[hygiene] no orphan TODO version markers" \
    "! grep -REn --exclude-dir=gates 'TODO\\((v0\\.5\\.1|v1\\.0)\\)' src/ tests/ tools/ \
        | grep -vE 'OPEN_QUESTIONS|issue#'"
check_sh "[hygiene] STATUS.md says v1.0 BUILD COMPLETE" \
    "grep -q 'v1.0 BUILD COMPLETE' STATUS.md"

# ---------------------------------------------------------------------------
# B) Lint
# ---------------------------------------------------------------------------

check "[lint] ruff check src tests tools" "$RUFF" check src tests tools
check "[lint] ruff format --check src tests tools" "$RUFF" format --check src tests tools
check_sh "[lint] shellcheck on bash files" \
    'find src tests tools -type f \( -name "*.sh" -o -path "*/helpers/*" \) ! -name "*.py" -print0 \
        | xargs -0 shellcheck -x'

# ---------------------------------------------------------------------------
# C) ALL stage smokes (v0.5 + v1.0)
# ---------------------------------------------------------------------------

for stage in a b c d e f; do
    check "[smoke v0.5] stage-$stage" bash "tests/smoke/stage-$stage.sh"
done
for stage in h i j k l m n; do
    check "[smoke v1.0] stage-$stage" bash "tests/smoke/stage-$stage.sh"
done

# ---------------------------------------------------------------------------
# D) Pytest
# ---------------------------------------------------------------------------

check "[pytest] full suite" "$PYTEST" tests/ -q --tb=no

# ---------------------------------------------------------------------------
# E) Doctor
# ---------------------------------------------------------------------------

check "[doctor] cc-autopipe doctor --offline returns 0" \
    "$DISPATCHER" doctor --offline

# ---------------------------------------------------------------------------
# F) v1.0 acceptance per SPEC-v1.md §3
# ---------------------------------------------------------------------------

# State schema v2.
check_sh "[acceptance] state schema v2 default for fresh state" \
    "python3 -c '
import sys, json
sys.path.insert(0, \"src/lib\")
import state
fresh = state.State.fresh(\"acceptance-test\").to_dict()
assert fresh[\"schema_version\"] == 2
for k in (\"detached\", \"current_phase\", \"phases_completed\",
          \"escalated_next_cycle\", \"successful_cycles_since_improver\",
          \"improver_due\"):
    assert k in fresh, k
print(\"v1.0 schema OK\")
'"

# All v1.0 helpers / surfaces wired.
check_sh "[acceptance] cc-autopipe stop subcommand wired (v0.5.1)" \
    "$DISPATCHER stop --help | grep -q -- '--timeout'"
check_sh "[acceptance] cc-autopipe detach subcommand wired (Stage H)" \
    "$DISPATCHER detach --help | grep -q -- '--check-cmd'"
check_sh "[acceptance] cc-autopipe install-systemd wired (Stage M)" \
    "$DISPATCHER install-systemd --help | grep -q install-systemd"
check_sh "[acceptance] cc-autopipe install-launchd wired (Stage M)" \
    "$DISPATCHER install-launchd --help | grep -q install-launchd"

# Subagents shipped.
check_sh "[acceptance] all 5 v1.0 subagents in template" \
    "python3 -c '
import json
d = json.load(open(\"src/templates/.cc-autopipe/agents.json\"))
assert set(d) == {\"io-worker\", \"verifier\", \"researcher\", \"reporter\", \"improver\"}
'"

# Config blocks.
check_sh "[acceptance] config.yaml carries auto_escalation block" \
    "grep -q '^auto_escalation:' src/templates/.cc-autopipe/config.yaml"
check_sh "[acceptance] config.yaml carries improver block" \
    "grep -q '^improver:' src/templates/.cc-autopipe/config.yaml"

# Backward compat.
check_sh "[backward-compat] v1 (schema_version=1) state migrates to v2" \
    "python3 -c '
import sys, json, tempfile, pathlib
sys.path.insert(0, \"src/lib\")
import state
with tempfile.TemporaryDirectory() as d:
    proj = pathlib.Path(d) / \"legacy\"
    (proj / \".cc-autopipe\").mkdir(parents=True)
    legacy = {\"schema_version\": 1, \"name\": \"legacy\", \"phase\": \"active\",
              \"iteration\": 1, \"session_id\": None, \"last_score\": None,
              \"last_passed\": None, \"prd_complete\": False,
              \"consecutive_failures\": 0, \"last_cycle_started_at\": None,
              \"last_progress_at\": None, \"threshold\": 0.85, \"paused\": None}
    (proj / \".cc-autopipe\" / \"state.json\").write_text(json.dumps(legacy))
    s = state.read(proj)
    assert s.schema_version == 2
    assert s.detached is None
    assert s.current_phase == 1
    assert s.phases_completed == []
    assert s.escalated_next_cycle is False
    assert s.successful_cycles_since_improver == 0
    assert s.improver_due is False
    state.write(proj, s)
    raw = json.loads((proj / \".cc-autopipe\" / \"state.json\").read_text())
    assert raw[\"schema_version\"] == 2
'"

# ---------------------------------------------------------------------------
# G) Engine size sanity check
# ---------------------------------------------------------------------------

check_sh "[size] engine code under 7K lines (sanity bound)" \
    "test \$(find src -name '*.py' -o -name 'orchestrator' -o -name '*.sh' | xargs wc -l 2>/dev/null | tail -1 | awk '{print \$1}') -lt 7000"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo
if [ $FAIL -gt 0 ]; then
    printf 'v1.0 FINAL GATE FAILED: %d check(s) failed\n' "$FAIL" >&2
    for item in "${FAILED_ITEMS[@]}"; do
        printf '  - %s\n' "$item" >&2
    done
    exit 1
fi

echo "v1.0 FINAL GATE PASSED."
echo
echo "v1.0 BUILD COMPLETE — Roman: please tag v1.0."
exit 0
