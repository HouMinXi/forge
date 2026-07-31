#!/bin/sh
# code-forge pre-commit gate-check (installed by code-forge install-hooks)
# .git jurisdiction check: silently skip in non-git directories
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# planning-leak guard: block .planning/ and CLAUDE.md staging
_LEAK=$(git diff --cached --name-only | grep -E '^\.planning/|^docs/adr/|(^|/)CLAUDE\.md$')
if [ -n "$_LEAK" ]; then
    echo "code-forge: BLOCKED: staged paths must never enter history:" >&2
    printf '%s\n' "$_LEAK" | sed 's/^/  /' >&2
    exit 1
fi

# non-code carve-out: skip verify+gate-check for non-code commits
NON_CODE='\.md$|\.txt$|\.yaml$|\.yml$|\.json$|\.toml$|\.cfg$|\.ini$|\.conf$|(^|/)\.gitignore$|(^|/)\.editorconfig$|(^|/)\.env\.example$|(^|/)LICENSE$|(^|/)README$|(^|/)CHANGELOG$|(^|/)Makefile$|(^|/)Dockerfile$|(^|/)\.dockerignore$'
STAGED=$(git diff --cached --name-only)
if [ -z "$STAGED" ]; then exit 0; fi
NON_MATCH=$(printf '%s\n' "$STAGED" | grep -vE "$NON_CODE")
if [ -z "$NON_MATCH" ]; then
    echo "code-forge: skipping verify (non-code commit)" >&2
    exit 0
fi

# code-forge receipt attestation check
code-forge verify --quiet 2>/dev/null || {
    echo "code-forge: receipt verification failed. Run: code-forge verify" >&2
    exit 1
}

# built-in: non-ASCII check on staged diff
# ai-smell mode blocks confusable typographic chars; strict mode blocks all non-ASCII
_NON_ASCII=$(git diff --cached -U0 | grep '^+' | grep -v '^+++' | \
    grep -P '[\x{2014}\x{2013}\x{2018}\x{2019}\x{201C}\x{201D}\x{2026}\x{2192}\x{00A0}]' | head -5)
if [ -n "$_NON_ASCII" ]; then
    echo "code-forge: non-ASCII characters in staged diff:" >&2
    printf '%s\n' "$_NON_ASCII" >&2
    exit 1
fi
# built-in: AI-vocab check on staged diff
# 6-word high-signal subset (narrower than SKILL.md's 19-word list to reduce false positives)
_AI_VOCAB=$(git diff --cached -U0 | grep '^+' | grep -v '^+++' | grep -iE \
    'delve|tapestry|testament|moreover|furthermore|it is worth noting' | head -5)
if [ -n "$_AI_VOCAB" ]; then
    echo "code-forge: AI vocabulary detected in staged diff:" >&2
    printf '%%s\n' "$_AI_VOCAB" >&2
    exit 1
fi

# LLM review: up to 2 rounds via CN backend
if command -v /home/houminxi/.local/bin/code-forge >/dev/null 2>&1; then
    FORGE_SKIP_WORKTREE_CHECK=1 /home/houminxi/.local/bin/code-forge review \
        --baseline HEAD --head INDEX \
        --max-total-rounds 2 --quiet || {
        _RC=$?
        if [ "$_RC" -eq 2 ]; then
            echo "code-forge: review skipped (no backend configured)" >&2
        elif [ "$_RC" -eq 5 ]; then
            echo "code-forge: review delegated (inline outlet)" >&2
        else
            echo "code-forge: review FAILED (exit $_RC)" >&2
            exit 1
        fi
    }
else
    echo "code-forge: review: code-forge not found, skipping" >&2
fi

exec /home/houminxi/.local/bin/code-forge gate-check
