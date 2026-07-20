---
phase: 25
slug: cross-repo-merge-review
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-06-17
---

# Phase 25 -- Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from 25-RESEARCH.md ## Validation Architecture (lines 735-780).

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (existing) |
| **Config file** | pyproject.toml |
| **Quick run command** | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py -x -q` |
| **Full suite command** | `PYTHONPATH=src python -m pytest -q` |
| **Estimated runtime** | ~30s targeted, ~5min full suite |

---

## Sampling Rate

- **After every task commit:** `PYTHONPATH=src python -m pytest tests/test_cross_repo.py -x -q`
- **After every plan completion:** Full suite `PYTHONPATH=src python -m pytest -q`
- **Coverage target:** 80%+ on new code (cross_repo.py, gate_check.py changes)
- **NEVER:** `pytest` without `PYTHONPATH=src` in forge worktree (Phase 18.1 isolation rule)

---

## Requirement -> Test Map

| Req ID | Behavior Under Test | Test Type | Automated Command | Provided by Plan |
|--------|---------------------|-----------|-------------------|-----------------|
| CROSS-01 SC-1 | Two repos declared, one joint context | integration | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_joint_context_contains_both_diffs -x` | 25-05 |
| CROSS-01 SC-2 | Finding in either repo appears in output | integration | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_findings_attributed_label -x` | 25-05 |
| CROSS-01 SC-3 | Single-repo path identical to pre-v2.5 | regression | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_single_repo_zero_drift -x` | 25-04 |
| D-03 (symlink guard) | Symlink-escape path rejected | unit | `PYTHONPATH=src python -m pytest tests/test_schema_corpus.py -x -k siblings_symlink` | 25-01 |
| D-05 (local only) | Remote https:// repo: rejected | unit | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_remote_url_rejected -x` | 25-04 |
| D-06 (fail-closed) | Invalid sibling ref fails review | integration | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_invalid_ref_fail_closed -x` | 25-03 |
| D-09 (same-stack) | Cross-language sibling rejected | unit | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_same_stack_validation -x` | 25-03 |
| D-11 (zero-drift) | _run_hold_loop called, not run_cross_repo | regression | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_single_repo_zero_drift -x` | 25-04 |
| D-12 (grouped output) | === [label] === sections in order | unit | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_grouped_verdict_output -x` | 25-06 |
| D-13 (attribution) | [label] finding in correct section only | unit | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_d13_finding_attribution_preserved -x` | 25-06 |
| CROSS-01 (stub pass) | Stub engine primary PASS yields joint PASS | integration | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_run_cross_repo_stub_primary_pass -x` | 25-05 |
| D-17 (verdict merge) | Primary FAIL yields joint FAIL regardless of siblings | integration | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_run_cross_repo_primary_determines_verdict -x` | 25-05 |
| D-06 (fail-closed integ) | Invalid sibling ref fails entire cross-repo review | integration | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_invalid_sibling_ref_fails_closed -x` | 25-05 |
| D-19 (receipt naming) | {label}-receipt-cNpM.json per repo | integration | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_receipt_naming_primary -x` | 25-05 |
| D-23a (F1 guard) | L0 runs on each repo (source_files non-empty absolute) | integration | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_l0_runs_on_each_repo -x` | 25-05 |
| F2 (PENDING guard) | PENDING from primary escalates to FAIL in cross-repo | unit | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_pending_escalates_to_fail -x` | 25-05 |
| R-04 (cwd isolation) | Per-repo distinct cwds proven | unit | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py::test_thread_isolation -x` | 25-03 |
| R-05 (label collision) | Duplicate/reserved labels rejected | unit | `PYTHONPATH=src python -m pytest tests/test_schema_corpus.py -x -k siblings_label` | 25-01 |
| Schema | siblings: passes gate.schema.json | unit | `PYTHONPATH=src python -m pytest tests/test_schema_corpus.py -x -k siblings` | 25-01 |

---

## Wave Checkpoints

| Wave | Plans | Gate Command | Expected Result |
|------|-------|-------------|-----------------|
| 1 | 25-01, 25-02 | `PYTHONPATH=src python -m pytest tests/test_schema_corpus.py tests/test_cross_repo.py -x -q -k siblings` | All siblings corpus tests pass; get_sibling_diff importable |
| 2 | 25-03 | `PYTHONPATH=src python -m pytest tests/test_cross_repo.py -x -q` | thread_isolation, same_stack, invalid_ref all pass |
| 3 | 25-04,05,06 | `PYTHONPATH=src python -m pytest -q` | Full suite green |

---

## Gaps and Known Limitations

- D-19 receipt test may be xfail until Plan 03 StateMachine wiring is fully live
- D-11 uses dispatch mock (not real StateMachine run) -- cost vs value tradeoff accepted
