---
phase: 17-trust-gate-eval-scaffold
plan: 04
subsystem: cli-eval, corpus
tags: [eval, cli, corpus, tdd, integration]
dependency_graph:
  requires:
    - phase: 17-02
      provides: trust subcommand pattern, known_subcommands set
    - phase: 17-03
      provides: eval subpackage (corpus.py, scorer.py, runner.py)
  provides:
    - eval CLI subcommand (code-forge eval --corpus --backend)
    - populated corpus manifest with 9 named entries
    - 8 real non-empty diff files (E1-E6, BUG-P12-01, ttl_class)
  affects: [cli.py]
tech_stack:
  added: []
  patterns: [lazy-import-cli, non-review-subcommand-error-convention]
key_files:
  created:
    - tests/test_cli_eval.py
    - tests/eval/corpus/diffs/E1-stale-nftables.diff
    - tests/eval/corpus/diffs/E2-pcap-suffix.diff
    - tests/eval/corpus/diffs/E3-transit-probe.diff
    - tests/eval/corpus/diffs/E4-curl-tproxy.diff
    - tests/eval/corpus/diffs/E5-fast-502.diff
    - tests/eval/corpus/diffs/E6-reprobe-blackout.diff
    - tests/eval/corpus/diffs/BUG-P12-01.diff
    - tests/eval/corpus/diffs/ttl_class.diff
  modified:
    - src/code_forge/cli.py
    - tests/eval/corpus/corpus.yaml
decisions:
  - "_run_eval returns EXIT_CLI_ERROR directly (non-review subcommand convention, matches gate-check/mutation-check)"
  - "E1-E6 diffs extracted as forward diffs (fix patches) from surflare-watchdog commits"
  - "BUG-P12-01 and ttl_class diffs constructed as realistic representations (original commits lost to filter-repo purge)"
  - "Tests mock at source module level (code_forge.eval.*) since cli.py uses lazy imports"
metrics:
  duration: 6m28s
  completed: 2026-06-10T02:04:04Z
  tasks_completed: 2
  tasks_total: 2
  tests_added: 17
  tests_passed: 17
---

# Phase 17 Plan 04: Eval CLI Subcommand + Corpus Population Summary

Eval CLI entry point wired into cli.py with _run_eval handler, corpus manifest populated with all 9 EVAL-01 SC1 named entries (E1-E6 runtime escapes, gate-yaml-rce, BUG-P12-01 hollow test, ttl_class wrong argument), each backed by a real non-empty diff file.

## Task Completion

| Task | Name | Type | Commits | Key Files |
|------|------|------|---------|-----------|
| 1 | Eval CLI subcommand + corpus population | auto/tdd | e4ccdce (RED), 0084335 (GREEN) | src/code_forge/cli.py, tests/test_cli_eval.py, tests/eval/corpus/corpus.yaml, 8 diff files |
| 2 | Full suite regression + carry-forward verification | auto | (verification only) | No changes |

## What Was Built

### cli.py changes
- `_run_eval(args)` handler: validates --runs >= 1, lazy-imports eval subpackage, loads corpus, replays entries, prints format_table to stderr, optionally writes JSON via write_json_report. Returns EXIT_CLI_ERROR (2) on validation/file/parse errors, EXIT_PASS (0) on success.
- Eval subcommand parser registered via `add_parser('eval')` with --corpus (required, Path), --backend (required, str), --runs (optional int), --output (optional Path).
- `'eval'` added to `known_subcommands` set for backward-compat routing.
- Dispatch in `main()`: `elif args.subcommand == 'eval': return _run_eval(args)`.

### Corpus population (9 entries)
- **gate-yaml-rce**: hostile gate.yaml with attacker base_url (existing from Plan 03)
- **E1-stale-nftables** (c327de6): stale nftables flush before packet trace
- **E2-pcap-suffix** (a15d410): pcap filename suffix and orphan sleep on SIGTERM
- **E3-transit-probe** (2694bc0): routing readiness poll and settle time
- **E4-curl-tproxy** (bcfb78b): transit probe measuring real VPN latency
- **E5-fast-502** (ff564c4): Google http_code validation in transit probe
- **E6-reprobe-blackout** (5d385f8): transit cache, health probe early-exit, safety fixes
- **BUG-P12-01**: hollow test asserting NotImplementedError against stub (constructed)
- **ttl_class**: wrong ttl_class='standard' argument in request builder (constructed)

E1-E6 extracted from ~/code/surflare-watchdog commit history. BUG-P12-01 and ttl_class constructed as realistic representations of the documented bugs (original forge commits lost to filter-repo history purge).

### Test Coverage
- tests/test_cli_eval.py: 17 test functions (7 parser, 7 dispatch, 1 known_subcommands, 2 corpus completeness)
- Full suite: 1292 passed, 5 skipped, 0 failures (17 new tests, no regressions)

## Carry-Forward Verification

| Item | Check | Result |
|------|-------|--------|
| Carry-forward 1 | test_advisory_does_not_reset_cycle_counter passes | PASS |
| Carry-forward 2 | grep percentage/percent scorer.py = 0 | PASS |
| Carry-forward 3 | grep entry_points/plugin runner.py = 0 | PASS |
| Carry-forward 4 | grep .trusted trust.py = 0 | PASS |
| SEC-01 | test_hostile_gate_yaml_no_exfil passes | PASS |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test mock targets for lazy imports**
- **Found during:** Task 1 GREEN phase
- **Issue:** Tests patched `code_forge.cli.load_corpus` etc., but _run_eval uses lazy local imports so those names do not exist at cli module level.
- **Fix:** Changed patch targets to source modules: `code_forge.eval.corpus.load_corpus`, `code_forge.eval.runner.replay_entry`, `code_forge.eval.scorer.*`.
- **Files modified:** tests/test_cli_eval.py
- **Commit:** 0084335

## TDD Gate Compliance

| Task | RED Commit | GREEN Commit | REFACTOR Commit |
|------|-----------|-------------|-----------------|
| 1 | e4ccdce (test) | 0084335 (feat) | N/A |
| 2 | (verification only -- no code changes) | | |

Task 1 followed RED-GREEN sequence. Task 2 is verification-only (no TDD applicable).

## Verification Results

All 17 new tests pass. Full suite (1292 tests) passes with zero failures and no regressions.

Acceptance criteria verified:
- add_parser('eval') registered in cli.py (line 488)
- _run_eval function exists in cli.py (2 references: definition + dispatch)
- Lazy imports: load_corpus, replay_entry, compute_summary, format_table all referenced in cli.py
- 'eval' in known_subcommands set
- corpus.yaml contains all 9 named entries
- All 9 diff files exist in tests/eval/corpus/diffs/ and are non-empty
- tests/test_cli_eval.py has 17 test functions (exceeds minimum 5)
- --runs 0 and --runs -1 return EXIT_CLI_ERROR with "--runs must be >= 1"
- Missing corpus file returns EXIT_CLI_ERROR
- Malformed YAML corpus returns EXIT_CLI_ERROR
- format_table output appears on stderr

## Self-Check: PASSED

All created files exist:
- tests/test_cli_eval.py
- tests/eval/corpus/diffs/E1-stale-nftables.diff
- tests/eval/corpus/diffs/E2-pcap-suffix.diff
- tests/eval/corpus/diffs/E3-transit-probe.diff
- tests/eval/corpus/diffs/E4-curl-tproxy.diff
- tests/eval/corpus/diffs/E5-fast-502.diff
- tests/eval/corpus/diffs/E6-reprobe-blackout.diff
- tests/eval/corpus/diffs/BUG-P12-01.diff
- tests/eval/corpus/diffs/ttl_class.diff
All 2 commits found in history (e4ccdce, 0084335).
