# Phase 28 v3 Cross-AI Review Consolidation

**Date:** 2026-06-24
**Round:** 3 (post-v3 replan)
**Reviewers:** DeepSeek V4 Pro, MiMo v2.5 Pro, Kimi K2.7 Code, Gemini 3.1 Pro + 2 cold agents (plan-checker, code-reviewer)

## Overall Verdicts

| Reviewer | Verdict | Blockers | Warnings | Notes |
|----------|---------|----------|----------|-------|
| DeepSeek V4 Pro | REQUEST_CHANGES | 1 | 2 | 1 |
| MiMo v2.5 Pro | REQUEST_CHANGES | 2 | 2 | 4 |
| Kimi K2.7 Code | REQUEST_CHANGES | 1 | 0 | 2 |
| Gemini 3.1 Pro | REQUEST_CHANGES | 3 | 1 | 0 |
| Cold plan-checker | ISSUES FOUND | 2 | 3 | 0 |
| Cold code-reviewer | BLOCK | 1 | 3 | 2 |

## Round 2 MUST-FIX Verification: ALL 7 CONFIRMED RESOLVED

All 6 reviewers independently confirmed MF2-1 through MF2-7 are substantively fixed:

| MF2# | Status | Unanimous |
|------|--------|-----------|
| MF2-1 validate_canary_findings replaces validate_reviewer_json | RESOLVED | 6/6 |
| MF2-2 depends_on: [28-02], no Verdict fallback | RESOLVED | 6/6 |
| MF2-3 _load_gate_backends returns tuple (with caveats N3-1/N3-2) | RESOLVED | 6/6 |
| MF2-4 args.mode dead code removed, git diff HEAD | RESOLVED | 6/6 |
| MF2-5 _canary_provider prompt includes "original" | RESOLVED | 6/6 |
| MF2-6 textwrap.dedent in is_non_equivalent | RESOLVED | 6/6 |
| MF2-7 Template fallback documented as degraded-quality path | RESOLVED | 6/6 |

Round 2 SHOULD-FIX: ALL 4 CONFIRMED RESOLVED (SF2-1 through SF2-4).

---

## Remaining Issues (ALL from cold agent N3 findings -- no new external discoveries)

### MF3-1: _load_gate_backends has 3 call sites, Plan 03 only updates 1

**Consensus:** 6/6 BLOCKER (all reviewers + both cold agents)
**Location:** Plan 03 Task 1 step 2; cli.py lines 826, 1298, 2281
**Finding:** Plan 03 changes return type to tuple but only unpacks at line 1298. Lines 826 (_run_eval) and 2281 (_run_resolve_outlet) will TypeError.
**Fix:** Add to Plan 03 Task 1 step 2:
- Line 826: `_eval_cfgs, _ = _load_gate_backends(_gate_path)`
- Line 2281: `cfgs, _ = _load_gate_backends(gate_yaml_path)`
- Add acceptance criterion: `grep -n '_load_gate_backends' src/code_forge/cli.py` shows exactly 4 lines (1 def + 3 calls), all 3 calls unpack tuple.

### MF3-2: Untrusted path returns ([], gd) leaking untrusted gate_data

**Consensus:** 6/6 (DS WARNING, MiMo BLOCKER, Kimi HIGH, Gemini BLOCKER, both cold agents)
**Location:** Plan 03 Task 1 step 1, line 133 untrusted path
**Finding:** Returning ([], gd) on untrusted path leaks unvalidated gate.yaml data to _load_canary_config. A malicious repo could force-enable canary.
**Fix:** Untrusted path returns ([], {}). Only trusted path returns (cfgs, gd).
Acceptance criterion: "untrusted repo returns ([], {}) -- gate_data is empty dict"

### SF3-1: _canary_provider prompt hardcodes "5 mutations" but n is configurable 3-5

**Consensus:** 6/6 WARNING
**Location:** Plan 03 Task 1 step 6 _canary_provider prompt
**Finding:** Prompt says "generate 5" but n=canary_config.get("n", 5) can be 3 or 4.
**Fix:** Use f-string: `f"generate {n} subtle semantic mutations..."`. Extract n before closure definition.

### SF3-2: validate_canary_findings immutability contract not explicit

**Consensus:** 6/6 (DS NOTE, MiMo WARNING, Kimi MEDIUM, Gemini BLOCKER, both cold agents)
**Location:** Plan 01 Task 1 validate_canary_findings spec
**Finding:** "drops the finding" is ambiguous -- return new list or mutate input?
**Fix:** Add: "Returns a NEW list. Input list NEVER mutated." Add test: `test_validate_canary_findings_immutable`.

---

## Action Items Summary

| # | Severity | Finding | Fix Size | Plans |
|---|----------|---------|----------|-------|
| MF3-1 | MUST-FIX | 3 call sites, only 1 updated | +2 lines | 03 |
| MF3-2 | MUST-FIX | Untrusted path leaks gate_data | 1 line change | 03 |
| SF3-1 | SHOULD-FIX | Hardcoded "5 mutations" | 1 line change | 03 |
| SF3-2 | SHOULD-FIX | Immutability contract unclear | 1 sentence + 1 test | 01 |

## Convergence Assessment

Round 3 achieved convergence:
- ALL 7 round-2 MUST-FIX confirmed resolved by all 6 reviewers
- ALL 4 round-2 SHOULD-FIX confirmed resolved
- NO new findings from external models that cold agents didn't already identify
- Remaining 4 items are ALL surgical (total: ~5 lines of plan text changes)
- No structural or architectural issues remain

The plans are ready for execution after these 4 targeted fixes.

---

*Round 3 complete. 2 MUST-FIX + 2 SHOULD-FIX remaining, all surgical. Plans have converged.*
