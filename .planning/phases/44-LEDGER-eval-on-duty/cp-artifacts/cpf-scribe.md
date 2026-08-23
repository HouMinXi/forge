# CP-FINAL Convergence Review -- Phase 44 (scribe / factual accuracy axis)

**Reviewer:** scribe (kanban t_a61a65f8)
**Date:** 2026-08-22
**Scope:** FINAL convergence check on the Phase 44 (EVAL-ON-DUTY) plan set (44-CONTEXT.md / 44-01-PLAN.md / 44-02-PLAN.md / 44-03-PLAN.md) following CP1 (PASS B=0) and CP1b (kimi B=3, deepseek H=1, gemini B=2). All 6 blockers were adjudicated and fixed. This review audits all cited file:line references against real source code on main @ 8bd01bc, checks internal consistency across the 27 CONTEXT decisions, verifies cross-plan terminology alignment (UNADJUDICATED, suppression, disposition, persistence), and ensures no stale text remains.

---

## 1. CP1b Blockers Verification (6 / 6)

### 1. kimi B-1: style findings ejected from data model (D-27)
- **Verdict:** CONFIRMED-FIXED
- **Evidence:** `advisory.py:26-36` docstring confirms `fingerprint` is intentionally excluded on `AdvisoryFinding` (line 34); 44-CONTEXT.md D-27 (lines 241-254) and 44-03 Task 2 (lines 228-236, 265-276, 289-293) keep style findings as fingerprinted `StateFinding`s with non-blocking disposition (`DISMISSED`), preserving ledger write/adjudicate/export paths while excluding them from `_count(CONFIRMED)` (machine.py:526-531); 44-01 Task 2 (lines 259-270, 315-316) explicitly scopes CI writes to include style-downgraded findings.

### 2. kimi B-2: kill-switch read load_gate_config dead in review-mode (D-19)
- **Verdict:** CONFIRMED-FIXED
- **Evidence:** `gate_check.py:64-71` raises `ValueError("gate.yaml needs an active 'test' section...")` when `test:` is absent; 44-CONTEXT.md D-19 (lines 171-183), 44-01 Task 2 (lines 228-234, 277-282, 300-303, 317-318), and 44-03 Task 2 (lines 240-246, 287-288) mandate tolerant raw-YAML read (`yaml.safe_load + dict.get`) inside try/except `OSError`, matching the established precedent in `outlet_resolver.py:130-143`.

### 3. kimi B-3: mutation-survivor FAIL (:360) skipped ledger write (D-01)
- **Verdict:** CONFIRMED-FIXED
- **Evidence:** `machine.py:351-360` returns `Verdict.FAIL` directly upon finding mutation survivors, bypassing the CI terminal at line 541; 44-01 Task 2 test (g2) (lines 235-239), action (lines 251-256), and acceptance (lines 296-297) funnel all CI terminal exits (including `:360` mutation survivor FAIL and `:541` normal exit) through `_write_ci_ledger_rows`.

### 4. gemini B-1: pinned_paths didn't suppress COVERAGE findings (D-26)
- **Verdict:** CONFIRMED-FIXED
- **Evidence:** `machine.py:1635-1645` counts `source == "COVERAGE" and disposition != Disposition.DISMISSED` separately from CONFIRMED; `coverage.py:100-115` initializes COVERAGE findings as `UNCERTAIN`; 44-03 Task 2 test (b2) (lines 219-222), action (lines 256-264), and acceptance (lines 285-286) set pinned-path COVERAGE findings to `DISMISSED` so `_count_coverage_gaps()` drops to 0.

### 5. gemini B-2: ESCAPED base==head -> empty diff false-green (D-02 / D-15)
- **Verdict:** CONFIRMED-FIXED
- **Evidence:** `cli.py:1619-1622` defaults `base_sha = head_sha = _git_head(cwd)` when omitted; 44-02 Task 1 test (f) (lines 164-170) adds `base_sha != head_sha` and non-empty diff checks, skipping empty-diff rows under a dedicated `empty-diff` skip counter (precedence: unadjudicated > stale-sha > duplicate-excluded > empty-diff > dedup-collapse, 44-02:24, 156-158, 302-303).

### 6. deepseek H-1: DUPLICATE mapped to expect-no-catch penalized real bugs (D-02 / D-13)
- **Verdict:** CONFIRMED-FIXED
- **Evidence:** 44-CONTEXT.md D-02 (lines 40-47), D-13 (line 126), and 44-02-PLAN.md line 21, Task 1 (lines 144-149, 176) explicitly exclude DUPLICATE rows from export under their own skip counter rather than emitting false `expect-no-catch` entries; no stale DUPLICATE->expect-no-catch text remains.

---

## 2. Factual Accuracy & Ground-Truth Verification

Every cited symbol, line range, and contract across all plans was verified against live source on `main @ 8bd01bc`:

| Plan Citation | Real Code Location | Verified Live State | Status |
|---|---|---|---|
| `TerminalState(str, Enum)` :27-38 | `src/code_forge/ledger.py:27-38` | `class TerminalState(str, Enum):` with 4 states | [KNOWN HIGH] |
| `LedgerRow` schema :40+ | `src/code_forge/ledger.py:40-56` | Schema v1 dataclass, 12 fields | [KNOWN HIGH] |
| `iter_rows` KeyError/skip :77-122 | `src/code_forge/ledger.py:101-122` | Skips malformed/invalid schema rows | [KNOWN HIGH] |
| `_run_ci` entry :310 | `src/code_forge/machine.py:310` | `def _run_ci(self) -> Verdict:` | [KNOWN HIGH] |
| Mutation survivor return :351-360 | `src/code_forge/machine.py:351-360` | Direct `return Verdict.FAIL` on survivors | [KNOWN HIGH] |
| `_run_ci` terminal :526-542 | `src/code_forge/machine.py:526-542` | `_count(CONFIRMED)` + `_count_coverage_gaps()` -> `_persist_state()` -> `return verdict` | [KNOWN HIGH] |
| `_write_ledger_rows` :1300-1360 | `src/code_forge/machine.py:1300-1360` | LOCAL writer with `(fingerprint, state)` dedup | [KNOWN HIGH] |
| `_count_coverage_gaps` :1635-1645 | `src/code_forge/machine.py:1635-1645` | Filters `f.source == "COVERAGE" and f.disposition != Disposition.DISMISSED` | [KNOWN HIGH] |
| `active_findings` accessor :240-244 | `src/code_forge/machine.py:240-244` | Excludes `Disposition.DISMISSED` (verified: 44-03:170-174 accurately documents this) | [KNOWN HIGH] |
| `STATE-09` CI skip :287-304 | `src/code_forge/machine.py:287-304` | CI skips loading `state.json` | [KNOWN HIGH] |
| `AdvisoryFinding` contract :26-36 | `src/code_forge/advisory.py:26-36` | Excludes `fingerprint` (line 34) | [KNOWN HIGH] |
| `load_gate_config` validation :39-71 | `src/code_forge/gate_check.py:39-71` | Raises `ValueError` if `"test" not in data` (:64-71) | [KNOWN HIGH] |
| `load_outlet_from_gate` :130-143 | `src/code_forge/outlet_resolver.py:130-143` | Avoids `load_gate_config`, uses `yaml.safe_load` | [KNOWN HIGH] |
| `_json_to_state_findings` :153-181 | `src/code_forge/reviewer_json.py:153-181` | Shared parse-time fingerprint recipe `sha256("%s:%d:%s")[:16]` | [KNOWN HIGH] |
| `_run_ledger` mark/list :1458-1689 | `src/code_forge/cli.py:1458-1689` | Subcommand dispatch; mark at :1474, list at :1661 | [KNOWN HIGH] |
| `mark` SHA default :1619-1622 | `src/code_forge/cli.py:1619-1622` | `base_sha = head_sha = _git_head(cwd)` | [KNOWN HIGH] |
| `mark` metadata drop :1643-1654 | `src/code_forge/cli.py:1643-1654` | Re-ruling drops prior location/SHA metadata | [KNOWN HIGH] |
| `StateMachine` constructors | `cli.py:3303`, `mcp_server.py:928` | Both constructor sites verified; MCP passes no config kwargs | [KNOWN HIGH] |
| `CorpusEntry.axis_tags` :76 | `eval/corpus.py:76` | `axis_tags: list[str]` (free-text list) | [KNOWN HIGH] |
| `load_corpus` validation :81-128 | `eval/corpus.py:81-128` | Validates `expected_verdict in ("HOLD", "PASS")` (:112), ignores unknown keys | [KNOWN HIGH] |
| `_create_gate_yaml` merge :522-563 | `eval/runner.py:522-563` | Existing non-backend keys win | [KNOWN HIGH] |
| `replay_entry` git apply :747-756 | `eval/runner.py:747-756` | Isolated temp repo, applies diff via `git apply` | [KNOWN HIGH] |

---

## 3. Consistency & Cross-Plan Integrity Audit

1. **Decision Coverage**: All 27 decisions in `44-CONTEXT.md` (D-01 through D-27) are explicitly mapped to tasks in the 3 execution plans:
   - Plan 44-01 (Write Path): D-01, D-05, D-06, D-07, D-08, D-10, D-11, D-12, D-16, D-19, D-20a, D-20b, D-21.
   - Plan 44-02 (Export-Eval): D-02, D-03, D-04, D-09, D-13, D-14, D-15, D-17, D-22 (with D-18 coupling).
   - Plan 44-03 (Convergence): D-23, D-24, D-25, D-26, D-27.
2. **Convergence Warnings (W-1..W-5) Status**:
   - `W-1` (AdvisoryFinding in 44-03 frontmatter): RESOLVED. 44-03 line 22 and lines 43-46 correctly specify StateFindings with non-blocking disposition.
   - `W-2` (44-02 skip counter enumeration): RESOLVED. Must-haves line 24, Task 1 test (d) lines 156-158, and Task 3 test (a) line 302-303 enumerate all 5 skip categories in strict precedence.
   - `W-3` (DUPLICATE->no-catch in 44-02): RESOLVED. Must-haves line 21 and Task 1 line 176 exclude DUPLICATE from export.
   - `W-4` (active_findings visibility claim in 44-03): RESOLVED. Task 1 action (lines 170-174) explicitly documents that `active_findings` excludes DISMISSED findings and points the audit trail to `self._state.findings`/`state.json`/`infra_errors`.
   - `W-5` (CI write scope for style findings in 44-01): RESOLVED. 44-01 Task 2 action (lines 259-270) and acceptance criteria (lines 315-316) mandate UNADJUDICATED rows for CONFIRMED and style-downgraded findings, and exclude suppressed-DISMISSED findings from re-writing.
3. **Terminology Consistency**:
   - `UNADJUDICATED`: Consistently used as the pending terminal state across 44-01, 44-02, and 44-03.
   - `suppression`: Consistently refers to the pre-verdict filtering of known terminal fingerprints (`FIXED`/`DISPROVED`/`DUPLICATE`), `pinned_paths`, and `style_downgrade` in `machine.py`.
   - `resolve_ledger_root`: Defined in `ledger.py` (44-01 Task 1) and uniformly consumed across all CLI, machine, and export entry points.
   - `PIPE_BUF` boundary: Strict 500-character cap with `... [truncated]` marker enforced in both 44-01 Task 1 and Task 3.

---

## 4. Residual Concerns

No blocking or high-severity concerns remain. Two minor informational items noted for executors:

- **[L-1] 44-03 Task 2 `<files>` lists advisory.py (LOW):** In `44-03-PLAN.md` line 204, `<files>` lists `src/code_forge/advisory.py`. Under revised D-27, style findings do not touch `advisory.py` (as correctly stated in interfaces line 124). Harmless artifact; no code changes required in `advisory.py`.
- **[L-2] Cross-plan dependency sequencing (LOW):** `44-02-PLAN.md` and `44-03-PLAN.md` declare `depends_on: [44-01]` and rely on `resolve_ledger_root` and CI-written rows from 44-01. Both are wave 2 plans and must be executed after 44-01 is merged.

---

## 5. Verdict

All 6 CP1b blockers are CONFIRMED-FIXED with exact line-level evidence verified against live code on `main @ 8bd01bc`. All 27 CONTEXT decisions are covered, all 5 CP1 convergence warnings are resolved, and all plan citations are factually accurate.

NO OBJECTION from scribe.

SCORECARD: B=0 H=0 M=0 L=2
