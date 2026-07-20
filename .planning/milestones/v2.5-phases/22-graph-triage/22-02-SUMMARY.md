---
phase: 22-graph-triage
plan: 02
subsystem: graph-triage
tags: [pipeline-wiring, advisory-runners, prompt-injection, e-corpus, FUSE-01]
dependency_graph:
  requires: [graph_triage.py, gate_check.py, advisory.py]
  provides: [GraphTriageRunner-pipeline-registration, blast-radius-prompt-context, E8-corpus-entry]
  affects: [cli.py, factories.py, corpus.yaml, test_cli_eval.py, test_eval_corpus.py]
tech_stack:
  added: []
  patterns: [FUSE-01-prompt-injection, pre-loop-cache, advisory-runner-registration]
key_files:
  created:
    - tests/eval/corpus/diffs/E8-blast-radius-llm-invoke.diff
    - tests/eval/corpus/base_files/E8-blast-radius-llm-invoke/src/code_forge/llm_invoke.py
  modified:
    - src/code_forge/cli.py
    - src/code_forge/factories.py
    - tests/eval/corpus/corpus.yaml
    - tests/test_cli_eval.py
    - tests/test_eval_corpus.py
decisions:
  - "Pre-loop GraphTriageRunner run builds impact table once; cached findings avoid redundant subprocess calls in hold loop"
  - "GraphTriageRunner inserted before LegacyRunner in advisory_runners list (legacy stays last for Phase 23 compat)"
  - "Blast Radius Context injected after Conventions Digest in L1 prompt via FUSE-01 pattern"
  - "E8 corpus entry targets llm_invoke (highest-impact entity per RESEARCH: 72 downstream dependents)"
metrics:
  duration: "16m 54s"
  completed: "2026-06-14T10:30:05Z"
  tasks: 2
  tests_added: 1
  tests_total: 1708
---

# Phase 22 Plan 02: Pipeline Wiring + E-corpus Entry Summary

GraphTriageRunner registered in advisory_runners with pre-loop cache for prompt context, blast-radius impact table injected into L1 review prompt via FUSE-01 pattern, and E8 corpus entry added for graph triage evaluation axis.

## What Was Built

- **src/code_forge/cli.py** (modified, +52 lines): Pre-loop GraphTriageRunner instantiation runs once before the hold loop to build a markdown impact table (Entity/File/Downstream/Top Dependents columns). Result stored as graph_impact_context string and passed to build_l1_provider. Inside the hold loop, GraphTriageRunner registered in advisory_runners list (before LegacyRunner) with _cached_findings set from pre-loop results to avoid redundant subprocess/SQLite calls. pre_graph_findings parameter added to _run_hold_loop signature.

- **src/code_forge/factories.py** (modified, +7 lines): build_l1_provider gains graph_impact_context: str = "" parameter. When non-empty, "## Blast Radius Context" section injected into L1 review prompt after Conventions Digest and before the diff content. Follows FUSE-01 deterministic context fusion pattern.

- **tests/eval/corpus/corpus.yaml** (modified): E8-blast-radius-llm-invoke entry appended with expected_verdict: PASS (advisory only), axis_tags: [GRAPH-TRIAGE], expected_advisory keywords: ["llm_invoke", "impact", "downstream"].

- **tests/eval/corpus/diffs/E8-blast-radius-llm-invoke.diff** (new): Minimal unified diff adding a retry_on_empty parameter to llm_invoke function signature. Targets the highest-impact entity per RESEARCH smoke test (72 downstream dependents).

- **tests/eval/corpus/base_files/E8-blast-radius-llm-invoke/src/code_forge/llm_invoke.py** (new): Base file seed for git-apply validation of E8 diff.

- **tests/test_cli_eval.py** (modified): REQUIRED_NAMES updated from 10 to 11 entries (added "E8-blast-radius-llm-invoke"). Test method renamed from test_corpus_has_all_nine_entries to test_corpus_has_all_entries with docstring updated to 11.

- **tests/test_eval_corpus.py** (modified): E8-blast-radius-llm-invoke added to parametrized test_corpus_entry_applies list.

## Task Log

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Wire GraphTriageRunner into cli.py + factories.py | 9c2586b | src/code_forge/cli.py, src/code_forge/factories.py |
| 2 | Add E-corpus entry for graph triage evaluation | 2255cae | tests/eval/corpus/corpus.yaml, E8 diff + base_files, tests/test_cli_eval.py, tests/test_eval_corpus.py |

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None. All code paths are fully wired.

## Threat Surface Verification

All mitigations from the plan's threat model are implemented:
- T-22-06: accepted (impact table is deterministic output from forge's own graph analysis)
- T-22-07: accepted (corpus diffs are test fixtures committed to repo)
- T-22-SC: no new packages installed

No new threat surface introduced beyond what the plan covers.

## Self-Check: PASSED

- [x] tests/eval/corpus/diffs/E8-blast-radius-llm-invoke.diff exists (FOUND)
- [x] tests/eval/corpus/base_files/E8-blast-radius-llm-invoke/src/code_forge/llm_invoke.py exists (FOUND)
- [x] Commit 9c2586b exists (Task 1)
- [x] Commit 2255cae exists (Task 2)
- [x] 1708 tests pass, 0 failures, 5 skipped
