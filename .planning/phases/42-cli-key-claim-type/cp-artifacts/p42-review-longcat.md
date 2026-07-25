You are reviewing two PLAN.md files for forge Phase 42. Your strength is L3: code-line verification, cross-validation, concrete fix proposals. You are the code-grounded verifier.

## Plans to review

Read both plan files:
- .planning/phases/42-cli-key-claim-type/42-01-PLAN.md (F8 fast-fail extension)
- .planning/phases/42-cli-key-claim-type/42-02-PLAN.md (claim_type oracle)

Also read the ACTUAL source code to verify every plan claim:
- src/code_forge/cli.py (lines 2390-2410, the existing guard)
- src/code_forge/llm_invoke.py (lines 838-862, runtime key resolution)
- src/code_forge/backend.py (lines 80-113, BackendConfig credential fields)
- src/code_forge/machine.py (lines 1200-1219, _write_ledger_rows)
- src/code_forge/ledger.py (lines 40-54, LedgerRow)
- src/code_forge/state.py (lines 66-86, StateFinding source Literal)
- src/code_forge/cli.py (lines 1314-1326, manual ledger mark)

## Review focus (your L3 strengths)

1. **Code-line verification**: Does the plan's description of existing code match what's ACTUALLY there? Verify every file:line reference against the real code. If the plan says "cli.py:2396" — is the guard really at line 2396?
2. **Cross-validation**: Do the two plans' claims about shared code agree? Does Plan 01's description of BackendConfig match Plan 02's?
3. **Concrete fix proposals**: If you find an issue, propose the exact fix (code snippet or specific line change).
4. **Bug-injection correctness**: Are the bug-injection sites correct? Will removing the code actually cause the test to fail? Or is there a false green risk (e.g., the test passes without the guard because of some other path)?
5. **Import/module correctness**: Are import paths exact? Will `from .claim import derive_claim_type` work? Is `claim.py` in the right package?

## Severity scale

Rate each finding:
- BLOCKER: prevents correct implementation; must fix before execute
- HIGH: likely to cause bugs or test failures; should fix
- MEDIUM: clarity issue, missing detail, potential confusion
- LOW: nitpick, style, nice-to-have

## Output format

```
## LongCat Review — Phase 42 Plans

### Findings

| # | Severity | Plan | Finding |
|---|----------|------|---------|
| 1 | B/H/M/L | 01/02 | description |

### Verdict: CLEAN / N findings (B/H/M/L breakdown)
```

If CLEAN: `## VERDICT: CLEAN — 0B/0H/0M/0L`

Read BOTH the plans AND the actual source code. Verify every claim against disk. Do NOT fabricate findings.
