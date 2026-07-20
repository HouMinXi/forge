---
phase: 10-detect-py-hardening
plan: "01"
subsystem: detect
tags: [detect, shell, deep-copy, merge-on-force, shellcheck]
dependency_graph:
  requires: []
  provides: [DET-01]
  affects: [detect.py, test_detect.py]
tech_stack:
  added: [copy (stdlib)]
  patterns: [deep-copy registry entries, registry-per-language, merge-not-clobber]
key_files:
  created: []
  modified:
    - src/code_forge/detect.py
    - tests/test_detect.py
decisions:
  - SHELL_TOOL_REGISTRY separate constant keeps language boundaries explicit
  - _get_tool_meta centralizes cross-registry lookup
  - _merge_and_write uses yaml.safe_load (not load_registry) to preserve unknown output_format entries
  - language field in idempotency path inferred from registry content
  - Shell detection runs independently; mixed projects return language=python
metrics:
  duration: "18min"
  completed: "2026-06-03"
  tasks: 2
  files: 2
---

# Phase 10 Plan 01: detect.py Hardening Summary

One-liner: multi-language alias-free regen-safe detect.py with shellcheck support, deepcopy fix, and merge-on-force.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Fix detect.py -- deep copy, merge-on-force, shell registry | f670981 | src/code_forge/detect.py |
| 2 | Add and update tests for all three fixes | 8716b89 | tests/test_detect.py |

## What Was Built

Three bugs/gaps fixed in detect.py:

**Bug 1 -- Shallow-copy aliasing (SC#2):**
`generate_tools_yaml` used `dict(meta["tools_yaml_entry"])` (shallow copy). The inner `file_patterns` list was the same object as the PYTHON_TOOL_REGISTRY module constant. Fixed with `copy.deepcopy(meta["tools_yaml_entry"])`.

**Bug 2 -- force=True clobbers user entries (SC#1):**
`detect_and_init` force path called `generate_tools_yaml` unconditionally, overwriting user-added entries. Fixed with `_merge_and_write`: reads existing tools.yaml via `yaml.safe_load`, preserves all existing entries, updates only detected entries. Falls back to fresh generation on corrupt or empty file.

**Gap 3 -- Python-only detection (SC#3):**
Added `SHELL_TOOL_REGISTRY` with shellcheck entry (output_format: shellcheck_json). Added `_get_tool_meta` for cross-registry lookup. Added registry parameter to `_scan_path_for_tools`. Added shell detection block: globs for `*.sh` in root and one level deep; if found, scans SHELL_TOOL_REGISTRY. Language field returns "shell" only when no Python indicators found; mixed projects return "python".

**Tests added (Task 2):**
- TestDeepCopy (2 tests): aliasing proof via id() and mutation-after-generate
- TestForceFlag additions (4 tests): preserves user shellcheck entry, preserves unknown-format entry, no crash on nonexistent file, graceful fallback on corrupt YAML
- TestShellDetection (5 tests): shell-only, shellcheck missing, mixed Python+shell, shell-only language field, nested *.sh files
- TestShellRoundTrip (1 test): shellcheck entry round-trips through load_registry

Total: 985 passed (973 baseline + 12 new), 0 failed.

## Deviations from Plan

**1. [Rule 2 - Missing critical functionality] Language inference in idempotency path**

The idempotency return path hardcoded `language="python"`. With shell-only registries now possible, this would return wrong metadata. Added `has_shell_tools` / `has_python_tools` check to infer language from registry content. Required for correctness on shell-only projects.

All other tasks executed exactly as written.

## Known Stubs

None. All implemented functionality is wired end-to-end through detect_toolchain, generate_tools_yaml, and load_registry.

## Threat Flags

None. No new network endpoints or auth paths. _merge_and_write uses yaml.safe_load (no code execution) per T-10-01 mitigation.

## Self-Check: PASSED

- src/code_forge/detect.py -- commit f670981
- tests/test_detect.py -- commit 8716b89
- python -m pytest tests/test_detect.py: 36 passed, 0 failed
- python -m pytest (full suite): 985 passed, 0 failed
