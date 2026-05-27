---
name: kernel-fp-verify
description: "False-positive verification pass: validate findings from three-cycle review before committing. Step 3.5 between adversarial-qe and smoke test. Use after all 3 review passes produce findings, to verify each finding is real before fixing."
---

# False-Positive Verification Pass

**When to use**: After three-cycle review (qodo-review / code-review-expert / adversarial-qe) produces findings. Run this BEFORE fixing findings to filter out false positives.

**When NOT to use**: When all three passes report zero findings (LGTM). Skip directly to smoke test.

## Input

A list of findings from the three review passes, with severity and location.

## 10-Step Verification Protocol

For EACH finding, execute all 10 steps. A finding that fails any step is downgraded or dismissed.

### Step 1: Re-read the code

Read the actual file at the cited location using the Read tool. Confirm the code matches the finding's description. If the code does not match, dismiss immediately -- the finding is based on hallucinated or stale code.

### Step 2: Prove the path is reachable

Trace the execution path from an entry point to the flagged code. For each branch/condition in the path, verify it can evaluate to the required value. If the path requires conditions that are structurally impossible (not just unlikely), dismiss.

### Step 3: Identify the concrete failure mode

State exactly what goes wrong: crash, wrong output, data corruption, security breach. "This looks wrong" or "this could be a problem" is not a failure mode. If you cannot name a specific failure, dismiss.

### Step 4: Check full context (2-3 levels)

Read the callers (2-3 levels up) and callees (2-3 levels down) of the flagged code. Does surrounding code already prevent the failure? Does a caller validate input before reaching this point? Use grep to find all call sites.

### Step 5: Check patch series context

If reviewing a multi-patch series, search subsequent patches for fixes to this finding. A finding fixed in a later patch within the same series is still valid (each patch must be self-contained correct), but note the relationship.

### Step 6: Verify against independent ground truth

Check at least one independent reference: kernel source, spec document, upstream implementation, known test vector. Round-trip self-consistency alone does not count -- encoder and decoder can share the same bug.

### Step 7: Check for intentional design

Read comments, commit messages, and related documentation. Is this behavior intentional? A documented limitation or design choice is not a bug. But: do not trust comments alone -- verify the implementation matches.

### Step 8: Test complex multi-step conditions

If the failure requires multiple conditions to be true simultaneously, verify each condition can actually hold at the same time. Enumerate the conditions and check for mutual exclusion.

### Step 9: Anti-hallucination check

Re-read the code one more time. Confirm:
- Every function/variable/constant you referenced actually exists
- The line numbers in your analysis match the current file
- You did not imagine code that is not there

### Step 10: Debate yourself

Argue the author's perspective: why is this code correct? Then argue back as the reviewer: why is it still a bug despite the author's argument? Only report the finding if it survives both sides.

## Output Format

For each finding from the three-cycle review:

```
### Finding: <original title>
**Source**: <which pass reported it> (e.g., adversarial-qe cycle 2)
**Verdict**: CONFIRMED / DOWNGRADED / DISMISSED
**Evidence**: <1-3 sentences of concrete evidence>
**Steps failed**: <which of the 10 steps led to dismissal, if any>
```

Summary table at the end:

```
| Finding | Source | Verdict | Reason |
|---------|--------|---------|--------|
| ...     | ...    | ...     | ...    |
```

## Dismissal Rules

The following are NOT valid reasons to dismiss a finding:
- "The caller normally prevents this input"
- "This only happens if [upstream function] fails"
- "Extremely unlikely in practice"
- "I cannot construct a test case" (absence of test != absence of bug)

The following ARE valid reasons to dismiss:
- Code at the cited location does not match the finding (hallucination)
- The path is structurally unreachable (dead code, compile-time constant, type system prevents it)
- The behavior is documented as intentional AND the implementation matches
- A subsequent patch in the same series addresses it (note: still flag for patch-level correctness)

## Boundaries

- This pass does NOT find new issues. It only validates existing findings.
- Do not expand scope beyond the findings list.
- Do not fix code -- only classify findings as confirmed/dismissed.
