---
phase: 17-trust-gate-eval-scaffold
plan: 02
subsystem: cli-trust-guard, machine-advisory
tags: [security, trust, advisory, cli, machine, tdd]
dependency_graph:
  requires: [trust.py, advisory.py, TrustStatus, AdvisoryFinding, AxisRunner]
  provides: [trust-subcommand, trust-guard, advisory-wiring, advisory-serialization, advisory-display]
  affects: [cli.py, machine.py, test_outlet_resolver.py]
tech_stack:
  added: []
  patterns: [lazy-import, mutually-exclusive-argparse, atomic-json-write, post-convergence-dispatch]
key_files:
  created:
    - tests/test_cli_trust.py
    - tests/test_machine_advisory.py
  modified:
    - src/code_forge/cli.py
    - src/code_forge/machine.py
    - tests/test_outlet_resolver.py
decisions:
  - "Trust guard in _load_gate_backends returns [] with stderr warning for untrusted repos (D-06)"
  - "Trust subcommand uses mutually_exclusive_group for --status/--revoke (D-04)"
  - "_run_advisory_axes dispatches once in run() after _run_local/_run_ci returns (D-16)"
  - "Advisory findings serialize to advisory-findings.json via atomic tmp+replace (D-15)"
  - "Advisory display uses --- Advisory --- separator line on stderr (D-17)"
metrics:
  duration: 11m32s
  completed: 2026-06-10T01:26:47Z
  tasks_completed: 2
  tasks_total: 2
  tests_added: 21
  tests_passed: 21
---

# Phase 17 Plan 02: CLI Trust Guard + Machine Advisory Wiring Summary

Trust CLI subcommand (trust/--status/--revoke) wired into cli.py with _load_gate_backends guard that returns [] for untrusted repos, plus machine.py advisory_runners injection point with post-convergence dispatch, separate serialization, and split stderr display.

## Task Completion

| Task | Name | Type | Commits | Key Files |
|------|------|------|---------|-----------|
| 1 | Trust CLI subcommand + _load_gate_backends guard | auto/tdd | b38f751 (RED), cbfa20d (GREEN) | src/code_forge/cli.py, tests/test_cli_trust.py |
| 2 | Machine.py advisory wiring + display separation | auto/tdd | 0264adc (RED), 3fc93d5 (GREEN) | src/code_forge/machine.py, tests/test_machine_advisory.py |

## What Was Built

### cli.py changes
- `_load_gate_backends` trust guard: calls `is_trusted()` before loading backends; returns `[]` with stderr warning "Untrusted repo backends ignored" when untrusted (D-06, SEC-01)
- Guard handles None/non-dict gate.yaml gracefully (returns `[]`)
- Trust subcommand registered via `add_parser('trust')` with `--status` and `--revoke` as mutually exclusive flags (D-04)
- `_run_trust(args, cwd)`: bare trust shows dangerous fields on stderr then records trust; --status prints trust state; --revoke removes entry
- "trust" added to `known_subcommands` set for backward-compat routing

### machine.py changes
- `advisory_runners: list` field on StateMachine dataclass (injection point for future AxisRunner implementations)
- `self._advisories: list` initialized empty in `__post_init__` (D-14: separate from `_state.findings`)
- `_run_advisory_axes()`: iterates advisory_runners, calls `runner.run(diff_text, cwd)`, extends `_advisories`; exception-safe with infra_errors logging
- `_serialize_advisories()`: writes `advisory-findings.json` via atomic tmp+replace (D-15: separate file)
- `_display_advisories()`: stderr output with blank line + "--- Advisory ---" separator + per-finding `[AXIS] file:range - description` (D-17)
- All three methods called in `run()` after `_run_local()`/`_run_ci()` returns, before returning verdict (D-16)

### Test Coverage
- tests/test_cli_trust.py: 14 tests (trust subcommand, --status, --revoke, dangerous fields, guard, hostile gate.yaml, parser)
- tests/test_machine_advisory.py: 7 tests (init, fixpoint isolation, runners dispatch, serialization, display)
- Full suite: 1234 passed, 5 skipped, 0 failures

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed test_outlet_resolver broken by trust guard**
- **Found during:** Task 1 GREEN phase
- **Issue:** TestForgeBackendRealEntry.test_forge_backend_routes_via_real_entry creates a gate.yaml with backends and calls _run_resolve_outlet, which goes through _load_gate_backends. The new trust guard returned [] because the gate.yaml was not trusted, causing the test to fail.
- **Fix:** Added monkeypatch for XDG_CONFIG_HOME and record_trust() call in the test setup so the gate.yaml is trusted before the test exercises the outlet resolution path.
- **Files modified:** tests/test_outlet_resolver.py
- **Commit:** cbfa20d

## TDD Gate Compliance

| Task | RED Commit | GREEN Commit | REFACTOR Commit |
|------|-----------|-------------|-----------------|
| 1 | b38f751 (test) | cbfa20d (feat) | N/A |
| 2 | 0264adc (test) | 3fc93d5 (feat) | N/A |

Both tasks followed RED-GREEN sequence. No refactoring needed.

## Verification Results

All 21 new tests pass. Full suite (1234 tests) passes with zero failures. Acceptance criteria verified:
- `is_trusted` called in cli.py _load_gate_backends (2 occurrences)
- "Untrusted repo backends ignored" message in cli.py
- Trust subcommand parser registered at line 473
- "trust" in known_subcommands set
- `_advisories` referenced 12 times in machine.py
- `advisory_runners` referenced 3 times in machine.py
- `_run_advisory_axes` method exists as dispatch point
- Advisory findings never appear in _fixpoint_reached or convergence logic
- test_hostile_gate_yaml_no_exfil exists and passes (SEC-01 SC2)
- test_trust_displays_dangerous_fields exists and passes (D-05)

## Self-Check: PASSED
