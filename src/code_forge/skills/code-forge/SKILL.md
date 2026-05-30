---
name: code-forge
description: "5-step code review pipeline with cycle-counter state machine, hook enforcement, and anti-hallucination gates. Minimum 9 static review passes before commit. Use when reviewing code changes before commit, or when user says /code-forge, 'review', 'three-cycle review', or 'run the full review pipeline'."
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

For `# docs`, `# config`, `# chore`, and `# wip` commits, Steps 5-7 (R1/R2/R3
dynamic gates) are also skipped, not just the static review pipeline. These
commit types are exempt from test-gate, mutation-check, and e2e-check because
they carry no runnable logic change.

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
     |        P0/P1 -> fix -> counter = 0 -> restart all
     |        P2 -> fix -> restart current cycle
     |        P3 -> accumulate (density check -> P2 escalation)
     |        Clean -> auto-continue (no user prompt)
     |        3 consecutive clean cycles -> proceed
     v
[Step 3.5] False-positive verification (if findings were fixed)
     |
     v
[Step 4] Smoke test (runtime verification)
     |
     v
[Step 5] R1 Test Gate (tests exist + pass for changed source)
     |
     v
[Step 6] R2 Mutation Check (tests kill mutants, not just pass)
     |
     v
[Step 7] R3 E2E Coverage (cross-component signature change has e2e artifact)
     |
     v
[COMMIT GATE] git commit  # post-review-c3
             Requires: 3 clean cycles + R1 PASS + R2 PASS + R3 PASS/SKIP
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

## Comprehensive Language Tables

Tool absence rule: if a tool is not installed, log `tool_missing: <tool>` to
`.code-forge/findings.json` and continue (WARN, not FAIL).

### Programming Languages (14)

| Language | 0a Syntax | 0b Lint | Test Runner (R1) | Mutation (R2) |
|---|---|---|---|---|
| Python | `python3 -m py_compile` | `ruff check` (preferred) or `pylint` | `pytest` | `mutmut` or `cosmic-ray` |
| Go | `go vet ./...` | `golangci-lint run` | `go test ./...` | `gremlins` or `go-mutesting` |
| Rust | `cargo check` | `cargo clippy` | `cargo test` | `cargo mutants` |
| JavaScript | `node --check` | `eslint` | `jest` / `vitest` / `mocha` | `stryker-mutator` |
| TypeScript | `tsc --noEmit` | `eslint` + `@typescript-eslint` | `jest` / `vitest` | `stryker-mutator` |
| Java | `javac -Xlint -d /tmp` | `checkstyle` + `spotbugs` | `mvn test` / `gradle test` | `pitest` |
| Kotlin | `kotlinc -script` or `-Werror` | `ktlint` + `detekt` | `gradle test` | `pitest` |
| C | `gcc -fsyntax-only -Wall` | `cppcheck` + `clang-tidy` | `ctest` / `make test` | `mull` |
| C++ | `g++ -fsyntax-only -Wall` | `cppcheck` + `clang-tidy` | `ctest` / `make test` | `mull` |
| Kernel C | `make` (subsystem build) | `scripts/checkpatch.pl --strict` | Beaker functional | N/A |
| Shell | `bash -n` + `shellcheck` | `shellcheck` | `bats` / inline | LLM-inject 10 mutants |
| Ruby | `ruby -c` | `rubocop` | `rspec` / `minitest` | `mutant` |
| PHP | `php -l` | `phpstan` + `phpcs` | `phpunit` | `infection` |
| Swift | `swift -frontend -parse` | `swiftlint` | `swift test` | `muter` |

### Config / Markup (7)

| Format | 0a Syntax | 0b Lint | Notes |
|---|---|---|---|
| YAML | `yamllint` or `python3 -c "import yaml; yaml.safe_load(open(p))"` | `yamllint` | YNL netlink specs MUST run yamllint |
| JSON | `jq . > /dev/null` or `python3 -m json.tool` | `jsonlint` | |
| TOML | `python3 -c "import tomllib; tomllib.load(open(p,'rb'))"` | `taplo lint` | |
| XML | `xmllint --noout` | `xmllint --schema <xsd>` | |
| Markdown | N/A (always parses) | `markdownlint-cli2` | |
| HTML | `tidy -e -q` | `htmlhint` | |
| CSS | `stylelint` | `stylelint` | |

### Specialized DSL (7)

| DSL | 0a Syntax | 0b Lint | Notes |
|---|---|---|---|
| SQL | `sqlfluff parse` | `sqlfluff lint` | Dialect-specific |
| Dockerfile | `hadolint` (combined) | `hadolint` | |
| Terraform | `terraform validate` | `tflint` | Run `terraform init` first |
| Kubernetes YAML | `kubeconform` | `kube-linter` | Also run yamllint |
| Ansible | `ansible-playbook --syntax-check` | `ansible-lint` | |
| protobuf | `protoc --proto_path=. <file>` | `buf lint` | |
| GraphQL | `graphql-cli parse` | `graphql-schema-linter` | |

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

## Step 0 Context Fusion (FUSE-01)

After Step 0 completes, serialize ALL Step 0 findings into a context block.
This block is prepended to the prompt for EVERY LLM pass (Steps 1-3).

**Why:** Prevents LLM passes from re-flagging issues that Step 0 already caught.
Semgrep Multimodal achieved 8x more true positives and 50% less noise with this
deterministic+LLM fusion pattern.

**Step 1 -- Collect Step 0 findings:**
After Step 0 checks (0a syntax, 0b lint, 0c non-ASCII) complete, gather any
issues that were found and fixed. Record each finding with: file, line, tool, issue.

**Step 2 -- Serialize as markdown table (capped at 20 rows):**
Format the findings as a structured context block:

```markdown
## Step 0 Findings (deterministic, already addressed)

The following issues were detected by Step 0 deterministic checks.
They have been fixed by the author. Do NOT re-flag these specific issues.
If you find NEW instances of the same pattern elsewhere, report them.

| # | File | Line | Tool | Issue |
|---|------|------|------|-------|
| 1 | path/to/file.py | 42 | pylint W0707 | raise-missing-from |
| 2 | path/to/file.sh | 15 | shellcheck SC2086 | unquoted variable |
```

**Size cap:** If Step 0 found more than 20 issues, show only the first 20 rows
and add this note after the table:

```
[forge] Step 0 found N issues total. Showing first 20. Full list in .forge/step0_findings.txt.
```

Write the complete list to `.forge/step0_findings.txt` for reference.

If Step 0 found zero issues, use this shorter block:

```markdown
## Step 0 Findings (deterministic)

Step 0 checks (syntax, lint, non-ASCII) found zero issues. No prior context.
```

**Step 3 -- Inject into each LLM pass:**
Before invoking each pass (/qodo-review, /code-review-expert, /adversarial-qe),
prepend the Step 0 context block to the review prompt. The context block goes
BEFORE the diff content, so the LLM sees it first.

**Rules for LLM passes when receiving Step 0 context:**
1. Do NOT re-flag the exact same issue at the exact same file:line that Step 0 caught
2. DO flag NEW instances of the same pattern in OTHER locations
3. DO flag related-but-different issues at the same location (e.g., Step 0 caught
   a missing import, but Pass 2 notices the function using that import has a logic error)
4. When in doubt, report the finding but note "Step 0 caught a related issue at this location"

---

# Steps 1-3: Three-Cycle Static Review

## State Machine

```
State: cycle_counter = 0  (target = 3)
       p3_by_rule = {}     # {rule_type: [file_paths]}
       changed_lines = N   # from git diff --stat

loop:
  run Cycle (Pass 1 -> Pass 2 -> Pass 3)
  
  After EACH pass:
    normalize findings to P0/P1/P2/P3 (see Severity Normalization)
    validate finding data before storing (see Finding Persistence)
    persist ALL findings to .forge/findings.json (see Finding Persistence)
    
    if zero findings:
      [AUTO-CONTINUE] immediately proceed to next pass (TRUST-06)
      report: "[forge] Cycle N/3, Pass P/3: skill-name -- CLEAN"
      do NOT wait for user input
    
    else if any P0 or P1 finding:
      [FULL RESET] fix all findings, cycle_counter = 0 (TRUST-07)
      report: "[forge] P0/P1 found -- full reset. cycle_counter = 0"
      goto loop
    
    else if any P2 finding (no P0/P1):
      [CYCLE RESTART] fix P2 findings, restart current cycle (TRUST-07)
      report: "[forge] P2 found -- restarting current cycle"
      do NOT reset cycle_counter to 0
      restart current cycle from Pass 1
    
    else if only P3 findings:
      [ACCUMULATE with density-based escalation] (TRUST-07 + P3-THRESHOLD-RESEARCH)
      
      Step A -- Deduplicate: group new P3s by rule type
        for each P3: p3_by_rule[rule_type].append(file_path)
      
      Step B -- Compute metrics:
        distinct_per_file = max(len(set(rules_in_file)) for each file)
        distinct_per_diff = len(p3_by_rule.keys())
        density = total_p3_count / changed_lines
      
      Step C -- Check thresholds (any one triggers escalation):
        if distinct_per_file > 5:
          report: "[forge] P3 density: >5 distinct violations in {file} -- P2 escalation"
          restart current cycle (P2-equivalent)
        else if distinct_per_diff > 10:
          report: "[forge] P3 density: >10 distinct violations across diff -- P2 escalation"
          restart current cycle (P2-equivalent)
        else if density > 0.15:
          report: "[forge] P3 density: {density:.2f}/line (>0.15) -- P2 escalation"
          restart current cycle (P2-equivalent)
        else:
          report: "[forge] P3: {N} findings ({distinct_per_diff} distinct rules), density {density:.2f}/line -- below threshold, continuing"
          proceed to next pass without fixing
  
  After all 3 passes in a cycle complete:
    cycle_counter += 1
    if cycle_counter == 3:
      proceed to Step 3.5 or Step 4
    else:
      goto loop
```

**Critical change from current behavior:** The current state machine resets cycle_counter on ANY finding. The new state machine only resets on P0/P1. P2 restarts the current cycle without resetting the counter. P3 uses density-based escalation with deduplication: per-file >5, per-diff >10, or density >0.15/line triggers P2-equivalent restart. Based on P3-THRESHOLD-RESEARCH.md (Google Tricorder, BitsAI-CR, Broken Windows theory, ESLint --max-warnings).

## Auto-Continue Protocol (TRUST-06)

After each pass completes:
- If **zero findings**: immediately invoke the next pass. Do not output
  "waiting for input" or "how would you like to proceed?" prompts.
  Report the clean result in one line and move on:
  `[forge] Cycle 2/3, Pass 1/3: qodo-review -- CLEAN`
- If **findings exist**: pause and present findings for user decision
  (accept/reject/fix). Only proceed after user responds.

This eliminates the current UX pain of typing "continue" after every clean pass.
The pipeline should flow silently through clean passes and only stop when
human judgment is needed.

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
- 14 attack dimensions:
  1. Correctness and logic
  2. Edge cases and boundaries (including "successful command, empty output" pattern)
  3. Error handling and resilience
  4. Security (injection, auth, secrets, TOCTOU)
  5. Concurrency (races, deadlocks, lifecycle)
  6. API and contract (breaking changes, validation)
  7. Bidirectional correctness (round-trip encode/decode)
  8. Graceful degradation (missing optional dependencies)
  9. Convention adherence (grep FULL FILE, not just diff) -- expanded with naming quality and readability
  10. Performance and scalability
  11. Test quality
  12. AI-generated code smells
  13. Documentation completeness [SHADOW] -- public API docstrings, changelog entries, README updates for user-facing changes
  14. Change scope [SHADOW] -- single-concern diffs, flag unfocused changes mixing unrelated concerns
- 3-step finding verification gate: (1) Re-read code, (2) Ground truth verification, (3) Debate yourself
- Output: Severity-ordered table with Location / Finding / Evidence / Suggestion

## Severity Normalization

Every finding from any pass MUST be normalized to P0/P1/P2/P3 before recording. Use this mapping:

| qodo-review | code-review-expert | adversarial-qe | Normalized |
|-------------|-------------------|----------------|------------|
| Red (must fix) | P0 Critical | Critical | P0 |
| Red (must fix) | P1 High | High | P1 |
| Yellow (problematic) | P2 Medium | Medium | P2 |
| Green (minor) | P3 Low | Low/Nit | P3 |

When a pass reports findings without explicit severity, classify based on impact:
- P0: Data loss, security breach, crash in normal path
- P1: Logic error, wrong output, security weakness
- P2: Missing validation, incomplete error handling, non-trivial code smell
- P3: Style preference, naming nit, minor readability issue

## Finding Persistence (TRUST-01)

After each pass completes and findings are normalized, persist EVERY finding to `.forge/findings.json`. This includes zero-finding passes (record the pass metadata in runs).

**Recording a finding:** Use a Bash tool call with Python heredoc to append to findings.json:

```bash
python3 << 'PYEOF'
import json, uuid, datetime, os, tempfile, subprocess, sys

findings_file = '.forge/findings.json'
os.makedirs('.forge', exist_ok=True)

try:
    with open(findings_file, 'r') as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {'version': 1, 'findings': [], 'runs': []}

# Get commit SHA via subprocess (NOT shell substitution -- quoted heredoc does not expand $())
try:
    commit_sha = subprocess.check_output(
        ['git', 'rev-parse', '--short', 'HEAD'],
        stderr=subprocess.DEVNULL, text=True
    ).strip()
except Exception:
    commit_sha = 'unknown'

# VALIDATION: check extracted values before storing
VALID_SEVERITIES = {'P0', 'P1', 'P2', 'P3'}
VALID_DIMENSIONS = {
    'correctness', 'security', 'performance',
    'concurrency', 'api_contract', 'bidirectional', 'graceful_degradation',
    'convention', 'test_quality', 'ai_code_smell',
    'error_handling', 'edge_cases',
    'doc_completeness', 'change_scope',
    'unknown',
}

severity = 'REPLACE_WITH_SEVERITY'
dimension = 'REPLACE_WITH_DIMENSION'
file_path = 'REPLACE_WITH_ACTUAL_FILE'

if severity not in VALID_SEVERITIES:
    print(f"[forge-warn] Invalid severity '{severity}', defaulting to P2", file=sys.stderr)
    severity = 'P2'
if dimension not in VALID_DIMENSIONS:
    print(f"[forge-warn] Invalid dimension '{dimension}', defaulting to unknown", file=sys.stderr)
    dimension = 'unknown'
if file_path != 'unknown' and not os.path.isfile(file_path):
    print(f"[forge-warn] File not found: '{file_path}', storing as-is", file=sys.stderr)

evidence_count = 1  # REPLACE_WITH_EVIDENCE_COUNT
llm_self_report = 0.8  # REPLACE_WITH_LLM_CONFIDENCE

if not isinstance(evidence_count, int) or evidence_count < 0:
    print("[forge-warn] Invalid evidence_count, defaulting to 1", file=sys.stderr)
    evidence_count = 1
if not isinstance(llm_self_report, (int, float)) or not (0.0 <= llm_self_report <= 1.0):
    print("[forge-warn] Invalid llm_self_report, defaulting to 0.8", file=sys.stderr)
    llm_self_report = 0.8

data['findings'].append({
    'id': str(uuid.uuid4()),
    'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'file': file_path,
    'line': -1,
    'dimension': dimension,
    'pass': 1,
    'cycle': 1,
    'severity': severity,
    'description': 'REPLACE_WITH_FINDING_TEXT',
    'outcome': 'pending',
    'reject_reason': None,
    'commit_sha': commit_sha,
    'cost_tokens': {'input': 0, 'output': 0},
    'confidence': 0.0,
    'confidence_signals': {
        'dimension_fp_rate': 0.0,
        'pass_agreement': 1.0,
        'evidence_count': evidence_count,
        'llm_self_report': llm_self_report,
    },
    'shadow': False,  # True for shadow-mode dimensions (doc_completeness, change_scope)
})

# Atomic write
dir_name = os.path.dirname(findings_file) or '.'
fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.json')
try:
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, findings_file)
except Exception:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
PYEOF
```

Replace the placeholder values with actual finding data from the pass output. For each finding reported by a pass, execute one append call.

**Finding schema fields (D1):**
- `id`: UUID v4 (unique per finding)
- `timestamp`: ISO-8601 UTC
- `file`: relative path to the file with the finding
- `line`: line number (-1 if unknown)
- `dimension`: which review dimension (must be one of the 14 known dimensions in VALID_DIMENSIONS or "unknown")
- `pass`: which pass number (1, 2, or 3)
- `cycle`: which cycle number
- `severity`: normalized P0/P1/P2/P3 (validated before storage)
- `description`: finding text from the review pass
- `outcome`: "pending" (initial), "accepted", or "rejected"
- `reject_reason`: null (initial) or one of: HALLUCINATION, CONTEXT_MISSING, INTENTIONAL, NOT_APPLICABLE, STYLE_PREFERENCE, ACCEPTABLE_RISK
- `commit_sha`: short git SHA at time of finding (obtained via subprocess, NOT shell substitution)
- `cost_tokens`: {"input": N, "output": M} -- token counts for the pass that produced this finding (set to 0 during interactive mode; CLI wrapper populates actual values)
- `confidence`: float 0.0-1.0, computed by CLI post-run via backfill_confidence(). Set to 0.0 at recording time (SKILL.md heredoc cannot compute it -- needs historical FP data).
- `confidence_signals`: dict with raw signals for the confidence formula:
  - `dimension_fp_rate`: 0.0 (placeholder, computed by CLI from findings.json history)
  - `pass_agreement`: 1.0 (1.0 = finding from single pass; fraction of agreeing passes when multi-pass data available)
  - `evidence_count`: number of distinct code locations examined to support this finding
  - `llm_self_report`: LLM's stated confidence that this finding is a true positive (0.0-1.0)

## Confidence Signal Instructions

When recording a finding, you MUST set these fields to actual values, not defaults:

- `evidence_count`: Set to the number of distinct code locations (lines, functions, or
  files) you examined to support this finding. Count only locations you actually read
  and cite in the finding description. Minimum 1, typical range 1-10.

- `llm_self_report`: Set to your genuine confidence that this finding is a true positive,
  as a float from 0.0 to 1.0. Consider:
  - 0.9-1.0: You are certain this is a real issue (clear bug, obvious vulnerability)
  - 0.7-0.8: High confidence but some ambiguity (pattern match, context-dependent)
  - 0.4-0.6: Uncertain (could be intentional, might be a style choice)
  - 0.1-0.3: Low confidence (speculative, may be a false positive)
  Do NOT default to 0.8 -- assess each finding individually.

## Shadow Mode Dimensions (DIM-01, DIM-04)

Dimensions 13 (doc_completeness) and 14 (change_scope) operate in **shadow mode**:
- Findings ARE persisted to .forge/findings.json with `'shadow': True`
- Findings are NOT displayed to the user in review output
- Findings are NOT counted toward cycle reset decisions
- After 20+ shadow findings accumulate, FP rate is computed via `forge --eval --shadow`
- If FP < 10%: dimension is promoted to active. Use `forge --promote <dim>` to set all findings for that dimension to shadow=False.
- If FP >= 10%: SKILL.md prompt for that dimension needs improvement before retry

When recording a finding for dim 13 or 14, check config for promotion status before setting shadow flag:
```python
# Shadow dimension finding -- logged but NOT shown to user
# N4 fix: check promoted_dimensions in config before hardcoding shadow
SHADOW_DIMENSIONS = {'doc_completeness', 'change_scope'}
promoted = set(config.get('promoted_dimensions', []))
if dimension in SHADOW_DIMENSIONS and dimension not in promoted:
    finding['shadow'] = True
```

**DIM-01 Documentation Completeness (dim 13):**
Check whether public-facing code changes include adequate documentation updates:
- New public functions/methods/classes: do they have docstrings?
- Changed function signatures: is the docstring updated to match?
- User-facing feature changes: is there a changelog entry or README update?
- API endpoint changes: is API documentation updated?
Do NOT flag: internal/private functions, test files, configuration changes, refactoring that preserves behavior.

**DIM-04 Change Scope (dim 14):**
Check whether the diff contains a single coherent concern:
- Does the diff mix unrelated changes (e.g., feature + refactor + bugfix)?
- Are there files modified that have no logical connection to the primary change?
- Does the commit message describe one thing but the diff does several?
Do NOT flag: necessary supporting changes (e.g., updating imports when moving a function), test additions for the primary change, formatting changes required by the primary change.

NOTE (R15): Shadow mode display filtering is implemented in Plan 04 (Wave 3). Until Plan 04 executes, shadow findings will appear in --stats/--eval output. This is acceptable during Phase 2 execution -- data collection starts immediately, filtering is wired later.

## Session State (Hook Integration)

The `check_review_tracker.sh` hook writes severity data to `.forge/current_session.json`
after each review pass. This file contains:

```json
{
  "last_max_severity": "P2",
  "last_review_pass": "qodo-review",
  "qodo_runs": 3,
  "rounds_with_findings": 1
}
```

When available, read this file to cross-check severity classification. If the hook
detected a higher severity than the SKILL.md state machine assigned, use the higher
severity (conservative approach). This provides a second layer of severity enforcement
beyond the SKILL.md instructions alone.

## Feedback Collection (LEARN-07-LITE)

All findings are initially recorded with `outcome: "pending"`.

**When to collect feedback:**
Feedback collection happens ONCE, at the END of the pipeline -- specifically at
the commit gate, AFTER Step 4 (smoke test) completes. This is the single point
where the user reviews all accumulated findings before committing.

Do NOT collect feedback during individual passes (this conflicts with auto-continue).
Do NOT pause between passes to ask about findings.

**At pipeline completion (commit gate):**
Present a summary table of ALL findings from this session:

```
[forge] Pipeline complete. Findings summary:

  # | Severity | Dimension     | File                  | Status
  1 | P2       | security      | hooks/check_*.sh      | fixed (accepted)
  2 | P3       | style         | cli/forge_cli.py      | accumulated (pending)
  3 | P1       | correctness   | skills/forge/SKILL.md | fixed (accepted)

Classify pending findings? [y/n/defer]
```

If user chooses to classify:
  - For each pending finding, ask:
    - **Accept**: The finding was valid (outcome = "accepted")
    - **Reject**: The finding was a false positive (outcome = "rejected")
      If rejected, ask which category:
      1. HALLUCINATION -- the problem does not exist
      2. CONTEXT_MISSING -- reviewer lacked necessary context
      3. INTENTIONAL -- this was an intentional design choice
      4. NOT_APPLICABLE -- the rule does not apply here
      5. STYLE_PREFERENCE -- subjective, not a defect
      6. ACCEPTABLE_RISK -- real issue, but risk accepted

If user defers: findings remain "pending" for later classification via `forge --classify`.

**Findings that were fixed:**
When a finding triggers a code fix (P0/P1/P2 that caused reset), automatically
set its outcome to "accepted" -- the act of fixing it confirms it was valid.
Only accumulated P3 findings and unfixed findings remain "pending".

**Updating a finding outcome:** Use a Bash tool call with Python heredoc:

```bash
python3 << 'PYEOF'
import json, os, tempfile

findings_file = '.forge/findings.json'
finding_id = 'REPLACE_WITH_FINDING_UUID'
new_outcome = 'rejected'  # or 'accepted'
new_reason = 'HALLUCINATION'  # or None for accepted

with open(findings_file, 'r') as f:
    data = json.load(f)

for finding in data['findings']:
    if finding['id'] == finding_id:
        finding['outcome'] = new_outcome
        finding['reject_reason'] = new_reason if new_outcome == 'rejected' else None
        break

dir_name = os.path.dirname(findings_file) or '.'
fd, tmp = tempfile.mkstemp(dir=dir_name, suffix='.json')
try:
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, findings_file)
except Exception:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
PYEOF
```

## Why Each Pass Is Mandatory

- Pass 1 (qodo): catches structural/feature-level issues
- Pass 2 (code-review-expert): catches SOLID violations, architecture problems
- Pass 3 (adversarial-qe): catches regressions INTRODUCED BY fixes from Passes 1-2

This is the key insight: fixes create new bugs. Pass 3 exists to catch them.

## Cross-Function Enforcement

Diff-only review cannot catch cross-function inconsistencies. Pass 3 must grep the FULL FILE for consistency: error message prefixes, naming conventions, variable usage patterns.

## Handling Findings

Finding handling depends on severity (see Severity-Gated Cycle Reset above):

- **P0/P1 findings**: Fix ALL findings immediately. cycle_counter = 0. Restart from Cycle 1, Pass 1.
- **P2 findings**: Fix P2 findings. Restart current cycle from Pass 1. Do NOT reset cycle_counter.
- **P3 findings**: Record but do not fix immediately. Accumulate and continue to next pass.
  - Deduplicate by rule type, then check density thresholds:
  - Per-file >5 distinct rule violations: P2-equivalent restart
  - Per-diff >10 distinct rule violations: P2-equivalent restart
  - Density >0.15 P3 findings per changed line: P2-equivalent restart
  - Below all thresholds: accumulate silently, continue

After fixing any finding, verify no out-of-scope files were modified:
```bash
git diff --name-only
```
Revert any out-of-scope changes with `git checkout -- <file>`.

## Hard Stop

The `check_review_tracker.sh` hook tracks state. After 3 rounds where findings persist, it blocks all Edit/Write operations. This requires human intervention to unblock and prevents infinite fix-break loops.

## Steps 1-3 Gate

- **Entry**: Step 0 passed
- **Exit**: 3 consecutive cycles where ALL 3 passes report zero findings (minimum 9 passes total)
- **On P0/P1**: fix -> counter = 0 -> restart from Cycle 1
- **On P2**: fix -> restart current cycle
- **On P3 only**: accumulate (density check -> P2 escalation if thresholds exceeded)

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

# Step 5: R1 Test Gate

## Purpose

Tests must exist for every diff-impacted source file and must pass. The gate
detects changed source files, maps them to expected test files using ecosystem
conventions, runs the test suite, and fails if any test fails or if no test
file can be found for a public function in the changed source.

## Algorithm (language-independent)

1. Determine changed source files from the diff (exclude test files themselves).
2. For each changed source file, locate candidate test files using ecosystem
   naming conventions (see Tool Table below).
3. Run the test suite restricted to those candidate test files.
4. If no candidate test file exists for a public function in the changed source,
   emit R1 PARTIAL (LLM fallback applies -- see Fallback).
5. If any test fails, R1 FAIL. If all pass (or skip), R1 PASS.

## Tool Table

| Language | Test Runner (R1) | Test file naming convention |
|---|---|---|
| Python | `pytest` | `tests/test_<module>.py` or `test_<module>.py` |
| Go | `go test ./...` | `<package>_test.go` in same directory |
| Rust | `cargo test` | `tests/` dir or `#[cfg(test)]` in same file |
| JavaScript | `jest` / `vitest` / `mocha` | `<module>.test.js` or `__tests__/<module>.js` |
| TypeScript | `jest` / `vitest` | `<module>.test.ts` or `__tests__/<module>.ts` |
| Java | `mvn test` / `gradle test` | `<Class>Test.java` or `Test<Class>.java` |
| Kotlin | `gradle test` | `<Class>Test.kt` |
| C | `ctest` / `make test` | `test_<module>.c` or `<module>_test.c` |
| C++ | `ctest` / `make test` | `test_<module>.cpp` or `<module>_test.cpp` |
| Kernel C | Beaker functional | `runtest.sh` under test case directory |
| Shell | `bats` / inline | `test_<script>.bats` or `test_<script>.sh` |
| Ruby | `rspec` / `minitest` | `<module>_spec.rb` or `test_<module>.rb` |
| PHP | `phpunit` | `<Class>Test.php` |
| Swift | `swift test` | `<Module>Tests.swift` |

## Python CLI Fast Path (optional)

```
code-forge gate-check
```

Reads `.code-forge/gate.yaml` for test command and path filter configuration.

## Fallback (no test file found)

When no test file can be located for a changed public function:
1. LLM identifies all public functions in the changed source.
2. For each untested public function, generates a stub test that calls the
   function with representative inputs and asserts the return type.
3. Mark R1 PARTIAL in findings.json. The stub test is advisory -- it does not
   replace a real test.

## Failure Handling

- FAIL -> fix (add or repair tests) -> cycle_counter = 0 -> restart from Step 0
- Record to `.code-forge/findings.json`:

```json
{
  "gate": "R1",
  "result": "FAIL",
  "failed_tests": ["tests/test_foo.py::test_bar"],
  "missing_coverage": ["src/foo.py::public_fn"]
}
```

---

# Step 6: R2 Mutation Check

## Purpose

Tests must be capable of killing mutants introduced into the changed code, not
just achieve line coverage. A passing test suite that cannot detect a simple
mutation (e.g., flipped boolean, off-by-one) is toothless. R2 detects this by
mutating the changed files and running the test suite against each mutant. Any
surviving mutant means the tests cannot catch the corresponding change.

## Algorithm (language-independent)

1. Scope mutation to diff-changed files only (not the full codebase).
2. Run the baseline test suite three times to confirm it is not flaky.
3. If the mutation tool is not installed, log `tool_missing` and WARN (not FAIL).
4. Apply the mutation tool to generate mutants for each changed file.
5. Run the test suite against each mutant.
6. Collect surviving mutants (mutants not killed by any test).
7. If survivor count > 0, R2 FAIL with survivor list. Otherwise R2 PASS.

## Tool Table

| Language | Mutation Tool (R2) | Notes |
|---|---|---|
| Python | `mutmut` (preferred) or `cosmic-ray` | `mutmut run` + `mutmut results` |
| Go | `gremlins` or `go-mutesting` | `gremlins unleash ./...` |
| Rust | `cargo mutants` | `cargo mutants --workspace` |
| JavaScript | `stryker-mutator` | `npx stryker run` |
| TypeScript | `stryker-mutator` | `npx stryker run` |
| Java | `pitest` | `mvn org.pitest:pitest-maven:mutationCoverage` |
| Kotlin | `pitest` | `gradle pitest` |
| C | `mull` | `mull-runner <test-binary>` |
| C++ | `mull` | `mull-runner <test-binary>` |
| Kernel C | N/A | Beaker functional tests only; skip R2 |
| Shell | LLM-inject 10 mutants | See Fallback below |
| Ruby | `mutant` | `mutant run` |
| PHP | `infection` | `./vendor/bin/infection` |
| Swift | `muter` | `muter run` |

## Python CLI Fast Path (optional)

```
code-forge mutation-check --timeout 600
```

Defaults to uncommitted changes. Pass `--diff <path>` to specify a diff file.
Pass `--paths <glob>` to restrict to matching files.

## Fallback (no tool installed)

When the mutation tool is not installed:
1. Log `tool_missing: <tool_name>` to `.code-forge/findings.json`.
2. LLM injects 10 representative mutants per changed function manually:
   negate a boolean, flip a comparison operator, remove a guard clause,
   swap two arguments, change a return value.
3. Run the test suite after each manual mutation.
4. Report surviving manual mutants as R2 advisory findings (not FAIL).
5. Mark R2 PARTIAL in findings.json.

## Failure Handling

- FAIL -> add or strengthen tests -> cycle_counter = 0 -> restart from Step 0
- Record to `.code-forge/findings.json`:

```json
{
  "gate": "R2",
  "result": "FAIL",
  "survivors": [
    "code_forge.mutation.run_mutation__mutmut_3",
    "code_forge.mutation.run_mutation__mutmut_7"
  ]
}
```

---

# Step 7: R3 E2E Coverage

## Purpose

When a diff touches multiple source components AND modifies a function signature
or return type, cross-component integration is at risk. R3 checks whether an
e2e test artifact exists that covers the boundary. It operates in two layers:
Layer 1 (heuristic, always active) emits an advisory finding when >=2 source
groups are changed and a signature modification is detected. Layer 2 (opt-in,
requires `.code-forge/components.yaml`) emits a blocking finding when a hub
component and a dependent are both modified and no e2e artifact exists under the
dependent's paths.

## Algorithm (language-independent)

1. Parse the diff to detect signature changes (Python `def`, shell functions,
   section headers matching a def pattern).
2. Group changed source files by component using path heuristics or
   `.code-forge/components.yaml` if present.
3. **Layer 1 (heuristic):** if >=2 source groups changed AND a signature change
   detected -> emit advisory finding (DISMISSED disposition, non-blocking).
4. **Layer 2 (explicit, opt-in):** if `components.yaml` present, resolve hub +
   dependent co-occurrence. If both touched and no e2e artifact matches the
   configured `e2e_patterns` under the dependent's paths -> emit blocking finding
   (UNCERTAIN disposition, R3 FAIL). `e2e_absent_ok` in components.yaml
   provides an escape hatch for components intentionally lacking e2e coverage.
5. If no components.yaml and no path heuristic match -> SKIP with WARN.

## Tool Table

| Ecosystem | E2E artifact patterns | Notes |
|---|---|---|
| Python | `tests/e2e/**`, `test_*integration*` | Default patterns |
| Go | `*_integration_test.go`, `e2e/**/*_test.go` | |
| Rust | `tests/integration_*.rs`, `tests/e2e_*.rs` | |
| JavaScript/TS | `e2e/**/*.spec.*`, `**/*.e2e-spec.*`, `cypress/**` | |
| Java/Kotlin | `*IT.java`, `*IntegrationTest.java`, `*IT.kt` | |
| C/C++ | `test/integration_*`, `tests/e2e_*` | |
| Shell | `tests/e2e_*.sh`, `tests/integration_*.sh` | |

## Python CLI Fast Path (optional)

```
code-forge e2e-check
```

Defaults to uncommitted changes and current directory as repo root. Pass
`--diff <path>` to specify a diff file. Pass `--repo-root <path>` to set
the repository root for artifact search.

## Fallback (no components.yaml, no path heuristic match)

When `.code-forge/components.yaml` is absent and the path heuristic cannot
group changed files into >=2 components:
- SKIP with WARN: log `e2e_check: skip: no components config and no
  cross-component change detected` to `.code-forge/findings.json`.
- R3 result is SKIP (not FAIL); the pipeline proceeds to commit gate.

## Failure Handling

- Layer 1 finding (advisory): accumulate, do not block pipeline.
- Layer 2 finding (blocking): FAIL -> add or identify e2e test artifact ->
  cycle_counter = 0 -> restart from Step 0.
- Record to `.code-forge/findings.json`:

```json
{
  "gate": "R3",
  "result": "FAIL",
  "survivors": [],
  "description": "cross-component change: hub 'core' + dependent 'api' both touched; no e2e artifact found"
}
```

SKIP records:

```json
{
  "gate": "R3",
  "result": "SKIP",
  "survivors": []
}
```

---

# Receipt Protocol

When running outside the CLI (editor mode / Path C), write one receipt JSON file per review pass to `.code-forge/receipts/`.

## File naming

`receipt-c{cycle}p{pass}.json` where cycle is 1-3 and pass is 1-3.

A complete review produces 9 receipt files: c1p1 through c3p3.

## Receipt schema

```json
{
  "cycle": 1,
  "pass": 1,
  "skill": "qodo-review",
  "diff_sha256": "<sha256 of normalized diff>",
  "timestamp": "2026-05-28T10:04:00Z",
  "findings_count": 2,
  "findings": [
    {
      "file": "src/foo.py",
      "line": 42,
      "description": "[qodo] potential null dereference",
      "disposition": "UNCERTAIN"
    }
  ],
  "anchors": [
    {"file": "src/foo.py", "line": 42, "text": "def bar():"}
  ],
  "code_excerpts": [
    {
      "file": "src/foo.py",
      "start_line": 40,
      "end_line": 45,
      "content": "def bar():\n    x = get()\n    return x.value\n",
      "rationale": "null dereference if get() returns None"
    }
  ],
  "covered_line_ranges": [
    {"file": "src/foo.py", "start": 30, "end": 60}
  ]
}
```

## Pass-to-skill mapping

| Pass | Skill name |
|------|-----------|
| 1 | qodo-review |
| 2 | code-review-expert |
| 3 | adversarial-qe |

## Verification checks

Run `code-forge verify` to validate receipts. Seven checks:

1. **Completeness**: 9 receipts, unique cycle/pass matrix, findings_count matches
2. **Diff hash**: All receipts reference the current diff SHA256
3. **Anchor reality**: Anchor files exist in the current diff
4. **Timestamps**: Monotonically increasing across all receipts
5. **Excerpt verification**: Code excerpts match actual file content; missing file = FAIL
6. **Coverage quota**: Each cycle covers at least 60% of changed lines
7. **Jaccard overlap**: Coverage Jaccard between cycle pairs must be below 0.8 (anti-rubber-stamp)

## What verify does not catch

These seven checks are a tamper check on the receipt set, not proof that a
review happened. They confirm that nine receipts exist, hash to the current
diff, quote file content verbatim, and claim adequate non-rubber-stamped
coverage. They do not confirm that the reviewer read the code.

A zero-findings receipt set passes whenever its claimed `covered_line_ranges`
clear the 60% floor: check 5 (excerpt verification) only inspects reported
findings, so a clean pass with no findings has nothing to verify, and an
editor-mode reviewer (Path C) can hand-write coverage ranges it never
performed. `code-forge verify` cannot distinguish a diligent clean review
from a fabricated one.

The real anti-shirk guarantees live elsewhere:

- **R1 pre-commit test gate** runs the test suite and blocks on new failures
  versus a baseline -- it gates on real test results, not self-reported claims.
- **StateMachine consecutive-clean counter** (CLI / Path A) requires three
  independent clean cycles and resets on any finding.

Use `verify` to detect tampered or incomplete receipts, not as a substitute
for running tests.

## Diff SHA256 computation

The diff hash uses `compute_source_hash()` from `source.py` -- NOT shell `sha256sum`. The hash includes a `mode=git` prefix and normalizes whitespace. All three components (receipt writer, verify, hook) use this same function.

---

# Commit Gate

Only after ALL steps complete:

```bash
git commit -m "<subsystem>/<case>: <summary>

<detailed description>

Signed-off-by: Minxi Hou <houminxi@gmail.com>"  # post-review-c3
```

## Completion Checklist

Before committing, all of the following must be satisfied:

- [ ] 3 consecutive clean review cycles (Steps 1-3) with zero findings
- [ ] Step 3.5 false-positive verification complete (if findings were fixed)
- [ ] Step 4 smoke test: PASS
- [ ] Step 5 R1 test gate: PASS (or PARTIAL with stub tests generated)
- [ ] Step 6 R2 mutation check: PASS (or PARTIAL if tool absent + LLM fallback done)
- [ ] Step 7 R3 e2e check: PASS or SKIP (SKIP is acceptable when no cross-component change detected)

## findings.json: dynamic_gate_run entry shape

Each dynamic gate (R1/R2/R3) run appends an entry to `.code-forge/findings.json`
under a `dynamic_gate_run` key. The schema:

```json
{
  "dynamic_gate_run": {
    "gate": "R1",
    "result": "PASS",
    "timestamp": "2026-05-27T12:00:00Z",
    "survivors": [],
    "failed_tests": [],
    "missing_coverage": [],
    "tool": "pytest",
    "tool_missing": false,
    "infra_errors": []
  }
}
```

Fields:
- `gate`: "R1", "R2", or "R3"
- `result`: "PASS", "FAIL", "SKIP", or "PARTIAL"
- `timestamp`: ISO-8601 UTC
- `survivors`: list of mutant names (R2) or finding descriptions (R3)
- `failed_tests`: list of test identifiers that failed (R1 only)
- `missing_coverage`: list of source locations with no test file (R1 only)
- `tool`: name of the tool invoked (e.g., "mutmut", "pytest", "e2e_check")
- `tool_missing`: true if the tool was not installed (soft dependency)
- `infra_errors`: list of infrastructure error strings

## Rules

- `# post-review-c3` is an internal gate marker ONLY -- it triggers the hook check
- The marker must NEVER appear in the commit message content itself
- The commit message must read as if written by a human engineer
- Zero AI markers: no Co-Authored-By, no model names, no review process metadata

## Non-Code Exemptions

These commit types bypass the full pipeline but still require worktree and
AI-attribution checks. Steps 5-7 (R1/R2/R3) are also skipped for these types:

- `# docs` -- documentation only
- `# config` -- configuration changes
- `# chore` -- tooling, dependencies, cleanup
- `# wip` -- work in progress

---

# Adaptive Mechanisms

These are built into the pipeline and must be followed:

1. **Severity-Gated Cycle Reset (TRUST-07)**: P0/P1 findings reset counter to 0 and restart from Cycle 1 Pass 1. P2 findings restart the current cycle without resetting the counter. P3 findings accumulate with density-based escalation: deduplicate by rule type, then check per-file >5, per-diff >10, density >0.15/line -- any trigger causes P2-equivalent restart. Below threshold: accumulate silently, report count, continue. This replaces the previous unconditional reset behavior, reducing wasted passes by an estimated 60%+ while maintaining quality for critical issues.

2. **Hard Stop After 3 Rounds With Findings**: hook blocks all Edit/Write. Forces human intervention. Prevents infinite fix-break loops.

3. **Cross-Function Grep (Pass 3)**: dimension 9 "Convention adherence" requires grepping the full file, not just the diff. Catches cross-function inconsistencies.

4. **Anti-Hallucination Gates**: Pass 1 (re-read + grep), Pass 3 (3-step verification), Step 3.5 (10-step protocol with existence check).

5. **Cross-Model Complementarity**: different AI models catch different bug classes. The 3-pass structure exploits this: structural (Pass 1), architectural (Pass 2), adversarial (Pass 3).

6. **Ground Truth Verification for Test Infrastructure**: test assertions validated via bug injection: inject bug -> FAIL -> revert -> PASS. Static analysis alone cannot catch faulty assertion logic.

7. **Full Pipeline Restart on Smoke Test Failure**: smoke test FAIL -> fix -> restart from Step 0 (not Step 4). The fix itself may introduce new lint/review issues.

8. **Bidirectional Correctness**: round-trip operations (encode/decode, serialize/deserialize) verified in both directions. Origin: Sashiko review gap.

9. **Graceful Degradation**: missing optional dependencies must degrade gracefully, not crash. Review checks for this explicitly. Origin: Sashiko review gap.

10. **Scope Verification After Automated Tools**: after any review pass, check `git status` / `git diff --name-only` to confirm no out-of-scope files were modified. Revert any out-of-scope changes immediately.

11. **Auto-Continue on Clean Pass (TRUST-06)**: when a pass reports zero findings, forge immediately proceeds to the next pass/cycle without waiting for user input. Only pauses when findings exist and user decision is needed. Eliminates the "type continue after every LGTM pass" UX friction.

12. **Finding Persistence (TRUST-01)**: every finding is recorded to .forge/findings.json with structured metadata (severity, dimension, outcome, reject_reason). Extracted data is validated before storage (severity must be P0-P3, dimension must be in known set, file path existence checked). This enables Phase 1b calibration via 30+ days of accumulated data.

13. **Feedback Collection (LEARN-07-LITE)**: binary accept/reject feedback collected ONCE at pipeline completion (commit gate). Findings fixed during the pipeline are auto-accepted. Pending findings can be classified at commit gate or deferred to `forge --classify`. Feedback is NOT collected during individual passes to avoid conflicting with auto-continue.

14. **Step 0 Context Fusion (FUSE-01)**: deterministic Step 0 findings are serialized as a markdown table (capped at 20 rows) and injected into every LLM pass prompt. This prevents redundant flagging and lets LLM passes focus on issues that static tools cannot catch.

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
3. **Run Step 0**: syntax + lint + non-ASCII. Stop on any failure. After all Step 0 checks pass, serialize findings into FUSE-01 context block for LLM passes (cap at 20 rows).
4. **Initialize cycle_counter = 0**
5. **Run cycles**: invoke /qodo-review, /code-review-expert, /adversarial-qe sequentially. Apply severity-gated state machine: P0/P1 = full reset, P2 = cycle restart, P3 = accumulate (density check -> P2 escalation), clean = auto-continue. Persist all findings to .forge/findings.json with validation.
6. **After 3 clean cycles**: run Step 3.5 if findings were ever fixed during the process.
7. **Run Step 4**: invoke /smoke-test. Full pipeline restart on any FAIL.
8. **Report**: summary of passes completed, findings fixed, smoke test results.
8.5. **Feedback collection**: present finding summary table. Collect accept/reject for pending findings (LEARN-07-LITE). Users can defer to `forge --classify`.
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
