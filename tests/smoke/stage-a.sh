#!/bin/bash
# tests/smoke/stage-a.sh — Stage A DoD validation end-to-end.
# Refs: AGENTS.md §2 Stage A
#
# Exits non-zero on first failure. Prints a one-line PASS/FAIL summary.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32mOK \033[0m %s\n' "$*"; }
die() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

# Resolve pytest + ruff. Prefer .venv if present (host pytest may be
# stale), otherwise fall back to PATH.
if [ -x "$REPO_ROOT/.venv/bin/pytest" ]; then
    PYTEST="$REPO_ROOT/.venv/bin/pytest"
    RUFF="$REPO_ROOT/.venv/bin/ruff"
else
    PYTEST="pytest"
    RUFF="ruff"
fi

# 1. Lint.
log "ruff check"
"$RUFF" check src tests || die "ruff check failed"
ok "ruff check clean"

log "ruff format --check"
"$RUFF" format --check src tests || die "ruff format dirty (run: ruff format src tests)"
ok "ruff format clean"

log "shellcheck on bash files"
SHELL_FILES=$(find src tests tools -type f \( -name '*.sh' -o -path '*/helpers/*' \) ! -name '*.py')
# shellcheck disable=SC2086
shellcheck $SHELL_FILES || die "shellcheck failed"
ok "shellcheck clean ($(echo "$SHELL_FILES" | wc -l | tr -d ' ') files)"

# 2. Pytest unit tests.
log "pytest tests/unit/test_state.py"
"$PYTEST" tests/unit/test_state.py -q || die "state.py unit tests failed"
ok "state.py unit tests pass"

# 3. compat.sh contract.
log "compat.sh function smoke"
COMPAT_OUT=$(bash -c 'source src/lib/compat.sh; echo "$CC_AUTOPIPE_OS|$CC_AUTOPIPE_DATE_FLAVOR|$CC_AUTOPIPE_STAT_FLAVOR"; date_from_epoch 1714363800; tmp=$(mktemp); file_mtime "$tmp"; rm "$tmp"')
echo "$COMPAT_OUT" | grep -qE '^(macos|linux|unknown)\|(gnu|bsd)\|(gnu|bsd)$' || die "compat.sh did not export OS/flavor labels"
echo "$COMPAT_OUT" | grep -qE '^2024-04-29T04:10:00Z$' || die "date_from_epoch produced unexpected value: $(echo "$COMPAT_OUT" | sed -n '2p')"
echo "$COMPAT_OUT" | tail -1 | grep -qE '^[0-9]+$' || die "file_mtime did not return an epoch integer"
ok "compat.sh date_from_epoch and file_mtime correct"

# 4. tg.sh exit-0 contract.
log "tg.sh exits 0 with secrets absent"
TMP_HOME=$(mktemp -d)
trap 'rm -rf "$TMP_HOME"' EXIT
CC_AUTOPIPE_SECRETS_FILE="$TMP_HOME/nope.env" bash src/lib/tg.sh "smoke msg" \
    || die "tg.sh failed with secrets absent"
ok "tg.sh exit 0 (secrets absent)"

log "tg.sh exits 0 with unreachable secrets"
cat > "$TMP_HOME/secrets.env" <<'EOF'
TG_BOT_TOKEN=fake-token-not-real
TG_CHAT_ID=0
EOF
CC_AUTOPIPE_SECRETS_FILE="$TMP_HOME/secrets.env" bash src/lib/tg.sh "test msg" \
    || die "tg.sh did not exit 0 on bad credentials"
ok "tg.sh exit 0 (bad creds)"

# 5. cc-autopipe dispatcher --help.
log "cc-autopipe --help lists every subcommand"
HELP=$(bash src/helpers/cc-autopipe --help)
for sub in init start stop status resume run tail doctor checkpoint block; do
    echo "$HELP" | grep -qE "^\s+$sub\b" || die "--help missing subcommand: $sub"
done
ok "--help covers all subcommands"

log "cc-autopipe --version prints VERSION"
VERS=$(bash src/helpers/cc-autopipe --version)
EXPECTED_VERSION=$(cat src/VERSION)
echo "$VERS" | grep -qF "$EXPECTED_VERSION" || die "--version did not match VERSION file"
ok "--version matches src/VERSION ($EXPECTED_VERSION)"

log "cc-autopipe rejects unknown subcommand with rc=64"
set +e
bash src/helpers/cc-autopipe zzz-not-a-command >/dev/null 2>&1
RC=$?
set -e
[ "$RC" = "64" ] || die "expected rc=64 on unknown command, got $RC"
ok "unknown command rc=64"

# 6. install.sh dry-run sanity (--no-copy + isolated user-home).
log "install.sh --no-copy --user-home <tmp>"
INST_HOME=$(mktemp -d)
bash src/install.sh --no-copy --user-home "$INST_HOME" >/dev/null \
    || die "install.sh failed"
[ -f "$INST_HOME/projects.list" ]      || die "install.sh did not create projects.list"
[ -f "$INST_HOME/log/aggregate.jsonl" ] || die "install.sh did not create aggregate.jsonl"
[ -f "$INST_HOME/secrets.env" ]        || die "install.sh did not seed secrets.env"
PERM=$(stat -c %a "$INST_HOME/secrets.env" 2>/dev/null || stat -f %Lp "$INST_HOME/secrets.env")
[ "$PERM" = "600" ] || die "secrets.env perms = $PERM, expected 600"
rm -rf "$INST_HOME"
ok "install.sh seeds user-home with correct perms"

echo
echo "Stage A: OK"
