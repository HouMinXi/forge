---
phase: 01-layer-0-baseline-registry
plan: 03
subsystem: runner, delta
tags: [subprocess, tool-execution, delta-computation, baseline-mode]
dependency_graph:
  requires: [01-01]
  provides: [run_tool, run_tools, capture_tool_version, filter_delta]
  affects: [01-04]
tech_stack:
  added: []
  patterns: [subprocess-list-args, sorted-iteration, pure-function-delta]
key_files:
  created:
    - src/forge/runner.py
    - src/forge/delta.py
    - tests/test_runner.py
    - tests/test_delta.py
decisions:
  - "subprocess.run always receives list args, never shell=True (T-01-07)"
  - "cargo_root detection deferred to Phase 2 -- runner skips file args but does not change cwd"
  - "Tool iteration uses sorted(registry.keys()) for GATE-02 determinism"
  - "filter_delta returns 2-tuple (delta, all) to preserve all_findings for reporter"
  - "ToolError items pass through delta filter unchanged (tool-level failures always reported)"
metrics:
  duration: "2m"
  completed: "2026-05-16T09:28:30Z"
  tasks: 2/2
  tests_added: 38
  tests_total: 162
---

# Phase 01 Plan 03: Tool Runner and Delta Computation Summary

Tool execution engine via subprocess with version capture, path resolution, and timeout handling; delta computation module that filters findings to changed lines only for baseline mode.

## Task Results

| Task | Name | Commit(s) | Files | Tests |
|------|------|-----------|-------|-------|
| 1 | Tool runner with version capture, crash detection, and path resolution | 2c68651 (RED), ce701b9 (GREEN) | src/forge/runner.py, tests/test_runner.py | 26 |
| 2 | Delta computation for baseline mode | b67d265 (RED), a72cc05 (GREEN) | src/forge/delta.py, tests/test_delta.py | 12 |

## Implementation Details

### runner.py

- `_resolve_command(command)`: tries `shutil.which` first, then falls back to `os.path.isfile` + `os.access` for relative paths (e.g., `scripts/checkpatch.pl`). Addresses DeepSeek's checkpatch finding.
- `capture_tool_version(command)`: runs `<cmd> --version`, returns first line of stdout. Returns `"not_installed"` or `"unknown"` on failure. Addresses Consensus #3 (GATE-02 determinism).
- `run_tool(tool_config, files)`: executes tool via `subprocess.run` with list args, timeout, and `check=False`. Returns `(stdout, returncode, stderr)` 3-tuple or `None`. Raises `RuntimeError` for missing required tools. Never uses `shell=True`.
- `run_tools(registry, files)`: orchestrates all tools. Calls `match_tools` once (Mimo F-04), iterates `sorted(registry.keys())` (Round 3 item 11), populates `tool_versions` (Consensus #3) and `tools_skipped`.
- `working_dir="cargo_root"` skips file args but does not change cwd (full cargo_root detection deferred to Phase 2 per DeepSeek H-2).

### delta.py

- `filter_delta(findings, changed_lines)`: pure function, no I/O. Returns `(delta_findings, all_findings)` 2-tuple.
- Multi-line findings: kept if ANY line in `range(finding.line, finding.end_line + 1)` intersects changed lines (RESEARCH.md Pitfall 2).
- `ToolError` items always pass through (not filtered by line -- tool-level failures must always be reported).
- `all_findings` is a copy of the input list, preserved for the reporter to show pre-existing violation counts (Mimo review).

## Deviations from Plan

None -- plan executed exactly as written.

## TDD Gate Compliance

- Task 1: RED commit `2c68651` (test) precedes GREEN commit `ce701b9` (feat) -- PASS
- Task 2: RED commit `b67d265` (test) precedes GREEN commit `a72cc05` (feat) -- PASS

## Verification

- 38 new tests pass (26 runner + 12 delta)
- 162 total tests pass (124 prior + 38 new)
- No `shell=True` in runner.py code (only in docstring warning)
- `sorted(registry.keys())` confirmed in runner.py
- delta.py has zero `open`/`subprocess`/`os.*` calls (pure function)
- All imports work: `from forge.runner import run_tool, run_tools, capture_tool_version`
- All imports work: `from forge.delta import filter_delta`

## Self-Check: PASSED

- All 4 created files exist on disk
- All 4 commit hashes verified in git log
