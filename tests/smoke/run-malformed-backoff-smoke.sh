#!/bin/bash
# tests/smoke/run-malformed-backoff-smoke.sh — v1.3.12 VERIFY-MALFORMED-BACKOFF
#
# Drives src/hooks/stop.sh end-to-end with a deliberately broken verify.sh
# (the canonical `|| echo 0` double-zero bug → "0\n0" stdout, not valid
# JSON). Confirms:
#   - 3 consecutive verify_malformed events accumulate consecutive_malformed
#     (NOT consecutive_failures — the auto-escalation budget must remain
#     untouched by a script bug)
#   - HUMAN_NEEDED.md is written at threshold 3 with `|| echo 0` /
#     `|| true` guidance
#   - phase is NOT set to "failed" by the malformed path
#   - A subsequent passing verify resets consecutive_malformed but does
#     NOT touch consecutive_failures or auto-delete HUMAN_NEEDED.md

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }
die() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

UHOME="$TMP/uhome"
mkdir -p "$UHOME/log"
export CC_AUTOPIPE_USER_HOME="$UHOME"

# stop.sh resolves state.py via CC_AUTOPIPE_HOME — point it at src/ so
# the local working tree is exercised, not whatever's installed.
export CC_AUTOPIPE_HOME="$REPO_ROOT/src"

PROJECT="$TMP/p"
CCA="$PROJECT/.cc-autopipe"
mkdir -p "$CCA/memory"

# Initialise state.json so reads see a fresh State (avoids any migration
# noise from a missing file).
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
state.write(Path("$PROJECT"), state.State.fresh("p"))
PY

# Broken verify.sh — exact replica of the `|| echo 0` bug. grep -c on a
# missing pattern exits 1 and prints "0"; `|| echo 0` then prints a
# second "0". Result: stdout "0\n0", which fails jq type-checking.
cat > "$CCA/verify.sh" <<'EOF'
#!/bin/bash
PRD="$(dirname "$0")/prd.md"
[ -f "$PRD" ] || touch "$PRD"
UNCHECKED=$(grep -c '^- \[ \]' "$PRD" || echo 0)
echo "$UNCHECKED"
EOF
chmod +x "$CCA/verify.sh"

# Sanity: confirm verify.sh actually outputs the broken double-zero.
RAW="$("$CCA/verify.sh")"
[ "$(printf '%s' "$RAW" | wc -l)" -ge 1 ] \
    || die "verify.sh fixture not producing the expected double-zero output"

STOP_SH="$REPO_ROOT/src/hooks/stop.sh"

run_stop_with_cwd() {
    printf '{"cwd":"%s","session_id":"sess-%s"}' "$PROJECT" "$1" \
        | bash "$STOP_SH" >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# 3 cycles with broken verify
# ---------------------------------------------------------------------------

log "Drive stop.sh 3x with broken verify.sh (double-zero output)"
run_stop_with_cwd 1
run_stop_with_cwd 2
run_stop_with_cwd 3

log "Assert state counters after 3 malformed cycles"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
s = state.read(Path("$PROJECT"))
assert s.consecutive_malformed == 3, (
    f"expected consecutive_malformed=3, got {s.consecutive_malformed}"
)
assert s.consecutive_failures == 0, (
    f"verify_malformed must NOT increment consecutive_failures, got {s.consecutive_failures}"
)
assert s.phase != "failed", (
    f"phase must NOT be 'failed' for a malformed-script bug, got {s.phase!r}"
)
PY
ok "consecutive_malformed=3, consecutive_failures=0, phase != failed"

log "Assert HUMAN_NEEDED.md exists with || echo 0 / || true guidance"
HN="$CCA/HUMAN_NEEDED.md"
[ -f "$HN" ] || die "HUMAN_NEEDED.md not written at threshold 3"
grep -q '|| echo 0' "$HN" \
    || die "HUMAN_NEEDED.md missing the broken-pattern hint '|| echo 0'"
grep -q '|| true' "$HN" \
    || die "HUMAN_NEEDED.md missing the fix '|| true'"
grep -qi 'verify.sh' "$HN" \
    || die "HUMAN_NEEDED.md missing 'verify.sh' reference"
grep -q 'reset-malformed' "$HN" \
    || die "HUMAN_NEEDED.md missing 'reset-malformed' CLI hint"
ok "HUMAN_NEEDED.md present with script-fix guidance"

# ---------------------------------------------------------------------------
# Fix verify.sh; one passing cycle should clear malformed counter
# ---------------------------------------------------------------------------

cat > "$CCA/verify.sh" <<'EOF'
#!/bin/bash
echo '{"passed": true, "score": 0.95, "prd_complete": false}'
EOF
chmod +x "$CCA/verify.sh"

log "Drive stop.sh once with passing verify.sh"
run_stop_with_cwd 4

log "Assert counters reset, HUMAN_NEEDED.md preserved"
"$PY" - <<PY
import sys
from pathlib import Path
sys.path.insert(0, "$REPO_ROOT/src/lib")
import state
s = state.read(Path("$PROJECT"))
assert s.consecutive_malformed == 0, (
    f"expected consecutive_malformed=0 after passing verify, got {s.consecutive_malformed}"
)
assert s.consecutive_failures == 0, (
    f"consecutive_failures must remain 0 (never touched by malformed path), got {s.consecutive_failures}"
)
assert s.last_passed is True, f"last_passed expected True, got {s.last_passed!r}"
PY
[ -f "$HN" ] \
    || die "HUMAN_NEEDED.md auto-deleted by passing verify — operator must still see it"
ok "consecutive_malformed=0 after passing verify; HUMAN_NEEDED.md preserved"

printf '\033[32m===\033[0m PASS — v1.3.12 verify_malformed backoff smoke\n'
