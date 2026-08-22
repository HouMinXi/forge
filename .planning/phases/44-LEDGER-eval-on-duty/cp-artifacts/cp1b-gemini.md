# CP1b External Adversarial Review — Phase 44 EVAL-ON-DUTY (gemini)

**Reviewer:** onmi-gemini3.6 (external adversarial reviewer, fresh context; no credit given to CP1 PASS B=0)  
**Base verified against:** `main @ 8bd01bc` (re-grepped every cited file:line anchor)  
**Scope:** `44-CONTEXT.md` (D-01..D-27), `44-RESEARCH.md` (R-1..R-8), `44-01-PLAN.md`, `44-02-PLAN.md`, `44-03-PLAN.md`, vs real codebase (`src/code_forge/ledger.py`, `machine.py`, `cli.py`, `reviewer_json.py`, `gate_check.py`, `coverage.py`, `advisory.py`, `eval/{corpus,runner,scorer}.py`, `mcp_server.py`).

Notation: `[B]` = Blocker, `[H]` = High, `[M]` = Medium, `[L]` = Low.

---

## Executive Summary

Phase 44 establishes the closed-loop evaluation data supply: writing review outcomes from CI runs into an append-only ledger (`44-01`), extracting adjudicated rows into standalone eval corpus entries (`44-02`), and consuming prior ledger rulings to enable CI convergence without re-reporting resolved findings (`44-03`).

While the plans demonstrate strong architectural intent (single-write O_APPEND under PIPE_BUF, worktree isolation via `--git-common-dir`, tolerant YAML parsing for review-only modes), fresh adversarial analysis uncovered **2 Blockers**, **4 High-severity issues**, **3 Medium issues**, and **2 Low issues**. The most critical concerns are:
1. **Cross-plan data-model drop:** Plan 44-03 assigns style findings a non-blocking disposition (`STYLE`), but Plan 44-01's CI write path only collects `CONFIRMED` findings, silently dropping all style findings from the ledger.
2. **Schema validation crash on clean exports:** Zero-finding PASS runs record `file=""`, which crashes `load_corpus` if exported with `expected_findings` due to mandatory non-empty file validation.
3. **Adjudication revocation hazard:** Taking the latest row per fingerprint allows an incoming `UNADJUDICATED` observation from a parallel or unsynced run to shadow and revoke an explicit human `FIXED`/`DISPROVED` adjudication.

---

## Detailed Findings by Axis

### Axis 1 — Design Correctness vs Real Code (Anchors Re-grepped)

- **[B-1] Style findings downgrade (D-27) unblocks findings without recording them in the ledger, breaking the eval-corpus pipeline.**
  - *Rationale:* In `44-03-PLAN.md` Task 2, style findings remain `StateFinding` instances with fingerprints but receive a non-blocking disposition (e.g. `Disposition.STYLE`). However, `44-01-PLAN.md` Task 2 specifies that `_write_ci_ledger_rows` writes rows *only* for `CONFIRMED` findings (`self._count(Disposition.CONFIRMED)` / `f.disposition == Disposition.CONFIRMED`). Because `44-01` does not include `STYLE` in its write-filter, style findings are never written to `ledger.jsonl`. Consequently, they cannot be adjudicated via `ledger adjudicate` or exported via `ledger export-eval`, violating the primary goal of Phase 44 (corpus grows from real reviewed work).
  - *File/Line:* `44-03-PLAN.md:247-261` vs `44-01-PLAN.md:257-265` vs `src/code_forge/machine.py:1328-1335`

- **[H-4] `_suppress_known_findings` operates exclusively on `CONFIRMED` findings, leaving `COVERAGE` gaps (`UNCERTAIN`) unsuppressed under `pinned_paths` and failing CI.**
  - *Rationale:* D-26 specifies that files matching `pinned_paths` are out of scope. However, `coverage.py:100-115` generates `source="COVERAGE"` findings with `disposition=Disposition.UNCERTAIN` for uncovered files. If `_suppress_known_findings` only iterates over and suppresses `CONFIRMED` findings, `COVERAGE` findings on pinned paths remain `UNCERTAIN`. In `machine.py:527-531`, `coverage_gaps = self._count_coverage_gaps()` checks `f.source == "COVERAGE" and f.disposition != Disposition.DISMISSED`, which remains `> 0` and causes `_run_ci` to return `Verdict.FAIL`. `pinned_paths` must either suppress `COVERAGE` findings or be passed into `coverage.py`'s exempt filter.
  - *File/Line:* `src/code_forge/machine.py:527,1635-1645` vs `44-03-PLAN.md:247-250`

- **[M-1] `ledger adjudicate` fails to inherit `version_sensitive` from source UNADJUDICATED row, silently resetting it to `False`.**
  - *Rationale:* `LedgerRow` defines `version_sensitive: bool = False`. `44-01-PLAN.md` Task 3 specifies inheriting `file, line, axis_claim, base_sha, head_sha, repo_root`, but omits `version_sensitive`. Any source finding derived from a version-sensitive L1 pass loses its version-sensitivity flag upon adjudication.
  - *File/Line:* `44-01-PLAN.md:355-358` vs `src/code_forge/ledger.py:55`

---

### Axis 2 — Concurrency and Atomicity

- **[H-2] `json.dumps()` default `ensure_ascii=True` escapes non-ASCII characters to `\uXXXX` sequences, causing 500-character evidence to exceed the 2048-byte PIPE_BUF margin.**
  - *Rationale:* Python's `json.dumps()` defaults to `ensure_ascii=True`. A 500-character Unicode evidence field (e.g. CJK error messages, AST dumps, or unicode-formatted stack traces) escapes to 6 bytes per character (`\uXXXX`), producing up to 3,000 bytes for the evidence field alone. Added to the metadata fields (~400 bytes), the serialized line reaches ~3,400 bytes, failing the `< 2048 bytes` test and risking atomic write safety on non-standard POSIX environments. `append_row` must pass `ensure_ascii=False` (since `open()` specifies `utf-8`) or enforce byte-level truncation.
  - *File/Line:* `src/code_forge/ledger.py:72` vs `44-01-PLAN.md:158-163,180`

- **[M-3] Extractor deduplication on `(fingerprint, terminal_state)` collapses distinct defects occurring across different diffs.**
  - *Rationale:* D-08 defines dedup by `(fingerprint, base_sha, head_sha)`. However, `44-02-PLAN.md` Task 1 specifies read-side deduplication keyed on `(fingerprint, terminal_state)` with latest-row-wins. If the same rule/linter flags a bug in commit A and later in commit B, only commit B's diff is exported. Commit A's diff is dropped as `dedup-collapsed`, artificially reducing evaluation corpus diversity.
  - *File/Line:* `44-02-PLAN.md:148-155` vs `44-CONTEXT.md:D-08`

---

### Axis 3 — CI-Write Insertion Point

- **[M-2] `_persist_state()` is invoked before `_write_ci_ledger_rows()`, preventing write-path `infra_errors` from being saved to `state.json`.**
  - *Rationale:* `44-01-PLAN.md` places `_write_ci_ledger_rows` after `_persist_state()` (machine.py:541). When `_write_ci_ledger_rows` catches an `OSError` and records a warning to `self._state.infra_errors`, `state.json` is not re-saved. External consumers and MCP extractors reading `state.json` will not see the ledger failure in `infra_errors`.
  - *File/Line:* `src/code_forge/machine.py:541` vs `44-01-PLAN.md:252-255`

---

### Axis 4 — Adjudicate Inheritance Model

- **[H-1] `known_terminal_fingerprints` latest-row-per-fingerprint logic allows a newer UNADJUDICATED row to revoke an existing terminal adjudication.**
  - *Rationale:* `44-03-PLAN.md` Task 1 computes `known_terminal_fingerprints` by taking the latest row per fingerprint and checking if it is `FIXED`, `DISPROVED`, or `DUPLICATE`. If a finding is adjudicated `FIXED` at time $T_1$, and a subsequent CI run (from another branch, a parallel worktree, or a re-run with disabled suppression) appends an `UNADJUDICATED` row at time $T_2 > T_1$, the latest row becomes `UNADJUDICATED`. This revokes the suppression and causes the finding to block CI again. In an append-only ledger, an observation must never shadow or revoke an explicit terminal ruling.
  - *File/Line:* `44-03-PLAN.md:159-178` vs `44-CONTEXT.md:D-23`

---

### Axis 5 — Export-Eval Extractor

- **[B-2] Clean PASS rows (`file=""`, `line=0`) exported with `expected_findings` will crash `load_corpus` due to mandatory non-empty file validation.**
  - *Rationale:* A zero-finding PASS run records an `axis_claim="clean"`, `file=""`, `line=0` row in the ledger (D-07). If `export-eval` emits an `expected_findings` entry for this row, `ExpectedFinding(file="", ...)` is written to `manifest.yaml`. When `load_corpus` parses the manifest, `corpus.py:160` explicitly validates `not file.strip()` and raises `ValueError("expected_findings entry in %r missing file")`. Export-eval must ensure that PASS / clean entries emit `expected_findings: []` (empty list), never an entry with `file=""`.
  - *File/Line:* `src/code_forge/eval/corpus.py:159-163` vs `44-02-PLAN.md:140-145`

- **[H-3] `ESCAPED` rows created via `ledger mark --new` with default `base==head==HEAD` generate empty diffs and permanently uncatchable eval entries.**
  - *Rationale:* In `cli.py:1616-1629`, `ledger mark --new <fp> ESCAPED` defaults `base_sha = head_sha = _git_head(cwd)`. Both SHAs exist (`cat-file -e` passes), but `git diff base..head` produces an empty 0-byte diff. The extractor materializes this empty diff with `expected_verdict="HOLD"`. When replayed, `code-forge review` sees an empty diff and returns `PASS`, causing the entry to permanently score as `MISSED` (false green). The extractor must check `base_sha != head_sha` (and non-empty diff) and skip empty diffs under a dedicated skip counter.
  - *File/Line:* `src/code_forge/cli.py:1616-1629` vs `44-02-PLAN.md:148-155`

---

### Axis 6 — Operability

- **[L-1] `resolve_ledger_root` non-git fallback to `cwd` causes divergent ledger paths when CLI commands are invoked from subdirectories of a non-git project.**
  - *Rationale:* In non-git directory trees, `resolve_ledger_root` returns `cwd`. Running `code-forge ledger list` or `adjudicate` from a subdirectory looks for `<cwd>/.code-forge/ledger.jsonl` rather than discovering the root `.code-forge/` directory.
  - *File/Line:* `44-01-PLAN.md:175-179`

---

### Axis 7 — What Everyone Missed

- **[L-2] `finding_hit` line=0 token fallback fails when reviewer description does not share 2 significant 4-character tokens with `axis_claim`.**
  - *Rationale:* For findings with `line=0`, `valid_line_range` is False, forcing `finding_hit` (scorer.py:83-100) to fall back to description token overlap. If `expected.description` is a generic `axis_claim` (e.g. `"type-check"`), and the reviewer outputs a specific diagnostic (`"P1: Incompatible return value type"`), no two $\ge 4$-character tokens match, resulting in a false-negative score.
  - *File/Line:* `src/code_forge/eval/scorer.py:83-100` vs `src/code_forge/eval/corpus.py:19-32`

---

## Verdict and Scorecard

```
SCORECARD: B=2 H=4 M=3 L=2
```
