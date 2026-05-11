#!/bin/bash
# PostToolUse(Write/Edit) + PreToolUse(Bash rm/git rm) hook:
# Exit 2 when code files are modified/deleted so the reminder is visible to Claude.
# Exit 0 for non-code files (no action needed).

INPUT=$(cat)
PYFILE=$(mktemp /tmp/hook_py.XXXXXX) || exit 0
trap 'rm -f "$PYFILE"' EXIT
cat >"$PYFILE" <<'PYEOF'
import sys, json, re, os

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool = data.get('tool_name', '')

# --- Write / Edit: code file created or modified ---
if tool in ('Write', 'Edit'):
    file_path = data.get('tool_input', {}).get('file_path', '')
    # Skip non-code files
    skip_exts = {'.md', '.json', '.yaml', '.yml', '.txt', '.toml', '.ini', '.cfg', '.conf'}
    skip_names = {'CLAUDE.md', 'MEMORY.md', 'settings.json'}
    ext = os.path.splitext(file_path)[1].lower()
    name = os.path.basename(file_path)
    if ext in skip_exts or name in skip_names or not ext:
        sys.exit(0)
    action = 'created' if tool == 'Write' else 'modified'
    print(f'ACTION REQUIRED: Code file {action} ({os.path.basename(file_path)}).', file=sys.stderr)

# --- Bash: code file deletion (rm / git rm on code extensions only) ---
elif tool == 'Bash':
    cmd = data.get('tool_input', {}).get('command', '')
    CODE_EXTS = r'\.(py|sh|groovy|tcl|fmf|c|h|cpp|go|rs|java|ts|js)(?=\s|$)'
    if not re.search(r'(?:\brm\b\s+.*' + CODE_EXTS + r'|\bgit\s+rm\b.*' + CODE_EXTS + r')', cmd):
        sys.exit(0)
    print('ACTION REQUIRED: Code file deleted.', file=sys.stderr)

else:
    sys.exit(0)

print('  Run review cycle before git commit:', file=sys.stderr)
print('    Cycle 1-3: /adversarial-qe -> fix -> /code-review-expert -> fix -> /adversarial-qe', file=sys.stderr)
print('  Then: git commit -m "..."  # post-review-c3', file=sys.stderr)
sys.exit(2)
PYEOF
printf '%s' "$INPUT" | python3 "$PYFILE"
