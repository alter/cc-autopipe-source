#!/bin/bash
# tests/smoke/run-detach-defaults-smoke.sh — v1.3.1 DETACH-CONFIG end-to-end.
# Pins the resolution chain: CLI > env > config > hardcoded.

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

DETACH="$REPO_ROOT/src/helpers/cc-autopipe-detach"
export CC_AUTOPIPE_HOME="$REPO_ROOT/src"

read_field() {
    local proj="$1" field="$2"
    "$PY" -c "
import json
print(json.loads(open('$proj/.cc-autopipe/state.json').read())['detached']['$field'])
"
}

seed() {
    local proj="$1" cfg="${2:-}"
    rm -rf "$proj"
    mkdir -p "$proj/.cc-autopipe/memory"
    if [ -n "$cfg" ]; then
        printf '%s\n' "$cfg" > "$proj/.cc-autopipe/config.yaml"
    fi
}

# Test 1: hardcoded fallback (no env, no config, no CLI).
log "hardcoded fallback: 600 / 14400"
P="$TMP/p1"
seed "$P"
unset CC_AUTOPIPE_DEFAULT_CHECK_EVERY CC_AUTOPIPE_DEFAULT_MAX_WAIT
bash "$DETACH" --reason r --check-cmd "true" --project "$P" >/dev/null
[ "$(read_field "$P" check_every_sec)" = "600" ]   || die "check_every_sec != 600"
[ "$(read_field "$P" max_wait_sec)" = "14400" ]    || die "max_wait_sec != 14400"
ok "hardcoded 600 / 14400"

# Test 2: config block sets max_wait_sec=43200 (12h).
log "config detach_defaults block applied"
P="$TMP/p2"
seed "$P" "$(cat <<'EOF'
detach_defaults:
  check_every_sec: 900
  max_wait_sec: 43200
EOF
)"
bash "$DETACH" --reason r --check-cmd "true" --project "$P" >/dev/null
[ "$(read_field "$P" check_every_sec)" = "900" ]   || die "config check_every_sec != 900"
[ "$(read_field "$P" max_wait_sec)" = "43200" ]    || die "config max_wait_sec != 43200"
ok "config block: 900 / 43200"

# Test 3: env beats config.
log "env CC_AUTOPIPE_DEFAULT_MAX_WAIT beats config block"
P="$TMP/p3"
seed "$P" "$(cat <<'EOF'
detach_defaults:
  max_wait_sec: 43200
EOF
)"
CC_AUTOPIPE_DEFAULT_MAX_WAIT=86400 bash "$DETACH" \
    --reason r --check-cmd "true" --project "$P" >/dev/null
[ "$(read_field "$P" max_wait_sec)" = "86400" ]    || die "env did not win"
ok "env beats config: 86400"

# Test 4: CLI beats env.
log "CLI --max-wait beats env var"
P="$TMP/p4"
seed "$P"
CC_AUTOPIPE_DEFAULT_MAX_WAIT=86400 bash "$DETACH" \
    --reason r --check-cmd "true" --max-wait 172800 --project "$P" >/dev/null
[ "$(read_field "$P" max_wait_sec)" = "172800" ]   || die "CLI did not win over env"
ok "CLI beats env: 172800"

# Test 5: CLI beats config.
log "CLI --max-wait beats config block"
P="$TMP/p5"
seed "$P" "$(cat <<'EOF'
detach_defaults:
  max_wait_sec: 43200
EOF
)"
unset CC_AUTOPIPE_DEFAULT_MAX_WAIT
bash "$DETACH" --reason r --check-cmd "true" --max-wait 172800 --project "$P" >/dev/null
[ "$(read_field "$P" max_wait_sec)" = "172800" ]   || die "CLI did not win over config"
ok "CLI beats config: 172800"

# Test 6: AI-trade scenario — config sets 12h, no env, no CLI override.
log "AI-trade scenario: 12h config default applied"
P="$TMP/ai-trade"
seed "$P" "$(cat <<'EOF'
detach_defaults:
  check_every_sec: 600
  max_wait_sec: 43200
EOF
)"
unset CC_AUTOPIPE_DEFAULT_CHECK_EVERY CC_AUTOPIPE_DEFAULT_MAX_WAIT
bash "$DETACH" --reason ml-train --check-cmd "test -f model.pt" --project "$P" >/dev/null
got=$(read_field "$P" max_wait_sec)
[ "$got" = "43200" ] || die "AI-trade scenario: max_wait_sec=$got, expected 43200"
ok "AI-trade scenario: 12h max_wait_sec landed"

printf '\033[32m===\033[0m PASS — detach-defaults smoke (DETACH-CONFIG chain pinned)\n'
