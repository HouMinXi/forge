# Forge Hooks

Reference implementations of Claude Code hooks that enforce the forge review
pipeline. These hooks run automatically during tool use and block operations
that violate the pipeline rules.

## Installation

1. Copy the hook scripts to your hooks directory:

```bash
cp hooks/check_*.sh ~/.claude/hooks/
chmod +x ~/.claude/hooks/check_*.sh
```

2. Add the hook configuration to your `~/.claude/settings.json`.
   See `settings-snippet.json` for a ready-to-paste JSON fragment.

## Hook Inventory

### check_worktree.sh

**Trigger**: PreToolUse on Edit and Write

Blocks edits in the main git worktree. Only linked worktrees (where `.git` is
a file, not a directory) are allowed. Prevents accidental edits to the main
checkout.

**Exception**: always allows edits to `~/.claude/` (hooks, memory, settings).

### check_non_ascii.sh

**Trigger**: PreToolUse on Write and Edit

Detects non-ASCII characters in file content before writing. LLMs silently
emit typographic characters (em dashes, smart quotes, arrows) that look
identical to ASCII but break shell scripts and configs.

### check_read_before_edit.sh

**Trigger**: PostToolUse on Read, PreToolUse on Edit and Write

Enforces a 1:1 read-before-edit ratio: each file must be read before each
edit. For existing files, blocks Write operations that change more than 5% of
total lines (forces Edit for surgical changes).

### check_review_tracker.sh

**Trigger**: PostToolUse on Bash (detects qodo-review invocations),
PreToolUse on Edit and Write

Implements the review cycle state machine. Tracks:
- Number of qodo-review runs
- Whether the last review had findings
- Whether code was modified since the last review
- Number of consecutive rounds with findings

**Hard stop**: after 3 rounds where findings persist, blocks all Edit/Write
operations until human intervention.

**Adaptation required**: the finding detection uses Chinese-language patterns
(e.g., patterns for "no issues found", "meets requirements"). Replace these
regex patterns with your preferred language.

### check_git_commit_review.sh

**Trigger**: PreToolUse on Bash (detects `git commit`)

Blocks commits without the `# post-review-c3` marker (or exempt markers:
`# docs`, `# config`, `# chore`, `# wip`).

Also blocks commits containing AI attribution (Co-Authored-By, model names).

**Adaptation required**: uses `klist` for Kerberos principal detection to
determine the author name in error messages. Replace with your own author
detection method or hardcode your name.

### check_git_push_review.sh

**Trigger**: PreToolUse on Bash (detects `git push`)

Blocks pushes without completed review (similar to the commit check).

## Adaptation Guide

These hooks were extracted from a specific development environment. Areas you
will need to adapt:

| Area | Files | What to change |
|------|-------|---------------|
| Author detection | check_git_commit_review.sh | Replace `klist` with your auth method |
| Language patterns | check_review_tracker.sh | Replace Chinese regex with your language |
| Box-drawing chars | check_read_before_edit.sh, check_git_commit_review.sh | UI output characters (functional, keep or replace) |
| State file paths | check_review_tracker.sh | Default: `~/.local/state/claude/` |
