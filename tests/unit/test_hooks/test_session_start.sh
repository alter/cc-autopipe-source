#!/bin/bash
# tests/unit/test_hooks/test_session_start.sh — DoD: outputs valid context, exits 0.
# Refs: SPEC.md §10.1

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=tests/unit/test_hooks/_lib.sh
. "$SCRIPT_DIR/_lib.sh"

echo "== test_session_start.sh =="

# Case 1: minimal stdin → outputs context, exits 0, fires log_event.
fresh_project
run_hook session-start "$(jq -nc --arg cwd "$PROJECT" '{session_id:"sess-1",cwd:$cwd}')"
assert_eq "rc=0 on minimal input"               0     "$HOOK_RC"
assert_contains "stdout has 'cc-autopipe context'"   "cc-autopipe context"  "$HOOK_OUT"
assert_contains "stdout shows phase"                  "Phase: active"        "$HOOK_OUT"
assert_contains "stdout shows iteration"              "Iteration: 0"         "$HOOK_OUT"
assert_contains "aggregate.jsonl logs hook fired"     '"event":"hook_session_start"'  "$(cat "$USER_HOME/log/aggregate.jsonl" 2>/dev/null)"
cleanup_project

# Case 2: checkpoint.md present → output instructs RESUME.
fresh_project
echo "WIP work" > "$PROJECT/.cc-autopipe/checkpoint.md"
run_hook session-start "$(jq -nc --arg cwd "$PROJECT" '{cwd:$cwd}')"
assert_eq "rc=0 with checkpoint"                0     "$HOOK_RC"
assert_contains "stdout mentions RESUME"          "RESUME:"              "$HOOK_OUT"
assert_contains "stdout points to checkpoint.md"  "checkpoint.md"        "$HOOK_OUT"
cleanup_project

# Case 3: failures.jsonl present → recent-failures section appears.
fresh_project
mkdir -p "$PROJECT/.cc-autopipe/memory"
printf '%s\n' \
    '{"ts":"2026-04-29T15:00:00Z","error":"verify_failed","details":{"score":0.6}}' \
    '{"ts":"2026-04-29T15:10:00Z","error":"hook_pretooluse_blocked","reason":"long_op"}' \
    > "$PROJECT/.cc-autopipe/memory/failures.jsonl"
run_hook session-start "$(jq -nc --arg cwd "$PROJECT" '{cwd:$cwd}')"
assert_eq "rc=0 with recent failures"           0     "$HOOK_RC"
assert_contains "stdout lists recent failures"   "Recent failures"      "$HOOK_OUT"
assert_contains "stdout references verify_failed" "verify_failed"       "$HOOK_OUT"
cleanup_project

# Case 4: state.json corrupted → still exits 0 (state.read recovery).
fresh_project
echo "{ not json" > "$PROJECT/.cc-autopipe/state.json"
run_hook session-start "$(jq -nc --arg cwd "$PROJECT" '{cwd:$cwd}')"
assert_eq "rc=0 with corrupted state.json"      0     "$HOOK_RC"
# After a corruption-recovery read, jq on the partial JSON would fail —
# the hook should fall through to its echo defaults.
assert_contains "stdout still produces context block" "cc-autopipe context"  "$HOOK_OUT"
cleanup_project

# Case 5: missing config.yaml → falls back to project basename.
fresh_project
rm "$PROJECT/.cc-autopipe/config.yaml"
run_hook session-start "$(jq -nc --arg cwd "$PROJECT" '{cwd:$cwd}')"
assert_eq "rc=0 with missing config.yaml"       0     "$HOOK_RC"
assert_contains "stdout uses project basename"  "Project: $(basename "$PROJECT")"  "$HOOK_OUT"
cleanup_project

# Case 6: cwd in stdin overrides actual cwd.
fresh_project
ALT=$(mktemp -d)
cd "$ALT"
run_hook session-start "$(jq -nc --arg cwd "$PROJECT" '{cwd:$cwd}')"
assert_eq "rc=0 honours stdin cwd"              0     "$HOOK_RC"
assert_contains "context built from stdin cwd"   "cc-autopipe context"  "$HOOK_OUT"
cd "$REPO_ROOT"
rm -rf "$ALT"
cleanup_project

print_summary "test_session_start"
exit "$FAIL"
