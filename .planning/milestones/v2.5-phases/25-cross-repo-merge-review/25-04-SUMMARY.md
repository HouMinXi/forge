# Phase 25-04 Summary

**Wave 3: cli.py cross-repo dispatch + fail-closed gate-siblings load**
**Commits:** a7c58d2 (dispatch) + 4d707e7 (fail-open fix + dispatch test)
**Date:** reconstructed 2026-06-19

> Reconstructed post-hoc; original execution SUMMARY not persisted to main .planning/.
> Grounded in the merged diff AND the main-session independent verification (own
> bug-injection + unmocked real-path smoke) recorded before each ff-merge.

## What changed

src/code_forge/cli.py:
  - _load_gate_siblings(gate_yaml_path): reads gate.yaml directly via yaml.safe_load
    (NOT load_gate_config, per 25-04 R2). Missing/empty file -> ({}, None) single-repo;
    malformed yaml OR non-mapping -> CliError (fail-CLOSED, D-06). For inline backends
    this is the only gate.yaml parse, so a bad file can no longer silently hide siblings.
  - _cross_repo_verdict_or_none(...): extracted dispatch helper. If siblings present,
    validates with the NARROW base (gate_yaml_path.parent), guards baseline is a
    GitRefBaseline and head ref is not WORKING/INDEX (committed refs only), builds
    primary_ref from raw git refs (OPTION B, not the serialized baseline_repr), calls
    run_cross_repo, and returns its Verdict; otherwise returns None to fall through.
  - _run(): dispatch is a conditional early-return BEFORE the ForgeLock block; single-repo
    (no siblings) falls through to _run_hold_loop unchanged.

tests/test_cross_repo.py: test_single_repo_zero_drift, test_remote_url_rejected,
  test_malformed_gate_yaml_fails_closed (fail-closed proof), test_dispatch_verdict_helper
  (drives the dispatch helper both directions with run_cross_repo patched at its lazy
  import site).

## Must-haves verified (main-session, own runs)

- Fail-open CLOSED: malformed / non-mapping gate.yaml -> CliError, proven by an unmocked
  real-path smoke and by own bug-injection (reinjected fail-open -> dispatch test FAILED,
  restored -> PASS): YES
- Dispatch test actually exercises the conditional both directions (inverting the guard
  makes it FAIL): YES
- Narrow validation base (gate_yaml_path.parent); primary_ref via OPTION B; committed-ref
  + GitRefBaseline security guards fire unmocked: YES
- Scope was exactly cli.py + test_cross_repo.py; cross_repo.py + gate_check.py untouched
- 35 test_cross_repo.py tests pass; non-ASCII clean; ruff clean on changed regions
- Commit-message hygiene corrected on merge (review marker moved out of the message)

## Remaining in Phase 25 (not executed)

25-05 (D-20 integration tests), 25-06 (D-12/D-13 grouped verdict output), 25-07 (CLI chore:
remove deprecated --state-dir / --staged). Phase 25 is NOT complete; Waves 1-3 are.
