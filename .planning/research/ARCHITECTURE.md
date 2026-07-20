# Architecture: Forge v2.4 "Honest Green" Integration Map

**Domain:** 6 new review axes + eval framework hooking into existing forge pipeline
**Researched:** 2026-06-09
**Confidence:** HIGH (all integration points verified by reading source)

---

## Current Architecture Summary

```
cli.py::_run()
  |-- resolve_baseline()         baseline.py
  |-- build_falsifier()          factories.py:25
  |-- build_l1_provider()        factories.py:200
  |-- build_revert_fn()          factories.py:96
  |-- StateMachine.run()         machine.py:158
      |-- _execute_round()       machine.py:614
          |-- _run_l0_phase()    machine.py:483  (registry tools)
          |-- _run_l1_phase()    machine.py:500  (LLM review, 3 passes)
          |-- _run_l2_phase()    machine.py:531  (mutation via mutmut)
          |-- _run_e2e_phase()   machine.py:561  (cross-component)
          |-- _run_coverage()    machine.py:587  (per-file gap check)
          |-- _merge_findings()  machine.py:803
          |-- write_receipts()   receipt.py
          |-- save_state()       state.py
```

Key types: `StateFinding` (state.py:38), `Disposition` (disposition.py), `Verdict` (state.py:29), `L1Provider` (machine.py:52).

---

## Per-Axis Integration Points

### Axis 1: Fix-Validation (Revert-Test-RED)

**What it does:** After a fix commit, revert it, run tests, confirm they fail. Green-after-revert = untested fix.

| Component | File:Line | Change Type | Detail |
|-----------|-----------|-------------|--------|
| New module | `src/code_forge/fix_validate.py` | **NEW** | `validate_fix(commit_sha, test_cmd, cwd) -> FixValidationResult`. Shells out to `git stash`, `git revert --no-commit`, runs test_cmd, checks exit code, restores. |
| CLI subcommand | `cli.py:_build_parser()` ~L290 | **MODIFY** | Add `fix-validate` subparser: `--commit SHA`, `--test-cmd CMD`. |
| CLI dispatch | `cli.py:main()` ~L648 | **MODIFY** | Route `fix-validate` to handler. |
| State finding | `state.py:48` | **MODIFY** | Add `"FIX_VALIDATE"` to source Literal union. |
| Machine integration | `machine.py:_execute_round()` L614 | **MODIFY** (optional) | Add `_run_fix_validate_phase()` after L2, before coverage. Only runs if `--committed` flag is set. |
| Overfit guard | `machine.py:_apply_autofix_loop_to()` L677 | **MODIFY** | After `FixOutcome.SUCCESS`: revert fix, run test, confirm RED. If GREEN -> mark as `OVERFIT` (new FixOutcome). |
| Factory | `factories.py` | **MODIFY** | Add `build_fix_validator()` returning the callable. |

**Build order:** fix_validate.py (standalone) -> CLI subcommand -> machine integration -> overfit guard.

**Dependencies:** gate_check.py `load_gate_config()` for test command. git.py for git operations.

---

### Axis 2: Trust-Boundary Taint (Config-to-Sink)

**What it does:** Add "input provenance" question to L1 reviewer prompt. Annotate findings with danger score.

| Component | File:Line | Change Type | Detail |
|-----------|-----------|-------------|--------|
| L1 prompt | `factories.py:246-269` | **MODIFY** | In `_provider()`, add to each pass prompt: `"For each external input in the diff, state: (1) provenance (user HTTP, env var, config file, hardcoded), (2) what happens if an attacker controls it."` |
| Subagent prompt | `cli.py:_make_subagent_spawn()` L471-509 | **MODIFY** | Mirror the same provenance question in the subagent pass prompt. |
| StateFinding schema | `state.py:38` | **MODIFY** | Add optional `danger_score: Optional[int] = None` field (0-10). Additive, no schema bump per D2 convention. |
| Reviewer JSON schema | `reviewer_json.py:14` | **MODIFY** | Add optional `"danger_score"` to finding fields. Extract in `_json_to_state_findings()`. |
| Coverage hardening | `coverage.py:42` | **MODIFY** (optional) | For files matching `*.yaml`, `*.yml`, `*.toml`, `*.env*` patterns, require L0 coverage even if L1 active. Config files are trust boundaries. |

**Build order:** Prompt change (factories.py + cli.py) -> schema extension (state.py + reviewer_json.py) -> coverage hardening.

**Dependencies:** None. Purely additive. Prompt change is lowest-risk, highest-value.

---

### Axis 3: Runtime-Contract Review (Verdict Calibration)

**What it does:** Track whether each review layer actually ran successfully. Prevent false-green from silent backend failure.

| Component | File:Line | Change Type | Detail |
|-----------|-----------|-------------|--------|
| L1Provider type | `machine.py:52` | **MODIFY** | Change from `Callable[[], tuple[list, list, Usage, float]]` to include 5th bool: `ran_successfully`. |
| L1 provider impl | `factories.py:228-329` | **MODIFY** | Track whether ANY pass succeeded. Return `ran_ok` as 5th element. All-passes-failed: `([], [], usage, dur, False)`. |
| Stub provider | `factories.py:219` | **MODIFY** | Return `([], [], Usage(), 0.0, False)` -- stub did NOT run L1. |
| Machine L1 phase | `machine.py:500-529` | **MODIFY** | Unpack 5th element. Store `self._l1_ran_this_round`. |
| Coverage gate | `machine.py:587` -> `coverage.py:42` | **MODIFY** | Pass `l1_ran_this_round` instead of `self.coverage_l1_active`. Runtime truth, not config assumption. |
| State schema | `state.py` | **MODIFY** | Add `l1_ran: bool = False` and `l1_skipped_reason: Optional[str] = None`. Additive. |
| Round snapshot | `machine.py:858-887` | **MODIFY** | Add `"l1_ran": self._l1_ran_this_round` to snapshot dict. |
| CI verdict | `machine.py:374-390` | **MODIFY** | If `not l1_ran` and engine != "stub": verdict = FAIL, infra_error "L1 backend unreachable." |

**Build order:** L1Provider type -> factories.py -> machine.py -> coverage.py -> state.py.

**This is the foundational change.** Every other axis depends on knowing if L1 actually ran.

---

### Axis 4: Legacy Code Surfacing (Blame-Aware)

**What it does:** Annotate findings with file age. Optionally raise cycle threshold for old files.

| Component | File:Line | Change Type | Detail |
|-----------|-----------|-------------|--------|
| New function | `git.py` | **MODIFY** | Add `file_last_modified(filepath, cwd) -> Optional[datetime]`. Runs `git log --format='%aI' -1 -- <file>`. |
| Diff enrichment | `diff.py` | **MODIFY** | Add `annotate_file_ages(changed_files, cwd) -> dict[str, int]`. Returns `{file: days_since_last_modified}`. |
| StateFinding schema | `state.py:38` | **MODIFY** | Add optional `file_age_days: Optional[int] = None`. Additive. |
| Machine round | `machine.py:614` | **MODIFY** | After `_merge_findings()`, populate `file_age_days` on each finding. |
| Tier threshold | `diff.py:53` | **MODIFY** (optional) | Accept `max_file_age` param. If any file > 365 days old, +1 to threshold. |

**Build order:** git.py -> diff.py -> state.py -> machine.py -> tier_threshold.

**Dependencies:** None. Independent of other axes.

---

### Axis 5: Graph-Triage (Blast-Radius Ranking)

**What it does:** Extract entities from diff, query dependency graph, annotate findings with blast-radius score.

| Component | File:Line | Change Type | Detail |
|-----------|-----------|-------------|--------|
| New module | `src/code_forge/entity_extract.py` | **NEW** | `extract_entities(diff_text) -> list[Entity]`. Entity = `(file, name, kind, start_line, end_line)`. Regex for Python/Go/shell. Tree-sitter optional. |
| New module | `src/code_forge/graph_client.py` | **NEW** | `query_blast_radius(entities, cwd) -> dict[str, int]`. Tries MCP -> CLI fallback -> empty dict. 5s timeout. |
| StateFinding schema | `state.py:38` | **MODIFY** | Add optional `blast_radius: Optional[int] = None`. Additive. |
| Machine round | `machine.py:614` | **MODIFY** | After `_merge_findings()`, populate `blast_radius`. |
| Finding sort | `reporter.py` or `machine.py:803` | **MODIFY** | Sort by `blast_radius` descending before display. |

**Build order:** entity_extract.py -> graph_client.py -> state.py -> machine.py -> reporter.

**Dependencies:** External: code-review-graph (MCP or CLI). Graceful degradation if absent.

---

### Axis 6: False-Green-Rate Evaluation

**What it does:** Replay labeled bug corpora through forge, measure detection rate per backend.

| Component | File:Line | Change Type | Detail |
|-----------|-----------|-------------|--------|
| New subpackage | `src/code_forge/eval/__init__.py` | **NEW** | Eval subpackage. |
| Corpus loader | `src/code_forge/eval/corpus.py` | **NEW** | `load_bugsinpy(path) -> list[BugCase]`. BugCase = `(project, bug_id, buggy_sha, fixed_sha, bug_file, bug_lines)`. |
| Replay runner | `src/code_forge/eval/runner.py` | **NEW** | `replay_case(case, backend, cwd) -> EvalResult`. Checkout buggy, run forge, parse state.json for overlap with known bug. |
| Metrics | `src/code_forge/eval/metrics.py` | **NEW** | `compute_fgr(results) -> FalseGreenReport`. FGR = FN/(FN+TP). Per-backend breakdown. |
| CLI subcommand | `cli.py:_build_parser()` | **MODIFY** | Add `eval` subparser: `--corpus`, `--backend`, `--output`. |
| CLI dispatch | `cli.py:main()` | **MODIFY** | Route `eval` to handler. |

**Build order:** corpus.py -> runner.py -> metrics.py -> CLI subcommand.

**Dependencies:** BugsInPy corpus (external). Forge CLI must be functional.

---

## New vs Modified Module Summary

| Module | Status | Axis | Lines (est.) |
|--------|--------|------|-------------|
| `fix_validate.py` | **NEW** | 1 | 80-120 |
| `entity_extract.py` | **NEW** | 5 | 60-100 |
| `graph_client.py` | **NEW** | 5 | 50-80 |
| `eval/__init__.py` | **NEW** | 6 | 5 |
| `eval/corpus.py` | **NEW** | 6 | 60-80 |
| `eval/runner.py` | **NEW** | 6 | 80-120 |
| `eval/metrics.py` | **NEW** | 6 | 40-60 |
| `factories.py` | MODIFY | 2,3 | +30 |
| `machine.py` | MODIFY | 1,3,4,5 | +60 |
| `state.py` | MODIFY | 1-5 | +15 (additive fields) |
| `cli.py` | MODIFY | 1,6 | +40 |
| `diff.py` | MODIFY | 4 | +20 |
| `git.py` | MODIFY | 4 | +15 |
| `coverage.py` | MODIFY | 2,3 | +10 |
| `reviewer_json.py` | MODIFY | 2 | +10 |

**Total new:** ~550-700 lines (7 files). **Total mods:** ~200 lines (9 files).

---

## Architectural Patterns

### Additive Schema (state.py)
All new fields optional with defaults. No schema_version bump (D2). Old state.json loads without error.

### Layer Injection (machine.py)
Each axis = a callable injected via constructor, same as l1_provider/l2_runner/e2e_runner. Machine never imports implementations.

### Graceful Degradation (graph_client.py, coverage.py)
External tools absent = no annotation, not crash. Matches: mutmut absent -> MUTATION_SKIPPED.

### Factory Centralization (factories.py)
All "which impl" decisions in factories.py. New axes get `build_fix_validator()` etc.

---

## Anti-Patterns to Avoid

| Anti-Pattern | Why Bad | Instead |
|--------------|---------|---------|
| Coupling axes | Bug in graph-triage breaks fix-validation | Each phase runs independently, merge at end |
| Schema breaks | verify.py hashes state; breaks corrupt audit trail | Additive fields only |
| Blocking on external tools | Hung graph server blocks pipeline | 5s timeout, return empty, log infra_error |
| Inlining eval in review | Eval corpus replay is batch, not per-review | Separate `eval` subcommand |

---

## Build Order (Recommended)

```
Phase 1: Axis 3 (verdict calibration)     -- foundational
Phase 2: Axis 6 (eval framework)          -- measures everything
Phase 3: Axis 1 (fix-validation)          -- builds on gate_check
Phase 4: Axis 2 (trust-boundary prompt)   -- low risk, high value
Phase 5: Axis 5 (graph-triage)            -- external dependency
Phase 6: Axis 4 (legacy surfacing)        -- lowest priority
```

## Sources

All integration points from direct source reading at `/home/houminxi/code/forge/src/code_forge/`:
cli.py (1735L), machine.py (916L), factories.py (332L), diff.py (255L), detect.py (509L), state.py, coverage.py, falsify_real.py, reviewer_json.py, sarif.py.
