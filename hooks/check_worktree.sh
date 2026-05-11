#!/bin/bash
# PreToolUse hook for Edit + Write:
# Block direct edits to the main git worktree; require a linked worktree.
#
# Detection:
#   .git is a DIRECTORY  ->  main worktree  ->  BLOCK (exit 2)
#   .git is a FILE       ->  linked worktree ->  allow (exit 0)
#   no .git found        ->  not a git repo  ->  allow (exit 0)
#
# Always allows writes to ~/.claude/ (hooks, memory, settings).
# Exit 0 = allow, Exit 2 = block.

INPUT=$(cat)
PYFILE=$(mktemp /tmp/hook_py.XXXXXX) || exit 0
trap 'rm -f "$PYFILE"' EXIT
cat >"$PYFILE" <<'PYEOF'
import sys, json, os

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool = data.get('tool_name', '')
if tool not in ('Write', 'Edit'):
    sys.exit(0)

file_path = data.get('tool_input', {}).get('file_path', '')
if not file_path:
    sys.exit(0)

file_path = os.path.abspath(file_path)

# Always allow: ~/.claude/ (hooks, memory, settings are not project code).
# Use realpath to resolve symlinks, preventing a symlink inside ~/.claude/
# from being used to bypass the main-worktree block.
claude_dir = os.path.realpath(os.path.expanduser('~/.claude'))
real_file_path = os.path.realpath(file_path)
if real_file_path == claude_dir or real_file_path.startswith(claude_dir + os.sep):
    sys.exit(0)

# Walk up the directory tree to find .git
search_dir = file_path if os.path.isdir(file_path) else os.path.dirname(file_path)
while True:
    git_path = os.path.join(search_dir, '.git')
    if os.path.exists(git_path):
        break
    parent = os.path.dirname(search_dir)
    if parent == search_dir:
        # Reached filesystem root  --  not in any git repo; allow
        sys.exit(0)
    search_dir = parent

repo_root = search_dir

if os.path.isdir(git_path):
    # .git is a directory -> this is the main worktree -> BLOCK
    repo_name = os.path.basename(repo_root)
    print(f'BLOCKED: Editing files directly in the main worktree is not allowed.', file=sys.stderr)
    print(f'  File: {file_path}', file=sys.stderr)
    print(f'  Repo: {repo_root}', file=sys.stderr)
    print(f'', file=sys.stderr)
    print(f'  Create a linked worktree first, e.g.:', file=sys.stderr)
    print(f'    git -C "{repo_root}" worktree add ".worktrees/work" <branch-or-new-branch>', file=sys.stderr)
    print(f'  Then make your edits inside the worktree directory.', file=sys.stderr)
    print(f'  (Or invoke the /using-git-worktrees skill for step-by-step guidance.)', file=sys.stderr)
    sys.exit(2)

# .git is a file -> linked worktree -> allow
sys.exit(0)
PYEOF
printf '%s' "$INPUT" | python3 "$PYFILE"
