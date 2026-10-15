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

### Capture requirements

Run each producer separately through the available tool/runner. Retain its exact command or file identity, source mode/layer, completion and originating exit status, separate raw stdout/stderr, and whether the received output is complete or truncated. Keep the captured bytes and any selected commit endpoints. Do not pipe a producer into a counter, hide its status in nested command substitution, or replace its failure with a later command's success.

Count and review only complete successful selected evidence. A failed, pending, missing or truncated required capture makes the selected scope incomplete, even if it emitted a partial patch or another capture succeeded. Name the unavailable source, retain diagnostic bytes, and stop the whole-scope review; do not claim whole-scope counts/batches, emptiness, clean coverage or a completed review. Independently successful evidence may be described only with that limitation. If the tool cannot expose the required status or completeness, report that limitation rather than inventing evidence or a new adapter. Preserve limitations in the existing summary/file-description surfaces when producing a partial report.

The ordinary diff commands below do not use `--exit-code` or `--quiet`: require exit status 0 and complete output. Other commands retain their own status semantics. The supported `--no-ext-diff --no-textconv` options suppress those named helpers; they do not exclude binary bytes or provide a general execution sandbox. Do not generate binary payloads with `--binary`, force text with `--text`/`-a`, fetch, change Git configuration/attributes/refs, or run conversion helpers for this review.

**Default (uncommitted):**

Capture these separately, retaining the staged and unstaged identities:

```bash
git --no-pager diff --no-ext-diff --no-textconv --no-color --cached --
```

```bash
git --no-pager diff --no-ext-diff --no-textconv --no-color --
```

Both captures must succeed completely, whether empty or nonempty. Keep both changes when a path occurs in both layers; a staged change reversed in the worktree must not disappear into a net HEAD-to-worktree comparison. Count the union of reliably identified files once, while keeping every selected layer for review and labeling layers in the existing descriptions. This retains the tracked/index boundary; it does not add untracked files.

**committed mode:**

Use separate read-only captures, not the old nested target/base pipeline. Bind the subject to its upstream before admitting the comparison:

1. Capture the original branch:
   ```bash
   git symbolic-ref --quiet HEAD
   ```
   Require one complete successful full ref under `refs/heads/`; record it as `SUBJECT_REF`. Derive `SUBJECT_BRANCH` only by removing that exact prefix. Detached, malformed or unknown subject identity leaves committed selection incomplete; stop rather than borrowing another branch's upstream or treating a failed lookup as absent configuration.

2. Capture these independently:
   ```bash
   git rev-parse --verify "HEAD^{commit}"
   ```
   ```bash
   git rev-parse --verify "${SUBJECT_REF}^{commit}"
   ```
   Require one complete successful commit ID from each and require equality. Record `HEAD_OID` with the original subject identity. Failure or mismatch blocks acquisition.

3. Resolve upstream for that captured branch, never a later bare `@{upstream}`:
   ```bash
   git rev-parse --verify --symbolic-full-name --end-of-options "${SUBJECT_BRANCH}@{upstream}"
   ```
   Require exactly one complete successful full-ref stdout line as `TARGET_REF`, with no delimiter or unrelated line. Keep `--verify` in acquisition and rechecks; the end-of-options delimiter alone does not validate output or identity.

4. Only if repository evidence establishes that this captured branch has no configured upstream, the origin-default fallback may use:
   ```bash
   git symbolic-ref --quiet refs/remotes/origin/HEAD
   ```
   Require one complete successful ref under `refs/remotes/origin/` and evidence that it is the intended fallback. Record this as the configured local origin-default ref, not proof of remote freshness. Missing/dangling refs, another upstream error, unknown intent or ambiguity block selection. Do not guess a local main/master, fetch, rewrite refs, or reuse a stale target.

5. Validate the selected full `refs/` reference before interpolation, then capture:
   ```bash
   git rev-parse --verify "${TARGET_REF}^{commit}"
   ```
   Require one complete successful commit ID as `TARGET_OID`; quoting alone is not ref validation.

6. Capture all merge-base candidates:
   ```bash
   git merge-base --all "$HEAD_OID" "$TARGET_OID"
   ```
   Require exactly one complete successful usable full commit ID as `BASE_OID`. Empty, malformed, multiple, failed or truncated output blocks acquisition. Never use an empty/stale base or an omitted-side comparison; a usable base alone does not prove target intent.

7. Before admitting the tuple, independently repeat the relevant queries above with their statuses and completeness: current symbolic HEAD must equal `SUBJECT_REF`; HEAD and that branch must still resolve to `HEAD_OID`; explicit-branch upstream discovery must retain the same target and selection route (or confirmed absence plus the same fallback ref); and the target must still resolve to `TARGET_OID`. A changed, unavailable or unresolved association invalidates acquisition, with zero diff invocations from it. Either report incomplete evidence or restart the entire acquisition with new records. Failed/truncated rechecks are not equality. This is an observational consistency check, not an atomic snapshot or a guarantee against unobserved change-and-reversion.

8. Only after those checks pass, admit the recorded subject, selection route, target and object IDs, then capture:
   ```bash
   git --no-pager diff --no-ext-diff --no-textconv --no-color "$BASE_OID" "$HEAD_OID" --
   ```
   Use only these frozen endpoints, even if symbolic HEAD/target refs move after admission but before the producer runs. This remains the original recorded comparison, not a newly current branch. A newly requested subject/target requires a new acquisition.

Counts, empty checks, batches and review all use this retained merge-base-to-subject patch. No failed subject/target/base acquisition may reach comparison.

**Patch file mode:**

Read the selected file as data, retaining its exact bytes, path, read status and completeness. With shell capture, use a quoted actual path:

```bash
cat -- "$PATCH_PATH"
```

Do not execute, apply or decode its contents, or regenerate the patch from live Git. Counts, emptiness and batches use only these supplied bytes. A missing/failed/truncated read or nonempty unrecognized input is incomplete evidence, not an empty diff. A supplied binary payload without an actual marker covered by the exception below remains explicitly unsupported/unreviewed; do not invent a marker.

### Counts, batching and evidence limits

- Derive file identities/statuses from the same retained per-file diff evidence, not a separate live name-only capture. Count each file once across default layers while retaining both changes. Display quoting is not a literal path; unresolved names, rename associations, combined/unmerged formats or other ambiguous inventory produce an explicit limitation, not guessed counts or a new parser.
- For the line threshold, count LF bytes in the complete retained patch, including headers/metadata. Sum staged and unstaged LF counts in default mode; use the sole selected capture in committed/patch mode. This is distinct from walkthrough added/removed code-line counts.
- **Empty diff:** respond "No changes to review" and stop only when every required selected capture succeeded completely and contains no change evidence. Both default layers must be empty. Binary markers and status/mode-only changes are nonempty. An unrelated clean worktree, failure, partial data, missing base or unidentified contents cannot establish emptiness.
- **Large diff:** if the unique file count >10 OR the selected LF count >500, partition retained evidence into batches of <=5 unique files. Keep every layer/change for a member together. Review serially and output each batch's results before starting the next. Do not rerun a live diff per batch. One large file may occupy one batch; if reliable partitioning is unavailable, report the limitation rather than substitute another source.
- Counts and batches stay tied to captured bytes/endpoints, not an atomic live snapshot. Observable acquisition or actual-file changes invalidate affected current-source assumptions; recapture the selected scope and recompute when necessary, never mix old counts with fresh hunks. Committed acquisition checks and post-admission frozen comparison follow the distinction above.
- The actual-file/name-verification gate in Step 2 is unchanged. Staged, committed or supplied hunks may differ from current files; if the actual cited file cannot confirm a finding, withhold it and retain the evidence limitation.

### Binary evidence and narrow walkthrough exception

Attributes may force text or binary classification. Preserve actual captured markers and path metadata; ordinary binary-only changes are not empty, and helper suppression is not a universal binary-byte exclusion guarantee.

Never infer content safety or reviewability from classification or capture success. Known binary contents remain explicitly unreviewed even when attributes make Git render them as text; that rendering grants no binary source-content findings or complete-clean credit. Do not synthesize a binary marker when none was captured; retain the unsupported/unreviewed coverage limitation.

For a **binary-only file entry**, the sole exception to Step 2's hunk/count template is: retain its actual captured binary marker verbatim in the existing diff block, retain its path/status, explicitly state that its contents were not reviewed, and use exactly `+N/A / -N/A (binary content not reviewed)`. Do not invent before/after hunks, numeric 0/0, content findings or complete-clean credit from a marker. Use only a marker actually present in the selected capture. Text entries, categories, severities and finding verification remain unchanged, including in mixed reviews.

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
