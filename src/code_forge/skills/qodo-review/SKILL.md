---
name: qodo-review
description: Change-aware pre-review with feature-grouped walkthrough and structured suggestions. Inspired by Qodo's review prompt, runs locally with no Qodo dependency. Replaces code-reviewer as pass 1 in three-cycle review.
---

# When to Use

- **Pass 1** in the three-cycle static review (before `/code-review-expert` and `/adversarial-qe`)
- Quick pre-review of uncommitted local changes before diving into deeper architectural review
- When you want a structured walkthrough grouped by feature/behavior, not just file-by-file

# When NOT to Use

- Not a substitute for `/code-review-expert` (architecture, SOLID, P0-P3 severity)
- Not a substitute for `/adversarial-qe` (adversarial testing gaps, edge case hunting)
- Not for reviewing committed branch changes in the three-cycle flow (use the full cycle for that)

# Arguments

- No argument: review uncommitted changes (staged + unstaged)
- `committed`: review current branch vs merge-base
- `<path>`: review a specific patch file (argument contains `/` or `.`)
- Other text: passed as context hint to focus the review

# How It Works

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

Follow this exact structure. Do not write any text between sections  --  only headings and structured entries. Do not include internal reasoning or thinking steps.

### Output Format

```
# Changes Summary
<1-3 sentence summary: what was changed, which components affected, the intent>

# Files Walkthrough

#### <Feature or Behavior Group 1>

**<file_path>** (`Changes` | `New file` | `Removed file`)  --  <theme>
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

## 🔴 Security Vulnerabilities (<N> issues)

### [🔴 High] <title>

**File:** <filePath>
**Description:** <description of the issue and why it matters>

Suggested fix:
\`\`\`<lang>
<code suggestion>
\`\`\`

### [🔴 High] <title>
...

## 🔴 Potential Bugs (<N> issues)

### [🔴 High | 🟡 Medium] <title>
...

## 🟡 Best Practice Violations (<N> issues)

### [🟡 Medium] <title>
...

## 🟢 Minor Issues (<N> issues)

### [🟢 Low] <title>
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
- Severity markers: 🔴 High (must fix before merge), 🟡 Medium (problematic but non-critical), 🟢 Low (minor/stylistic)
- Group files into features/behaviors in the walkthrough (e.g., "VLAN pop/push test rework", "Parser extension for push_vlan")
- Use `diff` code blocks for before/after in walkthrough
- Use language-specific code blocks for suggestions
- Every suggestion must include file path and a concrete code fix
- Categories in order: Security -> Bugs -> Best Practices -> Debug/Leftover Code -> Linting -> Other
- This is a **read-only review**  --  do not modify any files, only output suggestions
