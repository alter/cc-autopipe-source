#!/bin/bash
# tests/unit/test_hooks/test_stop.sh — verify.sh runner contract.
# Refs: SPEC.md §10.3, §7.7, §15.2

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=tests/unit/test_hooks/_lib.sh
. "$SCRIPT_DIR/_lib.sh"

echo "== test_stop.sh =="

write_verify() {
    local body=$1
    cat > "$PROJECT/.cc-autopipe/verify.sh" <<EOF
#!/bin/bash
$body
EOF
    chmod +x "$PROJECT/.cc-autopipe/verify.sh"
}

stop_input() {
    jq -nc --arg cwd "$PROJECT" --arg sid "${1:-}" \
        'if $sid == "" then {cwd:$cwd} else {cwd:$cwd, session_id:$sid} end'
}

# --- happy path: passing verify ---
fresh_project
write_verify 'echo '"'"'{"passed":true,"score":0.92,"prd_complete":false,"details":{}}'"'"
run_hook stop "$(stop_input sess-happy)"
assert_eq "rc=0 happy path" 0 "$HOOK_RC"
assert_jq "session_id saved"      "$PROJECT/.cc-autopipe/state.json" .session_id  "sess-happy"
assert_jq "last_passed=true"      "$PROJECT/.cc-autopipe/state.json" .last_passed "true"
assert_jq "last_score=0.92"       "$PROJECT/.cc-autopipe/state.json" .last_score  "0.92"
assert_jq "consecutive_failures=0" "$PROJECT/.cc-autopipe/state.json" .consecutive_failures 0
assert_contains "progress.jsonl gets verify event" '"event":"verify"' \
    "$(cat "$PROJECT/.cc-autopipe/memory/progress.jsonl")"
# §15.2: passing verify must NOT go to aggregate.jsonl
if [ -f "$USER_HOME/log/aggregate.jsonl" ]; then
    assert_not_contains "happy verify NOT in aggregate" '"event":"verify_malformed"' \
        "$(cat "$USER_HOME/log/aggregate.jsonl")"
fi
cleanup_project

# --- happy path: failing verify (passed=false, valid envelope) ---
fresh_project
write_verify 'echo '"'"'{"passed":false,"score":0.55,"prd_complete":false,"details":{}}'"'"
run_hook stop "$(stop_input)"
assert_eq "rc=0 failing verify" 0 "$HOOK_RC"
assert_jq "last_passed=false"     "$PROJECT/.cc-autopipe/state.json" .last_passed "false"
assert_jq "last_score=0.55"       "$PROJECT/.cc-autopipe/state.json" .last_score  "0.55"
assert_jq "consecutive_failures=1" "$PROJECT/.cc-autopipe/state.json" .consecutive_failures 1
# §15.2: verify_fail goes to BOTH progress.jsonl AND failures.jsonl
assert_contains "progress.jsonl has verify"      '"event":"verify"'   \
    "$(cat "$PROJECT/.cc-autopipe/memory/progress.jsonl")"
assert_contains "failures.jsonl has verify_failed" '"error":"verify_failed"' \
    "$(cat "$PROJECT/.cc-autopipe/memory/failures.jsonl")"
cleanup_project

# --- malformed: not JSON at all ---
fresh_project
write_verify 'echo not json output'
run_hook stop "$(stop_input)"
assert_eq "rc=0 on malformed verify" 0 "$HOOK_RC"
assert_jq "consecutive_failures=1"   "$PROJECT/.cc-autopipe/state.json" .consecutive_failures 1
assert_contains "failures.jsonl has verify_malformed" '"error":"verify_malformed"' \
    "$(cat "$PROJECT/.cc-autopipe/memory/failures.jsonl")"
# §15.2: verify_malformed goes to aggregate.jsonl
assert_contains "aggregate.jsonl has verify_malformed" '"event":"verify_malformed"' \
    "$(cat "$USER_HOME/log/aggregate.jsonl")"
cleanup_project

# --- malformed: JSON but wrong types ---
fresh_project
write_verify 'echo '"'"'{"passed":"yes","score":"high","prd_complete":1}'"'"
run_hook stop "$(stop_input)"
assert_eq "rc=0 on wrong-types verify" 0 "$HOOK_RC"
assert_jq "consecutive_failures=1" "$PROJECT/.cc-autopipe/state.json" .consecutive_failures 1
cleanup_project

# --- malformed: missing required key ---
fresh_project
write_verify 'echo '"'"'{"passed":true,"score":0.9}'"'"
run_hook stop "$(stop_input)"
assert_eq "rc=0 on missing prd_complete" 0 "$HOOK_RC"
assert_jq "consecutive_failures=1" "$PROJECT/.cc-autopipe/state.json" .consecutive_failures 1
cleanup_project

# --- missing verify.sh ---
fresh_project
rm -f "$PROJECT/.cc-autopipe/verify.sh"
run_hook stop "$(stop_input)"
assert_eq "rc=0 on missing verify.sh" 0 "$HOOK_RC"
assert_contains "failures.jsonl has verify_missing" '"error":"verify_missing"' \
    "$(cat "$PROJECT/.cc-autopipe/memory/failures.jsonl")"
assert_contains "aggregate.jsonl has verify_missing" '"event":"verify_missing"' \
    "$(cat "$USER_HOME/log/aggregate.jsonl")"
assert_jq "consecutive_failures=1" "$PROJECT/.cc-autopipe/state.json" .consecutive_failures 1
cleanup_project

# --- non-executable verify.sh treated as missing ---
fresh_project
write_verify 'echo "{}"'
chmod -x "$PROJECT/.cc-autopipe/verify.sh"
run_hook stop "$(stop_input)"
assert_eq "rc=0 on non-exec verify.sh" 0 "$HOOK_RC"
assert_contains "non-exec verify treated as missing" '"error":"verify_missing"' \
    "$(cat "$PROJECT/.cc-autopipe/memory/failures.jsonl")"
cleanup_project

# --- consecutive_failures resets on passing verify ---
fresh_project
# Seed consecutive_failures=2 by running malformed twice.
write_verify 'echo not json'
run_hook stop "$(stop_input)"
run_hook stop "$(stop_input)"
assert_jq "after 2 malformed: failures=2" "$PROJECT/.cc-autopipe/state.json" .consecutive_failures 2
write_verify 'echo '"'"'{"passed":true,"score":0.9,"prd_complete":false,"details":{}}'"'"
run_hook stop "$(stop_input)"
assert_jq "passing verify resets failures to 0" "$PROJECT/.cc-autopipe/state.json" .consecutive_failures 0
cleanup_project

# --- session_id in stdin is persisted (Q3 verification) ---
fresh_project
write_verify 'echo '"'"'{"passed":true,"score":1.0,"prd_complete":true,"details":{}}'"'"
run_hook stop "$(stop_input sess-Q3-verify)"
assert_jq "Q3: session_id round-trips through Stop hook" \
    "$PROJECT/.cc-autopipe/state.json" .session_id "sess-Q3-verify"
cleanup_project

# --- empty session_id is tolerated ---
fresh_project
write_verify 'echo '"'"'{"passed":true,"score":1.0,"prd_complete":true,"details":{}}'"'"
run_hook stop '{"cwd":"'"$PROJECT"'"}'
assert_eq "rc=0 with no session_id" 0 "$HOOK_RC"
assert_jq "session_id remains null when absent" \
    "$PROJECT/.cc-autopipe/state.json" .session_id "null"
cleanup_project

# ---------------------------------------------------------------------------
# v1.2 Bug A: CURRENT_TASK.md → state.json.current_task
# ---------------------------------------------------------------------------

# --- CURRENT_TASK.md absent → current_task stays null ---
fresh_project
write_verify 'echo '"'"'{"passed":true,"score":1.0,"prd_complete":false,"details":{}}'"'"
run_hook stop "$(stop_input sess-no-ct)"
assert_eq "rc=0 with no CURRENT_TASK.md" 0 "$HOOK_RC"
assert_jq "current_task remains null when CURRENT_TASK.md absent" \
    "$PROJECT/.cc-autopipe/state.json" .current_task "null"
cleanup_project

# --- CURRENT_TASK.md populated → state.current_task fields populated ---
fresh_project
write_verify 'echo '"'"'{"passed":false,"score":0.4,"prd_complete":false,"details":{}}'"'"
cat > "$PROJECT/.cc-autopipe/CURRENT_TASK.md" <<EOF
task: cand_imbloss_v2
stage: training
stages_completed: hypothesis
artifact: data/models/exp_cand_imbloss_v2/
notes: SwingLoss training kicked off
EOF
run_hook stop "$(stop_input sess-with-ct)"
assert_eq "rc=0 with CURRENT_TASK.md" 0 "$HOOK_RC"
assert_jq "current_task.id projected from CURRENT_TASK.md" \
    "$PROJECT/.cc-autopipe/state.json" .current_task.id "cand_imbloss_v2"
assert_jq "current_task.stage projected" \
    "$PROJECT/.cc-autopipe/state.json" .current_task.stage "training"
assert_jq "current_task.stages_completed projected (length)" \
    "$PROJECT/.cc-autopipe/state.json" '.current_task.stages_completed | length' 1
assert_jq "current_task.stages_completed[0]" \
    "$PROJECT/.cc-autopipe/state.json" '.current_task.stages_completed[0]' "hypothesis"
assert_jq "current_task.artifact_paths[0]" \
    "$PROJECT/.cc-autopipe/state.json" '.current_task.artifact_paths[0]' \
    "data/models/exp_cand_imbloss_v2/"
assert_jq "current_task.claude_notes" \
    "$PROJECT/.cc-autopipe/state.json" .current_task.claude_notes \
    "SwingLoss training kicked off"
cleanup_project

# --- empty CURRENT_TASK.md → current_task stays null ---
fresh_project
write_verify 'echo '"'"'{"passed":true,"score":1.0,"prd_complete":false,"details":{}}'"'"
: > "$PROJECT/.cc-autopipe/CURRENT_TASK.md"
run_hook stop "$(stop_input sess-empty-ct)"
assert_eq "rc=0 with empty CURRENT_TASK.md" 0 "$HOOK_RC"
assert_jq "current_task null with empty CURRENT_TASK.md" \
    "$PROJECT/.cc-autopipe/state.json" .current_task "null"
cleanup_project

# --- second hook fire with updated CURRENT_TASK.md replaces fully ---
fresh_project
write_verify 'echo '"'"'{"passed":false,"score":0.5,"prd_complete":false,"details":{}}'"'"

cat > "$PROJECT/.cc-autopipe/CURRENT_TASK.md" <<EOF
task: task_a
stage: setup
stages_completed: [foo, bar]
artifact: data/a/
EOF
run_hook stop "$(stop_input sess-1)"
assert_jq "first sync: task_a" \
    "$PROJECT/.cc-autopipe/state.json" .current_task.id "task_a"

# Claude switches task — fewer fields this time.
cat > "$PROJECT/.cc-autopipe/CURRENT_TASK.md" <<EOF
task: task_b
stage: init
EOF
run_hook stop "$(stop_input sess-2)"
assert_jq "second sync: task_b (replaced, not merged)" \
    "$PROJECT/.cc-autopipe/state.json" .current_task.id "task_b"
assert_jq "second sync: stages_completed reset to []" \
    "$PROJECT/.cc-autopipe/state.json" '.current_task.stages_completed | length' 0
assert_jq "second sync: artifact_paths reset to []" \
    "$PROJECT/.cc-autopipe/state.json" '.current_task.artifact_paths | length' 0
cleanup_project

# --- corrupted CURRENT_TASK.md must not abort hook ---
fresh_project
write_verify 'echo '"'"'{"passed":true,"score":1.0,"prd_complete":false,"details":{}}'"'"
# Write a binary blob that's neither valid text nor parseable.
printf '\x00\x01\x02\xff\xfe' > "$PROJECT/.cc-autopipe/CURRENT_TASK.md"
run_hook stop "$(stop_input sess-corrupt)"
assert_eq "rc=0 even with corrupt CURRENT_TASK.md" 0 "$HOOK_RC"
cleanup_project

print_summary "test_stop"
exit "$FAIL"
