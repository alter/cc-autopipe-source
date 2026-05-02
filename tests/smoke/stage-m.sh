#!/bin/bash
# tests/smoke/stage-m.sh — Stage M DoD validation.
# Refs: AGENTS-v1.md §3.4, SPEC-v1.md §2.6

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
log "ruff + shellcheck"
"$REPO_ROOT/.venv/bin/ruff" check src/cli/service.py || die "ruff failed"
ok "lint clean"

# 2. Templates exist + are valid (XML well-formed for the plist).
log "init templates exist + plist is well-formed XML"
[ -f src/init/cc-autopipe.service.template ] || die "systemd template missing"
[ -f src/init/com.cc-autopipe.plist.template ] || die "launchd template missing"
"$PY" -c "
import xml.etree.ElementTree as ET
ET.fromstring(open('src/init/com.cc-autopipe.plist.template').read())
print('plist template parses as XML')
" || die "launchd plist template is malformed XML"
ok "templates exist; plist parses as XML"

# 3. Pytest slice.
log "pytest tests/integration/test_cli_service.py"
"$PYTEST" tests/integration/test_cli_service.py -q --tb=short \
    || die "pytest failed"
ok "11 service tests pass"

# 4. End-to-end install + uninstall via dispatcher.
log "end-to-end via dispatcher: install + uninstall (systemd + launchd)"
SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT
SD_DIR="$SCRATCH/systemd"
LD_DIR="$SCRATCH/launchd"
FAKE_HOME="$SCRATCH/fake-home"

"$DISPATCHER" install-systemd --target-dir "$SD_DIR" --home "$FAKE_HOME" >/dev/null \
    || die "install-systemd failed"
[ -f "$SD_DIR/cc-autopipe.service" ] || die "systemd unit not written"
ok "install-systemd wrote unit"

"$DISPATCHER" uninstall-systemd --target-dir "$SD_DIR" >/dev/null \
    || die "uninstall-systemd failed"
[ ! -f "$SD_DIR/cc-autopipe.service" ] || die "systemd unit still present"
ok "uninstall-systemd removed unit"

"$DISPATCHER" install-launchd --target-dir "$LD_DIR" --home "$FAKE_HOME" >/dev/null \
    || die "install-launchd failed"
[ -f "$LD_DIR/com.cc-autopipe.plist" ] || die "launchd plist not written"
ok "install-launchd wrote plist"

"$DISPATCHER" uninstall-launchd --target-dir "$LD_DIR" >/dev/null \
    || die "uninstall-launchd failed"
[ ! -f "$LD_DIR/com.cc-autopipe.plist" ] || die "launchd plist still present"
ok "uninstall-launchd removed plist"

# 5. Idempotent uninstall on a fresh dir.
"$DISPATCHER" uninstall-systemd --target-dir "$SD_DIR" >/dev/null \
    || die "uninstall-systemd not idempotent"
"$DISPATCHER" uninstall-launchd --target-dir "$LD_DIR" >/dev/null \
    || die "uninstall-launchd not idempotent"
ok "uninstall is idempotent (rc=0 when absent)"

echo
echo "Stage M: OK"
