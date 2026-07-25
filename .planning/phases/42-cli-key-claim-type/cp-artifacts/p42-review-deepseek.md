You are reviewing two PLAN.md files for forge Phase 42. Your strength is L1: coverage breadth, doc/implementer-readiness, pattern/perf/dead-code analysis.

## Plans to review

Read both plan files:
- .planning/phases/42-cli-key-claim-type/42-01-PLAN.md (F8 fast-fail extension)
- .planning/phases/42-cli-key-claim-type/42-02-PLAN.md (claim_type oracle)

## Review focus (your L1 strengths)

1. **Coverage breadth**: Does every decision in CONTEXT.md have a plan? Are there source items (REQUIREMENTS, ROADMAP goal, CONTEXT decisions) not covered by any plan?
2. **Implementer-readiness**: Could an implementer build this without asking questions? Are file paths exact? Are function signatures complete? Are imports specified?
3. **Doc gaps**: Are acceptance criteria specific enough to verify mechanically? Do they use exact strings, test commands, observable behavior?
4. **Dead code / pattern issues**: Does the plan introduce unused imports, unreachable branches, or duplicate logic?

## Severity scale

Rate each finding:
- BLOCKER: prevents correct implementation; must fix before execute
- HIGH: likely to cause bugs or test failures; should fix
- MEDIUM: clarity issue, missing detail, potential confusion
- LOW: nitpick, style, nice-to-have

## Output format

```
## DeepSeek Review — Phase 42 Plans

### Findings

| # | Severity | Plan | Finding |
|---|----------|------|---------|
| 1 | B/H/M/L | 01/02 | description |

### Verdict: CLEAN / N findings (B/H/M/L breakdown)
```

If CLEAN: `## VERDICT: CLEAN — 0B/0H/0M/0L`

Read the plans from disk and review thoroughly. Do NOT fabricate findings — only report what you actually found in the plan text.
