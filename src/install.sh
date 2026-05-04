#!/bin/bash
# install.sh — install cc-autopipe engine to PREFIX and seed user home.
# Stage A: minimal but functional. Stage F finalises (doctor, PATH wiring).
# Refs: SPEC.md §5.1, §5.2, AGENTS.md §2 (Stage A DoD)
#
# Usage:
#   install.sh [--prefix DIR] [--user-home DIR] [--no-copy]
#
# Defaults: PREFIX=$HOME/cc-autopipe, USER_HOME=$HOME/.cc-autopipe.
# --no-copy: skip copying src/ to PREFIX (dev mode — uses CC_AUTOPIPE_HOME instead).

set -euo pipefail

PREFIX="$HOME/cc-autopipe"
USER_HOME="$HOME/.cc-autopipe"
DO_COPY=1

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)
            PREFIX=$2; shift 2 ;;
        --user-home)
            USER_HOME=$2; shift 2 ;;
        --no-copy)
            DO_COPY=0; shift ;;
        -h|--help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *)
            echo "install.sh: unknown arg: $1" >&2
            exit 64 ;;
    esac
done

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> cc-autopipe install"
echo "    src:       $SRC_DIR"
echo "    prefix:    $PREFIX"
echo "    user-home: $USER_HOME"

# Prerequisites check (warn, don't abort — doctor handles strict checks).
need() {
    local bin=$1
    if ! command -v "$bin" >/dev/null 2>&1; then
        echo "    WARN: '$bin' not on PATH" >&2
    fi
}
need bash
need python3
need jq
need curl
need git

# Sanity: Python 3.11+
if command -v python3 >/dev/null 2>&1; then
    PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    case "$PYV" in
        3.1[1-9]|3.[2-9][0-9]|[4-9].*) ;;
        *) echo "    WARN: python3 is $PYV, need >= 3.11" >&2 ;;
    esac
fi

# Sanity: bash 5+
BV=$(bash --version | head -1 | awk '{print $4}' | cut -d. -f1)
if [ -z "${BV:-}" ] || [ "$BV" -lt 5 ] 2>/dev/null; then
    echo "    WARN: bash version <5 may break hooks" >&2
fi

# 1. Create user-home skeleton.
mkdir -p "$USER_HOME/log"
touch "$USER_HOME/projects.list"
touch "$USER_HOME/log/aggregate.jsonl"
if [ ! -f "$USER_HOME/secrets.env" ]; then
    cat > "$USER_HOME/secrets.env" <<'EOF'
# cc-autopipe secrets — chmod 600
# Telegram (optional but recommended):
# TG_BOT_TOKEN=
# TG_CHAT_ID=
EOF
    chmod 600 "$USER_HOME/secrets.env"
fi
echo "==> user-home seeded at $USER_HOME"

# 2. Copy src/ to PREFIX unless --no-copy.
if [ "$DO_COPY" -eq 1 ]; then
    mkdir -p "$PREFIX"
    # Copy with cp -R (works on Linux + macOS without rsync dep).
    cp -R "$SRC_DIR/." "$PREFIX/"
    # Make executables executable.
    chmod +x "$PREFIX/install.sh" 2>/dev/null || true
    [ -f "$PREFIX/orchestrator" ] && chmod +x "$PREFIX/orchestrator"
    find "$PREFIX/helpers" -type f -exec chmod +x {} \; 2>/dev/null || true
    find "$PREFIX/hooks"   -type f -name '*.sh' -exec chmod +x {} \; 2>/dev/null || true
    find "$PREFIX/lib"     -type f -name '*.sh' -exec chmod +x {} \; 2>/dev/null || true
    # If installing from a git work-tree, freeze version from latest tag —
    # keeps PREFIX/VERSION accurate even when src/VERSION lags behind tags.
    if BAKED_VER=$(git -C "$SRC_DIR/.." describe --tags --dirty 2>/dev/null); then
        BAKED_VER=${BAKED_VER#v}
        printf '%s\n' "$BAKED_VER" > "$PREFIX/VERSION"
        echo "    version:   $BAKED_VER (from git tag)"
    fi
    echo "==> engine copied to $PREFIX"
else
    echo "==> skipping copy (--no-copy)"
fi

cat <<EOF

cc-autopipe install complete.

Next steps:
  1. Add helpers to PATH:
       export PATH="$PREFIX/helpers:\$PATH"
  2. Edit secrets:
       \$EDITOR $USER_HOME/secrets.env
  3. Verify install (once stage F lands):
       cc-autopipe doctor

EOF
