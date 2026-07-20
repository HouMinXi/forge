# Phase 5: Prerequisites - Cross-AI Plan Reviews

**Date:** 2026-05-31
**Reviewers:** DeepSeek, Mimo, Kimi (via aicc parallel dispatch)
**Artifact reviewed:** 4 PLAN.md files (05-01 through 05-04)
**Round:** Plan review (post CONTEXT review which had 2 rounds)

## Consensus Findings (2+ models agree)

| # | Severity | Issue | DS | Mimo | Kimi | Resolution |
|---|----------|-------|:--:|:----:|:----:|------------|
| C1 | BLOCKER | Plan 04 `tests/test_cli.py` does not exist; project uses `test_cli_<feature>.py` convention | B3 | B-02 | -- | MUST FIX: use `test_cli_detect.py` + `test_cli_integration.py` |
| C2 | WARNING | Detection report shows pytest but tools.yaml won't contain it -- user confusion | W3 | W-01 | W1-2 | Add "gate-only" category to report |
| C3 | WARNING | `load_outlet_from_gate` missing yaml.YAMLError handling | B2 | -- | W3-1 | Catch YAMLError, return None or raise ValueError with message |
| C4 | WARNING | D-08 write-through invalidation caller not specified (Phase 7) | -- | -- | W2-1 | Add TODO note in Plan 04 for Phase 7 |
| C5 | WARNING | No end-to-end SC#1 integration test (all mock) | W9 | -- | I-2 | Consider adding one integration test |

## Verified BLOCKER (single model, code-confirmed)

| # | Model | Issue | Resolution |
|---|-------|-------|------------|
| V1 | Mimo | `ToolConfig.command` is `str` (registry.py:38) but Plan 01 PYTHON_TOOL_REGISTRY uses `list` -- type mismatch causes downstream AttributeError | MUST FIX: change to string `"ruff check --output-format=json"` |

## Unique Findings Applied (planner should address)

| Model | Finding | Severity | Resolution |
|-------|---------|----------|------------|
| DS | B1: `language="existing"` sentinel in DetectionResult is not a real language | WARNING | Use actual language from loaded registry or separate field |
| DS | W1: Corrupted pyproject.toml unhandled (tomllib.TOMLDecodeError) | WARNING | Catch and fall back to PATH detection |
| DS | W4: `result.stderr` can be None, `.lower()` crashes | WARNING | Use `(result.stderr or "").lower()` |
| DS | W11: Plan 04 tests can't inject mock auth_probe_fn without monkeypatch | NOTE | Document monkeypatch approach |
| Kimi | B3-1: D-10 says resolver is pure but Plan 03 raises CliError | WARNING | Update D-10 wording (resolver raises on auth failure is correct behavior) |
| Kimi | B4-1: detect_and_init stdout pollutes review output | WARNING | Add `silent` parameter for D-20 path |
| Kimi | W2-2: Tests don't distinguish auth-not-configured vs auth-expired | WARNING | Add separate test cases for D-21 (b) vs (c) |
| Kimi | W4-1: `code-forge review` doesn't integrate outlet selection in Phase 5 | NOTE | Correct: BOTH-04 satisfied via `resolve-outlet` subcommand; review integration is Phase 7 |

## Deferred (not Phase 5 scope)

| Finding | Reason |
|---------|--------|
| D-08 invalidation caller | Phase 7 responsibility (CLI dispatcher calls invalidate_auth_cache) |
| Full SC#1 end-to-end test | Can be added during execution or verification |
| FORGE_AUTH_TIMEOUT wiring to review pipeline | Phase 7 (pipeline connection) |

## Per-Reviewer Summary

| Model | Findings | BLOCKERs | WARNINGs | NOTEs |
|-------|----------|----------|----------|-------|
| DeepSeek | 18 | 3 | 9 | 6 |
| Mimo | 12 | 3 | 6 | 3 |
| Kimi | 12 | 2 | 6 | 4 |

## Must-Fix Before Execution (2 items)

1. **V1**: Plan 01 `PYTHON_TOOL_REGISTRY` command values must be `str` not `list`
2. **C1**: Plan 04 test file must follow `test_cli_<feature>.py` convention

## Should-Fix Before Execution (5 items)

3. **C3**: `load_outlet_from_gate` yaml.YAMLError handling
4. **DS-B1**: `language="existing"` sentinel cleanup
5. **DS-W1**: tomllib.TOMLDecodeError handling
6. **DS-W4**: stderr None guard
7. **Kimi-B4-1**: detect_and_init silent mode for review path

## Model Observations

- **DeepSeek**: Most thorough on integration paths and cross-plan consistency. Found 4 cross-plan issues. Best at tracing error propagation chains.
- **Mimo**: Caught the ToolConfig.command type mismatch that others missed -- confirmed as real by reading registry.py:38. Strongest on actual code type checking.
- **Kimi**: Best at D-XX decision compliance. Found the D-10/Plan 03 semantic conflict and the stdout pollution issue. Most structured decision compliance matrix.

---

## Round 2 Plan Review (Convergence)

**Date:** 2026-05-31
**R1 fixes:** 7/7 VERIFIED by all 3 models, 0 regressions

### New Findings (to address in --reviews replan)

| # | Model | Severity | Issue |
|---|-------|----------|-------|
| R2-1 | DS | WARNING | pyproject.toml exists with [project] but no [tool.*] -- PATH fallback missing |
| R2-2 | Kimi | WARNING | _read_cache corrupted JSON (JSONDecodeError) not caught -- should be cache miss |
| R2-3 | Kimi | WARNING | load_outlet_from_gate missing PermissionError handling |
| R2-4 | Kimi | WARNING | _run() modification may drop original ValueError->CliError translation |
| R2-5 | Mimo | NOTE | RESEARCH.md still shows list commands (contradicts V1 fix) |
| R2-6 | Mimo | NOTE | ROADMAP SC#3 says "15 seconds" but D-07 is 20s |

---

## Round 3 Plan Review (Main-Session Gatekeeping)

**Date:** 2026-05-31
**Reviewer:** Main session (Opus 4.8) -- orchestrator gatekeeping pass, NOT a cross-AI panel round. Distinct provenance from R1/R2 (DeepSeek/Mimo/Kimi). Single reviewer by design (see Reviewer Note).
**Artifact reviewed:** 4 PLAN.md files (05-01 through 05-04), cross-checked against 05-CONTEXT.md decisions (incl. the B1/B2 post-review tightenings of D-03/D-23) and the locked constraints D-08/D-16.
**R1/R2 uptake confirmed:** all R1 must-fix (V1, C1) + should-fix (C3, DS-B1, DS-W1, DS-W4, Kimi-B4-1) and all R2 findings (R2-1..R2-4) are present in the plans as explicit tests + acceptance criteria. R2-5/R2-6 (NOTEs on RESEARCH.md list commands / ROADMAP "15s") not re-verified here.

### Locked-item compliance (B1/B2/D-08/D-16)

| Locked item | Status | Evidence |
|---|---|---|
| B1 (`tools: []` -> fail loud) | HONORED | 05-01 test_empty_tools_yaml_treated_as_missing + 05-04 end-to-end: load_registry={} -> detect_and_init -> D-02 CliError -> EXIT_CLI_ERROR |
| B2 (gate.yaml round-trip OR defer) | HONORED | Resolved via D-23 "defer" option: Phase 5 generates tools.yaml ONLY; gate.yaml deferred to Phase 6/7 (sidesteps the undefined-template problem) |
| D-08 (auth-cache staleness) | HONORED | 5-min TTL + failure-not-cached + write-through invalidation (05-02) |
| D-16 (no model-capability detect) | HONORED | 05-03 explicit acceptance criterion + threat T-05-11 |

### New Findings

| # | Severity | Plan | Issue | Resolution |
|---|----------|------|-------|------------|
| R3-1 | HIGH (BLOCKER) | 05-02 | Probe command `claude -p "ack" --max-tokens 1` is UNVERIFIED and mock-untestable. If `--max-tokens` is not a valid `claude` flag, the probe exits non-zero ("unknown option") and D-21 misclassifies it as auth-failure -> Outlet A never selects even when auth is fine -> every CLI review FAIL-CLOSES. The 15 mock tests cannot catch this (they fabricate returncode); only the `@pytest.mark.real_api` test (skipped by default) would. | MUST run the real_api probe once against a real `claude` before ship; confirm `--max-tokens 1` is valid or drop it. This is the "mechanical command bug breaks the gate" class -- the exact failure the v2.2 motivating episode warns about. |
| R3-2 | MEDIUM (WARNING) | 05-04 | `is_default_registry = (args.registry == ".code-forge/tools.yaml")` is a brittle exact-string compare. If the argparse default for `--registry` is not byte-identical (absolute path, `./` prefix, Path object), D-20 auto-detect never triggers -> zero-config first-run silently broken (reintroduces "no tools.yaml -> cannot review"). | Verify cli.py `--registry` default is exactly that literal; safer to compare resolved Paths. |
| R3-3 | MEDIUM (WARNING) | 05-01 | mypy is excluded from L0 (registry: "type checker, not linter", no tools_yaml_entry) because `_KNOWN_FORMATS` has no mypy format. A Python project with ONLY mypy (no ruff/pylint/flake8) -> detect finds mypy -> generate_tools_yaml filters it out -> empty -> D-02 "No toolchain detected" despite having a real static analyzer. | Document as known limitation; mypy-format support is a follow-up (needs a registry format addition, out of Phase 5 scope). |
| R3-4 | MEDIUM (WARNING) | 05-01 | CLI-04 scope is split: gate.yaml deferred to Phase 6/7 (legit per D-23), but 05-01 only asserts "CLI-04 tools.yaml half satisfied here." | Confirm ROADMAP SC + REQUIREMENTS traceability mark CLI-04 as PARTIALLY met in Phase 5, else the phase claims CLI-04 done while half is deferred. |
| R3-P1 | PROCESS | all | No Phase 0 worktree step in any plan. These modify real `src/code_forge/*.py` (not gitignored). CLAUDE.md: "Worktree setup must be Phase 0 ... plans that skip it are incomplete." | Confirm gsd-execute-phase creates a worktree, or add Phase 0. |
| R3-P2 | PROCESS | all | Plans are TDD but do not show the pre-commit three-cycle review + smoke gate. | Confirm the review gate runs before committing these modules (do not commit on TDD-green alone). |
| R3-m1 | MINOR (NOTE) | 05-03 | Whitespace-handling prose in resolve_outlet Task 2 is self-contradictory ("...will produce ''... Actually no:..."). Behavior is correct (pinned by test_env_whitespace_raises); only the explanation is garbled. | Clean the prose so the executor is not confused. |
| R3-m2 | MINOR (NOTE) | 05-04 | resolve-outlet maps auth-fail CliError -> exit 1 but config ValueError -> exit 2, while project convention is CliError -> exit 2 everywhere. Intentional per D-15. | Fine as long as SKILL.md only checks non-zero; flag the deliberate divergence. |
| R3-m3 | MINOR (NOTE) | 05-01 | detect globs `*.py` "root + one level deep" only; a src-layout project WITHOUT pyproject.toml (rare) would not be detected as Python. | Low risk; note for completeness. |

### Must-Fix Before Ship (1 item)

1. **R3-1**: Run the real_api probe once and confirm `claude -p ... --max-tokens 1` is a valid invocation (or drop `--max-tokens`). Blocks the entire Outlet A gate if wrong.

### Should-Clarify Before/During Execution (5 items)

2. **R3-2**: Verify `--registry` default matches the is_default_registry compare.
3. **R3-3**: Document the mypy-only L0 limitation.
4. **R3-4**: Reflect CLI-04 partial-in-Phase-5 in ROADMAP/REQUIREMENTS traceability.
5. **R3-P1**: Confirm worktree (Phase 0).
6. **R3-P2**: Confirm the pre-commit three-cycle review gate.

### Reviewer Note

R3 is a single-reviewer (main-session Opus 4.8) gatekeeping pass, deliberately NOT another multi-model panel round -- per the convergence judgment that R1/R2 already absorbed the decision-layer defects and further panel rounds would surface diminishing mechanical nits below planner altitude. R3's job was to catch what a panel structurally misses: a command-validity bug invisible to mock tests (R3-1), brittle integration glue (R3-2), and a tool-coverage gap (R3-3), plus process-rule compliance (worktree, review gate).
---

## Round 4 (Panel reconvergence + Main-Session Gatekeeping of the sub-session report)

**Date:** 2026-05-31
**Numbering note:** the `/gsd:plan-phase` sub-session called its panel pass "Round 3", but "Round 3" above is already the main-session gatekeeping pass. To resolve the collision, the sub-session's panel pass is recorded below as **4a** and this gatekeeping pass as **4b**.

### 4a. Panel reconvergence (DeepSeek + Mimo + Kimi, via the plan-phase sub-session) -- AS REPORTED

Reported by the sub-session, NOT independently re-run here:
- Claim: "7/7 R3 fixes VERIFIED, 0 regressions, 0 BLOCKER"; plans converged (sub-session counts 5 review rounds / 18 model evaluations across CONTEXT R1-R2 + PLAN R1-R3).
- New findings handled: `--max-tokens` residuals removed from RESEARCH.md and CONTEXT.md D-06.
- Deferred to executor (4, "no plan change"): (1) Kimi-HIGH retry `load_registry` ValueError propagation inside the except block; (2) DS-W2 mypy-only project missing a test; (3) DS-W3 `pyproject.toml` tool key non-dict guard; (4) Kimi-M2 cache `_read_cache` KeyError/TypeError breadth.
- NOTE: the "7/7" does not map cleanly onto R3's 9 findings; recorded as reported, NOT independently reconciled.

### 4b. Main-session gatekeeping of that report (Opus 4.8) -- VERIFIED, not rubber-stamped

- **R3-1 RESOLVED + independently proven.** grep confirms `--max-tokens` removed consistently across CONTEXT D-06, RESEARCH:103, and 05-02-PLAN (lines 149/168/202/206), all now `["claude","-p","ack"]`. `claude --help` (run this session) confirms `-p, --print` is a real flag and `--max-tokens` does NOT appear anywhere in help -- so R3-1 was a genuine gate-breaker (invalid flag -> non-zero exit -> misclassified auth-fail -> Outlet A never selects), correctly killed by dropping the flag. Residual (non-blocking): runtime exit-code semantics of `claude -p "ack"` are still only proven by the opt-in `@pytest.mark.real_api` test; the BLOCKER (flag validity) is closed.
- **F1 (LOW, FIXED): incomplete `--max-tokens` cleanup.** RESEARCH:159 still cited "D-06 `--max-tokens 1`" as the token-cost mitigation -- a stale residual contradicting :103 and the corrected D-06, so the sub-session's "RESEARCH fixed" claim was incomplete. Fixed to "minimal ack prompt (~5 tokens)".
- **F2 (deferred Kimi-HIGH ESCALATED -> DECIDED).** Deferring a HIGH as an untested "executor will broaden the except" left both an untested path and an implicit "silently overwrite a malformed user config" choice. Ground-truth smoke test of the REAL `load_registry` (registry.py:58-102): the 5 empty forms return `{}`; the 4 present-but-malformed forms raise `ValueError` with actionable messages. Decided **D-24** (CONTEXT): empty -> regenerate (D-03); malformed -> FAIL LOUD `CliError` (no silent overwrite); `force=True` escape hatch. Pinned with 2 new tests in 05-01-PLAN (`test_malformed_existing_tools_yaml_fails_loud`, `test_force_regenerates_malformed_tools_yaml`; plan test count 16 -> 18). The other 3 deferrals (DS-W2, DS-W3, Kimi-M2) accepted as genuine impl-time details.
- **Bookkeeping:** this entry records the previously-unrecorded panel round and resolves the two-R3 naming collision.

### Net state after R4

- R3-1 (the only must-fix-before-ship) CLEARED. No open BLOCKER.
- New locked decision: D-24. Docs touched this pass: CONTEXT (D-24), 05-01-PLAN (18 tests + step-5 branch), RESEARCH:159.
- Still open, non-blocking (clarify during execution): R3-2 (registry-default exact-string compare), R3-3 (mypy-only L0 limitation doc), R3-4 (CLI-04 partial-in-Phase-5 traceability), R3-P1 (worktree Phase 0), R3-P2 (pre-commit three-cycle review gate).
