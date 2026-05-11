---
name: forge
description: "5-step code review pipeline with cycle-counter state machine, hook enforcement, and anti-hallucination gates. Minimum 9 static review passes before commit. Use when reviewing code changes before commit, or when user says /forge, 'review', 'three-cycle review', or 'run the full review pipeline'."
---

# Forge -- Code Review Pipeline

5-step pipeline that forges code through repeated review cycles until zero defects remain.

## When to Use

- **Before any commit** of code changes (mandatory per CLAUDE.md)
- When user invokes `/forge` or asks for "full review", "three-cycle review"
- After fixing bugs, adding features, or refactoring -- before the commit step

## When NOT to Use

- Documentation-only commits (`# docs`)
- Configuration-only commits (`# config`)
- Tooling/dependency commits (`# chore`)
- Work-in-progress snapshots (`# wip`)

## Arguments

- No argument: review uncommitted changes (staged + unstaged)
- `committed`: review current branch vs merge-base
- `step N`: resume from a specific step (e.g., `step 4` to run smoke test only)
- `--skip-0`: skip Step 0 pre-checks (use only when re-entering after a fix that did not change syntax/lint)

## Prerequisites

- Code changes exist (staged or unstaged diff, or committed branch diff)
- Working inside a git worktree (not main tree -- enforced by check_worktree.sh hook)

---

# Pipeline Overview

```
Code Change
     |
     v
[Step 0] Syntax (0a) + Lint (0b) + Non-ASCII (0c)
     |
     v
[Steps 1-3] Three-cycle static review (cycle_counter state machine)
     |        Each cycle = Pass 1 + Pass 2 + Pass 3
     |        Any finding -> fix -> counter = 0 -> restart
     |        3 consecutive clean cycles -> proceed
     v
[Step 3.5] False-positive verification (if findings were fixed)
     |
     v
[Step 4] Smoke test (runtime verification)
     |
     v
[COMMIT GATE] git commit  # post-review-c3
```

---

# Step 0: Pre-Review Gate

All three sub-checks must pass. Only NEW warnings count -- pre-existing issues in untouched code are out of scope.

## 0a. Syntax Check

Run the appropriate tool for each language in the diff:

| Language | Command |
|----------|---------|
| Shell | `bash -n <file>` + `shellcheck <file>` |
| Python | `python3 -m py_compile <file>` |
| Go | `go vet ./...` |
| C (kernel) | `make` |
| Rust | `cargo check` |

## 0b. Format/Lint Check

| Language | Command |
|----------|---------|
| Shell | `shellcheck -W <file>`, verify line length <= 80 |
| Python | `pylint --enable=W,C <file>` or `ruff check <file>` |
| Go | `golangci-lint run` |
| C (kernel) | `scripts/checkpatch.pl --strict` |
| Rust | `cargo clippy` |
| All | `semgrep` (security lint, all languages) |

Project-specific overrides always win (e.g., kernel uses checkpatch.pl, not generic lint).

## 0c. Non-ASCII Check

LLMs silently emit non-ASCII characters (em dash U+2014, smart quotes U+201C/201D, arrow U+2192, ellipsis U+2026) that look identical to ASCII. Reviewers (also LLMs) have the same blind spot.

```bash
git diff HEAD --diff-filter=AM -U0 | grep '^+' | grep -P '[^\x00-\x7F]' && echo "FAIL: non-ASCII in new code"
```

Any hit = fix before proceeding. This check applies to ALL output: code, comments, commit messages, emails, drafts.

## Step 0 Gate

- **Entry**: code change exists (staged or unstaged diff)
- **Exit**: 0a + 0b + 0c all pass with zero new warnings
- **On failure**: fix the issue, re-run Step 0

---

# Steps 1-3: Three-Cycle Static Review

## State Machine

```
State: cycle_counter = 0  (target = 3)

loop:
  run Cycle (Pass 1 -> Pass 2 -> Pass 3)
  if ANY pass reports findings:
    fix ALL findings immediately
    cycle_counter = 0
    goto loop
  else:
    cycle_counter += 1
    if cycle_counter == 3:
      proceed to Step 3.5 or Step 4
    else:
      goto loop
```

## Each Cycle = 3 Sequential Passes

### Pass 1: /qodo-review

Invoke the `/qodo-review` skill.

- Change-aware pre-review with feature-grouped walkthrough
- Severity: Red (must fix) / Yellow (problematic) / Green (minor)
- Anti-hallucination gate: mandatory re-read via Read tool + grep verification before reporting any finding
- Large diffs (>500 lines or >10 files): split into batches, review serially
- Read-only analysis only -- no code modifications
- Output: Changes Summary -> Files Walkthrough -> Code Suggestions

### Pass 2: /code-review-expert

Invoke the `/code-review-expert` skill.

- Senior engineer lens: SOLID, architecture, security
- Severity: P0 (critical) / P1 (high) / P2 (medium) / P3 (low)
- Covers: SOLID + architecture -> removal candidates -> security scan -> commit message -> code quality
- Output: Summary -> Findings by severity -> Action plan
- Always asks user before implementing fixes

### Pass 3: /adversarial-qe

Invoke the `/adversarial-qe` skill.

- Red-team QE: assumes bugs exist until proven otherwise
- 12 attack dimensions:
  1. Correctness and logic
  2. Edge cases and boundaries (including "successful command, empty output" pattern)
  3. Error handling and resilience
  4. Security (injection, auth, secrets, TOCTOU)
  5. Concurrency (races, deadlocks, lifecycle)
  6. API and contract (breaking changes, validation)
  7. Bidirectional correctness (round-trip encode/decode)
  8. Graceful degradation (missing optional dependencies)
  9. Convention adherence (grep FULL FILE, not just diff)
  10. Performance and scalability
  11. Test quality
  12. AI-generated code smells
- 3-step finding verification gate: (1) Re-read code, (2) Ground truth verification, (3) Debate yourself
- Output: Severity-ordered table with Location / Finding / Evidence / Suggestion

## Why Each Pass Is Mandatory

- Pass 1 (qodo): catches structural/feature-level issues
- Pass 2 (code-review-expert): catches SOLID violations, architecture problems
- Pass 3 (adversarial-qe): catches regressions INTRODUCED BY fixes from Passes 1-2

This is the key insight: fixes create new bugs. Pass 3 exists to catch them.

## Cross-Function Enforcement

Diff-only review cannot catch cross-function inconsistencies. Pass 3 must grep the FULL FILE for consistency: error message prefixes, naming conventions, variable usage patterns.

## Handling Findings

When ANY pass reports findings:

1. Fix ALL findings immediately -- no cherry-picking, no deferring
2. After fixing, verify no out-of-scope files were modified:
   ```bash
   git diff --name-only
   ```
   Revert any out-of-scope changes with `git checkout -- <file>`
3. Reset cycle_counter = 0
4. Restart from Cycle 1, Pass 1

## Hard Stop

The `check_review_tracker.sh` hook tracks state. After 3 rounds where findings persist, it blocks all Edit/Write operations. This requires human intervention to unblock and prevents infinite fix-break loops.

## Steps 1-3 Gate

- **Entry**: Step 0 passed
- **Exit**: 3 consecutive cycles where ALL 3 passes report zero findings (minimum 9 passes total)
- **On finding**: fix -> counter = 0 -> restart from Cycle 1

---

# Step 3.5: False-Positive Verification

Invoke `/kernel-fp-verify` skill.

## When to Run

- **Run**: after three-cycle review accumulated findings that were fixed
- **Skip**: if all 3 cycles were clean from the start (no findings ever reported)

## 10-Step Verification Protocol

For each accumulated finding that was fixed, verify:

1. Re-read the code at the cited location
2. Prove the path is REACHABLE (not just "unlikely")
3. Identify concrete failure mode (crash / wrong output / data corruption / security breach)
4. Check full context (2-3 levels up/down the call chain)
5. Check patch series context (for multi-patch sets)
6. Verify against independent ground truth
7. Check for intentional design (read comments/docs)
8. Test complex multi-step conditions
9. Anti-hallucination check (does the function/variable/constant actually exist?)
10. Debate yourself (author's perspective vs reviewer's perspective)

## Valid Dismissal Reasons (exhaustive)

- Hallucination (the function/variable does not exist)
- Structurally unreachable path
- Documented intentional behavior
- Subsequent patch in the series fixes it

No other dismissal reasons are valid.

## Output

Each finding classified as: CONFIRMED / DOWNGRADED / DISMISSED, with evidence and which verification steps failed.

---

# Step 4: Smoke Test

Invoke the `/smoke-test` skill.

## Coverage Matrix

All categories required unless clearly N/A:

| Category | What to test |
|----------|-------------|
| Normal path | Primary execution path, expected output |
| Boundary | Empty input, null, max size, zero-length |
| Security | Injection payloads, path traversal |
| Concurrency | Race conditions (if applicable) |

## Workflow

- **A.** Analyze change: what changed, primary execution path, edge cases
- **B.** Select test primitives from decision table (language-specific)
- **C.** Assemble test script using standard patterns
- **D.** Execute and record results (PASS/FAIL counts)

## Language-Specific Test Runners

| Language | Runner | Primitives |
|----------|--------|-----------|
| Shell | primitives.sh | run_and_capture, assert_success, assert_failure, assert_output_contains, assert_stderr_contains, assert_file_exists, assert_no_zombie, assert_json_valid, assert_no_command_exec, assert_no_path_traversal |
| Python | pytest | standard pytest assertions |
| Go | go test | standard testing package |
| C | Beaker / framework | see Kernel C Exception |

## Shell-Specific Footguns

These evade `bash -n` and `shellcheck` -- test for them explicitly:

1. bash auto-reaps direct children (need non-bash intermediate for zombie detection)
2. `local` only valid inside functions
3. `((x++))` returns old value (post-increment evaluates to 0 when x=0)
4. `$(...)` captures multi-line output (use `grep -q` with stdout redirect)
5. `jq -e` prints to stdout (always `>/dev/null 2>&1`)

## Kernel C Exception

Pre-commit Step 4 = build passes + kernel-qe test plan exists + Beaker job XML generated.
Step 5 (Beaker submission) = pre-merge gate, not pre-commit requirement.

## Prohibited During Smoke Test

- Do NOT modify tested code
- Do NOT depend on network
- Do NOT include syntax checks (those belong in Step 0)

## Step 4 Gate

- **Entry**: cycle_counter = 3 and Step 3.5 complete (if applicable)
- **Exit**: all tests PASS
- **On failure**: fix the code -> restart from Step 0 (full pipeline restart, not just re-run smoke test)

---

# Commit Gate

Only after ALL steps complete:

```bash
git commit -m "<subsystem>/<case>: <summary>

<detailed description>

Signed-off-by: Minxi Hou <houminxi@gmail.com>"  # post-review-c3
```

## Rules

- `# post-review-c3` is an internal gate marker ONLY -- it triggers the hook check
- The marker must NEVER appear in the commit message content itself
- The commit message must read as if written by a human engineer
- Zero AI markers: no Co-Authored-By, no model names, no review process metadata

## Non-Code Exemptions

These commit types bypass the full pipeline but still require worktree and AI-attribution checks:

- `# docs` -- documentation only
- `# config` -- configuration changes
- `# chore` -- tooling, dependencies, cleanup
- `# wip` -- work in progress

---

# Adaptive Mechanisms

These are built into the pipeline and must be followed:

1. **Cycle Counter Reset**: any finding in any pass resets counter to 0, restart from Cycle 1 Pass 1. Prevents "fix forward" quality degradation.

2. **Hard Stop After 3 Rounds With Findings**: hook blocks all Edit/Write. Forces human intervention. Prevents infinite fix-break loops.

3. **Cross-Function Grep (Pass 3)**: dimension 9 "Convention adherence" requires grepping the full file, not just the diff. Catches cross-function inconsistencies.

4. **Anti-Hallucination Gates**: Pass 1 (re-read + grep), Pass 3 (3-step verification), Step 3.5 (10-step protocol with existence check).

5. **Cross-Model Complementarity**: different AI models catch different bug classes. The 3-pass structure exploits this: structural (Pass 1), architectural (Pass 2), adversarial (Pass 3).

6. **Ground Truth Verification for Test Infrastructure**: test assertions validated via bug injection: inject bug -> FAIL -> revert -> PASS. Static analysis alone cannot catch faulty assertion logic.

7. **Full Pipeline Restart on Smoke Test Failure**: smoke test FAIL -> fix -> restart from Step 0 (not Step 4). The fix itself may introduce new lint/review issues.

8. **Bidirectional Correctness**: round-trip operations (encode/decode, serialize/deserialize) verified in both directions. Origin: Sashiko review gap.

9. **Graceful Degradation**: missing optional dependencies must degrade gracefully, not crash. Review checks for this explicitly. Origin: Sashiko review gap.

10. **Scope Verification After Automated Tools**: after any review pass, check `git status` / `git diff --name-only` to confirm no out-of-scope files were modified. Revert any out-of-scope changes immediately.

---

# Hook Enforcement Layer

These hooks enforce the pipeline at the tool level:

| Hook | Trigger | Purpose |
|------|---------|---------|
| check_worktree.sh | PreToolUse Edit/Write | Block edits in main worktree |
| check_non_ascii.sh | PreToolUse Write/Edit | Non-ASCII character detection |
| check_read_before_edit.sh | PostToolUse Read + PreToolUse Edit | 1:1 Read:Edit ratio + size guard |
| check_review_tracker.sh | PostToolUse Bash (qodo) + PreToolUse Edit | Review state machine + hard stop |
| check_git_commit_review.sh | PreToolUse Bash (git commit) | Block unreviewed commits + AI attribution check |
| check_git_push_review.sh | PreToolUse Bash (git push) | Block unreviewed pushes |

---

# Execution Protocol

When `/forge` is invoked:

1. **Determine diff source**: uncommitted (default) or committed (if `committed` arg)
2. **Display pipeline banner**:
   ```
   Forge: starting 5-step review pipeline
   Diff: <N> files, <M> lines changed
   ```
3. **Run Step 0**: syntax + lint + non-ASCII. Stop on any failure.
4. **Initialize cycle_counter = 0**
5. **Run cycles**: invoke /qodo-review, /code-review-expert, /adversarial-qe sequentially. Apply state machine rules.
6. **After 3 clean cycles**: run Step 3.5 if findings were ever fixed during the process.
7. **Run Step 4**: invoke /smoke-test. Full pipeline restart on any FAIL.
8. **Report**: summary of passes completed, findings fixed, smoke test results.
9. **The commit itself is NOT performed by forge** -- it reports readiness and the user commits with the `# post-review-c3` marker.

## Progress Tracking

After each pass, report:

```
[forge] Cycle <N>/3, Pass <P>/3: <skill-name>
[forge] Result: <zero findings | N findings>
[forge] cycle_counter = <value>
```

After pipeline completes:

```
[forge] Pipeline complete
[forge] Total passes: <N> (minimum 9)
[forge] Findings fixed: <N>
[forge] Smoke test: PASS
[forge] Ready to commit with: # post-review-c3
```
