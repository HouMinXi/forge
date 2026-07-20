---
phase: 21-legacy-intent
plan: 02
subsystem: legacy-runner
tags: [advisory, legacy, blame, intent, satd, pre-existing]
dependency_graph:
  requires: [git_blame]
  provides: [LegacyRunner]
  affects: [machine.py, cli.py]
tech_stack:
  added: []
  patterns: [l0-runner-injection, manual-line-intersection, satd-substring-match, blame-cache-per-file, source-lines-cache]
key_files:
  created:
    - src/code_forge/legacy.py
    - tests/test_legacy.py
  modified: []
decisions:
  - "D-02 enforced: manual line-intersection loop instead of filter_delta (type incompatible)"
  - "D-03 accepted: ~55% precision for SATD/commit-signal substring matching"
  - "D-04 enforced: attribution format git-blame: {author} {sha[:8]} {subject}"
  - "D-05 enforced: follows TaintRunner/RuntimeRunner structural model"
  - "D-07 enforced: [pre-existing] {desc} [intent: intended/unintended] description format"
  - "l0_runner constructor injection avoids circular import (legacy -> machine)"
  - "Semantic flip guard: 'unintentional' prevents 'intentional' match"
metrics:
  duration: 10m 50s
  completed: 2026-06-13T09:06:14Z
  tasks: 2/2
  files_modified: 2
---

# Phase 21 Plan 02: LegacyRunner Advisory Axis Summary

Pre-existing L0 finding detection with git-blame attribution and SATD/commit-signal intent classification via manual line-intersection against diff-changed lines

## Tasks Completed

| Task | Name | Type | Commit | Key Changes |
|------|------|------|--------|-------------|
| 1 | LegacyRunner tests -- RED | test | bb312a0 | 21 test functions in tests/test_legacy.py covering pre-existing detection, path normalization, attribution format, SATD keywords, commit signals, semantic flip guards |
| 2 | LegacyRunner implementation -- GREEN | feat | 6a796f2 | src/code_forge/legacy.py with LegacyRunner class, _classify_intent, _build_legacy_skipped |

## Implementation Details

### LegacyRunner class

- Constructor: `LegacyRunner(l0_runner=None)` -- callable injection for tests; lazy import of `_default_l0_runner` from machine.py in production (avoids circular import)
- Attributes: `source_files`, `registry` (injected by machine.py), `infra_errors` (cleared each run)
- `is_advisory = True` (never blocks convergence)
- `run(diff_text, repo_root)` follows AxisRunner Protocol

### run() Algorithm (12 steps)

1. Clear infra_errors, resolve repo_root
2-4. Guard: empty diff, no source_files, no registry -> return [] or SKIPPED
5. `extract_changed_lines(diff_text)` for changed line map
6-7. Resolve and execute l0_runner (lazy import if None; exception -> SKIPPED)
8. Path normalization: build `changed_lines_norm` with both relative and absolute keys
9. Manual line-intersection loop (D-02): find delta findings (on changed lines), compute pre_existing = L0 findings NOT in delta AND in diff-touched files (D-01)
10-11. Per pre-existing finding: git_blame attribution + _classify_intent + build AdvisoryFinding
12. Return advisories

### _classify_intent(commit_subject, source_lines, finding_line)

- Checks commit subject against INTENT_SIGNALS (substring match after normalization)
- Semantic flip guards: "unintentional" blocks "intentional", "unknown issue" blocks "known issue"
- Checks surrounding lines (+/-3) for SATD_KEYWORDS (todo, fixme, hack, workaround, xxx, kludge)
- Default: "unintended"
- ~55% precision accepted per D-03

### Test Coverage (21 tests)

REVIEW-LEGACY-01 (12 tests):
- `test_pre_existing_finding_emitted`: unchanged line 20 -> AdvisoryFinding emitted
- `test_delta_finding_not_pre_existing`: changed line 5 -> excluded
- `test_d01_non_diff_file_excluded`: bar.py not in diff -> excluded
- `test_absolute_path_source_files_normalized`: absolute path finding matched via normalization
- `test_advisory_never_blocks`: is_advisory is True
- `test_skipped_when_no_source_files`: None -> SKIPPED finding
- `test_skipped_when_no_registry`: None -> SKIPPED finding
- `test_empty_diff_returns_empty`: "" -> []
- `test_empty_changed_lines_returns_empty`: header-only diff -> []
- `test_git_blame_unavailable_produces_unavailable_attribution`: {} -> "unavailable"
- `test_staged_line_attribution`: sha 0*40 -> "uncommitted staged change"
- `test_attribution_format`: D-04 format verified with sha[:8]

REVIEW-INTENT-01 (9 tests):
- `test_intent_label_in_description`: [pre-existing] ... [intent: ...] format
- `test_satd_surrounding_lines_intended`: TODO on line N-1 -> "intended"
- `test_commit_msg_signal_intended`: "workaround" in subject -> "intended"
- `test_commit_msg_signal_hack_intended`: "hack:" in subject -> "intended"
- `test_default_classification_unintended`: no signals -> "unintended"
- `test_satd_precision_acknowledged`: "xxx_default" -> "xxx" matches -> "intended" (D-03)
- `test_intent_signal_temp_false_positive`: "attempt" -> "temp" matches -> "intended" (D-03)
- `test_unintentional_not_classified_intended`: semantic flip guard -> "unintended"
- `test_l0_runner_exception_returns_skipped`: RuntimeError -> SKIPPED + infra_errors

## Deviations from Plan

None - plan executed exactly as written.

## TDD Gate Compliance

- RED gate (bb312a0): `test(21-02)` commit -- all 21 tests fail with ModuleNotFoundError
- GREEN gate (6a796f2): `feat(21-02)` commit -- all 21 tests pass
- REFACTOR gate: not needed -- implementation matches plan skeleton, no cleanup required

## Verification Results

- `pytest tests/test_legacy.py -x -q`: 21 passed
- `pytest -x -q` (full suite): 1639 passed, 5 skipped, 0 failed (no regressions)
- `PYTHONPATH=src python -c "from code_forge.legacy import LegacyRunner; r = LegacyRunner(); print(r.is_advisory)"`: True
- Non-ASCII check: clean (both files)
- No circular import: `from code_forge.legacy import LegacyRunner` succeeds

## Known Stubs

None -- LegacyRunner is fully implemented with no placeholder logic.

## Self-Check: PASSED

- [x] src/code_forge/legacy.py exists
- [x] tests/test_legacy.py exists
- [x] Commit bb312a0 exists (RED)
- [x] Commit 6a796f2 exists (GREEN)
- [x] LegacyRunner class defined in legacy.py
- [x] is_advisory property defined in legacy.py
