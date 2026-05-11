#!/usr/bin/env bash
# PreToolUse hook: block Write/Edit if content contains non-ASCII.
# Exit 2 = blocking error (works even in acceptEdits mode).

command -v jq >/dev/null 2>&1 || { echo "check_non_ascii: jq not found, skipping" >&2; exit 0; }

input=$(cat)
file_path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -z "$file_path" ] && exit 0

case "$file_path" in
    *.po|*.pot|*.mo|*.png|*.jpg|*.gif|*.ico|*.pdf) exit 0 ;;
    */resume/*|*/resume*) exit 0 ;;
    /tmp/*) exit 0 ;;
    */cedana-prep/*) exit 0 ;;
esac

content=$(printf '%s' "$input" | jq -r '(.tool_input.content // .tool_input.new_string) // empty' 2>/dev/null)
[ -z "$content" ] && exit 0

hits=$(printf '%s' "$content" | grep -nP '[^\x00-\x7F]' | head -5)
if [ -n "$hits" ]; then
    reason="non-ASCII in $file_path: $hits -- replace with ASCII equivalents"
    printf '%s' "$reason" | jq -Rs '{
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": .
        }
    }'
    exit 2
fi
