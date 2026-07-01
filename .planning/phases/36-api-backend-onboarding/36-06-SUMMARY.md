---
phase: 36-api-backend-onboarding
plan: 06
subsystem: cli-ux
tags: [error-handling, remediation, usability]
dependency_graph:
  requires: ["36-01", "36-04", "36-05"]
  provides: ["CliError.remediation field", "actionable error hints"]
  affects: ["src/code_forge/errors.py", "src/code_forge/cli.py", "src/code_forge/install_hooks.py"]
tech_stack:
  added: []
  patterns: ["optional-kwarg backward compat"]
key_files:
  created: []
  modified:
    - src/code_forge/errors.py
    - src/code_forge/cli.py
    - src/code_forge/install_hooks.py
decisions:
  - "Remediation rendered as 'Hint: ...' on stderr, not inline with error message"
  - "init catches both FileExistsError and NotADirectoryError for .code-forge-as-file"
  - "install-skill _show_available defined as closure to avoid passing src_root"
metrics:
  duration: "11m"
  completed: "2026-07-01"
  tasks: 2
  files: 3
---

# Phase 36 Plan 06: Error Remediation Summary

CliError gains optional remediation kwarg; 8 operational errors carry fix hints; install-hooks/skill UX improved.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add remediation field to CliError and backfill operational errors | 1c5ee12 | errors.py, cli.py |
| 2 | Fix install-hooks and install-skill UX errors | e14f78c | install_hooks.py, cli.py |

## Findings Addressed

| ID | Severity | Fix |
|----|----------|-----|
| MCP-09 | HIGH | CliError.remediation field + Hint: rendering in main() |
| MCP-17 | MEDIUM | Cross-repo error gets remediation hint |
| MCP-18 | MEDIUM | Registry not-found error gets remediation hint |
| MCP-26 | LOW | install-hooks backup collision prints exact rm command |
| MCP-27 | LOW | install-skill not-found lists available skills |
| MCP-38 | LOW | init handles .code-forge-as-file with CliError + remediation |

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None.

## Self-Check: PASSED
