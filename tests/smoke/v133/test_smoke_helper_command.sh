#!/bin/bash
# tests/smoke/v133/test_smoke_helper_command.sh — v1.3.3 Group M P3.
#
# Three sub-cases for cc-autopipe-smoke:
#   a) success.sh exits 0 → SMOKE_OK exit 0
#   b) fail.sh exits 7    → SMOKE_FAIL exit 1, stderr dump shows "boom"
#   c) long.sh sleeps     → SMOKE_OK alive past min-alive, killed
#                           pgrep -f long.sh returns nothing afterward
#
# Plus the dispatcher path (`cc-autopipe smoke <script>`) for parity
# with the way Claude calls the helper.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }
die() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

cd "$TMP"

# a) success.sh exits 0
cat > success.sh <<'EOF'
#!/bin/bash
echo ok
exit 0
EOF
chmod +x success.sh

log 'cc-autopipe-smoke success.sh'
set +e
OUT=$(bash "$REPO_ROOT/src/helpers/cc-autopipe-smoke" "$TMP/success.sh" 2>&1)
RC=$?
set -e
[ "$RC" -eq 0 ] || die "success.sh expected rc=0, got $RC; out:\n$OUT"
echo "$OUT" | grep -q "SMOKE_OK: script completed" || die "missing SMOKE_OK"
ok 'success.sh → SMOKE_OK rc=0'

# b) fail.sh exits 7 with stderr "boom"
cat > fail.sh <<'EOF'
#!/bin/bash
echo "boom" >&2
exit 7
EOF
chmod +x fail.sh

log 'cc-autopipe-smoke fail.sh'
set +e
OUT=$(bash "$REPO_ROOT/src/helpers/cc-autopipe-smoke" "$TMP/fail.sh" 2>&1)
RC=$?
set -e
[ "$RC" -eq 1 ] || die "fail.sh expected rc=1, got $RC; out:\n$OUT"
echo "$OUT" | grep -q "SMOKE_FAIL: script exited with rc=7" || die "missing SMOKE_FAIL rc=7"
echo "$OUT" | grep -q "boom" || die "stderr dump missing 'boom'"
ok 'fail.sh → SMOKE_FAIL rc=1 with stderr dump'

# c) long.sh sleeps 120; --timeout-sec 8 --min-alive-sec 5
cat > long.sh <<'EOF'
#!/bin/bash
sleep 120
EOF
chmod +x long.sh

log 'cc-autopipe-smoke long.sh --timeout-sec 8 --min-alive-sec 5'
set +e
OUT=$(bash "$REPO_ROOT/src/helpers/cc-autopipe-smoke" "$TMP/long.sh" \
    --timeout-sec 8 --min-alive-sec 5 2>&1)
RC=$?
set -e
[ "$RC" -eq 0 ] || die "long.sh expected rc=0 (alive past min-alive), got $RC; out:\n$OUT"
echo "$OUT" | grep -q "SMOKE_OK: script alive past min-alive threshold" || die "missing alive-past-min-alive"
ok 'long.sh → SMOKE_OK alive past min-alive, killed'

# Verify process tree was actually killed.
sleep 1  # allow killpg to settle
LEFTOVER=$(pgrep -f "$TMP/long.sh" || true)
[ -z "$LEFTOVER" ] || die "long.sh process still alive after smoke killed: $LEFTOVER"
ok 'process tree killed (pgrep finds nothing)'

# d) Dispatcher path: `cc-autopipe smoke <script>`
log 'dispatcher: cc-autopipe smoke success.sh'
set +e
OUT=$(bash "$REPO_ROOT/src/helpers/cc-autopipe" smoke "$TMP/success.sh" 2>&1)
RC=$?
set -e
[ "$RC" -eq 0 ] || die "dispatcher smoke expected rc=0, got $RC; out:\n$OUT"
echo "$OUT" | grep -q "SMOKE_OK" || die "dispatcher path missing SMOKE_OK"
ok 'dispatcher smoke <script> works'

# e) Misuse: nonexistent script → exit 2
log 'misuse: nonexistent script → exit 2'
set +e
OUT=$(bash "$REPO_ROOT/src/helpers/cc-autopipe-smoke" "$TMP/no-such.sh" 2>&1)
RC=$?
set -e
[ "$RC" -eq 2 ] || die "misuse expected rc=2, got $RC"
echo "$OUT" | grep -q "not found" || die "missing 'not found' message"
ok 'misuse → rc=2'

printf '\033[32m===\033[0m PASS — v1.3.3 P3 smoke helper command\n'
