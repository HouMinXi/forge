# Phase 28 Cross-AI Review Consolidation

**Date:** 2026-06-24
**Active round:** 5 (chief review, ground-truth)
**Prior rounds:** v1 (28-REVIEWS-v1.md), v2 (28-REVIEWS-v2.md), v3 (28-REVIEWS-v3.md)

---

## Round 4 (v4 replan): CONVERGENCE -- ALL APPROVE

**Reviewers:** DeepSeek V4 Pro, MiMo v2.5 Pro, Kimi K2.7 Code, Gemini 3.1 Pro
**Cold agents:** plan-checker (VERIFICATION PASSED), code-reviewer (FALSE POSITIVE -- compared plan text against unimplemented source, misunderstanding GSD workflow)

| Reviewer | Verdict | New Issues |
|----------|---------|------------|
| DeepSeek V4 Pro | APPROVE | 0 |
| MiMo v2.5 Pro | APPROVE | 0 |
| Kimi K2.7 Code | APPROVE | 0 |
| Gemini 3.1 Pro | APPROVE | 0 |
| Cold plan-checker | VERIFICATION PASSED | 0 |

All 4 surgical fixes from Round 3 (MF3-1, MF3-2, SF3-1, SF3-2) verified by all reviewers.
Zero new issues. Plans converged after 4 rounds.

**Trajectory:** R1 (7 MF + 5 SF) -> R2 (7 MF + 4 SF) -> R3 (2 MF + 2 SF) -> R4 (0).

---

## Round 5 (chief review): APPROVE WITH CHANGES

**Reviewer:** Main session ground-truth review against live source (main @ f359a36) + M1 @ c515db7
**Method:** Live-file verification, cross-artifact data flow tracing -- what per-plan text-only review cannot see.

### Verdict: APPROVE WITH CHANGES

The plans are well-grounded and executable. All line numbers RE-DERIVED against current main, M1 contracts are real, API signatures match, threshold math is bounded, cross-plan numeric choices are consistent, and all Round 3 fixes are incorporated. But 6 items the text-only panel MISSED remain.

### Ground-Truth Confirmed (plan is honest here)

- cli.py line claims vs live source @ f359a36: all OK (inline @1321, DELEGATED @1327, _load_gate_backends def @98, 3 call sites @826/1298/2281, git diff HEAD @1088, args.mode choices @206, epilogs @162/191)
- SC-1 byte-for-byte: real DELEGATED string matches plan's two-literal split
- _load_gate_backends return points match MF2-3 edit targets; MF3-2 trust-guard is SOUND
- M1 @ c515db7 is REAL and PURE-ADDITIVE (6 files, 614 ins, 0 mods); all signatures match
- llm_invoke/resolve_backend signatures correct; providers handle content dict|str
- Threshold math bounded: ratio >0.0..1.0, N 3..5, threshold in [1,N]
- 28-03 SF2-1 also fixes a PRE-EXISTING epilog staleness (5/6 undocumented before Phase 28)

### Findings

| # | Severity | Finding | Fix | Plans |
|---|----------|---------|-----|-------|
| F2 | MED-HIGH | inject hunk line-number not tied to Canary.line -> always-fail gate (false positive) | LINE-MATCH invariant: +start=1, K<=5, Canary.line=snippet bug line; bug-inject test | 28-01, 28-03 |
| F4 | MED | No real-model smoke test task (CONTEXT deliverable g unplanned) | New Plan 28-05 Task 2: end-to-end mimo-pro smoke | NEW 28-05 |
| F3 | MED | CONTEXT sec 9 spike-protocol validation omitted | New Plan 28-05 Task 1: spike discrimination test | NEW 28-05 |
| F1 | LOW | grep '_load_gate_backends' returns 5 (includes comment @~1350), criterion says 4 | grep -nE '_load_gate_backends\(' excludes comment, count = 4 | 28-03 |
| F5 | LOW | Default branch drops two D4 honesty floor comments; "4-line block" is 7 lines | Preserve comments; fix block count | 28-03 |
| F6 | LOW-MED | claude -p zero-config slow no-op (within LOCKED D-28-05) | Add one doc line to 28-04 | 28-04 (deferred) |

### Disposition

| # | Status | How |
|---|--------|-----|
| F2 | FIXED | 28-01 Task 2 rewritten with LINE-MATCH invariant + bug-inject test; 28-01 Task 1 template line def added; 28-03 prompt line semantics unified |
| F1 | FIXED | 28-03 acceptance criterion uses `grep -nE '_load_gate_backends\('` with note |
| F5 | FIXED | 28-03 step 6 preserves D4 honesty floor comments; block count corrected to 7 |
| F3 | FIXED | New Plan 28-05 Task 1 (spike discrimination, mimo-pro, gated) |
| F4 | FIXED | New Plan 28-05 Task 2 (end-to-end smoke, mimo-pro, gated) |
| F6 | DEFERRED | One doc line in 28-04; low priority, can be added during execution |

### Cross-Plan Consistency Verification

F2 requires cross-plan consistency on mutation["line"] semantics. Verified:
- 28-01 Task 1: template "line" = 1-based index of buggy line WITHIN code snippet, snippets <= 5 lines
- 28-01 Task 2: inject hunk @@ -0,0 +1,K @@, Canary.line = new-file line of bug (== mutation["line"] because +start=1)
- 28-03 _canary_provider prompt: '"line" is the 1-based line number of the bug WITHIN the code snippet, snippet <= 5 lines'
- 28-05 _mimo_canary_provider prompt: same semantics as 28-03

All 4 references use the same "1-based within code snippet" definition. No stale references.

---

## Action Items Summary (cumulative, all rounds)

| Round | MUST-FIX | SHOULD-FIX | Status |
|-------|----------|------------|--------|
| R1 | 7 | 5 | All resolved in v2 replan |
| R2 | 7 | 4 | All resolved in v3 replan |
| R3 | 2 | 2 | All resolved in v4 replan |
| R4 | 0 | 0 | Converged (all APPROVE) |
| R5 (chief) | 1 (F2) | 5 (F1/F3/F4/F5/F6) | F2/F1/F3/F4/F5 FIXED; F6 DEFERRED |

**Plans are ready for execution.** Wave order: 1a (28-02) -> 1b (28-01) -> 2 (28-03, 28-04) -> 3 (28-05).

---

---

## Round 6 (v6 regression check): CONVERGED -- ALL APPROVE

**Reviewers:** Cold plan-checker, Cold code-reviewer, DeepSeek V4 Pro, MiMo v2.5 Pro, Gemini 3.1 Pro
**Kimi:** TPD quota hit, skipped (3/4 external models sufficient for consensus)

| Reviewer | Verdict | Regressions | New Issues |
|----------|---------|-------------|------------|
| Cold plan-checker | APPROVE | 0/11 | 0 |
| Cold code-reviewer | APPROVE | 0/11 | 0 |
| DeepSeek V4 Pro | APPROVE | 0/11 | 0 |
| MiMo v2.5 Pro | APPROVE | 0/11 | 0 (2 INFO) |
| Gemini 3.1 Pro | APPROVE | 0/11 | 0 |

### Fix Verification: ALL 5 VERIFIED

| Fix | Status | Consensus |
|-----|--------|-----------|
| F2 (LINE-MATCH invariant) | VERIFIED | 5/5 |
| F1 (grep criterion) | VERIFIED | 5/5 |
| F5 (D4 honesty floor) | VERIFIED | 5/5 |
| F3 (spike protocol, 28-05 Task 1) | VERIFIED | 5/5 |
| F4 (real-model smoke, 28-05 Task 2) | VERIFIED | 5/5 |

### Regression Check: ALL 11 STILL HOLD

All 5 reviewers independently confirmed MF2-1 through SF3-2 remain intact. Zero regressions.

### INFO-level observations (non-blocking, from MiMo)

1. **Prompt duplication**: 28-03 and 28-05 share the same canary prompt template. Suggest extracting to a shared constant during execution. Not a plan-level issue.
2. **_load_canary_config priority**: --canary flag overrides gate.yaml canary.enabled:false. Standard CLI-flag-over-config behavior. Not a gap.

### Cross-Plan Consistency: VERIFIED

mutation["line"] semantics unified across all 4 touchpoints (28-01 templates, 28-01 inject, 28-03 prompt, 28-05 prompt) as "1-based within code snippet, K<=5". 5/5 consensus.

---

## Convergence Summary (all rounds)

| Round | MUST-FIX | SHOULD-FIX | Status |
|-------|----------|------------|--------|
| R1 | 7 | 5 | All resolved in v2 replan |
| R2 | 7 | 4 | All resolved in v3 replan |
| R3 | 2 | 2 | All resolved in v4 replan |
| R4 | 0 | 0 | Converged (all APPROVE) |
| R5 (chief) | 1 (F2) | 5 (F1/F3/F4/F5/F6) | F2/F1/F3/F4/F5 FIXED; F6 DEFERRED |
| R6 (regression) | 0 | 0 | Converged (all APPROVE, 0 regressions) |

**Plans fully converged after 6 rounds. Ready for execution.**
Wave order: 1a (28-02) -> 1b (28-01) -> 2 (28-03, 28-04) -> 3 (28-05).

---

*Round 6 complete. Zero regressions, zero new blockers. Plans ready for `/gsd:execute-phase 28`.*
