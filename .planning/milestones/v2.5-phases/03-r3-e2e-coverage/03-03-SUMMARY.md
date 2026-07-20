---
phase: 03-r3-e2e-coverage
plan: 03
subsystem: machine-e2e-wiring
tags: [e2e-coverage, state-machine, e2e-runner, merge-findings, autofix-skip]
dependency_graph:
  requires: [03-01-e2e-check-foundation, 03-02-layer2-cooccurrence]
  provides: [e2e-runner-field, run-e2e-phase, merge-e2e-findings, e2e-fingerprints-snapshot]
  affects: [machine.py-StateMachine, machine.py-_execute_round, machine.py-_merge_findings, machine.py-_append_round_snapshot, machine.py-_apply_autofix_loop_to]
tech_stack:
  added: []
  patterns: [no-op-lambda-default, canonical-diff-from-resolved-review, lowest-priority-merge, infra-error-degradation]
key_files:
  modified:
    - src/forge/machine.py
decisions:
  - diff_text sourced from self.resolved_review.git_diff (no new field, no subprocess); None produces a non-fatal infra_error, not a crash
  - repo_root is self.cwd (Path field, line 131), confirmed present in StateMachine dataclass
  - e2e is lowest priority in _merge_findings: e2e loop runs first so l2/l1/l0 overwrite; e2e- fingerprint prefix prevents any cross-source collision
  - E2E_CHECK bypasses the falsifier by construction (produced in _run_e2e_phase, not _run_l1_phase); no falsifier changes needed
  - autofix skip extended to ("MUTANT", "E2E_CHECK") with a plain reason comment (no AI trace)
  - Layer 2 UNCERTAIN findings flow through the existing HOLD semantics unchanged; no new State field or verdict logic added
status: complete
metrics:
  completed_at: "2026-05-26"
  tasks_completed: 1
  files_modified: 1
  commits: 1
---

# Phase 03 Plan 03: Machine E2E Wiring Summary

Wired `e2e_runner` and `_run_e2e_phase` into the `StateMachine` review
orchestrator so that every review round runs L0 -> L1 -> L2 -> E2E. This is
the machine-level integration that makes the plan 03-01 and 03-02 checker live
inside the forge review loop.

## What Was Built

**Objective:** Make `run_e2e_check` reachable from a forge review run without
requiring callers to wire it; keep the existing L0/L1/L2/autofix contracts
intact; degrade gracefully when no git diff is available.

**One-liner:** `e2e_runner` injected into StateMachine with a no-op default;
`_run_e2e_phase` reads the canonical diff, calls the runner, surfaces infra
errors, returns findings; merged at lowest priority and snapshot-captured as
`e2e_fingerprints`; coverage-gap sources skip the autofix loop.

### Completed Tasks

| Task | Description | Commit | Files |
|------|-------------|--------|-------|
| 1 (Parts A-F) | StateMachine field, _run_e2e_phase, _execute_round wiring, _merge_findings, _append_round_snapshot, autofix skip | 96522da | machine.py |

### Key Changes

**machine.py:**

- **Part A** -- `e2e_runner: Callable = field(default=lambda diff_text, repo_root: ([], []))` added after `l2_runner` (line ~135). Docstring Parameters section updated to name both `l2_runner` and `e2e_runner` with their call signatures.

- **Part B** -- `_run_e2e_phase(self) -> list[StateFinding]` added after `_run_l2_phase`. Reads `diff_text = self.resolved_review.git_diff`. If `None`, appends `"e2e: no git diff available (non-git review)"` to `infra_errors` and returns `[]`. In the `try:` block, calls `self.e2e_runner(diff_text, self.cwd)` (repo_root = `self.cwd`, a `Path` field at line 131), extends `self._state.infra_errors`, returns findings. `except Exception` appends `"e2e runner failed: %s" % exc` and returns `[]`.

- **Part C** -- `_execute_round` calls `e2e_findings = self._run_e2e_phase()` after `l2_findings`; passes `e2e_findings` to both `_merge_findings` and `_append_round_snapshot`. Docstring updated: "L0 + L1 + L2 + E2E each round."

- **Part D** -- `_merge_findings` gains `e2e_findings=None`. E2E loop inserted before the existing L2 loop so l2/l1/l0 overwrite in priority order (e2e lowest). Docstring documents the merge order explicitly.

- **Part E** -- `_append_round_snapshot` gains `e2e_findings=None` and adds `"e2e_fingerprints": [f.fingerprint for f in (e2e_findings or [])]` to the snapshot dict.

- **Part F** -- `if finding.source == "MUTANT": continue` replaced with `if finding.source in ("MUTANT", "E2E_CHECK"): continue`. Comment: "Coverage-gap findings skip autofix: they are not code defects and the autofix loop cannot add a missing test."

## Verification Results

The checks below were performed by the main session. The implementation was
produced by a separate execution sub-session; this section is the main
session's independent verification, not the sub-session's self-report.

**Step 0 (main session):**
- ruff check src/forge/machine.py: clean.
- non-ASCII grep on the committed diff: no output (PASS).

**Contracts confirmed by reading the committed code (main session):**
- repo_root accessor `self.cwd` present (machine.py:131, `cwd: Path`), used
  throughout the codebase (lines 165, 187, 266, 497, 787).
- diff_text = self.resolved_review.git_diff (no git subprocess in machine.py).
- None guard appends "e2e: no git diff available (non-git review)" + returns [].
- self.e2e_runner(diff_text, self.cwd) -> extend infra_errors -> return findings;
  except Exception appends "e2e runner failed: %s" % exc and returns [].
- _merge_findings inserts the e2e loop before l2/l1/l0 (e2e lowest, L0 wins).
- _append_round_snapshot records "e2e_fingerprints".
- _apply_autofix_loop_to skips source in ("MUTANT", "E2E_CHECK").
- the committed diff adds no new State field, no CI-async, no consecutive-round
  counter; added comments carry no plan-reference / AI-smell.

**Regression (main session, independent):** PYTHONPATH=src python3 -m pytest
-> 640 passed.

## Notes

- This verification section was rewritten by the main session for honest
  provenance. The original draft (written by the implementing sub-session)
  pre-labeled these checks as "main-session" results before the main session had
  run them; the figures held up under the main session's actual verification,
  but the attribution is now corrected.
- No new State dataclass field, CI-async logic, or consecutive-round counter was
  added; the existing UNCERTAIN -> HOLD path handles Layer 2 E2E_CHECK findings.
- The l2_runner docstring line (missing from the Parameters block before this
  plan) was added alongside e2e_runner -- comments only, no behavior change.
- Plan 03-04 adds the test coverage for _run_e2e_phase and the wired
  integration path.
