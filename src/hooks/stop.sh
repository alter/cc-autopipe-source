#!/bin/bash
# stop.sh — runs verify.sh, parses §7.7 JSON, updates state.
# Refs: SPEC.md §10.3, §7.7
#
# Input:  stdin JSON from Claude Code (session_id, cwd, ...)
# Output: stdout mostly empty; state mutations via state.py CLI.
# Exit:   0 (failures here are recoverable; state.py records them)

set -u

INPUT=$(cat || true)

PROJECT=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
if [ -z "${PROJECT:-}" ] || [ ! -d "$PROJECT" ]; then
    PROJECT=$(pwd)
fi

CC_AUTOPIPE_HOME="${CC_AUTOPIPE_HOME:-$HOME/cc-autopipe}"
STATE_PY="$CC_AUTOPIPE_HOME/lib/state.py"

CCA="$PROJECT/.cc-autopipe"
MEM="$CCA/memory"
mkdir -p "$MEM" 2>/dev/null || true

now_iso() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

log_failure() {
    local error=$1
    shift
    jq -nc \
        --arg ts "$(now_iso)" \
        --arg error "$error" \
        --argjson extras "${1:-{\}}" \
        '{ts: $ts, error: $error} + $extras' \
        >> "$MEM/failures.jsonl" 2>/dev/null || true
}

log_progress_verify() {
    local passed=$1 score=$2 prd=$3
    jq -nc \
        --arg ts "$(now_iso)" \
        --argjson passed "$passed" \
        --argjson score "$score" \
        --argjson prd "$prd" \
        '{ts: $ts, event: "verify", passed: $passed, score: $score, prd_complete: $prd}' \
        >> "$MEM/progress.jsonl" 2>/dev/null || true
}

# Save session_id for next resume (SPEC §6.1, §10.3).
SESSION=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
if [ -n "$SESSION" ]; then
    python3 "$STATE_PY" set-session-id "$PROJECT" "$SESSION" \
        >/dev/null 2>&1 || true
fi

# v1.2 Bug A: sync CURRENT_TASK.md → state.json.current_task. Claude
# writes the file at the start of work and updates it as the task
# progresses; this is its authoritative input channel for what task
# the engine should track. Missing/empty file = nothing to do, not
# an error. Always exits 0 (helper itself enforces this).
python3 "$CC_AUTOPIPE_HOME/lib/stop_helper.py" sync "$PROJECT" \
    >/dev/null 2>&1 || true

VERIFY="$CCA/verify.sh"
if [ ! -x "$VERIFY" ]; then
    log_failure "verify_missing"
    python3 "$STATE_PY" inc-failures "$PROJECT" >/dev/null 2>&1 || true
    # verify_missing → aggregate per §15.2 (treat like verify_malformed)
    python3 "$STATE_PY" log-event "$PROJECT" verify_missing >/dev/null 2>&1 || true
    exit 0
fi

# Run verify.sh with a 60s wall-clock timeout per SPEC §10.3 / §7.7.
RAW=$(timeout 60 "$VERIFY" 2>&1)
RC=$?

# Validate the §7.7 envelope: top-level passed (bool), score (number),
# prd_complete (bool). Anything else is "malformed" → failure path.
if ! printf '%s' "$RAW" | jq -e '
    type == "object"
    and (.passed | type == "boolean")
    and (.score | type == "number")
    and (.prd_complete | type == "boolean")
' >/dev/null 2>&1; then
    # Capture verify rc + truncated output for diagnosis.
    OUTPUT_TRIM=$(printf '%s' "$RAW" | head -c 2000)
    EXTRAS=$(jq -nc --argjson rc "$RC" --arg out "$OUTPUT_TRIM" \
        '{rc: $rc, output: $out}')
    log_failure "verify_malformed" "$EXTRAS"
    python3 "$STATE_PY" inc-failures "$PROJECT" >/dev/null 2>&1 || true
    python3 "$STATE_PY" log-event "$PROJECT" verify_malformed rc="$RC" \
        >/dev/null 2>&1 || true
    exit 0
fi

PASSED=$(printf '%s' "$RAW" | jq -r '.passed')
SCORE=$(printf '%s' "$RAW" | jq -r '.score')
PRD=$(printf '%s' "$RAW" | jq -r '.prd_complete')

# v1.2 Bug B: optional in_progress flag. Verify output that includes
# `"in_progress": true` signals "work is happening, don't count this
# as a failure". Missing field → false (backward-compat with v1.0
# verify scripts).
IN_PROGRESS=$(printf '%s' "$RAW" | jq -r '.in_progress // false' 2>/dev/null || echo "false")
case "$IN_PROGRESS" in
    true|false) ;;
    *) IN_PROGRESS="false" ;;
esac

python3 "$STATE_PY" update-verify "$PROJECT" \
    --passed "$PASSED" --score "$SCORE" --prd-complete "$PRD" \
    --in-progress "$IN_PROGRESS" \
    >/dev/null 2>&1 || true

# Per SPEC §15.2: verify_pass/verify_fail go to progress.jsonl only
# (NOT aggregate.jsonl). On fail, also append to failures.jsonl.
log_progress_verify "$PASSED" "$SCORE" "$PRD"
if [ "$IN_PROGRESS" = "true" ]; then
    # v1.2 Bug B: cycle_in_progress event in aggregate.jsonl so the
    # operator sees the project is "cooking" not "broken". No
    # failures.jsonl entry — would otherwise inflate the failure
    # categorizer (Bug H) into thinking verify is structurally broken.
    python3 "$STATE_PY" log-event "$PROJECT" cycle_in_progress \
        score="$SCORE" >/dev/null 2>&1 || true
elif [ "$PASSED" = "false" ]; then
    EXTRAS=$(jq -nc --argjson score "$SCORE" '{details: {score: $score}}')
    log_failure "verify_failed" "$EXTRAS"
fi

exit 0
