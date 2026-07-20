---
phase: 16-relief-mechanisms
plan: 02
subsystem: forge-state-machine
tags: [threshold-wiring, f3-fail-closed, infra-source, falsifier-skip]
dependency_graph:
  requires: [16-01]
  provides: [threshold-wiring, f3-fix]
  affects: [machine.py, cli.py, outlet_c.py, factories.py]
tech_stack:
  added: []
  patterns: [constructor-param-threading, source-tag-guard]
key_files:
  created: []
  modified:
    - src/code_forge/machine.py
    - src/code_forge/cli.py
    - src/code_forge/outlet_c.py
    - src/code_forge/factories.py
    - tests/test_consecutive_clean.py
    - tests/test_outlet_c.py
    - tests/test_machine_local.py
    - tests/test_factories.py
    - tests/test_cli_lock.py
    - tests/test_integration.py
decisions:
  - "Env var read moved from machine.py per-round loop to one-time computation in cli.py via tier_threshold"
  - "StateMachine receives threshold as constructor param (default=3) for backward compat"
  - "INFRA findings skip falsifier via source-tag guard in _run_l1_phase"
  - "Integration tests that relied on F3 defect updated to use --falsification-engine stub"
metrics:
  duration: 28m
  completed: 2026-06-09
---

# Phase 16 Plan 02: Threshold Wiring and F3 Fail-Closed Summary

Wire clean_round_threshold from cli.py through both outlet branches to StateMachine, replacing the per-round env var read; close the F3 false-green defect by tagging INFRA findings and adding a falsifier skip guard.

## What Changed

### Task 1: Wire clean_round_threshold (a1fdcfe)

- **machine.py**: Added `clean_round_threshold: int = 3` field to StateMachine dataclass. Replaced the 8-line `os.environ.get("FORGE_CLEAN_ROUND_THRESHOLD")` block inside `_run_local`'s per-round loop with `_threshold = self.clean_round_threshold`.
- **cli.py**: Added threshold computation between baseline resolution and outlet dispatch -- calls `count_diff_lines` and `tier_threshold` from diff.py (Plan 01). Parses `FORGE_CLEAN_ROUND_THRESHOLD` env var once. Threads `_clean_threshold` to both `run_outlet_c` (Outlet C) and `_run_hold_loop` (Outlet A).
- **outlet_c.py**: Added `clean_round_threshold: int = 3` parameter to `run_outlet_c`, passed through to StateMachine constructor.
- **cli.py _run_hold_loop**: Added `clean_round_threshold=3` keyword parameter, passed through to StateMachine constructor.
- **Tests**: Updated `test_threshold_1_recovers_single_fixpoint` to use constructor param instead of env var. Added `test_threshold_param_2` (exits after 2 clean rounds) and `test_threshold_param_4` (requires 4 clean rounds). Added `test_threshold_threading` in test_outlet_c.py (verifies outlet C threads threshold to SM).

### Task 2: F3 Fail-Closed (c117126)

- **factories.py**: Changed `source="L1"` to `source="INFRA"` on invoke-fail (line 298) and schema-fail (line 314) error-path findings.
- **outlet_c.py**: Changed `source="L1"` to `source="INFRA"` on spawn-fail (line 59) and schema-fail (line 76) error-path findings.
- **machine.py**: Added falsifier skip guard in `_run_l1_phase`: `if f.source == "INFRA": l1_findings.append(f); continue`. INFRA findings keep their CONFIRMED disposition, blocking fixpoint.
- **reviewer_json.py line 105**: Unchanged -- normal L1 findings with Disposition.UNCERTAIN still go through the falsifier.
- **Tests**: Added `test_infra_finding_blocks_fixpoint` (dismiss-all falsifier cannot override INFRA CONFIRMED; machine returns ESCALATED). Added 4 site-level tests: `test_factories_invoke_fail_tagged_infra`, `test_factories_schema_fail_tagged_infra`, `test_outlet_c_spawn_fail_tagged_infra`, `test_outlet_c_schema_fail_tagged_infra`.
- **Integration test fixes**: `test_cli_lock.py` and `test_integration.py` updated to use `--falsification-engine stub` -- these tests relied on the F3 defect (schema-fail findings dismissed by falsifier) to produce false PASS verdicts.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Integration tests relied on F3 defect for PASS verdict**
- **Found during:** Task 2 verification (full suite regression)
- **Issue:** `test_cli_lock.py::test_lock_released_after_run`, `test_integration.py::test_pass_on_clean_code`, `test_integration.py::test_baseline_preexisting_not_shown`, `test_integration.py::test_state_json_written_with_versions` called real `llm_invoke` which returned invalid JSON. Schema-fail findings were previously source="L1" and dismissed by the falsifier (the F3 defect). After the fix, INFRA findings stay CONFIRMED, correctly causing FAIL.
- **Fix:** Added `--falsification-engine stub` to all affected integration tests. These tests check lock/state/pipeline behavior, not L1 review quality, so stub engine is appropriate.
- **Files modified:** tests/test_cli_lock.py, tests/test_integration.py
- **Commit:** c117126

**2. [Rule 1 - Bug] test_factories mock path incorrect**
- **Found during:** Task 2 test_factories_invoke_fail_tagged_infra
- **Issue:** Patching `code_forge.factories.llm_invoke` failed because `llm_invoke` is imported locally inside `build_l1_provider`. Also, the provider must be constructed INSIDE the patch context so the closure captures the mock.
- **Fix:** Changed mock target to `code_forge.llm_invoke.llm_invoke` and moved `build_l1_provider` call inside the patch context manager.
- **Files modified:** tests/test_factories.py
- **Commit:** c117126

**3. [Rule 1 - Bug] LLMResult field name: content not text**
- **Found during:** Task 2 test_factories_schema_fail_tagged_infra
- **Issue:** Test used `LLMResult(text=...)` but the actual field is `content`.
- **Fix:** Changed to `LLMResult(content=...)`.
- **Files modified:** tests/test_factories.py
- **Commit:** c117126

## Verification Results

```
$ python3 -m pytest -x -q
1183 passed, 5 skipped, 3 warnings in 106.17s

$ grep -c "clean_round_threshold" src/code_forge/machine.py
2

$ grep "FORGE_CLEAN_ROUND_THRESHOLD" src/code_forge/machine.py
(none -- removed)

$ grep -c 'source="INFRA"' src/code_forge/factories.py
2

$ grep -c 'source="INFRA"' src/code_forge/outlet_c.py
2

$ grep 'f.source == "INFRA"' src/code_forge/machine.py
            if f.source == "INFRA":

$ grep -n 'source="L1"' src/code_forge/reviewer_json.py
105:            source="L1",
```

## Commits

| Task | Commit  | Description |
|------|---------|-------------|
| 1    | a1fdcfe | forge/threshold-wiring: wire clean_round_threshold through SM, cli.py, outlets |
| 2    | c117126 | forge/f3-fail-closed: tag INFRA findings and skip falsifier |
