# Pass 1: Code Review (qodo-style)

> **Path context**: All file paths in this document are relative to ~/.claude/skills/code-forge/.

Systematically cover the whole diff risk surface -- do not focus on one area and neglect others.

## Step 1: Gather Changes

Determine the diff source based on arguments:

**Default (uncommitted):**
```bash
git diff --name-only | wc -l        # file count
git diff | wc -l                     # line count
git diff --no-binary                 # actual diff
```

**committed mode:**
```bash
BASE=$(git merge-base HEAD $(git rev-parse --abbrev-ref @{upstream} 2>/dev/null || git remote show origin | grep 'HEAD branch' | awk '{print $NF}'))
git diff --no-binary $BASE...HEAD
```

**Patch file mode:**
```bash
cat <path>
```

### Edge Cases

- **Empty diff**: if `git diff --stat` produces no output, respond "No changes to review" and stop
- **Large diff**: if file count >10 OR line count >500, split into batches of <=5 files. Review each batch serially. Output each batch's results before starting the next. Use `git diff --no-binary -- <file1> <file2> ...` per batch.
- **Binary files**: always use `--no-binary` to exclude

## Step 2: Output the Review

Follow this exact structure. Do not write any text between sections -- only headings and structured entries. Do not include internal reasoning or thinking steps.

### Output Format

```
# Changes Summary
<1-3 sentence summary: what was changed, which components affected, the intent>

# Files Walkthrough

#### <Feature or Behavior Group 1>

**<file_path>** (`Changes` | `New file` | `Removed file`) -- <theme>
<1 sentence: why this file changed>

\`\`\`diff
- <relevant code before>
+ <relevant code after>
\`\`\`
+<linesAdded> / -<linesRemoved>

**<file_path_2>** (...)
...

#### <Feature or Behavior Group 2>
...

# Code Suggestions

## P0 - Critical (<N> issues)

### [P0] <title>

**File:** <filePath>
**Description:** <description of the issue and why it matters>

Suggested fix:
\`\`\`<lang>
<code suggestion>
\`\`\`

### [P0] <title>
...

## P1 - High (<N> issues)

### [P1] <title>
...

## P2 - Medium (<N> issues)

### [P2] <title>
...

## P3 - Low (<N> issues)

### [P3] <title>
...
```

### Anti-hallucination gate (mandatory per finding)

Before reporting any finding, you MUST:
1. Re-read the actual file at the cited line (use Read tool, not memory).
2. Confirm the code you are analyzing matches what is actually in the file.
3. If the finding references a function, variable, or constant by name, grep to confirm it exists.

Findings that fail this gate are false positives. Do not report them.

### Rules

- Only include a category if issues were found in that category. If zero issues in a category, omit the entire section.
- Severity markers: P0 (critical, must fix before merge), P1 (high, should fix), P2 (medium, non-critical), P3 (low, minor/stylistic)
- Group files into features/behaviors in the walkthrough (e.g., "VLAN pop/push test rework", "Parser extension for push_vlan")
- Use `diff` code blocks for before/after in walkthrough
- Use language-specific code blocks for suggestions
- Every suggestion must include file path and a concrete code fix
- Categories in order: Security -> Bugs -> Best Practices -> Debug/Leftover Code -> Linting -> Other
- This is a **read-only review** -- do not modify any files, only output suggestions
