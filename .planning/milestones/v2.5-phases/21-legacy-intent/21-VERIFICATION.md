---
phase: 21-legacy-intent
verified: 2026-06-13T10:42:00Z
status: passed
score: 18/18 must-haves verified
overrides_applied: 0
---

# Phase 21: Legacy + Intent Verification Report

**Phase Goal:** Pre-existing issues in code the diff touches are surfaced (not dropped, not blocked) with blame attribution and intent classification
**Verified:** 2026-06-13T10:42:00Z
**Status:** passed
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | git_blame() lives in git.py (D-06) | VERIFIED | `def git_blame` at line 358 of src/code_forge/git.py; module docstring updated to "(diff, blame)" at line 5 |
| 2 | git_blame() parses --porcelain output including SHA deduplication | VERIFIED | sha_cache dict populated on first SHA occurrence (lines 395-456); test_git_blame_dedup_sha passes with two lines sharing same SHA |
| 3 | Staged/uncommitted lines (SHA 0000...) returned with sentinel SHA | VERIFIED | test_git_blame_staged_line asserts sha == "0"*40, author == "Not Committed Yet", subject == ""; implementation at lines 408-418 |
| 4 | git_blame() returns {} on non-zero exit | VERIFIED | Line 387-388: `if result.returncode != 0: return {}`; test_git_blame_returns_empty_on_nonzero and test_git_blame_returns_empty_for_missing_file both pass |
| 5 | Pre-existing findings surfaced as AdvisoryFinding (never silently dropped) | VERIFIED | test_pre_existing_finding_emitted passes; SKIPPED finding emitted when source_files/registry not available (never-silent pattern) |
| 6 | Each AdvisoryFinding has attribution: git-blame: {author} {sha8} {subject} | VERIFIED | test_attribution_format asserts exact string "git-blame: Alice abc12345 fix: null"; implementation at legacy.py lines 231-245 |
| 7 | Intent label in description: [pre-existing] {desc} [intent: intended/unintended] | VERIFIED | test_intent_label_in_description asserts format; implementation at legacy.py line 257 |
| 8 | SATD keywords in surrounding lines (+/-3) classify as "intended" | VERIFIED | test_satd_surrounding_lines_intended passes; _classify_intent lines 80-84 scan range(max(1, finding_line-3), finding_line+4) |
| 9 | Intent commit signals in blame subject classify as "intended" | VERIFIED | test_commit_msg_signal_intended, test_commit_msg_signal_hack_intended both pass; INTENT_SIGNALS frozenset at line 31-34 |
| 10 | SKIPPED finding emitted when source_files/registry not injected | VERIFIED | test_skipped_when_no_source_files and test_skipped_when_no_registry both assert SKIPPED in description; _build_legacy_skipped at lines 37-46 |
| 11 | Manual line-intersection (StateFinding.line_range) correctly excludes delta findings; D-01 enforced | VERIFIED | test_delta_finding_not_pre_existing, test_d01_non_diff_file_excluded both pass; implementation at legacy.py lines 169-188 with changed_files_set guard |
| 12 | LegacyRunner.is_advisory is True | VERIFIED | Runtime check: `python -c "from code_forge.legacy import LegacyRunner; print(LegacyRunner().is_advisory)"` returns True; property at line 103 |
| 13 | LegacyRunner runs post-convergence alongside TaintRunner and RuntimeRunner | VERIFIED | machine.py line 178-180: _run_advisory_axes() called after convergence; cli.py line 1516: advisory_runners=[_taint_runner, _runtime_runner, _legacy_runner] |
| 14 | machine.py injects both source_files AND registry into LegacyRunner | VERIFIED | machine.py lines 979-985: hasattr guards for both source_files and registry; test_registry_injected_into_legacy_runner passes |
| 15 | Legacy advisory findings appear in advisory-findings.json on PASS verdict | VERIFIED | machine.py line 181: _serialize_advisories() called after _run_advisory_axes(); _serialize_advisories writes to advisory-findings.json (lines 998-1009) |
| 16 | Legacy findings never block convergence (advisory_runners dispatch only) | VERIFIED | _advisories (line 163) completely separate from _state.findings; convergence logic (lines 425, 444, 475, 822, 826) only checks _state.findings; test_advisory_isolation confirms no leaked IDs |
| 17 | Full pytest suite passes | VERIFIED | 1645 passed, 5 skipped, 0 failures in 306.73s |
| 18 | No circular import on cli.py | VERIFIED | `python -c "from code_forge.cli import _run_hold_loop"` exits 0 |

**Score:** 18/18 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/code_forge/git.py` | git_blame() function | VERIFIED | L1: exists (459 lines). L2: substantive -- full porcelain parser with sha_cache, hex validation, tab-prefix-first guard. L3: wired -- imported by legacy.py (`from .git import git_blame`), used in run() at line 210 |
| `src/code_forge/legacy.py` | LegacyRunner advisory axis | VERIFIED | L1: exists (275 lines). L2: substantive -- full run() algorithm with 12 steps, _classify_intent, _build_legacy_skipped. L3: wired -- imported by cli.py (line 1495), tests (2 files) |
| `src/code_forge/machine.py` | registry injection in _run_advisory_axes | VERIFIED | L1: exists. L2: substantive -- hasattr guard for registry at line 984-985. L3: wired -- called by StateMachine.run() at line 180 |
| `src/code_forge/cli.py` | LegacyRunner in advisory_runners list | VERIFIED | L1: exists. L2: substantive -- import at line 1495, instantiation at line 1499. L3: wired -- passed to StateMachine at line 1516 |
| `tests/test_git.py` | Unit tests for git_blame | VERIFIED | L1: exists. L2: 6 test functions covering simple parse, dedup, staged, nonzero exit, missing file, existence. L3: wired -- all 6 pass |
| `tests/test_legacy.py` | Unit tests for LegacyRunner | VERIFIED | L1: exists. L2: 21 test functions covering REVIEW-LEGACY-01 (12 tests) and REVIEW-INTENT-01 (9 tests). L3: wired -- all 21 pass |
| `tests/test_legacy_integration.py` | Integration tests for wiring | VERIFIED | L1: exists. L2: 6 test functions including real l0_runner e2e. L3: wired -- all 6 pass |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| legacy.py | git.py | `from .git import git_blame` | WIRED | Line 18 import; git_blame called at line 210 in run() |
| legacy.py | diff.py | `from .diff import extract_changed_lines` | WIRED | Line 17 import; extract_changed_lines called at line 138 |
| cli.py | legacy.py | `from .legacy import LegacyRunner` | WIRED | Line 1495 import; LegacyRunner() instantiated at line 1499; passed to StateMachine at line 1516 |
| machine.py | advisory_runners | `runner.registry = self.registry` | WIRED | Line 984-985: hasattr guard injects registry; confirmed by test_registry_injected_into_legacy_runner |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| legacy.py | l0_findings | l0_runner(registry, source_files) | Yes -- default path uses _default_l0_runner from machine.py which invokes real ruff/shellcheck | FLOWING |
| legacy.py | blame_cache | git_blame(blame_key, repo_root) | Yes -- calls git blame subprocess; test_real_default_l0_runner_e2e confirms real blame on temp repo | FLOWING |
| legacy.py | source_lines_cache | Path.read_text() | Yes -- reads real source files for SATD keyword scan | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| git_blame importable | `python -c "from code_forge.git import git_blame; print(type(git_blame))"` | `<class 'function'>` | PASS |
| LegacyRunner.is_advisory == True | `python -c "from code_forge.legacy import LegacyRunner; print(LegacyRunner().is_advisory)"` | `True` | PASS |
| No circular import | `python -c "from code_forge.cli import _run_hold_loop; print('import OK')"` | `import OK` | PASS |
| git_blame tests pass | `pytest tests/test_git.py -k git_blame -x -q` | 6 passed | PASS |
| LegacyRunner unit tests pass | `pytest tests/test_legacy.py -x -q` | 21 passed | PASS |
| Integration tests pass | `pytest tests/test_legacy_integration.py -x -q` | 6 passed | PASS |
| Full suite regression-free | `pytest -q --tb=no` | 1645 passed, 5 skipped | PASS |

### Probe Execution

Step 7c: SKIPPED (no probes declared in PLAN/SUMMARY, no conventional probe scripts found)

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| REVIEW-LEGACY-01 | 21-01, 21-02, 21-03 | Pre-existing findings surfaced as advisory with blame attribution; never auto-suppressed, never blocks; reuses R1 baseline primitive | SATISFIED | SC1: test_pre_existing_finding_emitted + test_attribution_format. SC2: is_advisory=True + _advisories separate from _state.findings. SC3: extract_changed_lines + manual line-intersection (same algorithm as filter_delta but adapted for StateFinding.line_range) |
| REVIEW-INTENT-01 | 21-02, 21-03 | Intent label present on legacy findings; never auto-suppresses/blocks; commit/PR text used as classification signal | SATISFIED | SC1: test_intent_label_in_description + [pre-existing]...[intent:] format. SC2: advisory-only axis, is_advisory=True. SC3: _classify_intent checks commit_subject against INTENT_SIGNALS and source lines against SATD_KEYWORDS |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none found) | - | - | - | - |

No TBD, FIXME, XXX, TODO, HACK, or PLACEHOLDER markers in modified source files (legacy.py, git.py lines 354-458). The string literals "todo", "fixme", "hack", "xxx" in SATD_KEYWORDS/INTENT_SIGNALS frozensets are data constants, not debt markers.

### Human Verification Required

(none -- all must-haves verified programmatically)

### Gaps Summary

No gaps found. All 18 must-haves verified. All 5 ROADMAP success criteria satisfied:

1. SC1 (advisory finding with blame): VERIFIED -- AdvisoryFinding emitted with git-blame attribution format
2. SC2 (never auto-suppressed, never blocks): VERIFIED -- is_advisory=True, separate _advisories collection
3. SC3 (reuses R1 baseline primitive): VERIFIED -- extract_changed_lines + manual line-intersection
4. SC4 (intent discriminator): VERIFIED -- _classify_intent with commit signals and SATD keywords
5. SC5 (intent labels never auto-suppress/block): VERIFIED -- labels are annotation-only in description string

---

_Verified: 2026-06-13T10:42:00Z_
_Verifier: Claude (gsd-verifier)_
