---
phase: 01a-trust-instrumentation
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - skills/forge/SKILL.md
autonomous: true
requirements:
  - TRUST-01
  - TRUST-06
  - TRUST-07
  - LEARN-07-LITE

must_haves:
  truths:
    - "Every review finding is recorded to .forge/findings.json with severity, dimension, outcome, and reject_reason fields"
    - "Zero-finding passes proceed automatically without waiting for user input"
    - "P3 findings use density-based escalation: deduplicate by rule type, then check per-file >5, per-diff >10, density >0.15/line; any trigger causes P2-equivalent restart. Below threshold: accumulate silentlyr user review; >10 P3s triggers P2-equivalent reset"
    - "P0/P1 findings reset cycle_counter to 0; P2 findings restart the current cycle only"
    - "Each finding is created with outcome 'pending' and can later be classified as 'accepted' or 'rejected' with a 6-category reject_reason"
    - "Feedback collection happens ONCE at pipeline completion (commit gate), not during passes"
    - "commit_sha is obtained via subprocess inside the Python heredoc, not via shell substitution in a quoted heredoc"
    - "Every extracted finding is validated: severity in P0/P1/P2/P3, dimension in known set, file path checked"
  artifacts:
    - path: "skills/forge/SKILL.md"
      provides: "Modified state machine with severity-gated reset, auto-continue, finding persistence, feedback collection, finding validation"
      contains: "Severity-Gated Cycle Reset"
  key_links:
    - from: "skills/forge/SKILL.md"
      to: ".forge/findings.json"
      via: "Python heredoc in Bash tool to append findings"
      pattern: "findings\\.append"
---

<objective>
Modify the forge SKILL.md state machine to support severity-gated cycle reset (TRUST-07), auto-continue on clean passes (TRUST-06), finding persistence to .forge/findings.json (TRUST-01), and binary feedback collection with 6-category reject_reason taxonomy (LEARN-07-LITE).

Purpose: This is the foundation of Phase 1a. The current SKILL.md has a simple "any finding resets everything" state machine with no persistence. After this plan, every finding is tracked with structured metadata, the pipeline is smarter about which findings warrant reset, and clean passes flow without user friction.

Output: Modified skills/forge/SKILL.md with 5 new sections and a modified state machine.

Review fixes addressed:
- Issue #4 (HIGH): Feedback collection happens ONCE at pipeline completion, not during passes
- Issue #5 (HIGH): Finding data extraction validation added -- severity/dimension/filepath checked
- Issue #6 (MEDIUM): commit_sha uses subprocess.check_output inside Python, not shell substitution in heredoc
- Issue #10 (MEDIUM): Feedback vs auto-continue conflict resolved -- feedback is post-pipeline only
- Issue #17 (USER): P3 density-based escalation with deduplication (per-file >5, per-diff >10, density >0.15/line)
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/01a-trust-instrumentation/01a-CONTEXT.md
@.planning/phases/01a-trust-instrumentation/01a-RESEARCH.md
@.planning/phases/01a-trust-instrumentation/01a-PATTERNS.md
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add severity normalization, finding persistence with validation, P3 threshold, auto-continue, and feedback collection to SKILL.md</name>
  <files>skills/forge/SKILL.md</files>
  <read_first>
    - skills/forge/SKILL.md (current 417-line state machine -- read the full file to understand structure)
    - .planning/phases/01a-trust-instrumentation/01a-CONTEXT.md (D1 schema, D2 taxonomy, D3 severity gates)
    - .planning/phases/01a-trust-instrumentation/01a-RESEARCH.md (Pattern 1: Finding Recording, Pattern 2: Severity-Gated Cycle Reset, Pitfall 4: Severity Classification Inconsistency)
    - .planning/phases/01a-trust-instrumentation/01a-PATTERNS.md (severity normalization table, existing SKILL.md structure patterns)
  </read_first>
  <action>
Modify skills/forge/SKILL.md with the following additions and changes. Preserve ALL existing content that is not being replaced. Use the existing instruction format pattern (numbered steps, markdown tables, code blocks).

**Addition 1: Severity Normalization Table**
Insert a new section after the "## Each Cycle = 3 Sequential Passes" section (after the Pass 3 description, before "## Why Each Pass Is Mandatory"). Title: `## Severity Normalization`.

Content:
```markdown
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
```

**Addition 2: Finding Persistence Instructions with Validation**
Insert a new section after the Severity Normalization section. Title: `## Finding Persistence (TRUST-01)`.

CRITICAL (addresses review issue #5 -- finding data extraction validation):
The finding persistence template MUST include a validation step. After extracting each finding's data from the pass output, validate:
- severity is one of P0, P1, P2, P3
- dimension is one of: correctness, security, performance, style, architecture, concurrency, api_contract, bidirectional, graceful_degradation, convention, test_quality, ai_code_smell, unknown
- file path exists on disk (os.path.isfile) or is "unknown"
Invalid entries are flagged with a warning on stderr, NOT silently stored.

CRITICAL (addresses review issue #6 -- commit_sha in heredoc):
The Python heredoc uses subprocess.check_output to get git SHA, NOT shell command substitution. In a quoted heredoc (<< 'PYEOF'), $(git rev-parse ...) is NOT expanded -- it would be stored as a literal string. Instead use:

```python
import subprocess
try:
    sha = subprocess.check_output(
        ['git', 'rev-parse', '--short', 'HEAD'],
        stderr=subprocess.DEVNULL, text=True
    ).strip()
except Exception:
    sha = 'unknown'
```

Content for the section:
```markdown
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
    'correctness', 'security', 'performance', 'style', 'architecture',
    'concurrency', 'api_contract', 'bidirectional', 'graceful_degradation',
    'convention', 'test_quality', 'ai_code_smell', 'unknown',
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
    'cost_tokens': {'input': 0, 'output': 0}
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
- `dimension`: which review dimension (must be one of the 12 known dimensions or "unknown")
- `pass`: which pass number (1, 2, or 3)
- `cycle`: which cycle number
- `severity`: normalized P0/P1/P2/P3 (validated before storage)
- `description`: finding text from the review pass
- `outcome`: "pending" (initial), "accepted", or "rejected"
- `reject_reason`: null (initial) or one of: HALLUCINATION, CONTEXT_MISSING, INTENTIONAL, NOT_APPLICABLE, STYLE_PREFERENCE, ACCEPTABLE_RISK
- `commit_sha`: short git SHA at time of finding (obtained via subprocess, NOT shell substitution)
- `cost_tokens`: {"input": N, "output": M} -- token counts for the pass that produced this finding (set to 0 during interactive mode; CLI wrapper populates actual values)
```

**Modification 1: Replace the State Machine section**
Replace the existing state machine (lines 113-128 approximately, the section starting with "State: cycle_counter = 0") with the severity-gated version. Keep the section title "## State Machine".

CRITICAL (addresses review issue #17 -- P3 accumulation threshold):
P3 findings accumulate without interrupt, but with density-based escalation. Uses deduplication + dual threshold (per P3-THRESHOLD-RESEARCH.md):
1. DEDUPLICATE: group P3 findings by rule type (e.g., 15 identical indentation findings = 1 distinct violation)
2. Three escalation triggers (whichever fires first):
   - Per-file: >5 distinct P3 rule violations in a single file -> escalate to P2
   - Per-diff: >10 distinct P3 rule violations across entire diff -> escalate to P2
   - Density: >0.15 P3 findings per changed line (~1 per 7 lines) -> escalate to P2
3. Escalation = P2 behavior (restart current cycle), NOT P0/P1 (full reset)
4. Below all thresholds: accumulate silently, report count, continue

New state machine content:
```markdown
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
          report: "[forge] P3 density: {density:.2f}/line (>{0.15}) -- P2 escalation"
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
```

**Addition 3: Auto-Continue Protocol**
Insert after the modified State Machine section. Title: `## Auto-Continue Protocol (TRUST-06)`.

Content:
```markdown
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
```

**Addition 4: Feedback Collection Protocol (LEARN-07-LITE)**
Insert after the Finding Persistence section. Title: `## Feedback Collection (LEARN-07-LITE)`.

CRITICAL (addresses review issues #4 and #10 -- feedback vs auto-continue conflict):
Feedback collection happens ONCE at pipeline completion (commit gate), NOT during individual passes. During passes, findings are recorded as "pending" and the pipeline flows per auto-continue rules. This eliminates the conflict between auto-continue (TRUST-06) and feedback collection.

Content:
```markdown
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
```

**Modification 2: Replace Handling Findings section**
Replace the existing "## Handling Findings" section to reference severity-gated behavior:

```markdown
## Handling Findings

Finding handling depends on severity (see Severity-Gated Cycle Reset above):

- **P0/P1 findings**: Fix ALL findings immediately. cycle_counter = 0. Restart from Cycle 1, Pass 1.
- **P2 findings**: Fix P2 findings. Restart current cycle from Pass 1. Do NOT reset cycle_counter.
- **P3 findings**: Record but do not fix immediately. Accumulate and continue to next pass.
  - Over 5 accumulated P3s: pause for user review (optional)
  - Over 10 accumulated P3s: treat as P2-equivalent reset (restart current cycle)

After fixing any finding, verify no out-of-scope files were modified:
```bash
git diff --name-only
```
Revert any out-of-scope changes with `git checkout -- <file>`.
```

**Modification 3: Update Pipeline Overview ASCII diagram**
Replace the line:
```
     |        Any finding -> fix -> counter = 0 -> restart
```
With:
```
     |        P0/P1 -> fix -> counter = 0 -> restart all
     |        P2 -> fix -> restart current cycle
     |        P3 -> accumulate (>10 = P2 reset)
     |        Clean -> auto-continue (no user prompt)
```

**Modification 4: Update Execution Protocol**
In the "# Execution Protocol" section, update step 5:

Replace:
```
5. **Run cycles**: invoke /qodo-review, /code-review-expert, /adversarial-qe sequentially. Apply state machine rules.
```
With:
```
5. **Run cycles**: invoke /qodo-review, /code-review-expert, /adversarial-qe sequentially. Apply severity-gated state machine: P0/P1 = full reset, P2 = cycle restart, P3 = accumulate (>10 = P2 reset), clean = auto-continue. Persist all findings to .forge/findings.json with validation.
```

Add after step 8 (Report):
```
8.5. **Feedback collection**: present finding summary table. Collect accept/reject for pending findings (LEARN-07-LITE). Users can defer to `forge --classify`.
```

**Modification 5: Update Adaptive Mechanisms**
Replace item 1:
```
1. **Severity-Gated Cycle Reset (TRUST-07)**: P0/P1 findings reset counter to 0 and restart from Cycle 1 Pass 1. P2 findings restart the current cycle without resetting the counter. P3 findings accumulate with threshold: >5 pauses for review, >10 triggers P2-equivalent reset. This replaces the previous "any finding resets everything" behavior, reducing wasted passes by an estimated 60%+ while maintaining quality for critical issues.
```

Add item 11:
```
11. **Auto-Continue on Clean Pass (TRUST-06)**: when a pass reports zero findings, forge immediately proceeds to the next pass/cycle without waiting for user input. Only pauses when findings exist and user decision is needed. Eliminates the "type continue after every LGTM pass" UX friction.
```

Add item 12:
```
12. **Finding Persistence (TRUST-01)**: every finding is recorded to .forge/findings.json with structured metadata (severity, dimension, outcome, reject_reason). Extracted data is validated before storage (severity must be P0-P3, dimension must be in known set, file path existence checked). This enables Phase 1b calibration via 30+ days of accumulated data.
```

Add item 13 (placeholder for FUSE-01, will be filled by Plan 03):
```
13. **Feedback Collection (LEARN-07-LITE)**: binary accept/reject feedback collected ONCE at pipeline completion (commit gate). Findings fixed during the pipeline are auto-accepted. Pending findings can be classified at commit gate or deferred to `forge --classify`. Feedback is NOT collected during individual passes to avoid conflicting with auto-continue.
```
  </action>
  <verify>
    <automated>grep -c "Severity-Gated Cycle Reset" skills/forge/SKILL.md && grep -c "Auto-Continue Protocol" skills/forge/SKILL.md && grep -c "Finding Persistence" skills/forge/SKILL.md && grep -c "Feedback Collection" skills/forge/SKILL.md && grep -c "LEARN-07-LITE" skills/forge/SKILL.md && grep -c "TRUST-06" skills/forge/SKILL.md && grep -c "TRUST-07" skills/forge/SKILL.md && grep -c "TRUST-01" skills/forge/SKILL.md && grep -c "findings.json" skills/forge/SKILL.md && grep -c "VALID_SEVERITIES" skills/forge/SKILL.md && grep -c "VALID_DIMENSIONS" skills/forge/SKILL.md && grep -c "subprocess.check_output" skills/forge/SKILL.md && grep -c "p3_by_rule" skills/forge/SKILL.md && grep -c "commit gate" skills/forge/SKILL.md</automated>
  </verify>
  <acceptance_criteria>
    - skills/forge/SKILL.md contains section header "## Severity Normalization" with the 4-row mapping table
    - skills/forge/SKILL.md contains section header "## Finding Persistence (TRUST-01)" with Python heredoc template
    - The finding persistence template contains VALID_SEVERITIES and VALID_DIMENSIONS sets for validation (addresses review issue #5)
    - The finding persistence template uses subprocess.check_output for commit_sha, NOT shell $() substitution (addresses review issue #6)
    - The finding persistence template prints warnings to stderr for invalid severity/dimension/filepath (addresses review issue #5)
    - skills/forge/SKILL.md contains section header "## Auto-Continue Protocol (TRUST-06)"
    - skills/forge/SKILL.md contains section header "## Feedback Collection (LEARN-07-LITE)" that specifies feedback collection at commit gate only (addresses review issues #4 and #10)
    - The State Machine section contains p3_by_rule tracking with density-based escalation: per-file >5, per-diff >10, density >0.15/line (addresses review issue #17 + P3-THRESHOLD-RESEARCH.md)
    - The State Machine section contains deduplication step: "group new P3s by rule type"
    - The State Machine section contains "if any P0 or P1 finding:" and "cycle_counter = 0" and "if any P2 finding" and "if only P3 findings"
    - The State Machine section does NOT contain the old "if ANY pass reports findings:" unconditional reset
    - The Pipeline Overview ASCII diagram contains "P0/P1 -> fix -> counter = 0 -> restart all" and "P3 -> density check"
    - The Handling Findings section mentions P3 density-based escalation with deduplication
    - Execution Protocol includes step 8.5 for feedback collection at commit gate
    - The Adaptive Mechanisms list includes items for TRUST-07, TRUST-06, TRUST-01, and LEARN-07-LITE
    - findings.json schema in SKILL.md includes all 13 fields
    - grep -c "any finding.*resets" skills/forge/SKILL.md returns 0 (old unconditional reset language removed)
    - YAML frontmatter (name: forge) is intact
  </acceptance_criteria>
  <done>SKILL.md contains severity normalization table, finding persistence with validation (severity/dimension/filepath checked), auto-continue protocol, feedback collection at commit gate only, P3 density-based escalation with deduplication (per-file >5, per-diff >10, density >0.15/line), and severity-gated state machine. The old unconditional "any finding resets everything" behavior is fully replaced. All four requirement IDs (TRUST-01, TRUST-06, TRUST-07, LEARN-07-LITE) are referenced. Review issues #4, #5, #6, #10, #17 are addressed. P3 threshold design backed by P3-THRESHOLD-RESEARCH.md (Google Tricorder, BitsAI-CR, Broken Windows theory).</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| SKILL.md instructions -> LLM behavior | LLM may not follow complex instructions perfectly |
| User input -> findings.json | Finding descriptions from LLM output written to filesystem |
| LLM-extracted data -> validation | Severity/dimension/filepath validated before storage |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-01a-01 | T (Tampering) | .forge/findings.json | mitigate | Atomic write via tempfile.mkstemp + os.replace prevents corruption |
| T-01a-02 | I (Information Disclosure) | .forge/findings.json | accept | Local-only file; no PII beyond file paths. .gitignore prevents accidental commit |
| T-01a-03 | D (Denial of Service) | SKILL.md instruction length | accept | Adding ~250 lines to 417-line file; within model context limits |
| T-01a-04 | T (Tampering) | Finding data extraction | mitigate | VALID_SEVERITIES/VALID_DIMENSIONS validation prevents corrupted data from entering findings.json |
</threat_model>

<verification>
1. grep for all 4 requirement IDs (TRUST-01, TRUST-06, TRUST-07, LEARN-07-LITE) in SKILL.md
2. Verify old unconditional reset language is gone: `grep "any finding.*resets\|ANY pass reports findings" skills/forge/SKILL.md` should return nothing
3. Verify all 6 FP categories present: `grep -c "HALLUCINATION\|CONTEXT_MISSING\|INTENTIONAL\|NOT_APPLICABLE\|STYLE_PREFERENCE\|ACCEPTABLE_RISK" skills/forge/SKILL.md` should be >= 6
4. Verify validation sets: `grep "VALID_SEVERITIES\|VALID_DIMENSIONS" skills/forge/SKILL.md` returns matches
5. Verify subprocess for SHA: `grep "subprocess.check_output" skills/forge/SKILL.md` returns match
6. Verify P3 density escalation: `grep "p3_by_rule\|density.*0.15\|distinct.*rule" skills/forge/SKILL.md` returns matches
7. Verify feedback at commit gate: `grep "commit gate" skills/forge/SKILL.md` returns matches
8. Verify YAML frontmatter: `head -5 skills/forge/SKILL.md` should show `---` and `name: forge`
</verification>

<success_criteria>
The forge SKILL.md state machine is transformed from "any finding resets everything" to a severity-gated system with P3 accumulation thresholds. Clean passes auto-continue. Every finding is persisted to .forge/findings.json with validated data (severity, dimension, filepath checked). Feedback collection happens once at commit gate, not during passes. commit_sha is obtained via subprocess, not shell substitution. An executor following these instructions can modify SKILL.md without ambiguity.
</success_criteria>

<output>
After completion, create `.planning/phases/01a-trust-instrumentation/01a-01-SUMMARY.md`
</output>
