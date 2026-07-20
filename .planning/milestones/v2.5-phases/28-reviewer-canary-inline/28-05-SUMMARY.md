---
phase: 28-reviewer-canary-inline
plan: 05
subsystem: canary-smoke
tags: [canary, smoke, mimo-pro, gated, spike-protocol]
dependency_graph:
  requires: [28-01, 28-02, 28-03]
  provides: [gated-smoke-tests, spike-protocol-validation]
  affects: []
tech_stack:
  added: []
  patterns: [gated-test, majority-voting, spike-protocol]
key_files:
  created:
    - tests/test_canary_smoke.py
    - spikes/canary_fence/README.md
    - spikes/canary_fence/ledger.py
    - spikes/canary_fence/report_service.py
    - spikes/canary_fence/widget.py
  modified: []
decisions: []
metrics:
  duration_seconds: 139
  completed: 2026-06-25T05:09:28Z
---

# Phase 28 Plan 05: Gated Smoke Tests Summary

Gated mimo-pro smoke tests for canary discrimination and end-to-end pipeline validation, never in CI.

## What was done

Two gated smoke tests in `tests/test_canary_smoke.py`, both decorated with
`@skipUnless(os.environ.get("FORGE_SMOKE_MIMO") == "1", ...)` so they are
completely inert in the default pytest suite and CI.

**Task 1 -- Spike-protocol discrimination test:**
Generates canaries from a synthetic multi-function Python diff via mimo-pro,
then runs 3 repetitions each of genuine and overloaded reviews. Asserts
separation on majority (>= 2/3): genuine catches enough canaries, overloaded
does not. Diagnostic output records per-run catch rates for manual inspection.

**Task 2 -- End-to-end pipeline smoke:**
Calls `run_inline_canary` with both providers backed by mimo-pro. Tolerant
assertions: verdict in {DELEGATED, UNRELIABLE}, no canary entries in
real_findings, working tree clean after run. Timeout/error returns DELEGATED
(graceful degradation path).

**Prerequisite -- Spike fixtures:**
Cherry-picked from commit 5d9b1dc (canary fence validation fixtures: 3 Python
files with intentional planted defects plus a run protocol document).

## Commits

| Commit | Description |
|--------|-------------|
| ed29ba3 | spikes: add canary fence validation fixtures |
| 16386a9 | canary/smoke: add gated mimo-pro discrimination and e2e tests |

## Verification

```
$ PYTHONPATH=src python -m pytest tests/test_canary_smoke.py -x -v
tests/test_canary_smoke.py::TestCanaryDiscriminationMimo::test_canary_discrimination_mimo SKIPPED [ 50%]
tests/test_canary_smoke.py::TestRunInlineCanaryE2EMimo::test_run_inline_canary_e2e_mimo SKIPPED [100%]
2 skipped in 0.04s
```

Both tests SKIPPED as expected (no FORGE_SMOKE_MIMO=1 set). No network calls
made during verification. Existing 47 canary tests continue to pass.

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None. Both tests are fully wired to real `canary_gen` and `canary` module
functions. The `_source_lookup` returning None is intentional: the smoke test
uses a synthetic diff with no real file tree, so cite-verify correctly marks
findings as unverified (this is the expected behavior for a smoke without a
real repo checkout).

## Self-Check: PASSED
