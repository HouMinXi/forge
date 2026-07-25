# Phase 42 Plan Review — External Model

You are reviewing two PLAN.md files for forge Phase 42 (CLI key fast-fail + claim_type oracle).

## What was fixed in Round 1 (internal review)

The internal adversarial review found 1 HIGH finding that was fixed:
- **FIXED (HIGH)**: Tests 9-10 in Plan 02 originally claimed to verify "machine.py wiring" but actually constructed LedgerRow directly, never exercising machine.py's _write_ledger_rows. Fixed by adding Test 13: a source-code wiring verification that greps machine.py for derive_claim_type import and asserts hardcoded "review" is gone.

## Files to review

Read these plan files:
- .planning/phases/42-cli-key-claim-type/42-01-PLAN.md (F8 fast-fail extension, 1 task)
- .planning/phases/42-cli-key-claim-type/42-02-PLAN.md (claim_type oracle, 2 tasks)

Also read the ACTUAL source code to verify plan claims:
- src/code_forge/cli.py (lines 2390-2410, the existing guard)
- src/code_forge/backend.py (lines 80-113, BackendConfig credential fields)
- src/code_forge/llm_invoke.py (lines 838-862, runtime key resolution)
- src/code_forge/machine.py (lines 1200-1219, _write_ledger_rows)
- src/code_forge/ledger.py (lines 40-54, LedgerRow)
- src/code_forge/state.py (lines 66-86, StateFinding source Literal)
- src/code_forge/cli.py (lines 1314-1326, manual ledger mark)

## Review dimensions

Check ALL of these:
1. **Code accuracy**: Do file:line references match actual code?
2. **Logic correctness**: Is the guard extension correct? Is the claim_type mapping complete?
3. **Test coverage**: Are acceptance criteria specific and mechanically verifiable?
4. **Bug-injection correctness**: Will removing the code actually cause tests to FAIL?
5. **Backward compat**: Does LedgerRow.version_sensitive handle old rows?
6. **Import/module paths**: Are all imports resolvable?
7. **Scope**: Does either plan touch anything outside F8 + claim_type?

## Severity scale
- BLOCKER: prevents correct implementation; must fix
- HIGH: likely to cause bugs or test failures; should fix
- MEDIUM: clarity issue, missing detail, potential confusion
- LOW: nitpick, style, nice-to-have

## Output format

```
## [Model Name] Review — Phase 42 Plans (Round 2)

### Findings
| # | Severity | Plan | Finding |
|---|----------|------|---------|

### Verdict: CLEAN — 0B/0H/0M/0L
```

If CLEAN: `## VERDICT: CLEAN — 0B/0H/0M/0L`

Read BOTH plans AND source code from disk. Verify every claim. Do NOT fabricate findings.
