# CP-FINAL Convergence Review -- Phase 44 (devops / operability axis)

**Reviewer:** devops (kanban t_20d612c8)
**Date:** 2026-08-22
**Scope:** FINAL convergence check on the Phase 44 plan set (44-CONTEXT.md / 44-01 / 44-02 / 44-03) from the DevOps / Operations & Infrastructure perspective. Ground truth verified against live codebase (main @ 8bd01bc): `src/code_forge/{machine,ledger,gate_check,coverage,advisory,reviewer_json,cli,outlet_resolver,mcp_server}.py` and `eval/{corpus,runner}.py`.

---

## 1. CP1b Blockers Verification (6 / 6)

### 1. kimi B-1: style findings ejected from data model (D-27)
- **Verdict:** CONFIRMED-FIXED
- **Evidence:** `advisory.py:34` explicitly excludes `fingerprint`; 44-03 Task 2 (lines 228-236, 265-276, 289-293) keeps style findings as fingerprinted `StateFinding`s with non-blocking disposition (`DISMISSED`), preserving ledger write/adjudicate/export paths while excluding them from `_count(CONFIRMED)` (machine.py:526-531).

### 2. kimi B-2: kill-switch read load_gate_config dead in review-mode (D-19)
- **Verdict:** CONFIRMED-FIXED
- **Evidence:** `gate_check.py:64-71` raises `ValueError` if `test:` is absent; 44-01 Task 2 (lines 228-234, 277-282, 317-318) and 44-03 Task 2 (lines 240-246) use tolerant raw-YAML read (`yaml.safe_load + dict.get`) inside try/except `OSError`, matching `outlet_resolver.py:130-143`.

### 3. kimi B-3: mutation-survivor FAIL (:360) skipped ledger write (D-01)
- **Verdict:** CONFIRMED-FIXED
- **Evidence:** `machine.py:351-360` returns `Verdict.FAIL` prior to line 541; 44-01 Task 2 (lines 235-239, 251-256, 296-297) funnels all CI terminal exits (including `:360` mutation survivor FAIL and `:541` normal exit) through `_write_ci_ledger_rows`.

### 4. gemini B-1: pinned_paths didn't suppress COVERAGE findings (D-26)
- **Verdict:** CONFIRMED-FIXED
- **Evidence:** `machine.py:1635-1645` counts `source == "COVERAGE" and disposition != DISMISSED`; `coverage.py:104` initializes COVERAGE findings as `UNCERTAIN`; 44-03 Task 2 (lines 219-222, 256-264, 284-286) sets pinned-path COVERAGE findings to `DISMISSED` so `_count_coverage_gaps` drops to 0.

### 5. gemini B-2: ESCAPED base==head -> empty diff false-green (D-02 / D-15)
- **Verdict:** CONFIRMED-FIXED
- **Evidence:** `cli.py:1619-1622` defaults `base_sha = head_sha = _git_head(cwd)` when omitted; 44-02 Task 1 test (f) (lines 164-170) adds `base_sha != head_sha` and non-empty diff check, routing zero-diff rows to dedicated `empty-diff` skip counter.

### 6. deepseek H-1: DUPLICATE mapped to expect-no-catch penalized real bugs (D-02 / D-13)
- **Verdict:** CONFIRMED-FIXED
- **Evidence:** 44-CONTEXT.md D-02 (lines 40-47), D-13 (line 126), and 44-02 Task 1 test (a) (lines 144-149, 176) exclude DUPLICATE from export under its own skip counter rather than generating false `expect-no-catch` entries.

---

## 2. DevOps & Operations Ground-First Audit

### A. Kill-Switch Operability (Env Var + gate.yaml)
- **CI Global Kill:** `CODE_FORGE_DISABLE_LEDGER=1` env var evaluated before file operations. Allows emergency CI bypass across fleet without touching repository commits.
- **Repo Config Kill:** `gate.yaml` `ledger: { enabled: false }` read via tolerant raw-YAML parser. Operates cleanly in review-only projects without `test:` blocks.
- **Degradation:** Missing config file defaults to enabled; unparseable config caught in try block; zero review-blocking side effects.

### B. Failure Isolation (Day-2 CI Stability)
- **Storage Errors:** `_write_ci_ledger_rows` wrapped in `try/except OSError`. Disk full, RO mount, or permission denial degrades to stderr warning + `infra_errors` record. Review verdict (`Verdict.PASS` / `Verdict.FAIL`) is never compromised.
- **Git State Errors:** `resolve_ledger_root` falls back to `cwd` if not in a git repository or `git rev-parse` fails. No uncaught `subprocess.CalledProcessError`.

### C. Worktree Persistence & Clean Lifecycle
- **Resolver Path:** `git rev-parse --path-format=absolute --git-common-dir` with parent directory resolution targets the main worktree root (`<repo>/.code-forge/ledger.jsonl`), not disposable worktree locations.
- **Field Provenance:** Row `repo_root` records normalized main repo root instead of ephemeral worktree path (`D-20b`). Guarantees `git -C <row.repo_root> cat-file -e` resolves during future `export-eval` runs even after worktrees are pruned by CI cleanup hooks.
- **Atomic Writes:** Row sizes bounded under 2048 bytes with `_truncate_evidence` (<= 500 chars + `... [truncated]` marker) ensuring single-syscall atomic `O_APPEND` under `PIPE_BUF` (4096 bytes).

### D. Export Output Hygiene
- **Path Confinement:** Default `--out .code-forge/eval-export`.
- **Destructive Guard:** Non-empty output directory strictly requires `--force` (`D-22`).
- **File Safety:** Re-export selectively overwrites manifest-tracked files and leaves foreign operator files untouched.
- **PII / Secret Guard:** Exported manifests rewrite `repo_root` to repository basename (`D-09`), preventing host filesystem path leakage into shared eval corpora.

### E. Operator Experience & Tooling UX
- **Discovery:** `code-forge ledger list --unadjudicated` surfaces pending rows immediately for human review without manual JSONL inspection (`D-20a`).
- **Inheritance:** `code-forge ledger adjudicate <fp> <state>` automatically inherits metadata (file, line, base/head SHAs, axis_claim) and echoes inherited state to stderr. Eliminates manual copy-paste errors.
- **Reporting:** `export-eval` reports mutually exclusive counters (emitted, unadjudicated, stale-sha, duplicate-excluded, empty-diff, dedup-collapsed) summing exactly to total rows read (`D-15`).

---

## 3. Residual Concerns

No residual blockers, high, medium, or low operational risks found. All 6 CP1b blockers are verified resolved against live code.

NO OBJECTION from devops.

---

SCORECARD: B=0 H=0 M=0 L=0
