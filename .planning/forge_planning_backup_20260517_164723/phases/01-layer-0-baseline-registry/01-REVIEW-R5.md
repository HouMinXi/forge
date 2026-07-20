# Round 5 Cross-AI Plan Review -- Phase 1 (FULL)

**Reviewer:** Claude Opus 4.7
**Date:** 2026-05-16
**Type:** Full review (not delta-only)
**Plans:** 01-01, 01-02, 01-03, 01-04

## 1. Summary

The 4 plans have converged to a solid, well-documented Phase 1 implementation blueprint. All 4 Round 4 MEDIUM fixes are correctly addressed (R4-C1: regex allowlist + prose fix, R4-K1: integration test uses tracked-file modification, R4-D1: SARIF parser catches JSONDecodeError+KeyError+TypeError, R4-K2: CLI wraps parse_output in try/except KeyError). All 12 Round 3 fixes remain in place. Cross-plan interface consistency is strong -- every function signature matches between producer and consumer. Security is well-covered: yaml.safe_load exclusively, no shell=True, diff_spec validated via allowlist regex. The plans will achieve Phase 1 goals (deterministic Layer 0 gate, registry-driven tool execution, delta-only violation reporting).

Two MEDIUM concerns remain: clippy parser unsafe `spans[0]` access without empty-check, and a semantic gap where `filter_delta` receives already-filtered all_findings. No BLOCKER or HIGH issues.

## 2. Strengths

- **Interface contracts are precise and verified:** Every function signature is specified in the `<interfaces>` block of the producing plan and matched exactly in the consuming plan. 31 cross-plan references traced -- zero mismatches.
- **Round 4 MEDIUM fix verification:** R4-C1 (validate_diff_spec regex): now uses explicit allowlist `^[A-Za-z0-9_./~^@-]+(?:\.\.[A-Za-z0-9_./~^@-]+)?$` with curly braces excluded, prose documents `@{u}` as NOT permitted. R4-K1 (integration test): now modifies tracked file instead of using untracked file. R4-D1 (SARIF parser): exception handler now catches JSONDecodeError + KeyError + TypeError. R4-K2 (output_format validation): CLI wraps parse_output in try/except KeyError.
- **Security baseline is clean:** yaml.safe_load() exclusively (no unsafe_load/FullLoader), subprocess args always as list (no shell=True), diff_spec validated by allowlist before git subprocess, no eval/exec on tool output.
- **Round 3 critical fixes preserved:** B-1 (no list mutation while iterating -- CLI step g builds new lists), C-1 (format_report has tools_failed parameter), C-3 (ToolError.to_dict exists), C-4 (tools.yaml has 6 entries, checkpatch disabled), C-5 (shellcheck skipif guard), H-1 (JSON null safety with `or` pattern), H-2 (atomic write same-dir tempfile), H-4 (pytest.raises(SystemExit)), item 11 (sorted registry iteration).
- **Edge case coverage in acceptance criteria:** Each plan has specific automated verification commands testing success paths, error paths, and security invariants.
- **Architectural boundaries are clear:** git.py is the single owner of git diff subprocess calls (Consensus #1), parsers return list[Finding|ToolError] (Consensus #4), CLI uses EXIT_PASS/EXIT_FAIL constants (Consensus #6).

## 3. Concerns

### MEDIUM

| ID | Plan | Issue | Detail |
|----|------|-------|--------|
| R5-M1 | 01-02 | Clippy parser unsafe `spans[0]` access | Plan says `Extract from message.spans[0]`. Cargo diagnostics can have empty `spans` array (e.g., "aborting due to previous error" messages, or child diagnostics). IndexError on empty spans would crash, not produce ToolError. **Fix:** Add `if not message.get('spans')` guard before accessing `spans[0]`; skip the diagnostic or return ToolError. |
| R5-M2 | 01-04 | `all_findings_preserved` semantic gap | CLI step g removes optional-tool ToolErrors from `all_findings` before passing to `filter_delta`. Then `filter_delta` returns `all_findings_preserved` as a copy of its input. The reporter uses `all_findings_preserved` for the pre-existing count. The count is still correct (only Finding objects matter), but the name `all_findings_preserved` is misleading -- it is actually `filtered_findings` (optional tool errors already removed). Not a bug, but a latent confusion for future maintainers. **Recommendation:** Rename the variable or add a comment explaining that optional ToolErrors are excluded at this point. |

### LOW

| ID | Plan | Issue | Detail |
|----|------|-------|--------|
| R5-L1 | 01-04 | No state.json on zero-changed-files early exit (R4-D3) | CLI step d: `if no changed files: print "PASS", sys.exit(EXIT_PASS)` -- exits without writing state.json. Trivial case but inconsistent with the principle that every forge run produces state. Acceptable for Phase 1. |
| R5-L2 | 01-03 | key_links claims runner imports parse_output (R4-D2) | Plan 01-03 key_links says `from forge.parsers import parse_output` in runner.py. Actual parsing happens in CLI (Plan 01-04), not runner. Runner returns raw (stdout, returncode). Documentation error only -- does not affect implementation. |
| R5-L3 | 01-04 | `Verdict` type alias exported but never defined (R4-K3) | Artifacts section says `exports: ["determine_verdict", "Verdict"]`. No `Verdict` type alias is defined in the plan. `determine_verdict` returns `tuple[str, int]`. Either define the alias or remove from exports. |
| R5-L4 | 01-04 | tools_failed may contain duplicates (R4-K4) | If the same optional tool produces multiple ToolError items (e.g., SARIF parser returns ToolError for each unparseable file), `tools_failed.append(item.tool_name)` runs once per ToolError. The list may have duplicates. Deduplication would be cleaner but is cosmetic. |
| R5-L5 | 01-02 | `_parse_sarif_dispatch` private-named but in public dispatch dict (R4-K5) | The underscore prefix convention signals "internal use", but the function is exposed via PARSER_DISPATCH (a public API). Cosmetic: rename to `parse_sarif` or accept the naming inconsistency. |
| R5-L6 | 01-04 | `--version` argparse flag vs `version` in state.json | The CLI has both `--version` (print version and exit) and writes `"version": "2.0.0a1"` in state.json. No actual conflict -- `--version` exits before state is written. But the word "version" means two different things (forge version vs state format version). Low risk. |

## 4. Cross-Plan Interface Consistency

All 31 producer-to-consumer references verified:

### Wave 1 to Wave 2 (Plan 01-01 to 01-02)
| Producer | Consumer | Status |
|----------|----------|--------|
| `Finding` (base.py) | All 6 parsers | MATCH |
| `ToolError` (base.py) | All 6 parsers + __init__.py | MATCH |
| `Finding.to_dict()` | (used by state.py in Wave 3) | MATCH |
| `ToolError.to_dict()` | (used by state.py in Wave 3) | MATCH |

### Wave 1 to Wave 2 (Plan 01-01 to 01-03)
| Producer | Consumer | Status |
|----------|----------|--------|
| `ToolConfig` (registry.py) | runner.py | MATCH |
| `load_registry(yaml_path) -> dict[str, ToolConfig]` | (used by CLI in Wave 3) | MATCH |
| `match_tools(registry, files) -> dict[str, list[str]]` | runner.py (run_tools calls it internally) | MATCH |
| `extract_changed_lines(diff_text) -> dict[str, set[int]]` | (used by CLI in Wave 3) | MATCH |
| `get_changed_files(diff_text) -> list[str]` | (used by CLI in Wave 3) | MATCH |
| `Finding` (base.py) | delta.py | MATCH |
| `ToolError` (base.py) | delta.py | MATCH |

### Wave 2 to Wave 3 (Plan 01-02 to 01-04)
| Producer | Consumer | Status |
|----------|----------|--------|
| `parse_output(output, format, name, exit_code=0) -> list[Finding\|ToolError]` | cli.py step f | MATCH (4 params passed, return unpacked) |
| `PARSER_DISPATCH` | cli.py (implicit via parse_output) | MATCH |

### Wave 2 to Wave 3 (Plan 01-03 to 01-04)
| Producer | Consumer | Status |
|----------|----------|--------|
| `run_tools(registry, files) -> (results, versions, skipped)` | cli.py step e | MATCH (3-tuple unpacked) |
| `filter_delta(findings, changed_lines) -> (delta, all)` | cli.py step h | MATCH |
| `capture_tool_version(command) -> str` | (called internally by run_tools) | MATCH |

### Wave 1 to Wave 3 (Plan 01-01 to 01-04)
| Producer | Consumer | Status |
|----------|----------|--------|
| `EXIT_PASS, EXIT_FAIL` (__init__.py) | cli.py, verdict.py | MATCH |
| `run_git_diff(diff_spec="HEAD", extra_args=None) -> str` | cli.py step b | MATCH |
| `validate_diff_spec(diff_spec) -> str` | (called internally by run_git_diff) | MATCH |
| `load_registry(yaml_path) -> dict[str, ToolConfig]` | cli.py step a | MATCH |
| `extract_changed_lines(diff_text) -> dict[str, set[int]]` | cli.py step c | MATCH |
| `get_changed_files(diff_text) -> list[str]` | cli.py step c | MATCH |

## 5. Requirement Coverage

| Requirement | Covered By | Status |
|-------------|-----------|--------|
| GATE-01 (PASS/FAIL verdict) | Plan 01-04 verdict.py | COVERED |
| GATE-02 (deterministic FAIL) | Plan 01-03 capture_tool_version + sorted iteration + Plan 01-04 verdict | COVERED |
| GATE-04 (Layer 0 violations gate-blocking) | Plan 01-04 verdict.py -- all Layer 0 findings = FAIL | COVERED |
| LAYER0-01 (per-language tool registry) | Plan 01-01 registry.py + Plan 01-04 tools.yaml (6 tools) | COVERED |
| LAYER0-02 (baseline mode, only NEW violations) | Plan 01-03 delta.py filter_delta | COVERED |
| LAYER0-03 (SARIF parsing + line-number drift) | Plan 01-02 SARIF parser + Plan 01-01 diff.py drift docstring | COVERED |

Phase success criteria from ROADMAP.md:
1. FAIL/PASS on violations -- COVERED (integration tests)
2. Pre-existing not shown -- COVERED (delta filter + baseline test)
3. Adding tool = registry entry -- COVERED (tools.yaml design)
4. FAIL reproducible -- COVERED (GATE-02: version capture + sorted iteration)
5. SARIF parsing + line drift -- COVERED (parser + drift documented as N/A)

## 6. Risk Assessment

**Overall: LOW**

- **Correctness risk: LOW** -- All modules are pure functions where possible (delta, verdict), well-isolated by mockable dependencies (runner, git), and tested at both unit and integration levels.
- **Security risk: LOW** -- shell injection prevented by `shell=False` + args-as-list + allowlist validation; YAML injection prevented by `yaml.safe_load()`; no eval/exec.
- **Integration risk: LOW** -- Interface contracts are explicit and verified across all plan boundaries. Single owner for each cross-cutting concern (git diff, version capture, parsing).
- **Scope risk: LOW** -- checkpatch (disabled), cargo_root (deferred to Phase 2), semgrep determinism (documented) are clearly scoped out or deferred.

## 7. Verdict

**Grade: A-**

The plans are thorough, internally consistent, and correctly address all review feedback from Rounds 3 and 4. The 31 cross-plan interface references all match. Security is well-covered. The two MEDIUM concerns (R5-M1: clippy spans[0] guard, R5-M2: all_findings_preserved naming) are non-blocking and can be addressed during implementation.

### Recommendation: APPROVE for execution

Address R5-M1 and R5-M2 during implementation. The 6 LOW items are cosmetic and do not block.

---

*Review rounds: 5 total. Plans converged from B-/C+ (Round 1) to A- (Round 5).*
