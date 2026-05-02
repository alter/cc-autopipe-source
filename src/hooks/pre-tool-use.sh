#!/bin/bash
# pre-tool-use.sh — deterministic enforcement for critical rules.
# Refs: SPEC.md §10.2, §13.2
#
# Input:  stdin JSON {tool_name, tool_input, ...}
# Output: stderr for block reason
# Exit:   0 = allow, 2 = block (Claude Code propagates as a tool error),
#         1 = internal error (treated as allow with warning per SPEC §10.2)

set -u

INPUT=$(cat || true)

# Best-effort cwd resolution (same idiom as session-start.sh).
PROJECT=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
if [ -z "${PROJECT:-}" ] || [ ! -d "$PROJECT" ]; then
    PROJECT=$(pwd)
fi

TOOL=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)

# block <reason>: log to failures.jsonl, print to stderr, exit 2.
block() {
    local reason=$1
    local mem="$PROJECT/.cc-autopipe/memory"
    mkdir -p "$mem" 2>/dev/null || true
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    # jq -nc keeps the JSON on a single line and escapes the reason safely.
    jq -nc \
        --arg ts "$ts" \
        --arg tool "$TOOL" \
        --arg reason "$reason" \
        '{ts: $ts, error: "hook_pretooluse_blocked", tool: $tool, reason: $reason}' \
        >> "$mem/failures.jsonl" 2>/dev/null || true
    echo "cc-autopipe blocked $TOOL: $reason" >&2
    exit 2
}

case "$TOOL" in
    Bash)
        CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
        [ -z "$CMD" ] && exit 0  # nothing to inspect

        # Rule 1: secrets in command
        if printf '%s' "$CMD" | grep -qE 'secrets\.env|\.aws/credentials|id_rsa|\.ssh/.*key|TG_BOT_TOKEN'; then
            block "secrets reference in command"
        fi

        # Rule 2: destructive operations
        if printf '%s' "$CMD" | grep -qE 'git push.*--force|git push.*main|rm -rf [/~]|dd if='; then
            block "destructive operation"
        fi

        # Rule 3: long-running commands without nohup or trailing &
        if printf '%s' "$CMD" | grep -qE '(npm install|pip install|docker build|pytest --slow|python.*train.*\.py)'; then
            if ! printf '%s' "$CMD" | grep -qE 'nohup|&[[:space:]]*$'; then
                block "long operation without nohup. Split into smaller steps in v0.5"
            fi
        fi

        # Rule 7 (v1.0, SPEC-v1.md §2.1.4): explicitly allow nohup-launched
        # background commands paired with cc-autopipe-detach. The previous
        # rule already permits nohup, but we promote this combo to "OK,
        # take the slot release" so projects know the engine is aware.
        if printf '%s' "$CMD" | grep -qE 'nohup' \
            && printf '%s' "$CMD" | grep -qE '&[[:space:]]*($|[;&|])' \
            && printf '%s' "$CMD" | grep -qE 'cc-autopipe[ -]detach'; then
            exit 0
        fi
        ;;

    Write|Edit|MultiEdit)
        FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
        # Edit/MultiEdit pass new_string; Write passes content.
        CONTENT=$(printf '%s' "$INPUT" | jq -r '
            .tool_input.content
            // .tool_input.new_string
            // ([.tool_input.edits[]?.new_string] | join("\n"))
            // ""
        ' 2>/dev/null)

        # Rule 4: state.json (`*` matches both absolute paths and the
        # relative form ".cc-autopipe/state.json" since * can be empty)
        case "$FILE" in
            *.cc-autopipe/state.json)
                block "state.json is engine-managed. Use cc-autopipe-checkpoint." ;;
        esac

        # Rule 6: settings.json (independent rule, handled before content scan)
        case "$FILE" in
            *.claude/settings.json)
                block "settings.json is engine-managed. Hooks are in cc-autopipe/." ;;
        esac

        # Rule 5: secret patterns in content
        if [ -n "$CONTENT" ] && printf '%s' "$CONTENT" | grep -qE 'sk-ant-[a-zA-Z0-9_-]{30,}|TG_BOT_TOKEN=[0-9]+|ghp_[a-zA-Z0-9]{30,}|aws_secret'; then
            block "refusing to write apparent secret"
        fi
        ;;
esac

exit 0
