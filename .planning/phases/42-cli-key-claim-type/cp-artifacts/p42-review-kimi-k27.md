You are reviewing two PLAN.md files for forge Phase 42. Your strength is L2: cross-plan data flow, requirements compliance, defensive-programming gaps, scope-type crashes.

## Plans to review

Read both plan files:
- .planning/phases/42-cli-key-claim-type/42-01-PLAN.md (F8 fast-fail extension)
- .planning/phases/42-cli-key-claim-type/42-02-PLAN.md (claim_type oracle)

## Review focus (your L2 strengths)

1. **Cross-plan data flow**: Do the two plans share any data paths? Are there conflicting transforms? Does Plan 01's guard change affect Plan 02's assumptions?
2. **Requirements compliance**: Does the plan satisfy F8 and 7.1 completely? Are there gaps between what CONTEXT.md requires and what the plan delivers?
3. **Defensive-programming**: Are error paths handled? What happens on unexpected input (unknown source value, missing file, permission denied)? Is the ValueError on unknown source the right failure mode?
4. **Integration boundaries**: Does the plan correctly wire between modules (cli.py -> backend.py, machine.py -> claim.py -> ledger.py)? Are import paths exact? Are there circular import risks?
5. **Scope-type crashes**: Does the plan touch anything outside its stated scope? Could a change in one plan break the other?

## Severity scale

Rate each finding:
- BLOCKER: prevents correct implementation; must fix before execute
- HIGH: likely to cause bugs or test failures; should fix
- MEDIUM: clarity issue, missing detail, potential confusion
- LOW: nitpick, style, nice-to-have

## Output format

```
## Kimi K2.7 Review — Phase 42 Plans

### Findings

| # | Severity | Plan | Finding |
|---|----------|------|---------|
| 1 | B/H/M/L | 01/02 | description |

### Verdict: CLEAN / N findings (B/H/M/L breakdown)
```

If CLEAN: `## VERDICT: CLEAN — 0B/0H/0M/0L`

Read the plans from disk and review thoroughly. Do NOT fabricate findings — only report what you actually found in the plan text.
