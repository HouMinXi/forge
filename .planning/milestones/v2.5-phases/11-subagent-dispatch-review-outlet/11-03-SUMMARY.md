---
phase: 11-subagent-dispatch-review-outlet
plan: "03"
subsystem: outlet_resolver, cli, SKILL.md
tags: [feature, subagent-outlet, Outlet-C, BACKEND-02, forge-review]
dependency_graph:
  requires: [viability-pass-verdict]
  provides: [subagent-outlet, Outlet-C]
  affects: [outlet_resolver, cli, SKILL.md, test_outlet_resolver]
tech_stack:
  added: []
  patterns: [thin-extension, allow-list, fail-closed, fresh-context-guarantee]
key_files:
  modified:
    - src/code_forge/outlet_resolver.py
    - src/code_forge/cli.py
    - src/code_forge/skills/code-forge/SKILL.md
    - tests/test_outlet_resolver.py
decisions:
  - '"subagent" added to VALID_OUTLET_STRINGS -- no new logic, same allow-list + parse path'
  - "_parse_outlet_string error message now dynamic: |".join(sorted(VALID_OUTLET_STRINGS))"
  - "cli.py _run: subagent branch returns Verdict.PASS same as inline -- SKILL.md owns dispatch"
  - "SKILL.md Outlet C: per-pass Agent spawn, fail-closed contract, strong model (sonnet/opus), 180-300s timeout"
  - "VIABILITY.md revised with 4 requirements before 11-03 execute (REQ-V1 model pin, REQ-V2 timeout)"
metrics:
  duration: "~90 minutes (VIABILITY revision + implementation + 9-pass forge review)"
  completed: "2026-06-04"
  tasks_completed: 2
  files_changed: 4
---

# Phase 11 Plan 03: Subagent-Dispatch Review Outlet Summary

Added "subagent" as Outlet C -- the third outlet type alongside "cli" (Outlet A) and
"inline" (Outlet B). Each review pass spawns a fresh Agent with clean context window,
providing Outlet A's isolation guarantee without cold-start or API key cost.

## What Was Built

### Task 1: outlet_resolver + CLI + tests

**src/code_forge/outlet_resolver.py:**
- `VALID_OUTLET_STRINGS` extended: `{"cli": "cli", "inline": "inline", "subagent": "subagent"}`
- `_parse_outlet_string` error message: dynamic `"|".join(sorted(VALID_OUTLET_STRINGS))`
- Module docstring and `resolve_outlet` docstring updated (Outlet C entry, no-probe invariant)
- "subagent" short-circuits before reachability probe (same as "inline")

**src/code_forge/cli.py:**
- `--outlet choices`: `["cli", "inline", "subagent"]`
- `_run`: `if outlet == "subagent": return Verdict.PASS` (SKILL.md handles dispatch)
- Inline/subagent "why return PASS" comments restored (readability)

**tests/test_outlet_resolver.py -- TestSubagentOutlet (5 tests):**
- `test_env_subagent_accepted`
- `test_env_subagent_case_insensitive`
- `test_gate_yaml_subagent`
- `test_cli_value_subagent`
- `test_subagent_skips_reachability_probe` (proves Outlet C never probes, _bomb_probe pattern)

### Task 2: SKILL.md Outlet C documentation

Three locations updated:

1. Pipeline overview diagram: three-branch ASCII flow (inline / cli / subagent)
2. Outlet dispatch section: Outlet C block with per-pass Agent spawn instructions,
   fail-closed contract (identical language to Outlet A), strong model requirement
   (sonnet/opus from REQ-V1), timeout 180-300s (from REQ-V2)
3. Outlet Behavior section: Outlet C description + "When to use each outlet" table

## Three-Cycle Forge Review Summary

Full 9-pass inline forge review (Outlet B) with one P2 fix during Cycle 1:

| Cycle | Pass 1 (qodo) | Pass 2 (expert) | Pass 3 (adversarial) | Findings |
|-------|---------------|-----------------|----------------------|----------|
| 1 (restart after P2) | Clean | Clean | Clean | 0 (P2 fixed before restart) |
| 2 | Clean | Clean | Clean | 0 |
| 3 | Clean | Clean | Clean | 0 |

P2 finding fixed: `test_whole_file_maps_to_empty_baseline` had dead branch
(`is_git_repo(tmp_path)` always False for pytest tmp_path). Split into two tests:
non-git and git (using `git init` on tmp_path).

## Commits

| Hash | Message |
|------|---------|
| `f444052` | outlet: add subagent as third review outlet (Outlet C) |
| `0a672fa` | Merge p11-03: add subagent review outlet (Outlet C) |

## Verification

- `VALID_OUTLET_STRINGS contains "subagent"`: YES (outlet_resolver.py line 38)
- `CLI --outlet accepts "subagent"`: YES (cli.py line 211)
- `5 new subagent tests pass`: YES (30/30 in test_outlet_resolver.py)
- `SKILL.md "subagent" count >= 8`: YES (10 case-insensitive matches)
- `994 tests green post-merge`: YES (994 passed, 0 failed)
- `Forge review # post-review-c3`: YES (commit f444052)

## Known Stubs

None. Outlet C is fully specified in SKILL.md. Runtime behavior (actual Agent
invocation) is SKILL.md-driven -- no Python code needed.

## Self-Check: PASSED

- outlet_resolver.py contains "subagent": YES
- cli.py contains "subagent": YES
- SKILL.md contains "subagent": YES (10x)
- test_outlet_resolver.py contains "test_env_subagent": YES
- All outlet_resolver tests pass: 30/30
- Forge review complete: 9 genuine passes, post-review-c3
