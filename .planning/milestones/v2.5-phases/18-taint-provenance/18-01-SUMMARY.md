---
phase: 18-taint-provenance
plan: 01
subsystem: taint
tags: [taint, danger-score, trust, advisory, semgrep, L0]
dependency_graph:
  requires: [trust.py/DANGEROUS_FIELDS, advisory.py/AdvisoryFinding, state.py/StateFinding, parsers/semgrep.py]
  provides: [taint.py/danger_score_from_diff, taint.py/TaintRunner, taint.py/_findings_to_advisories]
  affects: [machine.py (future wiring in 18-02)]
tech_stack:
  added: [semgrep (optional runtime dependency for taint axis)]
  patterns: [AxisRunner Protocol, L0 blocking StateFinding, advisory AdvisoryFinding]
key_files:
  created:
    - src/code_forge/taint.py
    - tests/test_taint.py
  modified: []
decisions:
  - "D-15 deviation: danger_score_from_diff constructs StateFinding directly (no SARIF round-trip) because it parses diff text, not semgrep output"
  - "TaintRunner references rules/forge-taint.yaml path but does not validate existence at import time (Plan 02 creates the file)"
metrics:
  duration: "7m 53s"
  completed: "2026-06-11T17:59:49Z"
  tasks: 2
  tests_added: 25
  tests_total: 1342
  files_created: 2
---

# Phase 18 Plan 01: Taint Module (danger-score + TaintRunner) Summary

L0 danger-score diff scanner and semgrep-based TaintRunner advisory axis with full TDD coverage, both unit-testable in isolation before pipeline wiring.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | danger_score_from_diff with TDD | 915cbba (RED), 165f9d9 (GREEN) | src/code_forge/taint.py, tests/test_taint.py |
| 2 | TaintRunner advisory axis with TDD | e253a9c (RED), 50484cb (GREEN) | src/code_forge/taint.py, tests/test_taint.py |

## What Was Built

### danger_score_from_diff (L0 blocking)
- Scans unified diff new-lines for DANGEROUS_FIELDS in gate.yaml and .code-forge/* config files (D-01)
- Only scans added lines (+ prefix, excluding +++ header) per D-03
- Returns L0 CONFIRMED StateFindings with fingerprint "danger-score:{file}:{field}:{line}" (D-17)
- Imports DANGEROUS_FIELDS from trust.py (no redefinition)
- None/empty input returns [] (D-16 non-git mode guard)
- Correct line numbering via @@ hunk header parsing (multi-hunk aware)

### TaintRunner (advisory axis)
- Satisfies AxisRunner Protocol (is_advisory=True)
- Runs semgrep intraprocedural taint analysis on source_files (no git dependency, D-09)
- Parses SARIF output via existing parse_semgrep (DRY, no new parser)
- _findings_to_advisories converts Finding to AdvisoryFinding with axis="taint"
- AdvisoryFinding.id format: "taint:{file}:{line}:{rule_id}"
- attribution = "semgrep-ce/intraprocedural"
- D-05/D-06: loud-fail when semgrep absent (stderr message + infra_errors)
- Filters to .py files, checks file existence before invocation
- Timeout handling: 120s (T-18-04)
- infra_errors cleared on each run (no cross-run accumulation)

## Deviations from Plan

### Auto-fixed Issues

None -- plan executed exactly as written.

### Intentional Deviations (documented in plan)

**1. D-15 deviation: no SARIF round-trip for danger-score**
- danger_score_from_diff constructs StateFinding directly from diff text parsing
- SARIF path applies only to TaintRunner (which actually invokes semgrep)
- Documented in plan action section; avoids constructing synthetic SARIF only to immediately destructure it

## TDD Gate Compliance

- Task 1: RED (915cbba) -> GREEN (165f9d9) -- verified in git log
- Task 2: RED (e253a9c) -> GREEN (50484cb) -- verified in git log
- All RED commits confirmed failing (ImportError), all GREEN commits confirmed passing

## Test Coverage

- 12 tests for danger_score_from_diff (config detection, removed-line filtering, multi-hunk line numbers, fingerprint format, source/disposition, None/empty guards)
- 13 tests for TaintRunner (protocol conformance, semgrep absent, SARIF parsing, id format, attribution, caveat, non-Python filtering, timeout, infra_errors clearing, source_files guards)
- 25 total new tests, all passing
- Full suite: 1342 passed, 5 skipped, 0 failures (baseline was 1317)

## Self-Check: PASSED

- [x] src/code_forge/taint.py exists (284 lines)
- [x] tests/test_taint.py exists (451 lines, >= 80 line minimum)
- [x] All 4 commits verified in git log
- [x] No stubs found
- [x] No non-ASCII characters
- [x] No accidental file deletions
- [x] All exports accessible: danger_score_from_diff, TaintRunner, _findings_to_advisories
