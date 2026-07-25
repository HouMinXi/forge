# Phase 42 Plan Review — External Model (Round 3, Final)

You are reviewing two PLAN.md files for forge Phase 42 (CLI key fast-fail + claim_type oracle).

## What was fixed in Round 2 (internal + external review)

Round 2 found and fixed these issues:
1. **FIXED (HIGH, internal)**: Tests 9-10 claimed to verify "machine.py wiring" but constructed LedgerRow directly. Fixed by adding Test 13: source-code wiring verification.
2. **FIXED (BLOCKER, kimi)**: LedgerRow is @dataclass(frozen=True) with no default fields. Inserting version_sensitive: bool = False between axis_claim and pass_provenance would cause TypeError. Fixed: field moved to END of dataclass (after ts).
3. **FIXED (HIGH, kimi)**: Test 13 did not assert version_sensitive presence in machine.py. Fixed: added assertion (c) to Test 13.
4. **FIXED (MEDIUM, kimi)**: injection #1 referenced wrong test (test_ledger_row bypasses machine.py). Fixed: corrected to reference Test 13.
5. **FIXED (MEDIUM, kimi)**: files_modified missing ledger.py. Fixed: added.
6. **FIXED (LOW, kimi)**: Test 9-10 import missing TerminalState. Fixed: added to import.

## Files to review

Read these plan files:
- .planning/phases/42-cli-key-claim-type/42-01-PLAN.md (F8 fast-fail extension)
- .planning/phases/42-cli-key-claim-type/42-02-PLAN.md (claim_type oracle)

Also read the ACTUAL source code to verify:
- src/code_forge/cli.py (lines 2390-2410)
- src/code_forge/backend.py (lines 80-113)
- src/code_forge/llm_invoke.py (lines 838-862)
- src/code_forge/machine.py (lines 1200-1219)
- src/code_forge/ledger.py (lines 40-54)
- src/code_forge/state.py (lines 66-86)
- src/code_forge/cli.py (lines 1314-1326)

## Review dimensions
1. Code accuracy: file:line references match actual code?
2. Logic correctness: guard extension correct? claim_type mapping complete?
3. Test coverage: acceptance criteria specific and mechanically verifiable?
4. Bug-injection correctness: removing code causes tests to FAIL?
5. Backward compat: LedgerRow.version_sensitive handles old rows?
6. Import/module paths: all imports resolvable?
7. Scope: plans stay within F8 + claim_type?

## Severity scale
- BLOCKER: prevents correct implementation; must fix
- HIGH: likely to cause bugs; should fix
- MEDIUM: clarity issue, missing detail
- LOW: nitpick, style

## Output format

```
## [Model Name] Review — Phase 42 Plans (Round 3)

### Findings
| # | Severity | Plan | Finding |
|---|----------|------|---------|

### Verdict: CLEAN — 0B/0H/0M/0L
```

Read BOTH plans AND source code. Verify every claim. Do NOT fabricate findings.
