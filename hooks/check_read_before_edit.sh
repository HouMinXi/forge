#!/bin/bash
# Hook: enforce Read-before-Edit discipline with 1:1 ratio + Write size guard
#
# PostToolUse on Read  -> increments per-file read counter
# PreToolUse  on Edit  -> requires >=1 unspent Read; consumes one Read credit
# PreToolUse  on Write -> for EXISTING files: blocks if change > 5% of total lines
#                        (forces Edit for surgical changes to existing code)
#
# Cache is keyed on $PPID (Claude Code's PID) so all hook invocations
# within the same session share the same cache file.
#
# Exit 2 = block
# Exit 0 = allow

INPUT=$(cat)
export CLAUDE_SESSION_PID=$PPID

printf '%s' "$INPUT" | python3 -c "
import sys, json, os, fcntl

_state_dir = os.path.join(os.path.expanduser('~'), '.local', 'state', 'claude')
os.makedirs(_state_dir, mode=0o700, exist_ok=True)
try:
    os.chmod(_state_dir, 0o700)  # enforce even if dir already existed
except OSError:
    pass
CACHE = os.path.join(_state_dir, f'read_credits_{os.environ.get(\"CLAUDE_SESSION_PID\", \"default\")}.json')
LOCK  = CACHE + '.lock'

def load_cache():
    try:
        with open(CACHE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_cache(data):
    # Atomic write: temp file + fsync + os.replace to avoid partial JSON on crash
    tmp_path = CACHE + '.tmp'
    with open(tmp_path, 'w') as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, CACHE)

def is_exempt(fp):
    exemptions = ['.claude/projects/', '/tmp/', '__pycache__', '.git/']
    return any(ex in fp for ex in exemptions)

try:
    data = json.load(sys.stdin)
except Exception as e:
    print(f'READ-CREDIT HOOK: failed to parse hook input: {e}', file=sys.stderr)
    sys.exit(2)  # fail-closed: unknown input -> block

tool   = data.get('tool_name', '')
tinput = data.get('tool_input', {})

# ── PostToolUse on Read: increment read counter ─────────────────────────
if tool == 'Read':
    fp = tinput.get('file_path', '')
    if fp:
        fp = os.path.realpath(fp)  # normalise before exists check (consistent key)
    if fp and os.path.exists(fp):
        with open(LOCK, 'w') as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            cache = load_cache()
            cache[fp] = cache.get(fp, 0) + 1
            save_cache(cache)
    sys.exit(0)

# ── PreToolUse on Edit: require and consume one Read credit ──────────────
if tool == 'Edit':
    fp = tinput.get('file_path', '')
    if not fp:
        sys.exit(0)
    fp = os.path.realpath(fp)
    if is_exempt(fp):
        sys.exit(0)

    with open(LOCK, 'w') as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        cache = load_cache()
        count = cache.get(fp, 0)

        if count <= 0:
            print(
                f\"READ-BEFORE-EDIT BLOCKED: '{fp}' has no unspent Read credits.\n\"
                f\"You must Read the file before each Edit (1:1 ratio).\",
                file=sys.stderr
            )
            sys.exit(2)

        cache[fp] = count - 1
        save_cache(cache)
    sys.exit(0)

# ── PreToolUse on Write: size guard for existing files ───────────────────
if tool == 'Write':
    fp = tinput.get('file_path', '')
    if not fp:
        sys.exit(0)
    fp = os.path.realpath(fp)
    if is_exempt(fp):
        sys.exit(0)

    # New files are always allowed
    if not os.path.exists(fp):
        sys.exit(0)

    new_content = tinput.get('content', '')
    new_lines = len(new_content.splitlines()) if new_content else 0

    # Atomic: credit check + file read + size guard + consume  --  all in ONE lock
    with open(LOCK, 'w') as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        cache = load_cache()
        count = cache.get(fp, 0)

        if count <= 0:
            print(
                f\"READ-BEFORE-WRITE BLOCKED: '{fp}' has no unspent Read credits.\n\"
                f\"You must Read the file before overwriting it (1:1 ratio).\",
                file=sys.stderr
            )
            sys.exit(2)

        # Read file line count inside the lock to eliminate TOCTOU
        try:
            with open(fp, 'r') as f:
                existing_lines = sum(1 for _ in f)
        except (UnicodeDecodeError, PermissionError):
            print(
                f\"WRITE BLOCKED: '{fp}' is binary or unreadable.\",
                file=sys.stderr
            )
            sys.exit(2)
        except Exception as e:
            print(
                f\"WRITE BLOCKED: cannot read '{fp}': {e}\",
                file=sys.stderr
            )
            sys.exit(2)

        if existing_lines > 0:
            # Measures line-count delta, not content diff. A same-line-count
            # full rewrite reads as 0% change. This is acceptable: Write is
            # a full file overwrite; this guard catches accidental large
            # additions/deletions via Write instead of Edit.
            changed_lines = abs(new_lines - existing_lines)
            change_pct = (changed_lines / existing_lines) * 100
            if change_pct > 5.0:
                print(
                    f\"WRITE-SIZE BLOCKED: '{fp}' has {existing_lines} lines. \"
                    f\"Write would change {change_pct:.1f}% (threshold: 5%).\n\"
                    f\"Use Edit for surgical changes to existing files.\",
                    file=sys.stderr
                )
                sys.exit(2)

        # Allowed  --  consume one credit atomically
        cache[fp] = count - 1
        save_cache(cache)

sys.exit(0)
"
exit $?
