---
phase: 00-config-bootstrap-gate-yaml-baseline
plan: 01
subsystem: config
tags: [gate-yaml, gitignore, baseline, bootstrap]
dependency_graph:
  requires: []
  provides: [gate-yaml-config, gitignore-exception, test-baseline]
  affects: [.forge/gate.yaml, .gitignore]
tech_stack:
  added: [YAML config, pytest baseline JSON]
  patterns: [config-first, gitignore-exception]
key_files:
  created:
    - .forge/gate.yaml
    - .forge/test_baseline.json (gitignored)
  modified:
    - .gitignore
decisions:
  - PYTHONPATH=src is mandatory: bare pytest hits 44 import errors from src/ layout
  - test_baseline.json stays gitignored (runtime data, not config)
  - gate.yaml committed via !.forge/gate.yaml exception after .forge/* wildcard
  - Phase accepted retroactively as config commit, no formal worktree required
metrics:
  duration: retroactive
  tasks_completed: 3
  files_created: 1
  files_modified: 2
  tests_added: 0
  tests_total: 521
  commits: 1
completed_date: 2026-05-25
---

# Phase 00 Plan 01: Gate Config Bootstrap

**One-liner:** Created `.forge/gate.yaml` with test command spec and `.gitignore` exception, unblocking Phase 1 gate-check from a chicken-and-egg deadlock.

## Tasks Completed

### Task 1: Create .forge/gate.yaml

Gate configuration consumed by Phase 1 `forge gate-check`:

```yaml
test:
  command: ["python3", "-m", "pytest", "tests/", "-q"]
  env:
    PYTHONPATH: "src"
  timeout_seconds: 120
  cwd: "."
source_patterns: ["*.py"]
```

`test.env.PYTHONPATH=src` is mandatory -- bare `python3 -m pytest` fails with 44 import errors due to `src/` layout.

### Task 2: Update .gitignore

Added `!.forge/gate.yaml` exception after `.forge/*` wildcard so the config can be committed.

### Task 3: Generate test_baseline.json

521 tests, 0 known failures. Gitignored at `.forge/test_baseline.json`. Allows Phase 1 gate-check to compute new-failure deltas rather than blocking on all pre-existing failures.

## Exit Criteria Verified

- [x] `.forge/gate.yaml` valid YAML with test.command, test.env, test.timeout_seconds, test.cwd
- [x] `.gitignore` has `!.forge/gate.yaml` exception after `.forge/*`
- [x] `.forge/test_baseline.json` generated with schema_version, generated_at, total_tests, known_failures
- [x] bare `python3 -m pytest` fails (44 import errors); `PYTHONPATH=src` passes (521 tests)
- [x] gate.yaml passes yamllint
