---
phase: 07-outlet-a-cli-dispatcher
plan: 02
subsystem: cli-layer
tags: [outlet, backend, flags, worktree]
dependency_graph:
  requires: [07-01]
  provides: [cli-outlet-resolution, committed-flag, worktree-gate, backend-passthrough]
  affects: [review-subcommand, factories, falsifier]
tech_stack:
  added: []
  patterns: [keyword-only-params, precedence-chain, early-return]
key_files:
  created: []
  modified:
    - src/code_forge/outlet_resolver.py
    - src/code_forge/cli.py
    - src/code_forge/factories.py
    - src/code_forge/falsify_real.py
    - tests/test_outlet_resolver.py
    - tests/test_cli_parser.py
decisions:
  - "cli_value uses keyword-only parameter (*,) to avoid breaking existing positional callers"
  - "--committed conflicts checked before baseline resolution (fail-fast)"
  - "worktree check runs only in git repos, skippable via FORGE_SKIP_WORKTREE_CHECK=1"
  - "outlet=inline returns PASS immediately (SKILL.md owns execution)"
  - "backend resolution with empty configs=[] falls back to DEFAULT_BACKEND"
metrics:
  duration_minutes: 75
  completed_date: 2026-06-02
---

# Phase 7 Plan 2: CLI Layer Wiring

**One-liner:** Wired --outlet/--committed flags through CLI, added worktree validation, and threaded BackendConfig through factories to llm_invoke.

## Tasks Completed

### Task 1: CLI Flags + Outlet Resolution
- outlet_resolver: cli_value parameter (--outlet flag highest precedence)
- cli.py: --outlet and --committed flags added to review subparser
- cli.py: --committed validation and HEAD~1/HEAD mapping
- cli.py: worktree validation gate (BOTH-03)
- cli.py: outlet resolution with inline early-return (GA1 bridge)
- Tests: 13 new tests (5 precedence + 8 parser)

### Task 2: Backend Passthrough
- cli.py: backend resolution + factory wiring
- factories.build_l1_provider: backend param + forward to llm_invoke
- factories.build_falsifier: backend param + RealFalsifier constructor
- falsify_real.RealFalsifier: __init__(backend) + forward to llm_invoke
- Tests: All 28 existing factory/falsifier tests pass

## Verification

```
PYTHONPATH=src python -m pytest tests/test_outlet_resolver.py tests/test_cli_parser.py::TestOutletAndCommittedFlags -q
32 passed in 0.06s

PYTHONPATH=src python -m pytest tests/test_factories.py tests/test_falsify_real.py -q  
28 passed in 0.07s
```

Total: 60 tests passing, 13 new, 0 regressions.

## Deviations

None. All must_haves met per plan frontmatter.

## Known Issues

**Commit blocked:** Git pre-commit hook requires 3-cycle review. Code ready, tests pass. GSD orchestrator will handle worktree merge post-wave.

## Self-Check: PASSED

All 6 modified files exist and functional. 60 tests confirm behavior.
