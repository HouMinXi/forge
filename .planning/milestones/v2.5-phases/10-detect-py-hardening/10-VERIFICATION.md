---
phase: 10-detect-py-hardening
verified: 2026-06-03T14:30:00Z
status: passed
score: 3/3
overrides_applied: 0
---

# Phase 10: detect.py Hardening Verification Report

**Phase Goal:** code-forge detect generates the L0 toolchain safely for multi-language projects -- it neither clobbers user-added non-Python tool entries nor leaves a shared-mutable alias. Auto-detect stays idempotent and multi-lang-safe
**Verified:** 2026-06-03T14:30:00Z
**Status:** passed
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | code-forge detect --force preserves user-added non-Python entries in existing tools.yaml | VERIFIED | _merge_and_write() at detect.py:383-423 reads existing yaml.safe_load, preserves all entries, updates only detected tools. TestForceFlag.test_force_preserves_user_entries (line 337) and test_force_preserves_unknown_format_entry (line 364) both PASS: 36/36 |
| 2 | Generated tools.yaml entries have independent file_patterns lists (no shared mutable alias with module constants) | VERIFIED | copy.deepcopy(meta["tools_yaml_entry"]) at detect.py:369 and detect.py:415. TestDeepCopy.test_file_patterns_not_aliased_to_constant and test_mutation_of_generated_does_not_affect_constant both PASS: 36/36 |
| 3 | detect recognizes shell projects with shellcheck as a non-Python tool; shellcheck_json is a valid PARSER_DISPATCH key | VERIFIED | SHELL_TOOL_REGISTRY at detect.py:105-114 defines shellcheck with output_format="shellcheck_json". PARSER_DISPATCH["shellcheck_json"] = parse_shellcheck at parsers/__init__.py:25. TestShellRoundTrip.test_shell_tools_yaml_roundtrip PASS: load_registry roundtrip confirms output_format=="shellcheck_json" |

**Score:** 3/3 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/code_forge/detect.py` | SHELL_TOOL_REGISTRY constant, deep-copy in generate_tools_yaml, merge-on-force in detect_and_init | VERIFIED | Commit f670981. SHELL_TOOL_REGISTRY at line 105. copy.deepcopy at line 369 and 415. _merge_and_write at line 383. detect_and_init calls _merge_and_write at line 491 when force=True and file exists. |
| `tests/test_detect.py` | Tests for deep-copy aliasing, force-preserves-user-entries, shell detection; min_lines 40 | VERIFIED | Commit 8716b89. 630 lines total. TestDeepCopy (2 tests), TestForceFlag additions (4 new tests), TestShellDetection (5 tests), TestShellRoundTrip (1 test). All 36 tests pass. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| src/code_forge/detect.py | src/code_forge/parsers/__init__.py | SHELL_TOOL_REGISTRY output_format must match PARSER_DISPATCH key "shellcheck_json" | WIRED | SHELL_TOOL_REGISTRY["shellcheck"]["tools_yaml_entry"]["output_format"] = "shellcheck_json" (detect.py:111). PARSER_DISPATCH["shellcheck_json"] = parse_shellcheck (parsers/__init__.py:25). Key present. |
| src/code_forge/detect.py | src/code_forge/registry.py | generate_tools_yaml output must round-trip through load_registry | WIRED | TestShellRoundTrip calls generate_tools_yaml then load_registry; shellcheck entry is present with output_format="shellcheck_json" and isinstance(tc.command, str) -- PASS. TestRoundTrip for Python path also passes. |

### Data-Flow Trace (Level 4)

Not applicable -- detect.py is a detection/generation module that reads filesystem and writes YAML. It does not render dynamic data from a store or API. The data flow is: glob *.sh -> SHELL_TOOL_REGISTRY lookup -> copy.deepcopy -> yaml.safe_dump -> load_registry. All steps exercised by TestShellRoundTrip (PASS).

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| SHELL_TOOL_REGISTRY importable, shellcheck entry correct | python -c "from code_forge.detect import SHELL_TOOL_REGISTRY; assert SHELL_TOOL_REGISTRY['shellcheck']['tools_yaml_entry']['output_format'] == 'shellcheck_json'" | exit 0 | PASS |
| Deep copy: constant not mutated after generate_tools_yaml | python -m pytest tests/test_detect.py::TestDeepCopy -v | 2 passed | PASS |
| Force merge preserves user entries | python -m pytest tests/test_detect.py::TestForceFlag -v | 5 passed | PASS |
| Shell detection produces language="shell" | python -m pytest tests/test_detect.py::TestShellDetection -v | 5 passed | PASS |
| Full test suite: 36 tests, 0 failures | python -m pytest tests/test_detect.py -x | 36 passed in 0.07s | PASS |

### Probe Execution

No probes declared in PLAN.md for this phase and no conventional probe-*.sh scripts exist. Step 7c skipped.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| DET-01 | 10-01-PLAN.md | code-forge detect generates multi-language, alias-free, regen-safe tools.yaml | SATISFIED | All three sub-requirements met: shallow-copy aliasing fixed (SC#2), force-clobber fixed (SC#1), shell/shellcheck support added (SC#3). Verified by 36 passing tests. |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| None | - | No TBD/FIXME/XXX/TODO/HACK/PLACEHOLDER markers found in detect.py or test_detect.py | Info | None |

Stub scan: `generate_tools_yaml` uses `copy.deepcopy` (not `dict()`), `_merge_and_write` performs real yaml.safe_load + merge logic, `detect_toolchain` runs real glob patterns. No empty implementations, no hardcoded empty returns in code paths exercised by tests.

### Human Verification Required

None. All three success criteria are mechanically verifiable (import checks, id() aliasing checks, pytest runs). No visual UI, real-time behavior, or external service dependency.

### Gaps Summary

No gaps. All three roadmap success criteria are verified against actual codebase evidence:

- SC#1: `_merge_and_write` (detect.py:383) reads existing tools.yaml, preserves all entries, updates detected entries. TestForceFlag.test_force_preserves_user_entries confirms shellcheck entry survives force=True with Python project detection (ruff added, shellcheck kept). PASS.
- SC#2: `copy.deepcopy` at detect.py:369 and 415 replaces the former `dict()` shallow copy. TestDeepCopy confirms id() of PYTHON_TOOL_REGISTRY constant's file_patterns list is unchanged after generate_tools_yaml. PASS.
- SC#3: `SHELL_TOOL_REGISTRY` constant at detect.py:105 defines shellcheck with output_format="shellcheck_json". PARSER_DISPATCH["shellcheck_json"] exists at parsers/__init__.py:25. TestShellRoundTrip confirms round-trip through load_registry. PASS.

Both committed files exist (f670981 for detect.py, 8716b89 for test_detect.py). 36 tests pass, 0 fail.

---

_Verified: 2026-06-03T14:30:00Z_
_Verifier: Claude (gsd-verifier)_
