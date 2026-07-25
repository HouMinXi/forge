# Phase 42 Plan Review — Gemini (Manual Relay)

You are reviewing two PLAN.md files for forge Phase 42 (CLI key fast-fail + claim_type oracle). Your strength is L3: runtime semantics, API facts, lifecycle/type errors.

## Fixes applied across all rounds

- Round 1 (internal): Added Test 13 source-code wiring verification
- Round 2 (kimi): BLOCKER — moved version_sensitive to END of LedgerRow (frozen dataclass); HIGH — added version_sensitive assertion to Test 13; MEDIUM — corrected injection refs, added ledger.py to files_modified, added TerminalState import
- Round 3 (kimi): MEDIUM — corrected injection description (tests 9-10 bypass machine.py, only Test 13 catches); MEDIUM — extracted guard into _check_backend_credentials helper; LOW — refined XOR description
- Final (kimi): MEDIUM — fixed acceptance/verification stale "L0 test"/"L1 test" refs → "test 13"; MEDIUM — added type="api" to BackendConfig example; LOW — "retry loop" → "per-pass key resolution"; LOW — line 1211 → grep-based check

## Files to review

Read these plan files from disk:
- .planning/phases/42-cli-key-claim-type/42-01-PLAN.md (F8 fast-fail extension, 1 task)
- .planning/phases/42-cli-key-claim-type/42-02-PLAN.md (claim_type oracle, 2 tasks)

Also read the ACTUAL source code to verify plan claims:
- src/code_forge/cli.py (lines 2390-2410, the existing guard)
- src/code_forge/backend.py (lines 80-113, BackendConfig credential fields; lines 310-320, XOR enforcement)
- src/code_forge/llm_invoke.py (lines 838-862, runtime key resolution)
- src/code_forge/machine.py (lines 1200-1219, _write_ledger_rows with hardcoded axis_claim="review" at 1211)
- src/code_forge/ledger.py (lines 40-54, LedgerRow @dataclass(frozen=True), all fields no defaults)
- src/code_forge/state.py (lines 66-86, StateFinding.source Literal with 7 values)
- src/code_forge/cli.py (lines 1314-1326, manual ledger mark with axis_claim="manual")

## Review dimensions

1. **Runtime semantics**: Will the plan's code actually work at runtime? Edge cases (Path.read_text on binary file, concurrent access, permission denied)?
2. **Type errors**: Does the plan respect Python type contracts? ClaimType frozen dataclass correct? LedgerRow deserialization handles new field?
3. **API correctness**: Does the plan correctly use BackendConfig fields? Is the elif chain logic correct (api_key_env XOR api_key_file for non-vertex)?
4. **Lifecycle issues**: Does the guard run at the right time in CLI lifecycle? Could it run before backend resolution?
5. **Error message quality**: Are CliError messages specific enough for users to fix?
6. **Test coverage**: Acceptance criteria mechanically verifiable? Bug-injection correct?
7. **Backward compat**: LedgerRow.version_sensitive defaults handle old rows?
8. **Scope**: Plans stay within F8 + claim_type?

## Severity scale
- BLOCKER: prevents correct implementation; must fix
- HIGH: likely to cause bugs; should fix
- MEDIUM: clarity issue, missing detail
- LOW: nitpick, style

## Output format

```
## Gemini Review — Phase 42 Plans (Final)

### Findings
| # | Severity | Plan | Finding |
|---|----------|------|---------|

### Verdict: CLEAN — 0B/0H/0M/0L
```

If CLEAN: `## VERDICT: CLEAN — 0B/0H/0M/0L`

Read BOTH plans AND source code from disk. Verify every claim against actual code. Do NOT fabricate findings.
