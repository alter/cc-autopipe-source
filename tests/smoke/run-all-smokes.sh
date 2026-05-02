#!/bin/bash
# tests/smoke/run-all-smokes.sh — wrapper that runs every stage smoke
# sequentially and reports an N/M summary.
#
# Refs: AGENTS-v1.2.md §4 (pre-flight), §13 (final integration check)
#
# Stages covered:
#   - v0.5 baseline: a, b, c, d, e, f
#   - v1.0 additions: h, i, j, k, l, m, n
#   - v1.2 additions (added by their respective batches): none yet
#
# Each individual stage smoke internally re-runs lint + targeted pytest;
# total wall time on a clean tree is ~25 minutes. Runs are sequential so
# a fail short-circuits early when --fast is passed (default keeps going
# to give a full picture). Always exits non-zero if any stage fails.
#
# Usage:
#   bash tests/smoke/run-all-smokes.sh           # run all, report summary
#   bash tests/smoke/run-all-smokes.sh --fast    # exit on first failure
#   bash tests/smoke/run-all-smokes.sh a h n     # run only a, h, n
#
# Exit code: 0 if every requested smoke passes, 1 otherwise.

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

log()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32mOK \033[0m %s\n' "$*"; }
fail() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; }

ALL_STAGES=(a b c d e f h i j k l m n)

FAST=0
REQUESTED=()
for arg in "$@"; do
    case "$arg" in
        --fast) FAST=1 ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0 ;;
        *) REQUESTED+=("$arg") ;;
    esac
done

if [ "${#REQUESTED[@]}" -eq 0 ]; then
    STAGES=("${ALL_STAGES[@]}")
else
    STAGES=("${REQUESTED[@]}")
fi

PASS_LIST=()
FAIL_LIST=()
START_TS=$(date +%s)

for stage in "${STAGES[@]}"; do
    SCRIPT="tests/smoke/stage-${stage}.sh"
    if [ ! -x "$SCRIPT" ] && [ ! -f "$SCRIPT" ]; then
        fail "stage-${stage}: smoke script missing at $SCRIPT"
        FAIL_LIST+=("$stage:missing")
        if [ "$FAST" = "1" ]; then break; fi
        continue
    fi
    log "stage-${stage} starting"
    STAGE_START=$(date +%s)
    LOG="/tmp/run-all-smokes.stage-${stage}.log"
    if bash "$SCRIPT" >"$LOG" 2>&1; then
        STAGE_DUR=$(( $(date +%s) - STAGE_START ))
        ok "stage-${stage} (${STAGE_DUR}s)"
        PASS_LIST+=("$stage")
    else
        STAGE_DUR=$(( $(date +%s) - STAGE_START ))
        fail "stage-${stage} (${STAGE_DUR}s) — last 20 lines of $LOG:"
        tail -n 20 "$LOG" | sed 's/^/    | /' >&2
        FAIL_LIST+=("$stage")
        if [ "$FAST" = "1" ]; then
            fail "FAST mode: aborting after first failure"
            break
        fi
    fi
done

TOTAL_DUR=$(( $(date +%s) - START_TS ))
TOTAL=${#STAGES[@]}
PASSED=${#PASS_LIST[@]}
FAILED=${#FAIL_LIST[@]}

echo
echo "======================================================================"
printf 'run-all-smokes: %d/%d passed in %ds\n' "$PASSED" "$TOTAL" "$TOTAL_DUR"
if [ "$FAILED" -gt 0 ]; then
    printf 'FAILED stages: %s\n' "${FAIL_LIST[*]}" >&2
    echo "======================================================================"
    exit 1
fi
echo "======================================================================"
exit 0
