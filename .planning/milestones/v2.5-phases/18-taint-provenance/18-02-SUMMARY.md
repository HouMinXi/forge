---
phase: 18-taint-provenance
plan: 02
subsystem: taint
tags: [taint, danger-score, trust, advisory, semgrep, provenance, L0, pipeline-wiring]
dependency_graph:
  requires: [taint.py/danger_score_from_diff, taint.py/TaintRunner, advisory.py/AdvisoryFinding, machine.py/_run_l0_phase, machine.py/_run_advisory_axes, cli.py/_run_hold_loop]
  provides: [forge-taint.yaml, provenance-question-in-adversarial-pass, pipeline-wiring-for-REVIEW-TRUST-01]
  affects: [machine.py, cli.py, pass3-adversarial.md, pyproject.toml]
tech_stack:
  added: []
  patterns: [semgrep taint rule YAML with focus-metavariable, git-toplevel test anchoring for worktree compatibility]
key_files:
  created:
    - src/code_forge/rules/forge-taint.yaml
    - tests/test_taint_rule.py
    - tests/test_taint_integration.py
  modified:
    - src/code_forge/machine.py
    - src/code_forge/cli.py
    - src/code_forge/skills/code-forge/passes/pass3-adversarial.md
    - pyproject.toml
decisions:
  - "D-12 open-as-sink deferred: open() appears only as source in forge-taint.yaml; self-loop false-positive avoidance"
  - "Provenance test uses git-rev-parse with cwd anchor to handle worktree vs editable-install path divergence"
metrics:
  duration: "19m 33s"
  completed: "2026-06-11T18:22:16Z"
  tasks: 2
  tests_added: 15
  tests_total: 1357
  files_created: 3
  files_modified: 4
---

# Phase 18 Plan 02: Pipeline Wiring + Semgrep Rule + Provenance Summary

Three REVIEW-TRUST-01 sub-capabilities wired into the live pipeline: danger-score as L0 blocking in _run_l0_phase, TaintRunner as advisory axis via advisory_runners, provenance question unconditionally in pass3-adversarial.md, plus semgrep taint rule validated via --validate and --test.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Semgrep taint rule file with --validate/--test verification | 7c46a15 | src/code_forge/rules/forge-taint.yaml, tests/test_taint_rule.py, pyproject.toml |
| 2 | Pipeline wiring + provenance prompt + integration tests | a4ee142 | src/code_forge/machine.py, src/code_forge/cli.py, pass3-adversarial.md, tests/test_taint_integration.py |

## What Was Built

### Semgrep taint rule (forge-taint.yaml)
- Two rules: forge-taint-config-to-subprocess (5 sinks) and forge-taint-config-to-network (3 sinks)
- mode: taint, severity: WARNING, languages: [python]
- Sources: os.environ, os.getenv, yaml.safe_load, yaml.load, json.load, open (read-mode only)
- All sinks use focus-metavariable for precise argument matching
- open() as source only, never as sink (D-12 self-loop constraint)
- Message includes "Intraprocedural only -- cross-function flows not detected"
- Validated via semgrep --validate and semgrep --test with ruleid/ok annotations

### Pipeline wiring (machine.py + cli.py)
- _run_l0_phase restructured: return moved to end of method; danger_score_from_diff called after existing L0 runner
- Non-git mode: loud-skip with infra_error "Danger-score requires a diff -- skipping in non-git mode"
- _run_advisory_axes: source_files injection from resolved_review.source_files before dispatch (D-09)
- _run_advisory_axes: infra_errors collection from runners that track them (hasattr check preserves Protocol narrowness)
- cli.py _run_hold_loop: fresh TaintRunner() created per cycle inside the for-loop, passed as advisory_runners=[_taint_runner] to StateMachine

### Provenance question (pass3-adversarial.md)
- New section "### External input provenance" inserted before "### Dismissal discipline"
- Text: "For each external input in the changed code: who controls the source of this data, and what is the worst value a malicious caller could inject?"
- Runs unconditionally every review (D-08) -- no diff content gate

### pyproject.toml
- package-data updated: rules/**/* added alongside skills/**/* so pip install bundles forge-taint.yaml

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] open() substring false-match in D-12 test**
- **Found during:** Task 1
- **Issue:** `"open(" not in pat` false-matched `subprocess.Popen` because "open(" is a substring of "Popen("
- **Fix:** Changed to regex word-boundary check `re.search(r"(?<![A-Za-z])open\(", pat)`
- **Files modified:** tests/test_taint_rule.py
- **Commit:** 7c46a15

**2. [Rule 1 - Bug] Provenance test path resolution in worktree**
- **Found during:** Task 2
- **Issue:** Editable install (`pip install -e .`) resolves code_forge to main repo src/, not worktree src/. Earlier tests change cwd, causing `git rev-parse --show-toplevel` to return main repo path where the edit does not exist.
- **Fix:** Pass `cwd=test_dir` (from `Path(__file__).resolve().parent`) to subprocess.run for git rev-parse, anchoring to the worktree regardless of pytest's current cwd.
- **Files modified:** tests/test_taint_integration.py
- **Commit:** a4ee142

## Test Coverage

- 8 tests in test_taint_rule.py: YAML structure validation (syntax, rule IDs, mode, severity, D-12 open-not-sink, focus-metavariable) + semgrep --validate + semgrep --test
- 7 tests in test_taint_integration.py: L0 wiring (danger-score in _run_l0_phase, danger-score in full round), non-git loud-skip, TaintRunner advisory dispatch, source_files injection, provenance question text, SC#5 corpus regression guard
- 15 new tests total, all passing
- Full suite: 1357 passed, 5 skipped, 0 failures (baseline was 1342)

## Self-Check: PASSED

- [x] src/code_forge/rules/forge-taint.yaml exists
- [x] tests/test_taint_rule.py exists
- [x] tests/test_taint_integration.py exists
- [x] Commit 7c46a15 verified in git log
- [x] Commit a4ee142 verified in git log
- [x] No stubs found
- [x] No accidental file deletions
- [x] danger_score_from_diff referenced in machine.py (count: 2)
- [x] TaintRunner referenced in cli.py (count: 3)
- [x] Provenance question in pass3-adversarial.md (count: 1)
- [x] mode: taint in forge-taint.yaml (count: 2)
- [x] rules/**/* in pyproject.toml (count: 1)
