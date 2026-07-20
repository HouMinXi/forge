---
phase: 22-graph-triage
plan: 01
subsystem: graph-triage
tags: [advisory-axis, blast-radius, sem-cli, graphdb, gate-check]
dependency_graph:
  requires: [advisory.py, gate_check.py]
  provides: [GraphTriageRunner, find_entity_dependents, validate_graph_triage]
  affects: [machine.py, cli.py]
tech_stack:
  added: []
  patterns: [AxisRunner-Protocol, tool-absent-loud-fail, IMPORTS_FROM-disambiguation]
key_files:
  created:
    - src/code_forge/graph_triage.py
    - tests/test_graph_triage.py
  modified:
    - src/code_forge/gate_check.py
    - tests/test_gate_check.py
decisions:
  - "sem CLI is preferred backend; graph.db is degraded fallback with quality caveat"
  - "gate.yaml graph_triage.enabled=false disables even when backends detected"
  - "find_entity_dependents() exported as public utility for future axes"
metrics:
  duration: "12m 9s"
  completed: "2026-06-14T09:52:00Z"
  tasks: 2
  tests_added: 30
  tests_total: 1707
---

# Phase 22 Plan 01: GraphTriageRunner Core + gate_check Validation Summary

GraphTriageRunner advisory axis with dual-backend detection (sem CLI preferred, graph.db SQLite fallback), blast-radius ranking of top-10 entities by downstream impact, gate.yaml schema validation, and find_entity_dependents utility for cross-phase consumption.

## What Was Built

- **src/code_forge/graph_triage.py** (new, 370 lines): GraphTriageRunner class implementing AxisRunner Protocol (is_advisory=True). Dual-backend detection via _detect_backend() with priority: sem CLI > gate.yaml db_path > auto-discover > CRG_DB_PATH env. sem backend uses subprocess.run with list args (no shell=True) for sem diff --patch and sem impact --json. graphdb backend uses sqlite3 with IMPORTS_FROM disambiguation to reduce short-name false positives. Top-10 entities by downstream impact count, sorted descending. find_entity_dependents() public utility exported for future axes.

- **src/code_forge/gate_check.py** (modified): Added validate_graph_triage() function validating graph_triage section schema (enabled: bool, db_path: str, unknown keys allowed for forward-compatibility). Integrated into load_gate_config() after presubmit validation block.

- **tests/test_graph_triage.py** (new, 25 tests): Covers AxisRunner Protocol compliance, empty/whitespace diff guards, backend detection priority (sem preferred, graphdb fallback, gate.yaml override, env var, both absent), tool-absent loud-fail with infra_errors, explicit disable via gate.yaml, sem diff/impact invocation with list args, ranking top-10 with 15 entities, unnamed entity skipping (module-level, lines N), timeout handling per entity, AdvisoryFinding format verification, graphdb node queries with IMPORTS_FROM disambiguation, graphdb quality caveat in attribution, no shell=True source check, find_entity_dependents with sem/graphdb/none backends.

- **tests/test_gate_check.py** (modified, 5 new tests): Covers graph_triage valid config, invalid enabled type, invalid db_path type, absent section OK, extra keys forward-compatible.

## TDD Gate Compliance

- RED commit: 302337a (test(22-01): all 30 tests fail with ModuleNotFoundError/ValueError)
- GREEN commit: aae417b (feat(22-01): all 30 tests pass, 1707 total suite green)
- REFACTOR: not needed (implementation clean on first pass)

## Task Log

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | RED: failing tests | 302337a | tests/test_graph_triage.py, tests/test_gate_check.py |
| 2 | GREEN: implementation | aae417b | src/code_forge/graph_triage.py, src/code_forge/gate_check.py |

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None. All code paths are fully wired with real backend detection and subprocess/sqlite3 invocation.

## Threat Surface Verification

All mitigations from the plan's threat model are implemented:
- T-22-01: subprocess.run uses list args only, never shell=True (verified by test_no_shell_true)
- T-22-02: validate_graph_triage strict schema validation added to gate_check.py
- T-22-03: parameterized queries with ? placeholders in all sqlite3 calls
- T-22-04: _SEM_TIMEOUT_S=15 per entity; TimeoutExpired caught, entity skipped
- T-22-05: accepted (graph.db is repo-local)
- T-22-SC: no new packages installed

No new threat surface introduced beyond what the plan covers.

## Self-Check: PASSED

- [x] src/code_forge/graph_triage.py exists (FOUND)
- [x] tests/test_graph_triage.py exists (FOUND)
- [x] Commit 302337a exists (RED)
- [x] Commit aae417b exists (GREEN)
- [x] 1707 tests pass, 0 failures, 5 skipped
