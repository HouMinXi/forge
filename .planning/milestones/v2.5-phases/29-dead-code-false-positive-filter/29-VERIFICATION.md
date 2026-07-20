---
phase: 29-dead-code-false-positive-filter
verified: 2026-06-26T06:42:00Z
status: passed
score: 11/11
overrides_applied: 0
---

# Phase 29: Dead-Code False-Positive Filter Verification Report

**Phase Goal:** Advisory axes filter out callers inside statically-dead code (if TYPE_CHECKING, if False, #if 0) so they do not inflate cross-repo impact or graph-triage findings. Shared SQL extraction eliminates duplication (SC#3).
**Verified:** 2026-06-26T06:42:00Z
**Status:** passed
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | _is_dead_call_site returns True for Python lines inside if TYPE_CHECKING: / if False: | VERIFIED | test_type_checking_is_dead and test_if_false_is_dead both PASS; dead_code.py:166 checks _DEAD_CONDITIONS frozenset containing b"TYPE_CHECKING" and b"False" |
| 2 | _is_dead_call_site returns True for Python lines inside sys.version_info guard that is False on running interpreter | VERIFIED | test_version_guard_always_dead PASSES (sys.version_info < (3, 0) is always False on Python 3.x); test_version_guard_always_live PASSES (sys.version_info < (3, 99) is NOT blanket-flagged dead) |
| 3 | _is_dead_call_site returns True for C lines inside #if 0 blocks | VERIFIED | test_inside_if0_is_dead PASSES; dead_code.py:190-223 implements lexical upward scan |
| 4 | _is_dead_call_site returns False for live code, unreadable files, unknown extensions, and parse errors | VERIFIED | TestFailSafe class: 8 tests all PASS (None file, None line, unreadable, .go/.rs/.java/.xyz, detector exception). dead_code.py:256-265 implements fail-safe dispatch |
| 5 | _live_callers returns only LiveCaller objects for callers NOT inside dead code | VERIFIED | TestLiveCallers::test_filters_dead_keeps_live PASSES: 4-caller fixture (3 dead, 1 live), asserts len(result)==1 and result[0].qualified=="live::live_caller" |
| 6 | Bug-inject proof: neutralizing _is_dead_call_site causes dead callers to reappear (SC#2) | VERIFIED | TestBugInject::test_neutralize_and_restore PASSES: monkeypatch -> 4 callers; undo -> 1 caller |
| 7 | No SQL duplication: CALLS+IMPORTS_FROM query exists only in dead_code.py (SC#3) | VERIFIED | TestNoSqlDuplication: 3 tests PASS. grep confirms cross_repo_impact.py:0 matches, graph_triage.py:0 matches, dead_code.py:1 match for "c.kind = 'CALLS'" |
| 8 | find_cross_repo_callers returns only live callers | VERIFIED | cross_repo_impact.py:135 calls _live_callers(cursor, name, module_name); builds result dicts from LiveCaller objects. 19 existing tests pass |
| 9 | _run_graphdb dependent_count excludes dead callers; top_dependents contains only live callers | VERIFIED | graph_triage.py:260-268 calls live = _live_callers(cursor, name, module_name); dependent_count = len(live); top_dependents = [lc.qualified for lc in live[:5]]. 25 existing tests pass |
| 10 | find_entity_dependents graphdb branch returns only live caller qualified names | VERIFIED | graph_triage.py:379 calls live = _live_callers(cursor, entity_name, module_name); line 381 returns [lc.qualified for lc in live]. sem branch (lines 353-360) untouched per D-09 |
| 11 | Honest ceiling documented in module docstring (SC#4) | VERIFIED | TestHonestCeiling: 4 tests PASS asserting dead_code.__doc__ contains "cheap", "not general reachability", "build-config-dependent", "without a registered detector". Rice's theorem referenced at dead_code.py:21 |

**Score:** 11/11 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/code_forge/dead_code.py` | _is_dead_call_site, _live_callers, LiveCaller, _DETECTORS dict | VERIFIED | 328 lines; all 4 exports present; compiles clean; 17 fail-safe return False paths |
| `tests/test_dead_code.py` | Unit tests, bug-inject, fail-safe, honest ceiling | VERIFIED | 669 lines; 38 tests in 10 test classes, all pass |
| `src/code_forge/cross_repo_impact.py` | find_cross_repo_callers using _live_callers from dead_code | VERIFIED | Line 26: from .dead_code import _live_callers; line 135: live = _live_callers(cursor, name, module_name); inline SQL removed (0 matches for c.kind = 'CALLS') |
| `src/code_forge/graph_triage.py` | _run_graphdb and find_entity_dependents using _live_callers from dead_code | VERIFIED | Line 28: from .dead_code import _live_callers; line 260 (site B) and line 379 (site C) both call _live_callers; inline SQL removed (0 matches) |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| cross_repo_impact.py | dead_code.py | from .dead_code import _live_callers | WIRED | Line 26 import; line 135 call site; 19 tests pass |
| graph_triage.py | dead_code.py | from .dead_code import _live_callers | WIRED | Line 28 import; lines 260, 379 call sites; 25 tests pass |
| dead_code.py | tree_sitter_language_pack | get_parser('python') for AST ancestor walk | WIRED | Line 54 guarded import; line 56 parser compilation; _PYTHON_PARSER confirmed non-None on this system |
| tests/test_dead_code.py | dead_code.py | from code_forge.dead_code import _is_dead_call_site, _live_callers, LiveCaller | WIRED | Lines 24-29; 38 tests exercise all exported functions |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| dead_code._live_callers | callers | sqlite3 cursor.execute(_CALLERS_SQL) | Yes -- SQL query on graph.db edges/nodes tables | FLOWING |
| cross_repo_impact.find_cross_repo_callers | live | _live_callers(cursor, name, module_name) | Yes -- delegates to dead_code shared SQL | FLOWING |
| graph_triage._run_graphdb | live | _live_callers(cursor, name, module_name) | Yes -- delegates to dead_code shared SQL | FLOWING |
| graph_triage.find_entity_dependents | live | _live_callers(cursor, entity_name, module_name) | Yes -- delegates to dead_code shared SQL | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| dead_code.py compiles | python3 -m py_compile src/code_forge/dead_code.py | exit 0, "COMPILE OK" | PASS |
| All 38 dead_code tests pass | python3 -m pytest tests/test_dead_code.py -x -v | 38 passed in 0.09s | PASS |
| cross_repo_impact + graph_triage regression | python3 -m pytest tests/test_cross_repo_impact.py tests/test_graph_triage.py -x -q | 44 passed in 0.22s | PASS |
| Real-path smoke: machine.py:32 detected as dead | TestRealPathSmoke::test_detector_on_real_source | PASSED (line 32 is inside if TYPE_CHECKING:) | PASS |
| Real-path smoke: pipeline no-crash on real graph.db | TestRealPathSmoke::test_pipeline_no_crash_on_real_db | PASSED | PASS |
| Bug-inject proof: neutralize -> 4 callers, restore -> 1 | TestBugInject::test_neutralize_and_restore | PASSED | PASS |

### Probe Execution

Step 7c: SKIPPED -- no probe scripts declared for this phase.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SPEC-01 | 29-01, 29-02 | Advisory honesty -- forge's thesis is honest signal; a false positive in the anti-noise tool is a defect | SATISFIED | Liveness filter catches dead-code false positives; honest ceiling documented; fail-safe direction is miss-not-noise (never drops a live caller). However, SPEC-01 is NOT formally defined in REQUIREMENTS.md -- it exists only in ROADMAP.md and PLAN frontmatter |

**Note:** SPEC-01 is referenced in ROADMAP.md Phase 29 and both PLANs but is absent from the REQUIREMENTS.md traceability table. This is a documentation-level gap (the requirement intent is fulfilled in code), not a code-level gap.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none) | - | - | - | No debt markers (TBD/FIXME/XXX/TODO/HACK/PLACEHOLDER) found in any modified file |

### Human Verification Required

No items require human verification. All truths are verifiable via automated tests and code inspection.

### Gaps Summary

No gaps found. All 11 observable truths verified, all 4 artifacts pass all levels (exists, substantive, wired, data flowing), all 4 key links wired, no anti-patterns, no debt markers, all tests pass (38 dead_code + 19 cross_repo_impact + 25 graph_triage = 82 total). SPEC-01 missing from REQUIREMENTS.md traceability table is a documentation-only gap with no code impact.

---

_Verified: 2026-06-26T06:42:00Z_
_Verifier: Claude (gsd-verifier)_
