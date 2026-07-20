---
phase: 08-hardening
plan: "02"
status: complete
one_liner: "Add CLI-08 cost tracking: State fields, StateMachine accumulation, state.json cost section, and stderr human-readable summary"
subsystem: core-state-machine
tags: [cost-tracking, cli, state, machine, factories]
dependency_graph:
  requires: [08-01]
  provides: [CLI-08-cost-tracking]
  affects: [state.json, stderr output, all test files using l1_provider]
tech_stack:
  added: []
  patterns: [tuple-return-protocol, cost-accumulation-per-round, backward-compat-load]
key_files:
  created: []
  modified:
    - src/code_forge/state.py
    - src/code_forge/machine.py
    - src/code_forge/factories.py
    - src/code_forge/cli.py
    - tests/test_machine_local.py
    - tests/test_cli_integration.py
    - tests/test_cli_hold_resume.py
    - tests/test_consecutive_clean.py
    - tests/test_ordering.py
    - tests/test_round.py
    - tests/test_machine_ci.py
decisions:
  - "L1Provider signature changed from Callable[[], list[StateFinding]] to Callable[[], tuple[list[StateFinding], Usage, float]] to carry cost data without adding a side-channel"
  - "Cost accumulated at round boundary (after L0+L1+L2+E2E all complete), not inside _run_l1_phase, so partial-round failures do not produce inconsistent per_pass entries"
  - "state.json cost section uses a nested dict under cost key for backward compatibility: pre-08-02 state.json files load with zero defaults via data.get(cost, {})"
  - "stderr output is suppressed when cost_passes == 0 (stub or dry-run modes produce no L1 invocations)"
metrics:
  duration: "~15 minutes"
  completed: "2026-06-03"
  tasks_completed: 7
  files_changed: 11
  tests_added: 5
  tests_total: 946
---

# Phase 08 Plan 02: Cost Tracking and Stderr Display Summary

## Summary

Added end-to-end token cost tracking (CLI-08) across four modules: State gains five cost fields,
StateMachine accumulates per-round usage from the updated L1Provider tuple return, save_state and
load_state write and read a cost section in state.json, and cli.py prints a human-readable cost
line to stderr after every review. All 946 tests pass.

## What Was Built

- **State dataclass (state.py)**: Added cost_total_input, cost_total_output, cost_total_duration,
  cost_passes, cost_per_pass fields. save_state writes a cost dict; load_state reads it with
  backward-compat defaults (data.get("cost", {})).

- **L1Provider protocol change (machine.py)**: Type alias changed from
  Callable[[], list[StateFinding]] to Callable[[], tuple[list[StateFinding], Usage, float]].
  Usage imported from llm_invoke. StateMachine.__post_init__ initializes _round_input_tokens,
  _round_output_tokens, _round_duration, _pass_counter round accumulators. Cost is accumulated
  at the end of _execute_round (after L0+L1+L2+E2E), not inside _run_l1_phase, satisfying
  the H3 fix requirement.

- **factories.py**: build_l1_provider stub path returns ([], Usage(), 0.0). Real path
  accumulates usage across the three review passes (qodo/expert/adversarial) and returns
  (all_candidates, Usage(total_input, total_output), total_duration).

- **cli.py cost display**: After StateMachine.run() completes, final state is loaded from disk
  (load_state(state_path)) and, when cost_passes > 0, prints to stderr:
  code-forge: cost: N tokens (IN in + OUT out), P passes, T.Ts

- **Test updates (6 files)**: All l1_provider=lambda: [] calls replaced with
  l1_provider=lambda: ([], Usage(), 0.0) in test_cli_hold_resume.py,
  test_consecutive_clean.py, test_ordering.py, test_round.py, test_machine_ci.py,
  test_machine_local.py.

- **New tests**: TestCostAccumulation in test_machine_local.py (3 tests);
  TestCostSummaryStderr in test_cli_integration.py (2 tests).

## Deviations from Plan

None. Plan executed exactly as written. All B4/B5/B6/B7/H3 fixes described in the plan are
present in the implementation.

## Self-Check: PASSED

Tests: 946 passed, 3 warnings (pre-existing pytest.mark.integration mark registration
warnings in test_lock_signals.py, unrelated to this plan)

Key artifacts verified:
- src/code_forge/state.py: contains cost_total_input field and cost section in save_state
- src/code_forge/machine.py: contains Usage import and _round_input_tokens accumulator
- src/code_forge/factories.py: stub returns ([], Usage(), 0.0) tuple
- src/code_forge/cli.py: contains code-forge: cost: stderr print

Commits:
- a9c8a37 wip: partial 08-02 changes before merging main for llm_invoke.py access
- 7d55044 feat(08-02): add cost tracking and stderr cost summary (CLI-08)
