---
status: complete
phase: 25
date: 2026-06-20
method: bash-manual
---

# Phase 25 UAT: Cross-Repo Merge Review

## SC1: Joint review context from sibling repos

| # | Test | Result | Evidence |
|---|------|--------|----------|
| 1 | All 6 cross-repo functions importable | PASS | `from code_forge.cross_repo import ...` |
| 2 | gate.schema.json has siblings (repo/ref/label) | PASS | `src/code_forge/gate.schema.json` |
| 3 | validate_siblings importable from gate_check | PASS | `from code_forge.gate_check import validate_siblings` |
| 4 | build_cross_repo_context produces joint header | PASS | `Cross-repo review:` in output |
| 5 | build_cross_repo_context includes both repo diff blocks | PASS | `## Repo: [primary]` + `## Repo: [plugin]` |

## SC2: Joint verdict reflects findings from both repos

| # | Test | Result | Evidence |
|---|------|--------|----------|
| 6 | format_cross_repo_output emits per-repo headers | PASS | `=== [primary] ===` + `=== [plugin] ===` |
| 7 | Findings attributed to correct repo label | PASS | `[primary] a.py:10` under primary section |
| 8 | No cross-contamination between repo sections | PASS | No `[plugin]` in primary section |
| 9 | Empty findings dict: no crash, headers still emitted | PASS | 2 header lines, 0 body lines |

## SC3: Single-repo path unchanged

| # | Test | Result | Evidence |
|---|------|--------|----------|
| 10 | CLI help: --state-dir absent | PASS | Not in `review --help` |
| 11 | CLI help: --staged absent | PASS | Not in `review --help` |
| 12 | CLI help: --committed, --whole-file, --outlet present | PASS | All 3 in help text |
| 13 | gate.yaml without siblings key: empty list, no cross-repo | PASS | `siblings=[]` |

## Test suite

| # | Test | Result | Evidence |
|---|------|--------|----------|
| 14 | Phase 25 test files (173 tests) | PASS | `173 passed in 56.10s` |
| 15 | Full suite (1862 tests) | PASS | `1862 passed, 5 skipped in 299.57s` |

## Verdict

**15/15 PASS.** Phase 25 success criteria met.
