---
phase: 01a-trust-instrumentation
plan: 04
subsystem: cli-wrapper
tags: [cli, dry-run, dashboard, cost-metering, sidecar, fallback]
dependency_graph:
  requires: [01a-01, 01a-02, 01a-03]
  provides: [cli-wrapper, fp-dashboard, cost-metering, dry-run-step0]
  affects: [cli/forge_cli.py, .forge/runs/]
tech_stack:
  added: []
  patterns: [atomic-write, subprocess-timeout-fallback, split-fp-dashboard]
key_files:
  created: [cli/forge_cli.py]
  modified: []
decisions:
  - Used %-formatting instead of f-strings for consistency with vba_extract.py analog
  - classify_findings uses sorted() on reject reasons for deterministic UI order
  - load_all_runs creates RUNS_DIR on every call to avoid race conditions
metrics:
  duration: 349s
  completed: 2026-05-12T05:14:32Z
  tasks_completed: 1
  tasks_total: 1
  files_created: 1
  files_modified: 0
---

# Phase 1a Plan 04: CLI Wrapper Summary

Standalone forge CLI wrapper with zero-LLM dry-run, sidecar run metadata, split FP dashboard, and system prompt file fallback.

## Task Results

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create forge CLI wrapper | 26b9353 | cli/forge_cli.py |

## What Was Built

### cli/forge_cli.py (913 lines)

Complete CLI wrapper with 13 functions implementing all 6 review issue fixes:

1. **Review issue #1 (HIGH)**: `run_dry_run()` runs Step 0 checks directly in Python (bash -n, shellcheck, pylint/ruff, non-ASCII grep). Zero LLM cost. Does NOT invoke claude -p.

2. **Review issue #2 (HIGH)**: `run_forge()` writes run metadata to `.forge/runs/<uuid>.json` sidecar files. Does NOT write to findings.json. CLI reads findings.json read-only after review.

3. **Review issue #3 (HIGH)**: `_invoke_claude()` tries `--append-system-prompt-file` first with 600s timeout. On TimeoutExpired, falls back to `--system-prompt` with SKILL.md content inline.

4. **Review issue #8 (MEDIUM)**: `show_stats()` splits FP rate into ToolFP (categories 1-4: HALLUCINATION, CONTEXT_MISSING, INTENTIONAL, NOT_APPLICABLE) and UserFP (categories 5-6: STYLE_PREFERENCE, ACCEPTABLE_RISK). FP% column measures tool quality using ToolFP only.

5. **Review issue #13 (MEDIUM)**: `cost_usd if cost_usd is not None else calculated_cost` -- avoids truthiness trap where cost_usd=0.0 would fall through to calculated_cost.

6. **Review issue #14 (MEDIUM)**: `actual_passes` counted from findings.json by unique (cycle, pass) pairs for the current commit SHA. Not hardcoded to 9.

### Functions Exported

- `run_forge(diff_spec)` -- full review via claude -p
- `run_dry_run(diff_spec)` -- Step 0 only, zero LLM cost
- `show_stats(json_format=False)` -- FP rate dashboard (terminal or JSON)
- `bootstrap_historical(filepath)` -- delegates to convert_historical.py
- `classify_findings()` -- interactive accept/reject classification
- `calculate_cost(usage, config)` -- pure function, no side effects
- `atomic_write(filepath, data)` -- tempfile.mkstemp + os.replace
- `load_findings()` -- read .forge/findings.json
- `load_config()` -- read cli/config.json
- `load_all_runs()` -- read .forge/runs/*.json
- `_get_commit_sha()` -- subprocess.check_output, not shell substitution
- `_invoke_claude(cmd, skill_path, prompt)` -- timeout + fallback
- `main()` -- argparse entry point

## Deviations from Plan

None -- plan executed exactly as written.

## Verification Results

- python3 -m py_compile: PASS
- All 11 required functions present: PASS
- --help prints usage: PASS
- --stats with sample data: PASS (terminal table with split FP rates)
- --stats --json with sample data: PASS (valid JSON output)
- TOOL_ERROR_REASONS constant present: PASS
- is not None check present: PASS
- runs/ sidecar path present: PASS
- system-prompt fallback present: PASS
- No non-ASCII characters: PASS (grep -P returns exit 1)
- File is executable: PASS
- Stdlib-only imports: PASS (argparse, datetime, glob, json, os, subprocess, sys, tempfile, uuid)
- run_dry_run body does NOT reference claude: PASS

## Self-Check: PASSED

- [x] cli/forge_cli.py exists: FOUND
- [x] Commit 26b9353 exists: FOUND
