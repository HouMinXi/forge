---
phase: 17-trust-gate-eval-scaffold
plan: 03
subsystem: eval
tags: [eval, corpus, scorer, runner, tdd, yaml, subprocess]
dependency_graph:
  requires:
    - phase: 17-01
      provides: trust.py (record_trust), advisory.py (AdvisoryFinding, AxisRunner)
  provides:
    - eval subpackage (corpus loader, scorer, runner)
    - CorpusEntry and load_corpus for manifest parsing
    - EvalResult, EvalSummary, compute_summary, format_table, write_json_report
    - replay_entry with subprocess isolation and axis-dependent run counts
    - AxisHook internal registration seam for scheduled axes
    - DETERMINISTIC_TAGS frozenset for run count defaults
    - Seed corpus manifest with gate-yaml-rce entry
  affects: [cli.py, eval CLI subcommand (17-04)]
tech_stack:
  added: []
  patterns: [four-quadrant-classification, axis-dependent-run-counts, subprocess-isolation, internal-hook-seam, raw-count-reporting]
key_files:
  created:
    - src/code_forge/eval/__init__.py
    - src/code_forge/eval/corpus.py
    - src/code_forge/eval/scorer.py
    - src/code_forge/eval/runner.py
    - tests/test_eval_corpus.py
    - tests/test_eval_scorer.py
    - tests/test_eval_runner.py
    - tests/eval/corpus/corpus.yaml
    - tests/eval/corpus/diffs/gate-yaml-rce.diff
  modified: []
decisions:
  - "Four-quadrant classification: caught/missed/correct_pass/false_positive (D-10)"
  - "Raw counts only, never percentages -- 9 entries is smoke test not benchmark (carry-forward 2, Pitfall 4)"
  - "SKIPPED excluded from denominator with distinct reasons for apply failure vs timeout (D-12)"
  - "AxisHook is internal seam: list append only, no entry_points/importlib/pkg_resources (D-13, carry-forward 3)"
  - "_run_single returns (flagged, skip_reason) tuple to distinguish apply failure from timeout"
  - "git apply without --allow-empty flag (plan bug: --allow-empty is git-commit only)"
patterns-established:
  - "Subprocess isolation: each replay run gets fresh tempdir + git init + isolated XDG_CONFIG_HOME"
  - "Axis-dependent defaults: DETERMINISTIC_TAGS frozenset drives 1-run vs 3-run decision"
  - "Four-quadrant eval classification: caught + missed + correct_pass + false_positive + skipped"
requirements-completed: [EVAL-01]
metrics:
  duration: 10m54s
  completed: 2026-06-10
  tasks_completed: 2
  tasks_total: 2
  tests_added: 41
  tests_passed: 41
---

# Phase 17 Plan 03: Eval Scaffold Core Summary

Eval subpackage with YAML corpus loader, four-quadrant scorer (raw counts, no percentages), and subprocess-isolated pipeline replay runner with axis-dependent run counts and internal hook seam.

## Performance

- **Duration:** 10m54s
- **Started:** 2026-06-10T01:14:10Z
- **Completed:** 2026-06-10T01:25:04Z
- **Tasks:** 2/2
- **Files created:** 9

## Accomplishments
- Corpus loader parses YAML manifest into frozen CorpusEntry list with missing-diff tolerance (D-12)
- Scorer computes four-quadrant classification with SKIPPED excluded from denominator, raw counts only
- Runner replays entries via subprocess with per-run temp dir isolation and XDG trust state isolation
- Axis-dependent run defaults: deterministic (TRUST/SEC/FIXVAL) = 1 run, LLM (RUNTIME/LEGACY/INTENT) = 3 runs
- Internal AxisHook seam ready for 5 scheduled axes (Phases 18-22)
- Seed corpus seeded with gate-yaml-rce entry (hostile base_url exfil diff)
- Full suite: 1254 passed, 5 skipped, 0 failures (41 new tests, no regressions)

## Task Completion

| Task | Name | Type | Commits | Key Files |
|------|------|------|---------|-----------|
| 1 | Eval corpus loader + scorer with TDD | auto/tdd | acbb15d (RED), b662405 (GREEN) | corpus.py, scorer.py, __init__.py, corpus.yaml |
| 2 | Eval pipeline replay runner with axis hook seam | auto/tdd | e2c2f7b (RED), 0599c59 (GREEN) | runner.py |

## Files Created
- `src/code_forge/eval/__init__.py` - Eval subpackage marker
- `src/code_forge/eval/corpus.py` - CorpusEntry frozen dataclass + load_corpus YAML loader
- `src/code_forge/eval/scorer.py` - EvalResult, EvalSummary, compute_summary, format_table, write_json_report
- `src/code_forge/eval/runner.py` - replay_entry, AxisHook, register_axis_hook, DETERMINISTIC_TAGS
- `tests/test_eval_corpus.py` - 9 tests for corpus loader
- `tests/test_eval_scorer.py` - 15 tests for scorer
- `tests/test_eval_runner.py` - 17 tests for runner
- `tests/eval/corpus/corpus.yaml` - Seed corpus manifest (1 entry)
- `tests/eval/corpus/diffs/gate-yaml-rce.diff` - Hostile gate.yaml exfil diff

## Decisions Made
- Four-quadrant classification (caught/missed/correct_pass/false_positive) with SKIPPED as sixth category excluded from denominator
- _run_single returns (flagged, skip_reason) tuple instead of Optional[bool] to distinguish apply failure from timeout in SKIPPED reason
- Corrected plan bug: removed invalid `--allow-empty` flag from `git apply` call (flag only exists on `git commit`)
- XDG_CONFIG_HOME set to temp dir inside each replay run to prevent trust state pollution

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed _run_single return type to distinguish skip reasons**
- **Found during:** Task 2 (runner implementation)
- **Issue:** _run_single returned Optional[bool] (None for both apply failure and timeout), making the caller unable to set a descriptive skipped_reason
- **Fix:** Changed return type to tuple[bool, str] where the string carries the specific reason ("git apply failed: ..." or "code-forge review timeout after 300s")
- **Files modified:** src/code_forge/eval/runner.py
- **Verification:** test_skipped_on_apply_failure and test_skipped_on_timeout both pass with distinct reason strings
- **Committed in:** 0599c59

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Fix was necessary for correctness -- without it, timeout SKIPPED entries would report "git apply failed" which is misleading. No scope creep.

## TDD Gate Compliance

| Task | RED Commit | GREEN Commit | REFACTOR Commit |
|------|-----------|-------------|-----------------|
| 1 | acbb15d (test) | b662405 (feat) | N/A |
| 2 | e2c2f7b (test) | 0599c59 (feat) | N/A |

Both tasks followed RED-GREEN sequence. No refactoring needed.

## Verification Results

All 41 new tests pass. Full suite (1254 tests) passes with zero failures and no regressions.

Acceptance criteria verified:
- src/code_forge/eval/__init__.py exists
- corpus.py exports CorpusEntry and load_corpus
- scorer.py exports EvalResult, EvalSummary, compute_summary, format_table, write_json_report
- runner.py exports replay_entry, AxisHook, register_axis_hook
- tests/eval/corpus/corpus.yaml exists with 1 entry
- No percentage computation in scorer.py (docstring mentions only to say "NOT percentages")
- format_table output contains "Caught: N/M" with raw integers
- SKIPPED entries excluded from denominator in compute_summary
- No entry_points/importlib.import_module/pkg_resources imports in runner.py
- DETERMINISTIC_TAGS frozenset present in runner.py
- Deterministic tags default to runs=1, LLM tags to runs=3
- Subprocess failure produces SKIPPED result with descriptive reason
- AxisHook pre_review and post_review called during replay

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Eval scaffold core complete, ready for CLI entry point wiring (Plan 04)
- AxisHook seam ready for axis implementations (Phases 18-22)
- Seed corpus ready for expansion with additional real bug entries

## Self-Check: PASSED

All 9 created files exist. All 4 commits found in history. SUMMARY.md written.

---
*Phase: 17-trust-gate-eval-scaffold*
*Completed: 2026-06-10*
