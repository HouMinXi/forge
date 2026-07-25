You are reviewing two PLAN.md files for forge Phase 42. Your strength is L3: runtime semantics, API facts, lifecycle/broadcast/type errors. You are the runtime precision sniper.

## Plans to review

Read both plan files:
- .planning/phases/42-cli-key-claim-type/42-01-PLAN.md (F8 fast-fail extension)
- .planning/phases/42-cli-key-claim-type/42-02-PLAN.md (claim_type oracle)

Also read the referenced source code to verify plan accuracy:
- src/code_forge/cli.py (lines 2390-2410, the existing guard)
- src/code_forge/llm_invoke.py (lines 838-862, runtime key resolution)
- src/code_forge/backend.py (lines 80-113, BackendConfig)
- src/code_forge/machine.py (lines 1200-1219, _write_ledger_rows)
- src/code_forge/ledger.py (lines 40-54, LedgerRow)
- src/code_forge/state.py (lines 66-86, StateFinding)

## Review focus (your L3 strengths)

1. **Runtime semantics**: Will the plan's code actually work at runtime? Are there edge cases the plan misses (e.g., Path.read_text() on a binary file, concurrent access to api_key_file)?
2. **Type errors**: Does the plan respect Python type contracts? Is ClaimType frozen dataclass correctly used? Does LedgerRow deserialization handle the new field correctly?
3. **API correctness**: Does the plan correctly use BackendConfig fields? Is the elif chain logic correct (api_key_env XOR api_key_file)?
4. **Lifecycle issues**: Does the guard run at the right time in the CLI lifecycle? Could the guard run before backend resolution is complete?
5. **Error message quality**: Are the CliError messages specific enough for users to fix the problem?

## Severity scale

Rate each finding:
- BLOCKER: prevents correct implementation; must fix before execute
- HIGH: likely to cause bugs or test failures; should fix
- MEDIUM: clarity issue, missing detail, potential confusion
- LOW: nitpick, style, nice-to-have

## Output format

```
## Gemini Review — Phase 42 Plans

### Findings

| # | Severity | Plan | Finding |
|---|----------|------|---------|
| 1 | B/H/M/L | 01/02 | description |

### Verdict: CLEAN / N findings (B/H/M/L breakdown)
```

If CLEAN: `## VERDICT: CLEAN — 0B/0H/0M/0L`

Read the plans AND source code from disk. Verify plan claims against actual code. Do NOT fabricate findings.
