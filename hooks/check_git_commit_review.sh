#!/bin/bash
# PreToolUse hook: BLOCK git commit if review cycle not completed
# or if commit message contains AI attribution.
# Exit 0 = allow, Exit 2 = block.
#
# To bypass review check, add a marker to the commit command:
#   git commit -m "..." # post-review-c3
# Accepted markers:
#   post-review-c3   --  full 3-cycle review + smoke test complete
#                     (coder -> code-review-expert -> adversarial-qe, 3 cycles, + smoke test)
#   docs             --  documentation-only change, no code
#   config           --  config-only change (e.g. settings, env vars)
#   chore            --  housekeeping (rename, move, gitignore, etc.)
#   wip              --  work-in-progress, explicitly skipping review

INPUT=$(cat)
PYFILE=$(mktemp /tmp/hook_py.XXXXXX) || exit 0
trap 'rm -f "$PYFILE"' EXIT
cat >"$PYFILE" <<'PYEOF'
import sys, json, re, subprocess, os

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool = data.get('tool_name', '')
if tool != 'Bash':
    sys.exit(0)

cmd = data.get('tool_input', {}).get('command', '')
if not re.search(r'\bgit\s+commit(?:\s|$)', cmd):
    sys.exit(0)

# ── Check for AI attribution patterns (always checked, before bypass) ────
AI_PATTERNS = [
    (r'co-authored-by\s*:', 'Co-Authored-By trailer'),
    (r'noreply@anthropic\.com', 'Anthropic no-reply address'),
    (r'generated\s+(?:by|with)\s+(?:claude|gpt|ai|copilot|gemini|cursor)', 'AI generation credit'),
    (r'(?:created|written|assisted|produced)\s+(?:by|with)\s+(?:claude|gpt|ai|copilot|gemini|cursor)', 'AI authorship credit'),
    (r'claude\s+(?:sonnet|opus|haiku|code)\b', 'Claude model name'),
    (r'gpt-[345]\b', 'GPT model name'),
    (r'\bcopilot\b.{0,40}\bgenerat', 'Copilot generation credit'),
    (r'\bai[- ]generated\b', 'AI-generated label'),
    (r'\bclaude\.ai\b', 'Claude AI URL'),
]

cmd_lower = cmd.lower()
for pattern, desc in AI_PATTERNS:
    if re.search(pattern, cmd_lower):
        # Determine correct author dynamically
        author = ''
        try:
            r = subprocess.run(['klist'], capture_output=True, text=True, timeout=3)
            klist_output = r.stdout + r.stderr
            for line in klist_output.splitlines():
                m = re.match(r'^\s*Default\s+principal\s*:?\s*(.+)', line, re.IGNORECASE)
                if m:
                    principal = m.group(1).strip()
                    user = principal.split('@')[0]
                    domain = principal.split('@')[1].lower() if '@' in principal else ''
                    if domain:
                        author = f'{user} <{user}@{domain}>'
                    else:
                        author = user
                    break
        except Exception:
            pass
        if not author:
            try:
                name = subprocess.run(['git', 'config', 'user.name'],
                    capture_output=True, text=True, timeout=3).stdout.strip()
                email = subprocess.run(['git', 'config', 'user.email'],
                    capture_output=True, text=True, timeout=3).stdout.strip()
                if name and email:
                    author = f'{name} <{email}>'
                elif name:
                    author = name
            except Exception:
                pass
        if not author:
            author = os.environ.get('USER', os.path.basename(os.path.expanduser('~')))

        print(f'BLOCKED: Commit contains AI attribution ({desc}).', file=sys.stderr)
        print(f'  Remove AI attribution from the commit message.', file=sys.stderr)
        print(f'  Use: Signed-off-by: {author}', file=sys.stderr)
        sys.exit(2)

# ── Check for review bypass marker ───────────────────────────────────────
if re.search(r'#\s*(post-review-c3|docs|config|chore|wip)\s*$', cmd, re.IGNORECASE | re.MULTILINE):
    sys.exit(0)

print('BLOCKED: git commit requires completed 3-cycle review + smoke test first.', file=sys.stderr)
print('  Each cycle = 3 passes (9 passes total):', file=sys.stderr)
print('    Pass 1: Agent(subagent_type="coder")', file=sys.stderr)
print('    Pass 2: /code-review-expert', file=sys.stderr)
print('    Pass 3: /adversarial-qe', file=sys.stderr)
print('  After 3 clean cycles: run smoke test (normal, boundary, security, concurrency).', file=sys.stderr)
print('  Marker post-review-c3 only after ALL 3 cycles pass AND smoke test PASS.', file=sys.stderr)
print('  Then commit with:  git commit -m "..."  # post-review-c3', file=sys.stderr)
print('  Non-code changes:  git commit -m "..."  # docs|config|chore|wip', file=sys.stderr)
sys.exit(2)
PYEOF
printf '%s' "$INPUT" | python3 "$PYFILE"
