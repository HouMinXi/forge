---
phase: 28-reviewer-canary-inline
plan: 02
subsystem: canary-infra
tags: [verdict, exit-code, gate-yaml, schema, validation, tdd]
dependency_graph:
  requires: []
  provides: [Verdict.UNRELIABLE, EXIT_UNRELIABLE, validate_canary_config, canary-schema]
  affects: [state.py, exit_codes.py, gate_check.py, gate.schema.json, init_template.py]
tech_stack:
  added: []
  patterns: [validate_graph_triage pattern reuse for canary validation]
key_files:
  created:
    - tests/test_canary_cli.py
  modified:
    - src/code_forge/state.py
    - src/code_forge/exit_codes.py
    - src/code_forge/gate_check.py
    - src/code_forge/gate.schema.json
    - src/code_forge/init_template.py
decisions:
  - "additionalProperties: true in canary schema (SF2-2, matches all other sections)"
  - "threshold_ratio rejects 0.0 because ceil(0*n)=0 crashes M1 evaluator"
  - "n range locked to 3..5 per D-28-03"
  - "bool exclusion in isinstance checks prevents YAML true/false coercion to int"
metrics:
  duration: 4m 53s
  completed: 2026-06-25
  tasks: 2/2
  test_count: 20
---

# Phase 28 Plan 02: Canary Infrastructure Summary

Verdict.UNRELIABLE (exit 7) + validate_canary_config (n: 3..5, ratio: >0.0..1.0) + gate.schema.json canary object with additionalProperties: true + init template commented-out canary block.

## Commits

| Hash | Type | Description |
|------|------|-------------|
| bd3f5fc | test | Failing tests for Verdict.UNRELIABLE and EXIT_UNRELIABLE (RED) |
| dfa5975 | feat | Verdict.UNRELIABLE enum member + EXIT_UNRELIABLE=7 + verdict_to_exit mapping (GREEN) |
| 0f67cbe | test | Failing tests for canary validation, schema, and template (RED) |
| 22a7fc3 | feat | validate_canary_config + gate.schema.json canary + init template (GREEN) |

## What Was Built

### Task 1: Verdict.UNRELIABLE + EXIT_UNRELIABLE

- Added `UNRELIABLE = "UNRELIABLE"` to the Verdict enum in state.py
- Added `EXIT_UNRELIABLE = 7` to exit_codes.py
- Wired `verdict_to_exit(Verdict.UNRELIABLE) -> 7` before the PENDING raise
- Exit code uniqueness guard verifies all 8 codes (0-7) are distinct

### Task 2: gate.yaml canary: block validation + schema + template

- `validate_canary_config(section)` in gate_check.py follows validate_graph_triage pattern:
  - Not a mapping -> ValueError mentioning "mapping"
  - enabled: must be bool (YAML true/false bool coercion excluded via isinstance check)
  - n: must be int in 3..5 (D-28-03 locked range)
  - threshold_ratio: must be numeric, > 0.0 and <= 1.0 (0.0 rejected because ceil(0*n)=0)
  - Unknown keys allowed (forward-compatible)
- `load_gate_config` dispatches to `validate_canary_config` when canary: present
- gate.schema.json: canary object with n (min 3, max 5), threshold_ratio (exclusiveMinimum 0.0, max 1.0), additionalProperties: true (SF2-2)
- init_template.py: commented-out canary block appended for discoverability

## Test Coverage

20 tests in tests/test_canary_cli.py:
- 4 verdict/exit-code tests (enum member, exit value, mapping, uniqueness)
- 13 validation tests (valid config, type errors, range errors, boundaries, backward compat)
- 3 boundary tests (n=3, n=5, ratio=1.0, ratio=0.01, int ratio)
- 1 init template test

## Deviations from Plan

None - plan executed exactly as written.

## TDD Gate Compliance

- RED gate: bd3f5fc (Task 1), 0f67cbe (Task 2) -- both test commits exist before implementation
- GREEN gate: dfa5975 (Task 1), 22a7fc3 (Task 2) -- both feat commits exist after tests
- REFACTOR gate: not needed (no cleanup required)

## Self-Check: PASSED

All 6 files found. All 4 commits verified in git log.
