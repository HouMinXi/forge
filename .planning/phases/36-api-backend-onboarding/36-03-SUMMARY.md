---
phase: 36-api-backend-onboarding
plan: "03"
subsystem: docs
tags: [docs, schema, hooks, cleanup]
dependency_graph:
  requires: []
  provides: [docs-cli-alignment, complete-exit-code-tables]
  affects: [configuration.md, setup-ci.md, setup-claude-code.md, setup-cursor.md, setup-pycharm.md, outlet-alignment.md, adoption-survey.md, manual.md, gate.schema.json, check_git_push_review.sh, check_non_ascii.sh]
tech_stack:
  added: []
  patterns: []
key_files:
  created: []
  modified:
    - docs/configuration.md
    - docs/setup-ci.md
    - docs/setup-claude-code.md
    - docs/setup-cursor.md
    - docs/setup-pycharm.md
    - docs/outlet-alignment.md
    - docs/adoption-survey.md
    - docs/manual.md
    - src/code_forge/gate.schema.json
    - hooks/check_git_push_review.sh
    - hooks/check_non_ascii.sh
decisions:
  - "MCP-52 DROPPED: canary spec status 'Design complete, not implemented' is correct"
  - "MCP-32 DROPPED: FORGE_LLM_MODEL scope description not proven wrong"
metrics:
  duration: 4m 51s
  completed: "2026-07-01"
  tasks_completed: 2
  tasks_total: 2
---

# Phase 36 Plan 03: Docs-vs-Reality Reconciliation Summary

Fix 13 docs-vs-reality mismatches across configuration, setup guides, schema, and hooks (MCP-52 and MCP-32 dropped as unproven/false).

## Task Summary

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Fix phantom CLI flags and exit code tables | ecbae72 | docs/configuration.md, docs/setup-ci.md, docs/setup-claude-code.md, docs/setup-cursor.md, docs/setup-pycharm.md, docs/outlet-alignment.md |
| 2 | Fix stale references in remaining docs, schema, and hooks | 7c23acd | docs/adoption-survey.md, docs/manual.md, src/code_forge/gate.schema.json, hooks/check_git_push_review.sh, hooks/check_non_ascii.sh |

## Findings Fixed

| ID | Severity | Fix |
|----|----------|-----|
| MCP-12 | MEDIUM | Removed --auth-timeout CLI flag refs from configuration.md (flag does not exist) |
| MCP-13 | MEDIUM | Changed --output to stdout redirect in setup-ci.md (--output is on eval, not review) |
| MCP-14 | LOW | Changed 'code-forge review --version' to 'code-forge --version' in setup-cursor and setup-pycharm |
| MCP-15 | LOW | Added commit-msg hook mention in setup-claude-code.md |
| MCP-28 | MEDIUM | Added gate-check prerequisite note (needs test section in gate.yaml) |
| MCP-29 | MEDIUM | Added ESCALATED, DELEGATED, UNRELIABLE to setup-ci.md exit code table |
| MCP-30 | LOW | Updated Wave 0 note in outlet-alignment.md to reflect Outlet C real implementation since Phase 24.1 |
| MCP-31 | LOW | Added TIMEOUT and UNRELIABLE to outlet-alignment.md exit code list |
| MCP-33 | LOW | Updated pass sequence in check_git_push_review.sh to current 3-pass-per-cycle convention |
| MCP-34 | LOW | Added jq install hint to check_non_ascii.sh error message |
| MCP-49 | HIGH | Replaced nonexistent 'code-forge baseline' with 'code-forge gate-check' in adoption-survey.md |
| MCP-50 | LOW | Added 'python' to known runners description in gate.schema.json |
| MCP-51 | LOW | Updated deepseek-chat to deepseek-v4-flash in manual.md |

## Dropped Findings

- **MCP-32**: FORGE_LLM_MODEL scope description -- not proven wrong; env var is used as shell-level override and docs show shell export examples accurately.
- **MCP-52**: reviewer-canary-spec.md status -- current status "Design complete, not implemented" is correct; canary.py is infrastructure but the feature is not shipping per spec.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed second --output reference in setup-ci.md**
- **Found during:** Task 1 verification
- **Issue:** Line 71 of setup-ci.md also referenced '--output report.json' as a review flag (the plan only targeted the YAML example on line 79)
- **Fix:** Replaced with prose describing stdout redirect for SARIF capture
- **Files modified:** docs/setup-ci.md
- **Commit:** ecbae72

## Self-Check: PASSED
