#!/bin/bash
# tests/unit/test_hooks/test_pre_tool_use.sh — one test per §10.2 block rule.
# Refs: SPEC.md §10.2, §13.2

set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=tests/unit/test_hooks/_lib.sh
. "$SCRIPT_DIR/_lib.sh"

echo "== test_pre_tool_use.sh =="

bash_input() {
    local cmd=$1
    jq -nc --arg cwd "$PROJECT" --arg cmd "$cmd" \
        '{tool_name:"Bash",cwd:$cwd,tool_input:{command:$cmd}}'
}

write_input() {
    local fp=$1 content=$2
    jq -nc --arg cwd "$PROJECT" --arg fp "$fp" --arg c "$content" \
        '{tool_name:"Write",cwd:$cwd,tool_input:{file_path:$fp,content:$c}}'
}

edit_input() {
    local fp=$1 ns=$2
    jq -nc --arg cwd "$PROJECT" --arg fp "$fp" --arg ns "$ns" \
        '{tool_name:"Edit",cwd:$cwd,tool_input:{file_path:$fp,old_string:"foo",new_string:$ns}}'
}

# --- Rule 1: Bash command references secrets ---
fresh_project
run_hook pre-tool-use "$(bash_input 'cat ~/.cc-autopipe/secrets.env')"
assert_eq "rule1 secrets.env: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(bash_input 'cat ~/.aws/credentials')"
assert_eq "rule1 .aws/credentials: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(bash_input 'cat ~/.ssh/id_rsa')"
assert_eq "rule1 id_rsa: rc=2" 2 "$HOOK_RC"
# shellcheck disable=SC2016
run_hook pre-tool-use "$(bash_input 'echo $TG_BOT_TOKEN')"
assert_eq "rule1 TG_BOT_TOKEN: rc=2" 2 "$HOOK_RC"
assert_contains "rule1 logs to failures.jsonl" '"error":"hook_pretooluse_blocked"' \
    "$(cat "$PROJECT/.cc-autopipe/memory/failures.jsonl")"
cleanup_project

# --- Rule 2: Bash destructive operations ---
fresh_project
run_hook pre-tool-use "$(bash_input 'git push --force')"
assert_eq "rule2 git push --force: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(bash_input 'git push origin main')"
assert_eq "rule2 git push main: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(bash_input 'rm -rf /')"
assert_eq "rule2 rm -rf /: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(bash_input 'rm -rf ~/important')"
assert_eq "rule2 rm -rf ~: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(bash_input 'dd if=/dev/zero of=/dev/sda')"
assert_eq "rule2 dd if=: rc=2" 2 "$HOOK_RC"
cleanup_project

# --- Rule 3: long-running ops without nohup or trailing & ---
fresh_project
run_hook pre-tool-use "$(bash_input 'pip install foo')"
assert_eq "rule3 pip install: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(bash_input 'npm install')"
assert_eq "rule3 npm install: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(bash_input 'docker build . -t foo')"
assert_eq "rule3 docker build: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(bash_input 'pytest --slow tests/')"
assert_eq "rule3 pytest --slow: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(bash_input 'python train.py')"
assert_eq "rule3 python train: rc=2" 2 "$HOOK_RC"
# nohup or trailing & should bypass the block.
run_hook pre-tool-use "$(bash_input 'nohup pip install foo &')"
assert_eq "rule3 nohup pip install: rc=0" 0 "$HOOK_RC"
run_hook pre-tool-use "$(bash_input 'pip install foo &')"
assert_eq "rule3 pip install &: rc=0" 0 "$HOOK_RC"
cleanup_project

# --- Rule 4: Write/Edit to .cc-autopipe/state.json ---
fresh_project
run_hook pre-tool-use "$(write_input '.cc-autopipe/state.json' '{}')"
assert_eq "rule4 Write rel state.json: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(write_input "$PROJECT/.cc-autopipe/state.json" '{}')"
assert_eq "rule4 Write abs state.json: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(edit_input '.cc-autopipe/state.json' '{}')"
assert_eq "rule4 Edit state.json: rc=2" 2 "$HOOK_RC"
cleanup_project

# --- Rule 5: Write/Edit content with apparent secret ---
fresh_project
run_hook pre-tool-use "$(write_input 'src/foo.py' 'API_KEY = "sk-ant-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"')"
assert_eq "rule5 sk-ant- secret: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(write_input '.env' 'TG_BOT_TOKEN=12345678')"
assert_eq "rule5 TG_BOT_TOKEN=N: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(write_input '.env' 'GH_TOKEN=ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA')"
assert_eq "rule5 ghp_ secret: rc=2" 2 "$HOOK_RC"
cleanup_project

# --- Rule 6: Write/Edit to .claude/settings.json ---
fresh_project
run_hook pre-tool-use "$(write_input '.claude/settings.json' '{}')"
assert_eq "rule6 Write rel settings.json: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(write_input "$PROJECT/.claude/settings.json" '{}')"
assert_eq "rule6 Write abs settings.json: rc=2" 2 "$HOOK_RC"
run_hook pre-tool-use "$(edit_input '.claude/settings.json' '{}')"
assert_eq "rule6 Edit settings.json: rc=2" 2 "$HOOK_RC"
cleanup_project

# --- Benign Bash + Write must be allowed ---
fresh_project
run_hook pre-tool-use "$(bash_input 'echo hello')"
assert_eq "benign Bash echo: rc=0" 0 "$HOOK_RC"
run_hook pre-tool-use "$(bash_input 'pytest tests/')"
assert_eq "benign Bash pytest: rc=0" 0 "$HOOK_RC"
run_hook pre-tool-use "$(write_input 'src/foo.py' 'print("hello")')"
assert_eq "benign Write code: rc=0" 0 "$HOOK_RC"
run_hook pre-tool-use "$(write_input 'README.md' '# project\n\nMost ordinary content.')"
assert_eq "benign Write markdown: rc=0" 0 "$HOOK_RC"
# No failure rows should have appeared for the benign cases.
if [ -f "$PROJECT/.cc-autopipe/memory/failures.jsonl" ]; then
    COUNT=$(wc -l < "$PROJECT/.cc-autopipe/memory/failures.jsonl" | tr -d ' ')
else
    COUNT=0
fi
assert_eq "benign cases produce no failures.jsonl entries" 0 "${COUNT:-0}"
cleanup_project

# --- block reasons go to failures.jsonl with structured JSON ---
fresh_project
run_hook pre-tool-use "$(bash_input 'rm -rf /')"
LINE=$(tail -1 "$PROJECT/.cc-autopipe/memory/failures.jsonl")
ERR=$(printf '%s' "$LINE" | jq -r .error)
TOOL=$(printf '%s' "$LINE" | jq -r .tool)
assert_eq "block log error field"  "hook_pretooluse_blocked" "$ERR"
assert_eq "block log tool field"   "Bash"                    "$TOOL"
cleanup_project

# --- MultiEdit secret detection (extension over §10.2 — exercised in commit) ---
fresh_project
ME_INPUT=$(jq -nc --arg cwd "$PROJECT" '
{
  tool_name: "MultiEdit",
  cwd: $cwd,
  tool_input: {
    file_path: "src/foo.py",
    edits: [
      {old_string:"a", new_string:"benign"},
      {old_string:"b", new_string:"key=sk-ant-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}
    ]
  }
}')
run_hook pre-tool-use "$ME_INPUT"
assert_eq "MultiEdit catches sk-ant- in any edit: rc=2" 2 "$HOOK_RC"
cleanup_project

print_summary "test_pre_tool_use"
exit "$FAIL"
