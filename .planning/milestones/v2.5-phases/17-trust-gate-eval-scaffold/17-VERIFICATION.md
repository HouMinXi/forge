---
phase: 17-trust-gate-eval-scaffold
verified: 2026-06-10T11:45:00Z
status: complete
score: 5/5
overrides_applied: 0
human_verification:
  - test: "Run code-forge eval --corpus tests/eval/corpus/corpus.yaml --backend <real-backend> and verify scorecard output on stderr"
    expected: "Human-readable ASCII table with raw counts (Caught: N/M), no percentages, all 9 entries listed"
    why_human: "Requires a live LLM backend to verify end-to-end eval pipeline; cannot invoke in unit test environment"
  - test: "Run code-forge review in a cloned hostile repo (gate.yaml with attacker base_url) without running code-forge trust first"
    expected: "stderr shows 'Untrusted repo backends ignored' and NO network call is made to the attacker URL"
    why_human: "Visual confirmation that no exfiltration occurs in a real repo clone scenario; unit test verifies _load_gate_backends but not the full CLI flow"
  - test: "Verify the second load_backend_configs path at cli.py line ~1057 does not bypass trust for realistic attack scenarios"
    expected: "When user runs code-forge review (no --backend flag) on an untrusted repo, the zero-config guard at resolve_outlet blocks before reaching the unguarded path"
    why_human: "Pre-existing code path requires manual flow analysis to confirm the trust guard coverage is sufficient for the stated attack vector (bare code-forge review in cloned repo)"
---

# Phase 17: Trust Gate + Eval Scaffold Verification Report

**Phase Goal:** Repo-supplied config cannot exfiltrate credentials; eval scorecard exists to measure each axis as it ships
**Verified:** 2026-06-10T11:45:00Z
**Status:** human_needed
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths (from ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Repo-supplied gate.yaml backends are NOT used without explicit user opt-in | VERIFIED | `_load_gate_backends` (cli.py:124-131) calls `is_trusted()` before `load_backend_configs()`; returns `[]` with stderr warning for untrusted repos. `trust.py` implements direnv-style trust store at XDG_CONFIG_HOME/code-forge/trusted.json. Test `test_hostile_gate_yaml_no_exfil` asserts `_load_gate_backends` returns `[]` for untrusted hostile gate.yaml. |
| 2 | A hostile gate.yaml fixture does NOT exfiltrate when code-forge review runs | VERIFIED | `test_hostile_gate_yaml_no_exfil` (test_cli_trust.py:204) creates gate.yaml with `base_url=attacker + api_key_env=REAL_KEY`, calls `_load_gate_backends`, asserts result is `[]` and stderr contains "Untrusted repo backends ignored". The guard at cli.py:124-131 returns `[]` before `load_backend_configs` is reached, so no BackendConfig with the hostile base_url is constructed. |
| 3 | Eval corpus contains at least the named real buggy/fixed pairs (E1-E6, gate-yaml-rce, BUG-P12-01, ttl_class) | VERIFIED | `tests/eval/corpus/corpus.yaml` contains all 9 named entries. All 9 diff files exist in `tests/eval/corpus/diffs/` and are non-empty: gate-yaml-rce (337B/14L), E1-stale-nftables (862B/19L), E2-pcap-suffix (1646B/49L), E3-transit-probe (2446B/48L), E4-curl-tproxy (480B/13L), E5-fast-502 (1218B/35L), E6-reprobe-blackout (12884B/349L), BUG-P12-01 (2112B/45L), ttl_class (1672B/48L). All start with `diff --git` unified diff headers. |
| 4 | Eval harness drives a real backend (never mocks) and computes false-green rate per backend | VERIFIED | `runner.py:256` invokes `subprocess.run(["code-forge", "review", "--backend", backend_name])` -- a real subprocess call to the forge CLI with the user-specified backend. Unit tests mock subprocess.run (appropriate for unit tests), but the production code path uses real subprocess invocation. `scorer.py:64` `compute_summary()` computes four-quadrant classification (caught/missed/correct_pass/false_positive/skipped) with SKIPPED excluded from denominator. |
| 5 | Scorecard output is human-readable (table or structured report, not raw JSON) | VERIFIED | `scorer.py:110` `format_table()` produces ASCII table with columns: Name, Expected, Actual, Runs, Caught, Status. Summary line uses raw counts "Caught: N/M" per carry-forward 2. `cli.py:702` prints format_table to stderr. `write_json_report` writes JSON to file only when `--output` specified. Tests verify "7/9" format, absence of "%", column headers present. |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/code_forge/trust.py` | Trust gate: hash, check, store CRUD, dangerous field detection | VERIFIED | 164 lines, 7 exports (is_trusted, record_trust, revoke_trust, trust_status, find_dangerous_fields, hash_backends_block, TrustStatus), XDG_CONFIG_HOME support, DANGEROUS_FIELDS frozenset with 7 members, atomic JSON write, corrupted-store recovery |
| `src/code_forge/advisory.py` | AdvisoryFinding dataclass + AxisRunner Protocol | VERIFIED | 79 lines, frozen=True dataclass with 6 fields (id, axis, file, line_range, description, attribution), no fingerprint/disposition/source fields, AxisRunner Protocol with is_advisory + run(), zero imports from state.py |
| `src/code_forge/eval/__init__.py` | Eval subpackage | VERIFIED | 80 bytes, package marker |
| `src/code_forge/eval/corpus.py` | YAML manifest loader + CorpusEntry dataclass | VERIFIED | 74 lines, CorpusEntry frozen dataclass, load_corpus with yaml.safe_load, empty/malformed handling |
| `src/code_forge/eval/scorer.py` | False-green rate computation + output formatting | VERIFIED | 201 lines, EvalResult + EvalSummary frozen dataclasses, compute_summary four-quadrant classification, format_table ASCII table with raw counts, write_json_report, zero percentage computation |
| `src/code_forge/eval/runner.py` | Pipeline replay per corpus entry + axis hook seam | VERIFIED | 265 lines, replay_entry with subprocess isolation, DETERMINISTIC_TAGS frozenset, AxisHook class with pre/post_review, _AXIS_HOOKS module list, no entry_points/importlib imports, per-run tempdir + isolated XDG_CONFIG_HOME |
| `tests/eval/corpus/corpus.yaml` | Eval corpus manifest with 9 named entries | VERIFIED | 49 lines, all 9 entries: gate-yaml-rce, E1-E6, BUG-P12-01, ttl_class |
| `tests/test_trust.py` | Trust gate unit tests | VERIFIED | 8692 bytes, 19 test functions |
| `tests/test_advisory.py` | Advisory type separation tests | VERIFIED | 7556 bytes, 9 test functions including test_advisory_does_not_reset_cycle_counter |
| `tests/test_cli_trust.py` | CLI trust subcommand tests + hostile regression | VERIFIED | 10099 bytes, 14 test functions including test_hostile_gate_yaml_no_exfil and test_trust_displays_dangerous_fields |
| `tests/test_machine_advisory.py` | Machine advisory wiring tests | VERIFIED | 5629 bytes, 7 test functions |
| `tests/test_eval_corpus.py` | Corpus loader tests | VERIFIED | 4186 bytes, 9 test functions |
| `tests/test_eval_scorer.py` | Scorer computation and formatting tests | VERIFIED | 6947 bytes, 15 test functions |
| `tests/test_eval_runner.py` | Runner replay and hook tests | VERIFIED | 10036 bytes, 17 test functions |
| `tests/test_cli_eval.py` | CLI eval subcommand tests | VERIFIED | 11443 bytes, 17 test functions |
| `src/code_forge/cli.py` | trust subcommand + trust guard + eval subcommand | VERIFIED | add_parser('trust') at line 473, add_parser('eval') at line 488, both in known_subcommands, _run_trust and _run_eval handlers wired in main() dispatch |
| `src/code_forge/machine.py` | advisories list + post-convergence advisory dispatch | VERIFIED | self._advisories list, advisory_runners injection point, _run_advisory_axes dispatch, _serialize_advisories, _display_advisories, "--- Advisory ---" separator, no convergence contamination |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| cli.py | trust.py | `from .trust import is_trusted` | WIRED | cli.py:124 imports is_trusted, cli.py:719 imports trust functions for _run_trust |
| cli.py | eval/runner.py | `from .eval.runner import replay_entry` | WIRED | cli.py:674 lazy import in _run_eval |
| cli.py | eval/corpus.py | `from .eval.corpus import load_corpus` | WIRED | cli.py:673 lazy import in _run_eval |
| cli.py | eval/scorer.py | `from .eval.scorer import compute_summary, format_table, write_json_report` | WIRED | cli.py:675 lazy import in _run_eval |
| machine.py | advisory.py | `from .advisory import AdvisoryFinding` | WIRED | machine.py:29 TYPE_CHECKING import, machine.py:824 lazy import in _run_advisory_axes |
| trust.py | trusted.json | JSON file CRUD | WIRED | _load_trust_store/_save_trust_store at XDG_CONFIG_HOME/code-forge/trusted.json |
| advisory.py | state.py | parallel type (NOT base class) | VERIFIED | Zero imports from state.py confirmed; AdvisoryFinding has no shared fields with StateFinding |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| trust.py | trust store | trusted.json file | Yes -- json.loads reads real file, json.dumps writes real data | FLOWING |
| scorer.py | EvalSummary | compute_summary(results) | Yes -- aggregates real EvalResult objects from replay_entry | FLOWING |
| runner.py | EvalResult | subprocess exit code from code-forge review | Yes -- real subprocess invocation, exit code determines verdict | FLOWING |
| corpus.py | CorpusEntry list | YAML corpus manifest | Yes -- yaml.safe_load parses real YAML file | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Phase 17 tests pass | `python -m pytest tests/test_trust.py tests/test_advisory.py tests/test_cli_trust.py tests/test_machine_advisory.py tests/test_eval_corpus.py tests/test_eval_scorer.py tests/test_eval_runner.py tests/test_cli_eval.py -x` | 107 passed in 0.21s | PASS |
| Full test suite no regressions | `python -m pytest tests/ -x` | 1292 passed, 5 skipped, 0 failures in 116.22s | PASS |
| Carry-forward 1: cycle counter invariant | `grep test_advisory_does_not_reset_cycle_counter tests/test_advisory.py` | Found at line 155, test passes | PASS |
| Carry-forward 2: no percentages | `grep -c "percentage\|percent" src/code_forge/eval/scorer.py` | 0 matches | PASS |
| Carry-forward 3: no plugin discovery | `grep -E "entry_points\|importlib.import_module\|pkg_resources" src/code_forge/eval/runner.py` | 0 matches | PASS |
| Carry-forward 4: no in-repo trust file | `grep -c "\\.trusted" src/code_forge/trust.py` | 0 matches | PASS |

### Probe Execution

Step 7c: SKIPPED -- no probe scripts found under scripts/*/tests/probe-*.sh and no probes declared in PLAN/SUMMARY.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| SEC-01 SC1 | 17-01, 17-02 | Repo-supplied backend NOT used without explicit opt-in | SATISFIED | Trust guard in _load_gate_backends returns [] for untrusted repos; `code-forge trust` required to opt in |
| SEC-01 SC2 | 17-02 | Hostile gate.yaml regression fixture does NOT exfiltrate | SATISFIED | test_hostile_gate_yaml_no_exfil passes; _load_gate_backends returns [] before hostile config is loaded |
| SEC-01 SC3 | 17-02 | Opt-in decision documented | SATISFIED | Trust subcommand with --status shows trust state; dangerous fields displayed on stderr before trust is recorded |
| EVAL-01 SC1 | 17-03, 17-04 | Corpus contains named real buggy/fixed pairs | SATISFIED | corpus.yaml has all 9 entries with non-empty real diff files |
| EVAL-01 SC2 | 17-03 | Eval drives real backend, never mocks | SATISFIED | runner.py:256 invokes `code-forge review --backend` via subprocess; production path never uses mocks |
| EVAL-01 SC3 | 17-03 | False-green rate metric computed per backend | SATISFIED | compute_summary four-quadrant classification; --backend flag selects backend for eval run |
| EVAL-01 SC4 | 17-03, 17-04 | Scorecard output human-readable | SATISFIED | format_table produces ASCII table with column headers and raw count summary line |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| src/code_forge/eval/runner.py | 97 | `"model": "eval-placeholder"` | INFO | Not a stub -- placeholder model name in gate.yaml template for temp repo; real backend specified via --backend flag to code-forge review |
| src/code_forge/cli.py | 1057 | `load_backend_configs(gate_data)` called without trust guard | WARNING | Pre-existing unguarded path that re-reads gate.yaml and loads backends without trust check. Phase 17 did not introduce this -- it existed before. For the stated attack vector ("bare code-forge review"), the zero-config guard in resolve_outlet blocks when cfgs=[] and no --backend flag. Risk exists if user specifies --backend matching a hostile repo backend name. Phase 18 taint analysis may address this. |

### Human Verification — COMPLETED 2026-06-26

### 1. End-to-end eval pipeline with real backend — PASS

**Test:** `code-forge eval --corpus tests/eval/corpus/corpus.yaml --backend mimo-pro --runs 1`
**Backend:** mimo-pro (token-plan-cn.xiaomimimo.com, mimo-v2.5-pro)
**Result:** ASCII table output with 12 corpus entries (9 original + 3 added in later phases). Columns: Name, Expected, Actual, Runs, Caught, Advisory, Status. Summary: "Caught: 3/3 | (1 skipped) | Over-block: 8 | Advisory caught: 0/8". Raw integer counts, no percentages. All blocking entries (gate-yaml-rce, BUG-P12-01, ttl_class) correctly CAUGHT. E7 correctly SKIPPED on infra failure.

### 2. Hostile repo clone scenario — PASS

**Test:** Created `/tmp/hostile-test-repo/` with gate.yaml containing `base_url: http://evil.attacker.example.com/v1` and `api_key_env: HOME`. Ran `code-forge resolve-outlet` without trust.
**Result:** stderr: "Untrusted repo backends ignored. Run 'code-forge trust' to enable." Exit 1. No network request made. Attacker URL never reached. HOME env var never read for exfiltration.

### 3. Pre-existing unguarded backend load path — PASS

**Test:** Static analysis of cli.py `load_backend_configs` call sites.
**Result:** `load_backend_configs` is called ONLY inside `_load_gate_backends()` (line 138), which is guarded by `is_trusted()` at line 130. All 3 call sites in cli.py (eval line 860, review line 1332, resolve-outlet line 2420) go through `_load_gate_backends`. The previously-flagged "unguarded path" at line ~1057 no longer exists -- line 1489-1491 now uses `cfgs` from `_load_gate_backends` with an explicit comment: "A raw load_backend_configs(gate_data) call at this point would bypass the trust check (SEC-02)". Trust guard coverage is complete for the SEC-01 threat model.

### Gaps Summary

No gaps found that block Phase 17 goal achievement. All 5 ROADMAP success criteria are met in the codebase with evidence. The pre-existing unguarded `load_backend_configs` path at cli.py:1057 is a pre-existing concern (not introduced by Phase 17) that warrants attention but does not invalidate the Phase 17 trust guard implementation.

All 107 Phase 17 tests pass. Full suite (1292 tests) passes with zero failures and no regressions. All four carry-forward invariants verified.

---

_Verified: 2026-06-10T11:45:00Z_
_Verifier: Claude (gsd-verifier)_
