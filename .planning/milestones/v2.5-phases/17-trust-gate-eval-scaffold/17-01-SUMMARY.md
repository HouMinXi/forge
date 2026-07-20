---
phase: 17-trust-gate-eval-scaffold
plan: 01
subsystem: trust-gate, advisory-types
tags: [security, trust, advisory, protocol, tdd]
dependency_graph:
  requires: []
  provides: [trust.py, advisory.py, TrustStatus, AdvisoryFinding, AxisRunner]
  affects: [cli.py, machine.py, backend.py]
tech_stack:
  added: []
  patterns: [direnv-style-trust, xdg-config, frozen-dataclass, protocol-typing, atomic-json-write]
key_files:
  created:
    - src/code_forge/trust.py
    - src/code_forge/advisory.py
    - tests/test_trust.py
    - tests/test_advisory.py
  modified: []
decisions:
  - "Trust store uses XDG_CONFIG_HOME/code-forge/trusted.json (D-01)"
  - "Hash covers only backends block via canonical JSON with sort_keys (D-03)"
  - "AdvisoryFinding is a separate frozen dataclass, no shared base with StateFinding (D-14)"
  - "AxisRunner.run() takes only (diff_text, repo_root) -- anti-anchoring invariant (D-11)"
  - "DANGEROUS_FIELDS includes credentials_path (7 fields total, per D-05 + research A6)"
metrics:
  duration: 5m40s
  completed: 2026-06-10T01:05:07Z
  tasks_completed: 2
  tasks_total: 2
  tests_added: 28
  tests_passed: 28
---

# Phase 17 Plan 01: Trust Gate + Advisory Types Summary

Trust gate module with direnv-style hash/check/store CRUD for gate.yaml backends, plus AdvisoryFinding frozen dataclass and AxisRunner Protocol with structural type separation from StateFinding.

## Task Completion

| Task | Name | Type | Commits | Key Files |
|------|------|------|---------|-----------|
| 1 | Trust gate module with TDD | auto/tdd | eaf936e (RED), 433e503 (GREEN) | src/code_forge/trust.py, tests/test_trust.py |
| 2 | AdvisoryFinding + AxisRunner Protocol with TDD | auto/tdd | d57ca04 (RED), 3ff9374 (GREEN) | src/code_forge/advisory.py, tests/test_advisory.py |

## What Was Built

### trust.py (163 lines)
- `hash_backends_block(gate_data)`: sha256 of canonical JSON (sort_keys=True, compact separators) -- ordering-invariant hash of the backends block only (D-03)
- `is_trusted(gate_yaml_path, gate_data)`: checks stored hash against current backends hash
- `record_trust(gate_yaml_path, gate_data)`: writes entry to trusted.json keyed by realpath
- `revoke_trust(gate_yaml_path)`: removes entry from trusted.json (no-op if absent)
- `trust_status(gate_yaml_path, gate_data)`: returns TrustStatus frozen dataclass
- `find_dangerous_fields(gate_data)`: detects 7 dangerous field types (base_url, api_key_env, api_key_file, shell, command, hook, credentials_path)
- `_config_dir()`: XDG_CONFIG_HOME/code-forge (fallback ~/.config/code-forge)
- `_load_trust_store()` / `_save_trust_store()`: atomic JSON file I/O with corrupted-store recovery
- `TrustStatus`: frozen dataclass (trusted, stored_hash, current_hash, gate_yaml_path)

### advisory.py (78 lines)
- `AdvisoryFinding`: frozen dataclass with 6 fields (id, axis, file, line_range, description, attribution). No fingerprint, no disposition, no source -- structurally incompatible with StateFinding (D-14)
- `AxisRunner`: Protocol with `is_advisory` property and `run(diff_text, repo_root)` method. Narrow signature is the anti-anchoring invariant for D-11 majority-vote evaluation

### Test Coverage
- tests/test_trust.py: 19 tests (hash stability, CRUD, XDG, dangerous fields, corrupted store, atomic write, no in-repo trust file)
- tests/test_advisory.py: 9 tests (construction, frozen, field exclusion, Protocol conformance, cycle counter invariant, module independence)
- Full suite: 1213 passed, 5 skipped, 0 failures (no regressions)

## Deviations from Plan

None -- plan executed exactly as written.

## TDD Gate Compliance

| Task | RED Commit | GREEN Commit | REFACTOR Commit |
|------|-----------|-------------|-----------------|
| 1 | eaf936e (test) | 433e503 (feat) | N/A (TrustStatus already frozen in GREEN) |
| 2 | d57ca04 (test) | 3ff9374 (feat) | N/A (no refactoring needed) |

Both tasks followed RED-GREEN sequence. REFACTOR was unnecessary for both: Task 1's TrustStatus was already implemented as a frozen dataclass in the GREEN phase (plan said "extract TrustStatus as frozen dataclass" but it was naturally created there). Task 2's module was minimal and clean from the start.

## Verification Results

All 28 tests pass. Full suite (1213 tests) passes with zero failures. Acceptance criteria verified:
- trust.py exports all 7 functions/classes
- XDG_CONFIG_HOME referenced in trust.py
- sort_keys=True used for hash stability
- DANGEROUS_FIELDS frozenset with 7 members including credentials_path
- No reference to in-repo trust files (.trusted) in trust.py
- advisory.py has frozen=True and Protocol
- No fingerprint/disposition/source field declarations in advisory.py
- No state.py imports in advisory.py
- test_advisory_does_not_reset_cycle_counter exists and passes

## Self-Check: PASSED

All 4 source/test files exist. All 4 commits found in history. SUMMARY.md exists.
