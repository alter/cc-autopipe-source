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
#   - v1.3 / v1.3.1 / v1.3.2 hotfix smokes (run-*.sh, no stage letter):
#     autonomy, meta-reflect, knowledge-enforce, research-plan,
#     stuck-detection, recovery-sweep, detach-defaults,
#     meta-reflect-trigger, research-mode-trigger, knowledge-mtime
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
# v1.3+ hotfix smokes don't fit the stage-letter scheme — they live as
# `run-<name>-smoke.sh` siblings. Listed here so a full smoke run
# covers everything that pins a hotfix invariant.
HOTFIX_SMOKES=(
    autonomy
    meta-reflect
    knowledge-enforce
    research-plan
    stuck-detection
    recovery-sweep
    detach-defaults
    meta-reflect-trigger
    research-mode-trigger
    knowledge-mtime
    research-task-completion
    promotion-validation
    leaderboard-elo
)
# v1.3.3 smokes use real CLI commands (no Python heredoc) and live
# under tests/smoke/v133/test_<name>.sh. Stage names start with `v133-`
# to make their location obvious and keep the resolver tidy.
V133_SMOKES=(
    v133-liveness-stale-detection
    v133-knowledge-gate-blocks-detach
    v133-smoke-helper-command
    v133-detach-with-liveness-flags
    v133-v132-backward-compat
)
# v1.3.4 hotfix smokes — same `v134-<rest>` convention, located under
# tests/smoke/v134/test_<rest>.sh. R8 covers the transient-retry path;
# R9 covers the network probe gate via a swap-and-restore stub.
V134_SMOKES=(
    v134-transient-retry
    v134-network-probe
)

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
    STAGES=(
        "${ALL_STAGES[@]}"
        "${HOTFIX_SMOKES[@]}"
        "${V133_SMOKES[@]}"
        "${V134_SMOKES[@]}"
    )
else
    STAGES=("${REQUESTED[@]}")
fi

PASS_LIST=()
FAIL_LIST=()
START_TS=$(date +%s)

# A stage name matches one of three conventions:
#   - single-letter / short token  → tests/smoke/stage-<name>.sh
#   - hotfix smoke name            → tests/smoke/run-<name>-smoke.sh
#   - v1.3.3+ real-CLI smoke       → tests/smoke/v133/test_<rest>.sh
#                                    (stage name format: "v133-<rest>")
# Older stage scripts win when both happen to exist (shouldn't happen
# in practice).
_resolve_smoke_script() {
    local name="$1"
    local stage_path="tests/smoke/stage-${name}.sh"
    local hotfix_path="tests/smoke/run-${name}-smoke.sh"
    if [ -f "$stage_path" ]; then
        echo "$stage_path"
        return
    fi
    if [ -f "$hotfix_path" ]; then
        echo "$hotfix_path"
        return
    fi
    if [[ "$name" == v133-* ]]; then
        local rest="${name#v133-}"
        local v133_path="tests/smoke/v133/test_${rest//-/_}.sh"
        if [ -f "$v133_path" ]; then
            echo "$v133_path"
            return
        fi
    fi
    if [[ "$name" == v134-* ]]; then
        local rest="${name#v134-}"
        local v134_path="tests/smoke/v134/test_${rest//-/_}.sh"
        if [ -f "$v134_path" ]; then
            echo "$v134_path"
            return
        fi
    fi
    echo ""
}

for stage in "${STAGES[@]}"; do
    SCRIPT="$(_resolve_smoke_script "$stage")"
    if [ -z "$SCRIPT" ]; then
        fail "smoke ${stage}: script missing (tried stage-${stage}.sh and run-${stage}-smoke.sh)"
        FAIL_LIST+=("$stage:missing")
        if [ "$FAST" = "1" ]; then break; fi
        continue
    fi
    log "${stage} starting"
    STAGE_START=$(date +%s)
    LOG="/tmp/run-all-smokes.${stage}.log"
    if bash "$SCRIPT" >"$LOG" 2>&1; then
        STAGE_DUR=$(( $(date +%s) - STAGE_START ))
        ok "${stage} (${STAGE_DUR}s)"
        PASS_LIST+=("$stage")
    else
        STAGE_DUR=$(( $(date +%s) - STAGE_START ))
        fail "${stage} (${STAGE_DUR}s) — last 20 lines of $LOG:"
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
