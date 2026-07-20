---
phase: 19
slug: fix-validation
status: approved
nyquist_compliant: true
wave_0_complete: true
created: 2026-06-12
---

# Phase 19 -- Validation Strategy

> Per-phase validation contract. Reconstructed from artifacts (State B) and
> confirmed by main-session goal-backward verification on 2026-06-12.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | pyproject.toml |
| **Quick run command** | `python -m pytest tests/test_fixval.py tests/test_fixval_integration.py -q` |
| **Full suite command** | `python -m pytest tests/ -q` |
| **Estimated runtime** | ~0.2s (fixval subset), ~177s (full suite) |

---

## Sampling Rate

- **After every task commit:** Run the quick command (fixval subset, 50 tests)
- **After every plan wave:** Run the full suite
- **Before merge/push:** Full suite must be green
- **Max feedback latency:** ~180 seconds (full suite)

---

## Per-Task Verification Map

Requirements are the Phase 19 ROADMAP success criteria (SC1-SC5).

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 19-01-01 | 01 | 1 | SC1 revert-RED / restore-GREEN on a bug-fix fixture | unit + integration | `pytest tests/test_fixval.py::TestEndToEndRealGit tests/test_fixval_integration.py::TestFixvalBlocksHollowTest tests/test_fixval_integration.py::TestFixvalPassesNonhollowTest` | yes | green |
| 19-01-01 | 01 | 1 | SC2 STING overfit guard: behavior-preserving transform, test still passes | unit | `pytest tests/test_fixval.py::TestVariableRenamer tests/test_fixval_integration.py::TestFixvalOverfitAdvisoryEmitted` | yes | green |
| 19-01-01 | 01 | 1 | SC3 written waiver path (explicit opt-out, never silent-skip) | unit + integration | `pytest tests/test_fixval.py -k waiver tests/test_fixval_integration.py::TestFixvalWaiverProducesAdvisory` | yes | green |
| 19-02-01 | 02 | 2 | SC4 FIXVAL blocks only the diff's own hollow test (advisory=false, blocks cycle via Verdict.FAIL) | integration | `pytest tests/test_fixval_integration.py::TestFixvalBlocksHollowTest tests/test_fixval_integration.py::TestFixvalNotRunOnNonConverged` | yes | green |
| 19-02-03 | 02 | 2 | SC5 eval scorecard records FIXVAL axis (false-green on BUG-P12-01) | integration | `pytest tests/test_fixval_integration.py::TestEvalFixvalHookRegistered tests/test_fixval_integration.py::TestEvalFixvalScoresBugP1201` | yes | green |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements. No Wave 0 stubs needed:
pytest + pyproject.toml were already present; Phase 19 added 38 unit tests
(tests/test_fixval.py) and 12 integration tests (tests/test_fixval_integration.py),
all green within the full suite (1421 passed, 5 skipped on 2026-06-12).

---

## Manual-Only Verifications

All phase behaviors have automated verification. In addition, the main session
ran a no-mock live experiment on 2026-06-12 (real scratch git repo, real
os.environ, /tmp/fixval_live_experiment.py) confirming two runtime behaviors
end-to-end beyond the mocked integration tests:

| Behavior | Requirement | Result |
|----------|-------------|--------|
| Hollow test with no waiver -> BLOCK, and the working tree is restored verbatim | SC1 / SC4 | confirmed (status BLOCK, "return a + b" restored) |
| Hollow test with FIXVAL_WAIVER env var -> WAIVED, advisory names the env channel | SC3 | confirmed (advisory: "FIXVAL waived via FIXVAL_WAIVER env var") |

This closes the sub-session's open UAT item (the waiver path was mocked in
the integration suite; the live run exercises the real os.environ channel at
gate time).

---

## Validation Sign-Off

- [x] All tasks have automated verify (no Wave 0 dependencies outstanding)
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references (none missing)
- [x] No watch-mode flags
- [x] Feedback latency < 180s
- [x] `nyquist_compliant: true` set in frontmatter

**Note (minor, non-blocking):** 19-01-SUMMARY.md frontmatter lists
`tech_stack.added: [unidiff]`, but unidiff was already a forge dependency
(pyproject.toml) before Phase 19 -- the revert engine reused it, it was not
newly added. Documentation nit in the SUMMARY; no effect on validation.

**Approval:** approved 2026-06-12 (main-session goal-backward verification)
