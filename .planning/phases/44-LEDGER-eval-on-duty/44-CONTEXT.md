# Phase 44: EVAL-ON-DUTY - Context

**Gathered:** 2026-08-21
**Status:** Round-1 review adjudicated 2026-08-22 (kanban t_6e58fa74,
SCORECARD B=2 H=5 M=4 L=2; both blockers confirmed against real code and
resolved below as D-15..D-21 + D-03/D-12/D-13 revisions). Ready for
round-2 review, then planning.

<domain>
## Phase Boundary

EVAL-02: eval case generation re-extracts diffs from the LEDGER so the
corpus grows from real reviewed work instead of hand curation. This
requires (a) the ledger to actually receive rows from the way forge is
really invoked (CI mode -- today it writes zero), and (b) an extractor
that turns adjudicated ledger rows into eval-bank-format corpus entries
(manifest + diffs + answers).

Prereq Phase 43 (LEDGER, merged 14328bb). The v2.9 schedule estimated
300-450 LOC; round-1 review confirmed the real scope is ~750+ LOC.
**Scope split (ratified):** Plan 44-01 = write path (CI-write, H2
persistence, adjudicate, schema honesty); Plan 44-02 = export-eval
extractor. Two plans, one phase. Root of the v2.9 lane -- Phase 51
(BASIS-DISCLOSE) and the 52/53a/53b chain hang off it.

</domain>

<decisions>
## Implementation Decisions

### D-01..D-04 (user-ratified 2026-08-21)

- **D-01: CI mode writes ledger rows.** CI review terminal states
  (PASS/FAIL/ESCALATED) append UNADJUDICATED rows carrying base/head
  SHA, verdict, CONFIRMED finding fingerprints, diff_sha256. H1 (CI
  never writes) is fixed IN this phase, not deferred -- user decision:
  "那必须要打通CI模式写ledger".
- **D-02: Terminal-state mapping (all three).** ESCAPED -> missed-bug
  entry (false negative), FIXED -> hit entry, DISPROVED ->
  false-positive entry. One extractor, three signal types.
- **D-03: Dead-SHA handling (REVISED round-1 -- contradiction
  resolved).** Extractor validates each row's base/head via
  `git -C <row.repo_root> cat-file -e`; unresolvable -> skip the row +
  stderr warning + summary stale count + the skipped rows are LISTED in
  the export summary report (fingerprint + reason). No manifest entry is
  generated for a skipped row -- a diff that cannot be materialized has
  no entry to annotate (round-1 B-1). The `source_status` field is
  dropped; entries are only emitted for fully materialized diffs.
- **D-04: CLI shape is `ledger export-eval` subcommand.** Reads
  ledger.jsonl, emits an independent manifest+diffs+answers directory.
  Coexists with hand-curated eval-bank/v1 -- no mixing. Independently
  reviewable, re-runnable, diffable.

### Fable constraints (ratified 2026-08-21 -- binding on planner)

- **D-05: D-01 and H2 are ONE atomic task.** CI-write without
  main-repo persistence is write-and-lose: forge's mandated workflow IS
  worktrees, and .code-forge/ is gitignored, so worktree removal
  destroys the rows. The plan MUST treat them as a single task.
- **D-06: UNADJUDICATED naming honesty.** The enum is named
  TerminalState and its docstring documents entry constraints for the
  four terminal states. Either rename the enum (LedgerState) or rewrite
  the docstring contract ("four terminal + one pending-adjudication").
  Planner picks; doing nothing is NOT an option. (Round-1 L-1 noted:
  old readers tolerate the new member silently -- iter_rows skips rows
  whose enum value fails to parse, per c5d420d hardening. Acceptable
  degradation; document it.)
- **D-07: Row size stays under PIPE_BUF (EXTENDED round-1).**
  Per-finding row granularity preserved; a run-level aggregate row only
  for PASS runs (zero findings) with axis_claim="clean". Round-1 H-2
  added: the `evidence` field is currently UNCAPS (`--evidence` accepts
  arbitrary text; machine.py writes f.error which can be a stack
  trace). Write path must truncate evidence to a bound (<=500 chars,
  explicit truncation marker) and a test must assert serialized row
  size < 2048 bytes.
- **D-08: UNADJUDICATED dedup key includes diff identity.** Dedup key
  for UNADJUDICATED must be (fingerprint, base_sha, head_sha) or
  diff_sha256 -- same diff re-run does not re-append. (Round-1 M-4:
  the check-then-act dedup race under parallel CI is accepted as
  best-effort per 2495035's stated contract; the EXTRACTOR dedups on
  read -- fingerprint+terminal_state, latest row wins.)
- **D-09: Extractor resolves SHAs against row.repo_root, NOT cwd
  (EXTENDED round-1).** Round-1 M-2 + L-2: repo_root is an absolute
  path (provenance, keep as recorded) but breaks when the repo moves
  or the ledger is read elsewhere -> export-eval accepts a
  `--repo-root` override to remap. Exported manifests must never
  contain absolute paths: provenance is rewritten to repo basename
  (PII/path-disclosure guard).

### Gray areas surfaced by territory exploration (2026-08-21)

- **D-10 (HIGH): adjudication/upgrade path does not exist (EXTENDED
  round-1).** Round-1 B-2 confirmed against cli.py:1643-1654 AND made
  it worse: marking an existing fingerprint today lets file/line/
  axis_claim be omitted, producing a metadata-free row. The new
  `ledger adjudicate` subcommand MUST inherit file/line/axis_claim/
  base_sha/head_sha from the source UNADJUDICATED row it upgrades
  (append-only: new terminal-state row referencing the fingerprint,
  old row never rewritten).
- **D-11 (HIGH): `git rev-parse --git-common-dir` is unreliable
  outside a git repo.** Persistence resolution: try --git-common-dir
  in cwd; on failure fall back to writing cwd-local.
- **D-12 (CORRECTED round-1): fingerprints already exist in memory.**
  Round-1 M-3 verified against reviewer_json.py:153-181: fingerprints
  are computed at finding-parse time (sha256 of file:line:desc, first
  16 hex) and ride the in-memory StateFinding. The CI gap is simply
  that _run_ci never calls the ledger writer. Fix = invoke the writer
  (or a CI variant) at CI terminal with in-memory findings; NO
  fingerprint recomputation, NO STATE-09 weakening.
- **D-13 (UPGRADED to HIGH round-1): diff polarity rule.** Round-1 H-3:
  the row's base/head must reference the diff IN WHICH THE FINDING WAS
  LIVE. CI-derived rows satisfy this automatically (single-round, the
  reviewed diff contains the flagged code). LOCAL-mode FIXED rows are
  written at terminal time and their SHAs may reference the POST-fix
  diff -- replay would then expect a catch on already-fixed code
  (inverted semantics). Write path must record SHAs at
  finding-confirmation time; if LOCAL terminal-time writing cannot
  provide them, that is a write-path defect fixed in this phase.
  Expected-answers derivation: FIXED/ESCAPED -> expect catch at the
  row's file/line/claim; DISPROVED/DUPLICATE -> expect no-catch.
- **D-14 (LOW): axis_claim free text vs axis enum.** Extractor needs a
  mapping rule (exact-match table + fallback -- planner decides:
  skip-with-warning or default axis).

### Round-1 additions (kanban t_6e58fa74, adjudicated 2026-08-22)

- **D-15 (from H-1): export-eval consumes terminal-state rows ONLY.**
  UNADJUDICATED rows are skipped with a count + a hint to run
  `ledger adjudicate`. The export summary always reports: entries
  emitted / stale-SHA skipped / unadjudicated skipped / dedup-collapsed.
- **D-16 (from M-1 + H-4): growth expectations documented, compaction
  deferred.** One row per finding per diff is inherent to an
  append-only ledger; D-08 dedup caps re-run inflation. The O(N)
  full-file dedup scan in _write_ledger_rows is accepted at current
  scale; trigger for an index/rotation follow-up: >10k rows or
  measured write latency regression.
- **D-17 (from M-5): corpus entries are toolchain-self-contained.**
  Replaying a foreign diff must not require the foreign project's
  toolchain. Extractor emits a per-entry minimal config stub
  (review-only: no test.command, or a no-op) unless the source repo's
  own .code-forge config is available and explicitly opted in.
- **D-18 (scope split, from H-5): two plans.** 44-01 write path:
  D-01, D-05, D-06, D-07, D-08, D-10, D-11, D-12, D-13(write side),
  D-16. 44-02 export-eval: D-02, D-03, D-04, D-09, D-13(expectations),
  D-14, D-15, D-17.

</decisions>

<code_context>
## Existing assets (re-verified on main @ 8bd01bc, code-graph 2026-08-21)

- `src/code_forge/ledger.py` (122 lines): LedgerRow schema v1 with
  base_sha/head_sha (docstring explicitly anticipates Phase 44
  re-extraction), TerminalState enum (FIXED/DISPROVED/DUPLICATE/
  ESCAPED), append_row (single-write O_APPEND atomicity), iter_rows
  (tolerant: skips rows failing validation per c5d420d).
- `src/code_forge/eval/` (1572 lines): corpus.py (CorpusEntry,
  ExpectedFinding, load_corpus, valid_line_range), runner.py
  (replay_entry -- isolated temp repo per run, applies diff, invokes
  code-forge review), scorer.py (finding_hit, score_findings,
  Kuhn matching, EvalSummary).
- `ledger mark --new` accepts --file/--line/--axis-claim (7b6101a);
  SHAs validated 40-hex pair-or-none (c5d420d); dedup best-effort
  (2495035); real-path SHA acceptance test (fab6d63). Re-ruling an
  existing fingerprint DROPS location/SHA metadata (cli.py:1643-1654)
  -- the gap D-10/D-18 close.
- Fingerprint recipe: sha256("file:line:description")[:16], computed
  at parse time in reviewer_json.py:153-181 (shared by Outlet A and C
  to keep both paths identical).
- `.planning/eval-bank/v1/`: 11 hand-curated entries, manifest.yaml
  with diff_state: pre-fix + per-entry answers.
- MCP path hardcodes mode=Mode.CI (mcp_server.py:929); _run_ci has
  zero ledger references (re-verified 2026-08-21); _write_ledger_rows
  call sites all live in _finalize_local_terminal (LOCAL-only).
- Empty-ledger diagnosis (2026-07-30, 8 evidence files):
  .planning/reports/ledger-empty-diagnosis-20260730.md -- H1..H5,
  re-verified against current main before this phase.
- Existing tests: tests/test_ledger.py, test_cli_ledger.py,
  test_claim_type.py.

## Patterns to follow

- Append-only discipline: upgrades append new rows, never rewrite.
- Atomic single-write rows under PIPE_BUF (D-07 enforced with a size
  test).
- CLI subcommand pattern: ledger mark/list precedent in cli.py:780+;
  eval precedent at cli.py:758+.
- Real-path test pattern: fab6d63 (real git history end-to-end).

</code_context>

<canonical_refs>
- REQUIREMENTS.md EVAL-02 (Phase 44 section)
- ROADMAP.md v2.9 section, Phase 44 entry + dependency graph
- .planning/reports/ledger-empty-diagnosis-20260730.md (+ evidence dir)
- .planning/eval-bank/v1/manifest.yaml (target corpus format)
- src/code_forge/ledger.py, src/code_forge/eval/{corpus,runner,scorer}.py
- Round-1 review: kanban t_6e58fa74 (archived; SCORECARD B=2 H=5 M=4
  L=2, full text in kanban log)
- Prior-phase review discipline: cp-artifacts/ pattern from Phase 54
</canonical_refs>

<deferred>
## Deferred ideas (out of scope)

- DISPROVED-entry findings-level expected-answers semantics (start
  verdict-level; refine when corpus has real DISPROVED rows).
- UNADJUDICATED counts in doctor output.
- Cross-repo ledger aggregation (one repo_root per export run).
- Ledger compaction/index (trigger: >10k rows or measured write
  latency regression -- D-16).
- Phase 51 BASIS-DISCLOSE -- separate phase, prereq only Phase 43
  (merged); may proceed in parallel.
</deferred>
