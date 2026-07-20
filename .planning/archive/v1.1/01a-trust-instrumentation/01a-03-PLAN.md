---
phase: 01a-trust-instrumentation
plan: 03
type: execute
wave: 2
depends_on:
  - 01a-01
files_modified:
  - skills/forge/SKILL.md
  - hooks/check_review_tracker.sh
autonomous: true
requirements:
  - FUSE-01
  - TRUST-07

must_haves:
  truths:
    - "Step 0 findings are serialized as a markdown table and injected into the system prompt for LLM passes (Steps 1-3)"
    - "LLM passes receive explicit instruction to NOT re-flag issues already detected by Step 0"
    - "Step 0 findings table is capped at 20 rows; if more, truncated with count note"
    - "The check_review_tracker.sh hook understands severity levels (P0/P1/P2/P3) and applies severity-gated reset logic"
    - "Hook detection covers all three passes (qodo-review, code-review-expert, adversarial-qe), not just qodo"
    - "Sidecar write failures log warnings to stderr, not silently pass"
    - "Hook uses content anchors (grep headings) for SKILL.md modifications, not line numbers"
  artifacts:
    - path: "skills/forge/SKILL.md"
      provides: "Step 0 Context Fusion protocol (FUSE-01) with size cap"
      contains: "Step 0 Context Fusion"
    - path: "hooks/check_review_tracker.sh"
      provides: "Severity-aware finding detection for all 3 passes, with logged sidecar failures"
      contains: "_max_severity"
  key_links:
    - from: "skills/forge/SKILL.md Step 0"
      to: "skills/forge/SKILL.md Steps 1-3"
      via: "Serialized Step 0 findings as context block"
      pattern: "Step 0 Findings"
    - from: "hooks/check_review_tracker.sh"
      to: ".forge/current_session.json"
      via: "Sidecar file with severity data (write failures logged)"
      pattern: "current_session"
---

<objective>
Add Step 0 context fusion (FUSE-01) to SKILL.md and upgrade check_review_tracker.sh to support severity-aware finding detection for all three review passes.

Purpose: FUSE-01 prevents redundant flagging by passing deterministic Step 0 findings as context to LLM passes. The hook upgrade ensures the enforcement layer understands the new severity-gated state machine from Plan 01, replacing boolean has_findings with severity-level detection, and covering all three passes (not just qodo).

Output: Modified SKILL.md with FUSE-01 protocol (size-capped), modified hook with severity-aware logic for all passes.

Review fixes addressed:
- Issue #7 (MEDIUM): Hook detection extended to code-review-expert and adversarial-qe, not just qodo
- Issue #9 (MEDIUM): FUSE-01 context table capped at 20 rows
- Issue #12 (MEDIUM): SKILL.md modifications use content anchors, not line numbers
- Issue #15 (MEDIUM): Sidecar write failures log to stderr instead of silent pass
- Issue #16 (MEDIUM): Chinese strings use literal characters matching existing code style
- DeepSeek HIGH #1: current_session.json integration -- SKILL.md reads sidecar for enforcement
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
@.planning/phases/01a-trust-instrumentation/01a-01-SUMMARY.md
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add Step 0 Context Fusion protocol (FUSE-01) with size cap to SKILL.md</name>
  <files>skills/forge/SKILL.md</files>
  <read_first>
    - skills/forge/SKILL.md (as modified by Plan 01 -- read the full file to see current state)
    - .planning/phases/01a-trust-instrumentation/01a-CONTEXT.md (D6: Step 0 findings serialized as context block)
    - .planning/phases/01a-trust-instrumentation/01a-RESEARCH.md (Pattern 4: Step 0 Context Fusion)
    - .planning/phases/01a-trust-instrumentation/01a-01-SUMMARY.md (what Plan 01 changed)
  </read_first>
  <action>
IMPORTANT (addresses review issue #12 -- content anchors, not line numbers):
Do NOT reference line numbers when locating insertion points. Use grep to find the exact heading text and insert relative to it. For example:
- Insert AFTER the section containing the heading "## Step 0 Gate"
- Insert BEFORE the section containing the heading "# Steps 1-3"

Add a new section to SKILL.md. Locate insertion point by grepping for "## Step 0 Gate" (insert after this section ends, before "# Steps 1-3: Three-Cycle Static Review"). Title: `## Step 0 Context Fusion (FUSE-01)`.

CRITICAL (addresses review issue #9 -- FUSE-01 context size limit):
The Step 0 findings table MUST be capped at 20 rows. If Step 0 produces more than 20 findings, show the first 20 and append a truncation note.

Content to add:

```markdown
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
```

Also, update the Execution Protocol section. Locate it by grepping for "# Execution Protocol". Modify step 3:

Replace (grep for "**Run Step 0**: syntax + lint + non-ASCII"):
```
3. **Run Step 0**: syntax + lint + non-ASCII. Stop on any failure.
```
With:
```
3. **Run Step 0**: syntax + lint + non-ASCII. Stop on any failure. After all Step 0 checks pass, serialize findings into FUSE-01 context block for LLM passes (cap at 20 rows).
```

Locate the Adaptive Mechanisms section by grepping for "# Adaptive Mechanisms". Add the next sequential item (after the items added by Plan 01):

```
14. **Step 0 Context Fusion (FUSE-01)**: deterministic Step 0 findings are serialized as a markdown table (capped at 20 rows) and injected into every LLM pass prompt. This prevents redundant flagging and lets LLM passes focus on issues that static tools cannot catch.
```

Also, add a SKILL.md instruction to read current_session.json (addresses DeepSeek HIGH #1 -- sidecar integration):

After the "## Finding Persistence (TRUST-01)" section (locate by grepping for the heading), add:

```markdown
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
```
  </action>
  <verify>
    <automated>grep -c "Step 0 Context Fusion" skills/forge/SKILL.md && grep -c "FUSE-01" skills/forge/SKILL.md && grep -c "Do NOT re-flag" skills/forge/SKILL.md && grep -c "deterministic, already addressed" skills/forge/SKILL.md && grep -c "cap.*20\|20 rows\|first 20" skills/forge/SKILL.md && grep -c "current_session.json" skills/forge/SKILL.md && grep -c "Session State" skills/forge/SKILL.md</automated>
  </verify>
  <acceptance_criteria>
    - skills/forge/SKILL.md contains section header "## Step 0 Context Fusion (FUSE-01)"
    - Section appears AFTER "## Step 0 Gate" and BEFORE "# Steps 1-3"
    - Contains the markdown table template with columns: #, File, Line, Tool, Issue
    - Contains the instruction "Do NOT re-flag these specific issues"
    - Contains the 4 rules for LLM passes when receiving Step 0 context
    - Contains size cap at 20 rows with truncation note (addresses review issue #9)
    - Contains the zero-findings variant ("Step 0 checks... found zero issues")
    - Contains "## Session State (Hook Integration)" section with current_session.json description (addresses DeepSeek HIGH #1)
    - Execution Protocol step 3 mentions "FUSE-01 context block" and "cap at 20 rows"
    - Adaptive Mechanisms list includes item about "Step 0 Context Fusion (FUSE-01)"
    - SKILL.md YAML frontmatter (name: forge) is intact
    - No line-number references in the plan action (content anchors only per review issue #12)
  </acceptance_criteria>
  <done>SKILL.md contains the FUSE-01 protocol with 20-row cap, session state integration for current_session.json, and updated execution protocol. Review issues #9, #12, and DeepSeek HIGH #1 are addressed.</done>
</task>

<task type="auto">
  <name>Task 2: Upgrade check_review_tracker.sh to severity-aware detection for all 3 passes</name>
  <files>hooks/check_review_tracker.sh</files>
  <read_first>
    - hooks/check_review_tracker.sh (current 294 lines -- read the full file)
    - .planning/phases/01a-trust-instrumentation/01a-CONTEXT.md (D3: severity-gated cycle reset)
    - .planning/phases/01a-trust-instrumentation/01a-RESEARCH.md (Pattern 2: Severity-Gated Cycle Reset)
    - .planning/phases/01a-trust-instrumentation/01a-PATTERNS.md (hook patterns: _has_findings, _save_state, _load_state)
  </read_first>
  <action>
Modify hooks/check_review_tracker.sh to support severity-aware finding detection for ALL THREE review passes (not just qodo).

CRITICAL (addresses review issue #7 -- hook detection for all 3 passes):
The current `_is_real_qodo()` function only detects qodo invocations. Add a new `_is_review_pass()` function that detects ANY of the three review passes: qodo-review, code-review-expert, adversarial-qe. This ensures severity-gated state machine applies to all three passes, not just qodo.

CRITICAL (addresses review issue #15 -- silent sidecar write failure):
Replace `except Exception: pass` with logging a warning to stderr. A silent failure means the state machine behaves incorrectly without explanation.

CRITICAL (addresses review issue #16 -- Chinese encoding in hook):
The existing `_has_findings()` function (lines 128-168) already uses LITERAL Chinese characters (not byte escapes). Use the same style for `_is_review_pass()` and `_max_severity()` -- literal Chinese characters. Read the existing file first to confirm this style, then match it exactly. The executor must look at the existing Chinese patterns in `_has_findings()` and use the same encoding approach.

**Modification 1: Add `_is_review_pass()` function**

Add a new function after `_is_real_qodo()` that detects all three review passes:

```python
def _is_review_pass(cmd, output):
    """Check if this Bash command was any of the three review passes.

    Detects: qodo-review (Pass 1), code-review-expert (Pass 2),
    adversarial-qe (Pass 3). Returns the pass name or None.

    Addresses review issue #7: severity-gated state machine must
    apply to all three passes, not just qodo.
    """
    # Check for qodo (existing detection)
    if _is_real_qodo(cmd, output):
        return 'qodo-review'

    # Check for code-review-expert (Pass 2)
    # English markers
    code_review_markers = [
        r'code.review.expert',
        r'/code-review-expert',
        r'SOLID.*architecture',
        r'P[0-3]\s+(?:Critical|High|Medium|Low)',
    ]
    # Chinese markers -- use literal Chinese matching the style
    # of existing _has_findings() patterns in this same file.
    # The executor should read lines 104-109 of the existing file
    # to see the Chinese character style, then add Chinese markers
    # for "architecture" and "code review expert" in that same style.
    code_review_cn = [
        # EXECUTOR: add literal Chinese for these concepts:
        # "architecture" (jia gou)
        # "code review expert" (dai ma shen cha zhuan jia)
        # "SOLID"
    ]
    has_code_review = (
        any(re.search(m, output, re.IGNORECASE) for m in code_review_markers)
        or any(marker in output for marker in code_review_cn)
    )
    if has_code_review and len(output) >= 500:
        return 'code-review-expert'

    # Check for adversarial-qe (Pass 3)
    adversarial_markers = [
        r'adversarial.qe',
        r'/adversarial-qe',
        r'red.team',
        r'12\s+(?:attack\s+)?dimensions?',
        r'(?:Critical|High|Medium|Low|Nit)\s+severity',
    ]
    # Chinese markers for adversarial-qe -- same literal style
    # EXECUTOR: add literal Chinese for these concepts:
    # "adversarial" (dui kang)
    # "red team" (hong dui)
    # "attack dimensions" (gong ji wei du)
    adversarial_cn = []
    has_adversarial = (
        any(re.search(m, output, re.IGNORECASE) for m in adversarial_markers)
        or any(marker in output for marker in adversarial_cn)
    )
    if has_adversarial and len(output) >= 500:
        return 'adversarial-qe'

    return None
```

NOTE TO EXECUTOR: The Chinese character lists (`code_review_cn` and `adversarial_cn`) are left as comments because this plan file must be ASCII-only. Read the existing `_has_findings()` function in the same file (around lines 104-168) to see the literal Chinese character style used there. Add the Chinese markers for code-review-expert and adversarial-qe using that same encoding style. The specific terms to add are documented in the comments above each list.

**Modification 2: Add `_max_severity()` function**

Add after the existing `_has_findings()` function. This returns the highest severity found:

```python
def _max_severity(output):
    """Parse review output to determine highest severity level.

    Returns: 'P0', 'P1', 'P2', 'P3', or 'none' (no findings).
    Used for severity-gated cycle reset (TRUST-07).
    """
    # P0 signals
    p0_signals = [
        r'\bP0\b',
        r'\bcritical\b.*\b(?:security|crash|data.loss)',
    ]
    if any(re.search(s, output, re.IGNORECASE) for s in p0_signals):
        return 'P0'

    # P1 signals (English)
    p1_signals = [
        r'\bP1\b',
        r'\bmust\s+fix\b',
        r'\bhigh\s+risk\b',
    ]
    # P1 signals (Chinese) -- EXECUTOR: use the same literal Chinese style
    # as in _has_findings(). Add the Chinese terms that already exist in
    # _has_findings() for finding signals: "bi xiu" (must fix),
    # "gao feng xian" (high risk), "yan zhong wen ti" (serious problem).
    # These are already on lines ~128-135 of the existing hook file.
    p1_cn = []  # EXECUTOR: populate with literal Chinese from existing patterns
    if any(re.search(s, output, re.IGNORECASE) for s in p1_signals):
        return 'P1'
    if any(marker in output for marker in p1_cn):
        return 'P1'

    # P2 signals (English)
    p2_signals = [
        r'\bP2\b',
        r'\bshould\s+fix\b',
        r'REQUEST_CHANGES',
    ]
    # P2 signals (Chinese) -- EXECUTOR: add literal Chinese for
    # "jian yi xiu fu" (suggest fix), "ying gai xiu gai" (should modify).
    # These are already on lines ~131-132 of the existing hook file.
    p2_cn = []  # EXECUTOR: populate with literal Chinese from existing patterns
    if any(re.search(s, output, re.IGNORECASE) for s in p2_signals):
        return 'P2'
    if any(marker in output for marker in p2_cn):
        return 'P2'

    # Check if there are any findings at all (using existing _has_findings logic)
    if _has_findings(output):
        return 'P3'  # has findings but none matched P0/P1/P2 = style nits

    return 'none'
```

**Modification 3: Update state structure**

Update the `_load_state()` default dict to include new fields:

Add `'last_max_severity': 'none'` and `'last_review_pass': ''` to the default state dict.

**Modification 4: Update PostToolUse Bash handler**

Replace the current PostToolUse handler logic that only checks `_is_real_qodo()` with the new `_is_review_pass()` that covers all three passes:

In the PostToolUse Bash handler, replace:
```python
if tool == 'Bash':
    cmd = tinput.get('command', '')
    if not _is_real_qodo(cmd, tresult):
        sys.exit(0)
```

With:
```python
if tool == 'Bash':
    cmd = tinput.get('command', '')
    pass_name = _is_review_pass(cmd, tresult)
    if pass_name is None:
        sys.exit(0)
```

Then inside the lock section, after loading state:
```python
        findings = _has_findings(tresult)
        severity = _max_severity(tresult)
        st['last_qodo_has_findings'] = findings
        st['last_max_severity'] = severity
        st['last_review_pass'] = pass_name
```

Update the rounds_with_findings counter to be severity-aware:
```python
        if findings:
            if had_mods or st['qodo_runs'] == 1:
                # Only count rounds with P0/P1/P2 toward hard stop (TRUST-07)
                # P3-only rounds do not count
                if severity in ('P0', 'P1', 'P2'):
                    st['rounds_with_findings'] += 1
            st['review_passed'] = False
```

Update reporting to include severity and pass name:
```python
    if findings:
        print(
            f"REVIEW TRACKER: {pass_name} run #{run_num} detected findings "
            f"(max severity: {severity}). Rounds with findings: {rounds_num}/3.",
            file=sys.stderr
        )
    else:
        print(
            f"REVIEW TRACKER: {pass_name} run #{run_num} passed clean. "
            f"Review status: PASSED.",
            file=sys.stderr
        )
```

**Modification 5: Write severity data to sidecar file with error logging**

After `_save_state(st)`, write to `.forge/current_session.json`. CRITICAL (review issue #15): Log warnings on write failure instead of silent pass:

```python
        _save_state(st)

        # Write severity data for SKILL.md state machine consumption
        session_file = os.path.join('.forge', 'current_session.json')
        os.makedirs('.forge', exist_ok=True)
        session_data = {
            'last_max_severity': severity,
            'last_review_pass': pass_name,
            'qodo_runs': st['qodo_runs'],
            'rounds_with_findings': st['rounds_with_findings'],
        }
        try:
            s_fd, s_tmp = tempfile.mkstemp(dir='.forge', suffix='.json')
            with os.fdopen(s_fd, 'w') as sf:
                json.dump(session_data, sf)
            os.replace(s_tmp, session_file)
        except Exception as e:
            # Addresses review issue #15: log warning, don't silently pass
            print(
                f"REVIEW TRACKER WARNING: failed to write sidecar "
                f"{session_file}: {e}",
                file=sys.stderr
            )
```

**Modification 6: Update header comment**

Update the header comment at the top of the file to reflect new behavior:

Add to state machine description:
```bash
#   - last_max_severity: highest severity from last review pass (P0/P1/P2/P3/none)
#   - last_review_pass: which pass was last detected (qodo-review/code-review-expert/adversarial-qe)
```

And update:
```bash
# Hard stop: after 3 rounds with P0/P1/P2 findings -> block edits
#            (P3-only rounds do not count toward hard stop)
# Detection: all 3 passes (qodo-review, code-review-expert, adversarial-qe)
```
  </action>
  <verify>
    <automated>bash -n hooks/check_review_tracker.sh && echo "syntax OK" && grep -c "_max_severity" hooks/check_review_tracker.sh && grep -c "_is_review_pass" hooks/check_review_tracker.sh && grep -c "last_max_severity" hooks/check_review_tracker.sh && grep -c "current_session" hooks/check_review_tracker.sh && grep -c "TRUST-07" hooks/check_review_tracker.sh && grep -c "code-review-expert" hooks/check_review_tracker.sh && grep -c "adversarial-qe" hooks/check_review_tracker.sh && grep -c "WARNING.*failed to write sidecar" hooks/check_review_tracker.sh</automated>
  </verify>
  <acceptance_criteria>
    - hooks/check_review_tracker.sh passes `bash -n` (no syntax errors)
    - Contains function `_is_review_pass(cmd, output)` that detects all 3 passes and returns pass name or None (addresses review issue #7)
    - Contains function `_max_severity(output)` that returns one of 'P0', 'P1', 'P2', 'P3', 'none'
    - `_max_severity` checks P0 before P1 before P2 (severity hierarchy)
    - `_max_severity` uses literal Chinese characters matching existing code style in `_has_findings()` (addresses review issue #16)
    - `_max_severity` falls back to `_has_findings()` for P3 detection
    - `_is_review_pass` detects code-review-expert via markers like "SOLID", "architecture", Chinese markers
    - `_is_review_pass` detects adversarial-qe via markers like "red team", "12 dimensions", Chinese markers
    - State dict includes `'last_max_severity': 'none'` and `'last_review_pass': ''` in defaults
    - PostToolUse handler uses `_is_review_pass` instead of only `_is_real_qodo` for dispatch
    - PostToolUse handler calls `_max_severity(tresult)` and stores result
    - PostToolUse handler writes `.forge/current_session.json` sidecar file
    - Sidecar write failure prints WARNING to stderr, NOT silently passes (addresses review issue #15)
    - rounds_with_findings only increments for P0/P1/P2 (comment references TRUST-07)
    - Header comment includes `last_max_severity` and `last_review_pass` fields
    - Header comment says "all 3 passes" and "P0/P1/P2 findings"
    - Existing `_has_findings()` and `_is_real_qodo()` functions are preserved unchanged
    - All existing functionality (edit counting, hard stop, lock file) is preserved
  </acceptance_criteria>
  <done>check_review_tracker.sh detects all three review passes (qodo, code-review-expert, adversarial-qe), detects severity levels (P0-P3), writes sidecar with logged failures, and only counts P0/P1/P2 toward hard stop. Review issues #7, #15, #16 and DeepSeek HIGH #1 addressed.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Step 0 output -> LLM prompt | Deterministic findings serialized into LLM context |
| Review output text -> severity parsing | Regex parsing of unstructured text for severity classification |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-01a-07 | S (Spoofing) | Step 0 context injection | accept | Context is locally generated by Step 0 tools (shellcheck, pylint), not external input |
| T-01a-08 | T (Tampering) | .forge/current_session.json | accept | Sidecar is informational; state machine in SKILL.md makes decisions, hook provides signal |
| T-01a-09 | D (Denial of Service) | _max_severity regex | accept | Regex patterns are simple (no backtracking); output size bounded by terminal buffer |
| T-01a-10 | I (Information Disclosure) | Sidecar failure logging | accept | Warning messages go to stderr (not stored), contain only filepath, no sensitive data |
</threat_model>

<verification>
1. `bash -n hooks/check_review_tracker.sh` passes
2. `grep "_max_severity\|_is_review_pass" hooks/check_review_tracker.sh` returns both function definitions
3. `grep "FUSE-01" skills/forge/SKILL.md` returns 3+ matches
4. `grep "current_session" hooks/check_review_tracker.sh` returns sidecar file references
5. `grep "WARNING.*failed" hooks/check_review_tracker.sh` returns warning message (not silent pass)
6. `grep "20 rows\|first 20\|cap.*20" skills/forge/SKILL.md` returns FUSE-01 size cap
7. `grep "code-review-expert\|adversarial-qe" hooks/check_review_tracker.sh` returns detection markers
8. The hook still works with existing state files (backward compatible)
</verification>

<success_criteria>
FUSE-01 is implemented in SKILL.md with 20-row size cap. Session state integration reads current_session.json. The hook detects all three review passes (not just qodo), is severity-aware (P0-P3), writes severity data to sidecar with logged failures, and only counts P0/P1/P2 rounds toward hard stop. Chinese strings use literal characters matching existing code style. SKILL.md modifications used content anchors, not line numbers.
</success_criteria>

<output>
After completion, create `.planning/phases/01a-trust-instrumentation/01a-03-SUMMARY.md`
</output>
