---
phase: 28-reviewer-canary-inline
plan: 01
subsystem: canary_gen
tags: [canary, generation, non-equivalence, injection, dispatch, orchestration]
dependency_graph:
  requires: [28-02]
  provides: [canary_gen.py, test_canary_gen.py]
  affects: [canary.py, evidence.py, findings.py, state.py]
tech_stack:
  added: []
  patterns: [DI-protocol-seam, frozen-dataclass, AST-structural-compare]
key_files:
  created:
    - src/code_forge/canary_gen.py
    - tests/test_canary_gen.py
  modified: []
decisions:
  - "validate_canary_findings replaces validate_reviewer_json for canary dispatch (MF2-1: no envelope/P0-P3 constraint)"
  - "Verdict.UNRELIABLE used directly without hasattr fallback (MF2-2: 28-02 guarantees the member)"
  - "textwrap.dedent before ast.parse in is_non_equivalent (MF2-6: indented snippets accepted)"
  - "Template fallback uses appended hunks as documented degraded-quality path (MF2-7)"
  - "Generic filenames from fixed pool instead of uuid-based names (SF2-4: avoid leaking canary intent)"
metrics:
  duration: 6m 16s
  completed: 2026-06-25T04:47:13Z
  tasks_completed: 2
  tasks_total: 2
  tests_added: 29
  lines_created: 915
---

# Phase 28 Plan 01: Canary Generation Module Summary

DI-seamed canary engine with template fallback, AST-based non-equivalence verification, and full gate orchestration pipeline

## What Was Built

**src/code_forge/canary_gen.py** (408 lines) -- the M2 core module that:

- Defines `CanaryProvider` and `ReviewProvider` as typing.Protocol DI seams so all LLM calls are injectable and testable with zero network calls
- Implements `is_non_equivalent(original, mutated)` using `textwrap.dedent` + `ast.dump` structural comparison; rejects comment-only, whitespace-only, and syntax-error mutations while accepting structural changes including indented code
- Implements `validate_canary_findings(findings)` as a lightweight validator checking 4 required keys (file, line, severity, description) with no P0-P3 enum constraint, returning a new list (never mutates input)
- Provides a 6-category template library (hardcoded_secret, none_deref, off_by_one, sql_injection, resource_leak, silent_except) using generic filenames from a fixed pool
- Implements `generate_canaries(diff_text, n, provider=)` with provider-first + template-fallback strategy, returning `CanarySkip` for non-Python diffs or when fewer than 2 verified canaries are available
- Implements `inject_canaries_into_diff(diff_text, mutations)` enforcing the LINE-MATCH invariant: +start=1, K<=5 lines, so Canary.line == mutation["line"] and evaluate_canary_coverage reliably detects caught canaries
- Implements `dispatch_canary_review(modified_diff, provider=)` with anti-anchoring prompt (no author narrative, no prior findings)
- Implements `run_inline_canary(diff_text, ...)` orchestrating the full pipeline: generate -> inject -> dispatch -> validate -> partition -> cite-verify -> evaluate, with graceful degradation to DELEGATED on any dispatch error

**tests/test_canary_gen.py** (507 lines, 29 tests) -- comprehensive TDD suite covering:
- Non-equivalence: comment rejection, whitespace rejection, operator/literal acceptance, syntax error rejection, indented code handling (7 tests)
- Validation: valid findings, missing keys, any severity, immutability (4 tests)
- Templates: valid mutations with parseable code, generic filenames (2 tests)
- Provider: seam injection, template fallback, insufficient skip, non-Python skip (4 tests)
- Injection: isolation, dispatch provider call, canary validator usage (3 tests)
- Orchestration: cite-reverify on real only, gate pass, gate miss (UNRELIABLE), skip on insufficient, dispatch error graceful, threshold ratio zero clamped, no tree mutation (7 tests)
- Invariant proof: canary catchable at recorded line, bug-inject shifted hunk breaks gate (2 tests)

## Commits

| Task | Commit | Type | Description |
|------|--------|------|-------------|
| 1 RED | `1efdef2` | test | Failing tests for generation and non-equivalence |
| 1 GREEN | `bf5037b` | feat | canary_gen module -- generation, non-equivalence, validation |
| 2 GREEN | `aa3c5a5` | feat | Injection, dispatch, and run_inline_canary orchestrator |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Template silent_except snippet exceeded K<=5 line limit**
- **Found during:** Task 2
- **Issue:** The _template_silent_except function produced a 7-line code snippet (try/except/pass/return spread across 7 lines), violating the K<=5 invariant required by inject_canaries_into_diff
- **Fix:** Condensed the template to 5 lines using inline try/except/pass syntax while maintaining valid Python and non-equivalence
- **Files modified:** src/code_forge/canary_gen.py
- **Commit:** aa3c5a5

**2. [Rule 1 - Bug] Test manifest mismatch in test_cite_reverify_on_real_only and test_gate_pass**
- **Found during:** Task 2
- **Issue:** Tests pre-computed a manifest from a separate generate_canaries call, but run_inline_canary internally generates its own canaries with different random template ordering, causing the review provider to return findings for wrong canary locations
- **Fix:** Rewrote both tests to parse canary hunk headers from the prompt string passed to the ReviewProvider, matching whatever canaries the internal pipeline generates
- **Files modified:** tests/test_canary_gen.py
- **Commit:** aa3c5a5

## TDD Gate Compliance

- RED gate: `1efdef2` (test commit, all tests fail -- module does not exist)
- GREEN gate: `bf5037b` (feat commit, 17 tests pass)
- Task 2 extends both files: `aa3c5a5` (29 tests pass)

## Verification Results

```
29 passed in 0.05s
```

All 29 tests pass with zero network calls. All 80 canary-related tests pass (29 canary_gen + 17 canary + 10 evidence + 10 findings + 14 canary_cli).

## Known Stubs

None -- all functions are fully implemented with no placeholder logic.

## Self-Check: PASSED

All files exist, all 3 commits found in git log, all 9 exports verified.
