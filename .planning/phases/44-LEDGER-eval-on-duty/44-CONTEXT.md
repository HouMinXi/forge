# Phase 44: EVAL-ON-DUTY - Context

**Gathered:** 2026-08-21
**Status:** Round-2 review adjudicated 2026-08-22 (reviewer t_2cc0f297
B=1 H=1 M=3 L=0; devops t_a5c16f5a B=0 H=4 M=3 L=1; scribe fact-check
done by architect, all R2 citations PASS). All findings resolved --
D-13 corrected to VERIFIED/no-change, D-15/D-17/D-18 extended, D-19..D-22
added. 22 decisions total. Ready for /gsd-plan-phase 44 (two plans per
D-18: 44-01 write path, 44-02 export-eval).

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
- **D-13 (VERIFIED round-2 -- polarity already correct, no change
  needed).** Round-1 H-3 feared LOCAL-mode FIXED rows might reference
  post-fix SHAs. Round-2 verification at machine.py:1316-1317 +
  machine.py:201,218: `resolved_review` base/head are snapshotted at
  RUN START (the diff under review), so LOCAL FIXED rows already
  record PRE-fix SHAs -- the polarity the extractor needs. D-13's
  original "SHAs at confirmation time" demand is satisfied by existing
  architecture; no write-path change required. Expected-answers
  derivation: FIXED/ESCAPED -> expect catch at the row's
  file/line/claim; DISPROVED/DUPLICATE -> expect no-catch.
- **D-14 (LOW): axis_claim free text vs axis enum.** Extractor needs a
  mapping rule (exact-match table + fallback -- planner decides:
  skip-with-warning or default axis).

### Round-1 additions (kanban t_6e58fa74, adjudicated 2026-08-22)

- **D-15 (from H-1, EXTENDED round-2): export-eval consumes
  terminal-state rows ONLY; summary counters are MUTUALLY EXCLUSIVE.**
  UNADJUDICATED rows are skipped with a count + a hint to run
  `ledger adjudicate`. Round-2 B-1: a single row can be both
  stale-SHA AND unadjudicated -- sequential counters would
  double-count. Each skipped row is attributed to exactly ONE reason
  by a documented precedence (unadjudicated first, then stale-SHA,
  then dedup-collapse). The export summary always reports: entries
  emitted / unadjudicated skipped / stale-SHA skipped /
  dedup-collapsed, summing to total rows read.
- **D-16 (from M-1 + H-4): growth expectations documented, compaction
  deferred.** One row per finding per diff is inherent to an
  append-only ledger; D-08 dedup caps re-run inflation. The O(N)
  full-file dedup scan is accepted at current scale (round-2 estimate:
  ~20 CI runs/day -> ~3600 rows/repo/6mo, millisecond-range parse);
  trigger for an index/rotation follow-up: >10k rows or measured write
  latency regression.
- **D-17 (from M-5, EXTENDED round-2): corpus entries are
  toolchain-self-contained AND merge with the runner's own gate.yaml
  generation.** Replaying a foreign diff must not require the foreign
  project's toolchain. eval/runner.py:757-768 (_create_gate_yaml)
  already generates a gate.yaml from backend_name/backend_config --
  the D-17 minimal stub must MERGE into or defer to that generation
  (review-only: no test.command / no-op test), never overwrite or
  collide with it. Planner must specify the merge/precedence rule.
- **D-18 (scope split, from H-5, EXTENDED round-2): two plans, with an
  explicit coupling rule.** 44-01 write path: D-01, D-05, D-06, D-07,
  D-08, D-10, D-11, D-12, D-16, D-19, D-20, D-21. 44-02 export-eval:
  D-02, D-03, D-04, D-09, D-13(expectations), D-14, D-15, D-17, D-22.
  Coupling rule (round-2 M-5): 44-02's tests must consume rows
  PRODUCED by 44-01's real write path (not hand-mocked ledger lines),
  so a 44-01 regression surfaces in 44-02's suite.

### Round-2 additions (reviewer t_2cc0f297 + devops t_a5c16f5a,
###   adjudicated 2026-08-22; scribe t_8c0b9154 fact-check done by
###   architect -- all R2 citations verified against real code/commits)

- **D-19 (devops DO-01+DO-08, HIGH): CI write path is failure-isolated
  and has a two-layer kill-switch.** `_write_ledger_rows` invoked from
  the CI terminal path must be wrapped in try/except OSError: a ledger
  write failure degrades to a stderr warning and NEVER fails the
  review verdict. Plus a two-layer disable: env var
  `CODE_FORGE_DISABLE_LEDGER=1` (CI-platform global kill) and
  gate.yaml `ledger: { enabled: false }` (repo-level). Neither exists
  today (verified: no ledger gate in gate_check.py:39-100).
- **D-20 (devops DO-04+DO-06, HIGH): adjudication discoverability +
  unambiguous persistence path.** (a) `ledger list` gains an
  `--unadjudicated` filter so operators can find pending rows;
  `ledger adjudicate` echoes the inherited metadata after writing.
  (b) H2 persistence resolves via
  `git rev-parse --path-format=absolute --git-common-dir`, normalized
  to the MAIN repo root (common-dir's parent), NOT inside .git; on any
  failure fall back to cwd-local (D-11).
- **D-21 (devops DO-02, MEDIUM): truncated evidence stays
  machine-parseable.** When D-07 truncates evidence, the truncation
  marker is explicit (`... [truncated]`) so a truncated row can never
  be mistaken for a complete one downstream.
- **D-22 (devops DO-07, MEDIUM): export-eval output hygiene.** Default
  `--out` is a dedicated dir (e.g. `.code-forge/eval-export`), and
  re-export semantics are explicit: only manifest-managed files are
  cleaned/overwritten, foreign files in the dir are left untouched,
  and a non-empty pre-existing dir requires `--force`.

### Scope extension (review pain-points work order, user-ratified
###   2026-08-22: all 5 suggestions merged into Phase 44; root cause =
###   STATE-09 CI zero-memory, same root as H1)

Pain-point evidence: 12 rounds / 39 receipts of MCP carve-out review
showed 52% repeat findings, 19% useful density -- CI reviews re-report
known/already-ruled findings because each CI run starts with zero memory
(STATE-09, machine.py:288-300, the author's own comment). Phase 44 fixes
the WRITE side; these decisions add the READ side (CI consumes the ledger
to converge). Full analysis: 44-SCOPE-EXTENSION-ANALYSIS.md.

- **D-23 (S1, finding suppression): CI reads the ledger and suppresses
  KNOWN fingerprints before the verdict count.** "Known" = a fingerprint
  with a FIXED/DUPLICATE ledger row (a real prior terminal state), read
  via resolve_ledger_root. Inserted before `_count(Disposition.CONFIRMED)`
  at machine.py:~528. CONSERVATIVE rule: only suppress fingerprints with a
  real terminal-state ledger row; an unrecognized fingerprint is NEVER
  suppressed (no false-green from over-matching).
- **D-24 (S2, human rebuttal as a suppressing signal): a human rebuttal
  rides the SAME adjudicate path (D-10) as a terminal-state row -- a
  rebutted finding becomes a DISPROVED/DUPLICATE row via
  `ledger adjudicate`, which D-23 then suppresses.** No separate
  rebuttals.json; the ledger IS the rebuttal registry (single source,
  no duplicate store -- golden rule). Suppression follows the row.
- **D-25 (S3, convergence): CI converges when the post-suppression
  new-CONFIRMED count is zero.** Because suppression is conservative
  (D-23), zero post-suppression CONFIRMED means every current finding
  carries a real terminal-state row. The verdict logic at machine.py:528
  counts post-suppression findings. A finding whose wording drifted (new
  fingerprint) is NOT suppressed and DOES block -- wording-drift
  re-finds are surfaced, not silenced (the R5/R7 rewording case is a
  reviewer-behavior issue, not a license to suppress by fuzzy topic
  match; fuzzy matching is a false-green risk and is REJECTED).
- **D-26 (S4, pinned paths): gate.yaml gains `pinned_paths: []` --
  findings whose file matches a pinned path are suppressed (owner has
  ruled the path out of scope).** Follows the coverage_exempt_patterns
  precedent (machine.py:232). Read via the same gate config load as the
  D-19 kill-switch. Pinned-path suppression is explicit and logged
  (infra_errors note), never silent.
- **D-27 (S5, style findings downgrade): findings classified as
  style/test-assertion/naming/idiomatic are emitted as AdvisoryFinding
  (never block, advisory.py:5-8) instead of CONFIRMED StateFinding.**
  The classification rule (which axes/keywords downgrade) is defined in
  the 44-03 plan; the mechanism (AdvisoryFinding) already exists.

</decisions>

<code_context>
## Existing assets (re-verified on main @ 8bd01bc, code-graph 2026-08-21)

- `src/code_forge/ledger.py` (122 lines): LedgerRow schema v1 with
  base_sha/head_sha (docstring explicitly anticipates Phase 44
  re-extraction), TerminalState enum (FIXED/DISPROVED/DUPLICATE/
  ESCAPED), append_row (single-write O_APPEND atomicity), iter_rows
  (tolerant: skips rows failing validation per c5d420d).
- `src/code_forge/eval/` (1450 lines): corpus.py (CorpusEntry,
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
