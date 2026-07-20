---
phase: 20-verdict-honesty
plan: "03"
subsystem: eval
tags: [eval, runtime, advisory, corpus, scoring]
dependency_graph:
  requires: [20-01, 20-02]
  provides: [RUNTIME-axis-eval-scoring, corpus-expected-advisory-schema, E1-E6-corrected]
  affects: [eval/corpus.py, eval/scorer.py, eval/runner.py, corpus.yaml]
tech_stack:
  added: []
  patterns: [advisory-keyword-substring-match, per-run-advisory-scoring, separate-advisory-count]
key_files:
  created:
    - tests/test_runtime_eval.py
  modified:
    - src/code_forge/eval/corpus.py
    - src/code_forge/eval/scorer.py
    - src/code_forge/eval/runner.py
    - tests/eval/corpus/corpus.yaml
decisions:
  - "advisory_caught_count is SEPARATE from caught_count on EvalResult (DS-R3)"
  - "advisory scoring in replay_entry per-run loop BEFORE temp dir cleanup (GM-R4/Kimi-R2)"
  - "RuntimeAxisHook.post_review is a no-op; scoring is in the runner loop only"
  - "eval concat excludes runtime-smoke-summary findings (GM-R6: surface names false-positive)"
  - "E1-E6 expected_verdict changed HOLD->PASS (D-06: RUNTIME advisory cannot block)"
  - "advisory_caught() uses case-insensitive substring matching, any keyword hit = True (D-12)"
metrics:
  duration: "~15 minutes"
  completed: "2026-06-12"
  tasks_completed: 2
  files_created: 1
  files_modified: 4
---

# Phase 20 Plan 03: RUNTIME Advisory Eval Axis + Corpus E1-E6 Correction Summary

Eval corpus schema extended with expected_advisory field; case-insensitive keyword
scoring helper added; per-run advisory scoring wired into runner before temp dir
cleanup; E1-E6 expected_verdict corrected from HOLD to PASS per D-06.

## What Was Built

### Task 1: CorpusEntry.expected_advisory + advisory_caught() helper

**src/code_forge/eval/corpus.py:**
- Added `expected_advisory: list[str] = field(default_factory=list)` to `CorpusEntry` frozen dataclass
- Updated `load_corpus()` to parse `expected_advisory` from YAML
- Backward-compatible: existing entries without the field get `[]` automatically

**src/code_forge/eval/scorer.py:**
- Added `advisory_caught(advisory_text, keywords) -> bool`: case-insensitive substring
  match per D-12; any single keyword hit = True; empty text or empty keywords = False
- Added `advisory_caught_count: int = 0` to `EvalResult` (separate from `caught_count`, DS-R3)
- Added `advisory_caught: int` and `advisory_missed: int` to `EvalSummary`
- Added `_is_pure_runtime_advisory()` predicate: expected_verdict=="PASS" AND non-empty expected_advisory
- Updated `compute_summary()` to score pure-RUNTIME entries via advisory keyword match
- Extended `format_table()` with Advisory column and advisory summary counts
- Extended `write_json_report()` with advisory fields

### Task 2: RuntimeAxisHook + corpus.yaml E1-E6 correction

**src/code_forge/eval/runner.py:**
- Added `FixvalAxisHook` (from main branch -- worktree branched before this landed)
- Added `RuntimeAxisHook(AxisHook)`: pre_review no-op, post_review no-op (GM-R4/Kimi-R2)
- Registered both hooks via `register_axis_hook()`
- Added `_read_advisory_findings(temp_dir)`: reads advisory-findings.json from temp dir
- Added `_concat_advisory_text(findings)`: concatenates descriptions excluding id=="runtime-smoke-summary"
- Wired advisory scoring in `replay_entry()` per-run loop BEFORE `shutil.rmtree()`:
  - Only when entry.expected_advisory is non-empty
  - advisory_caught_count stays separate from caught_count

**tests/eval/corpus/corpus.yaml:**
- E1-E6 expected_verdict HOLD -> PASS (D-06 correction)
- E1-E6 expected_advisory keyword lists added (3 keywords each per D-12 plan spec)
- ttl_class gains expected_advisory: ["ttl", "class", "header"], retains HOLD (FIXVAL blocks)
- gate-yaml-rce, BUG-P12-01: no expected_advisory (not RUNTIME entries)

**tests/test_runtime_eval.py:** 50 new tests covering all behaviors

## Test Results

```
python3 -m pytest tests/test_runtime_eval.py -x -q --tb=short
50 passed in 0.09s

python3 -m pytest tests/test_eval_corpus.py tests/test_eval_runner.py tests/test_eval_scorer.py -x -q
63 passed in 0.18s
```

## Commits

| Hash    | Type | Message |
|---------|------|---------|
| ae8b52f | test | test(20-03): add failing tests for RUNTIME advisory eval axis (RED) |
| 6a1bbf8 | feat | feat(20-03): RUNTIME advisory eval axis + corpus E1-E6 correction (GREEN) |

## Decisions Made

1. **advisory_caught_count separate from caught_count** (DS-R3): Advisory scoring never
   touches caught_count. actual_verdict is computed from caught_count only. A RUNTIME-only
   entry with keyword match gets advisory_caught_count>=2 while caught_count==0 and
   actual_verdict=="PASS". No cross-contamination.

2. **Advisory scoring in per-run loop, not post_review hook** (GM-R4/Kimi-R2): post_review
   runs after EvalResult is constructed and temp dir already cleaned up. Advisory findings
   must be read BEFORE shutil.rmtree(). RuntimeAxisHook.post_review is therefore a no-op.

3. **Exclude runtime-smoke-summary from advisory concat** (GM-R6): The summary finding
   contains surface names like "NOT VERIFIED: [nftables]". Including it would cause the
   "nftables" keyword to match even when the LLM found no stale-nftables risk in the diff.

4. **E1-E6 expected_verdict = PASS** (D-06): RUNTIME is structurally advisory (never blocks).
   E1-E6 diffs are runtime-escape cases that look correct statically -- they trigger no
   blocking axis (TRUST/FIXVAL/SEC). Setting expected_verdict to HOLD was wrong by design.

5. **pure-RUNTIME advisory scoring in compute_summary**: Predicate _is_pure_runtime_advisory()
   checks expected_verdict=="PASS" AND expected_advisory non-empty. Dual-axis entries
   (RUNTIME+FIXVAL, expected_verdict="HOLD") use verdict-match for caught/missed; their
   advisory_caught_count is tracked separately but does not gate caught classification.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] FixvalAxisHook missing from worktree runner.py**
- **Found during:** Task 2 implementation
- **Issue:** Worktree branched before Plan 20-02 merged FixvalAxisHook into runner.py.
  Worktree runner.py was 331 lines; main was 359 lines. Existing test_eval_runner.py
  expects FixvalAxisHook to be present and registered.
- **Fix:** Added FixvalAxisHook from main alongside the new RuntimeAxisHook.
- **Files modified:** src/code_forge/eval/runner.py
- **Commit:** 6a1bbf8

## Known Stubs

None. All fields are fully wired. advisory_caught_count defaulting to 0 for non-RUNTIME
entries is by design (empty expected_advisory = no advisory scoring applied).

## Threat Flags

None. All new code operates on developer-controlled corpus fixtures. No new network
endpoints, auth paths, file access patterns, or schema changes at trust boundaries.

## Note: Forge Review Pipeline

Per project memory [Forge review after gsd:execute-phase is manual]: the code-forge
9-pass review pipeline must be run separately before this phase is considered fully done.
GSD executor commits at # wip classification; the forge review is a post-execution gate.

## Self-Check: PASSED

- tests/test_runtime_eval.py: EXISTS (50 tests, all pass)
- src/code_forge/eval/corpus.py: EXISTS (expected_advisory field present)
- src/code_forge/eval/scorer.py: EXISTS (advisory_caught function present)
- src/code_forge/eval/runner.py: EXISTS (RuntimeAxisHook registered)
- tests/eval/corpus/corpus.yaml: EXISTS (E1-E6 have expected_advisory, expected_verdict=PASS)
- Commits ae8b52f and 6a1bbf8: verified in git log
