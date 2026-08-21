# Phase 44: EVAL-ON-DUTY - Context

**Gathered:** 2026-08-21
**Status:** Ready for planning

<domain>
## Phase Boundary

EVAL-02: eval case generation re-extracts diffs from the LEDGER so the
corpus grows from real reviewed work instead of hand curation. This
requires (a) the ledger to actually receive rows from the way forge is
really invoked (CI mode -- today it writes zero), and (b) an extractor
that turns adjudicated ledger rows into eval-bank-format corpus entries
(manifest + diffs + answers).

Prereq Phase 43 (LEDGER, merged 14328bb). ~300-450 LOC estimate from the
v2.9 schedule; the CI-write + H2 + adjudicate additions push toward the
upper bound. Root of the v2.9 lane -- Phase 51 (BASIS-DISCLOSE) and the
52/53a/53b chain hang off it.

</domain>

<decisions>
## Implementation Decisions

### D-01..D-04 (user-ratified 2026-08-21, with fable constraints)

- **D-01: CI mode writes ledger rows.** CI review terminal states
  (PASS/FAIL/ESCALATED) append UNADJUDICATED rows carrying base/head
  SHA, verdict, CONFIRMED finding fingerprints, diff_sha256. H1 (CI
  never writes) is fixed IN this phase, not deferred -- user decision:
  "那必须要打通CI模式写ledger".
- **D-02: Terminal-state mapping (all three).** ESCAPED -> missed-bug
  entry (false negative), FIXED -> hit entry, DISPROVED ->
  false-positive entry. One extractor, three signal types.
- **D-03: Dead-SHA handling (two layers).** Extractor validates each
  row's base/head via `git -C <row.repo_root> cat-file -e`; unresolvable
  -> skip row + stderr warning + summary stale count + generated entry
  carries `source_status: stale_sha` (when the row is otherwise usable).
  Both layers required: stderr for immediate feedback, manifest field
  for downstream corpus-quality visibility.
- **D-04: CLI shape is `ledger export-eval` subcommand.** Reads
  ledger.jsonl, emits an independent manifest+diffs+answers directory.
  Coexists with hand-curated eval-bank/v1 -- no mixing. Independently
  reviewable, re-runnable, diffable.

### Fable constraints (ratified 2026-08-21 -- binding on planner)

- **D-05: D-01 and H2 are ONE atomic task.** CI-write without
  main-repo persistence is write-and-lose: forge's mandated workflow IS
  worktrees, and .code-forge/ is gitignored, so worktree removal
  destroys the rows. Conversely H2-only is pointless. The plan MUST
  treat them as a single task, not two optional ones.
- **D-06: UNADJUDICATED naming honesty.** The enum is named
  TerminalState and its docstring documents entry constraints for the
  four terminal states. Adding a non-terminal member without touching
  either is a docstring-code mismatch (the exact class 14328bb fixed).
  Either rename the enum (LedgerState -- breaking, honest) or rewrite
  the docstring contract ("four terminal + one pending-adjudication"
  with its entry rule). Planner picks; doing nothing is NOT an option.
- **D-07: Row size stays under PIPE_BUF.** The ledger's atomicity
  guarantee (single write(2) under O_APPEND, atomic under Linux
  PIPE_BUF=4096 for row sizes "we emit") breaks if CI rows carry a
  whole run's CONFIRMED fingerprints in one line. Keep the existing
  per-finding row granularity (file/line/axis_claim are already
  per-row); a run-level aggregate row is only emitted for PASS runs
  (zero findings) with axis_claim="clean" (NOT empty string --
  7b6101a fixed "empty file:line says nothing"). PASS rows are the
  false-positive-rate negative samples; they cannot be skipped.
- **D-08: UNADJUDICATED dedup key includes diff identity.** Existing
  dedup key (fingerprint, terminal_state) lets N re-runs of the same
  CI diff append N identical UNADJUDICATED rows (consecutive
  convergence runs are forge's normal operating mode). Dedup key for
  UNADJUDICATED must be (fingerprint, base_sha, head_sha) or
  diff_sha256 -- same diff re-run does not re-append.
- **D-09: Extractor resolves SHAs against row.repo_root, NOT cwd.**
  Ledger rows record the REVIEWED project's base/head; when forge
  reviews other repos the SHAs are foreign to the forge repo. Extractor
  does `git -C <row.repo_root>` for both validation and diff
  materialization. repo_root missing -> D-03 skip path.

### Gray areas surfaced by territory exploration (2026-08-21, code-graph assisted)

- **D-10 (HIGH -- contract gap): adjudication/upgrade path does not
  exist.** `ledger mark` today: --new restricted to DUPLICATE/ESCAPED;
  FIXED/DISPROVED only from real state-machine runs; no action that
  upgrades an UNADJUDICATED row. Phase must add `ledger adjudicate`
  (or equivalent) whose writes are APPEND-ONLY upgrades (new row
  referencing the fingerprint; never rewriting the old row). Without
  it, the CI-write -> human-adjudicate -> extractor chain is dead on
  arrival.
- **D-11 (HIGH -- H2 fallback): `git rev-parse --git-common-dir` is
  unreliable outside a git repo.** CI cwd may be a non-repo path.
  Persistence resolution: try `git rev-parse --git-common-dir` in cwd;
  on failure fall back to writing cwd-local (the H2 scenario only
  matters inside worktrees anyway).
- **D-12 (HIGH -- data source): fingerprint has no CI-mode source.**
  CI does not persist state.json (STATE-09), so findings and their
  fingerprints die with the process. CI-write needs fingerprints
  computed in-process at terminal time (content hash over
  file+line+description, same recipe as the LOCAL path) -- do NOT
  weaken STATE-09 for this. The recipe must match the existing
  fingerprint derivation or adjudication matching breaks.
- **D-13 (MEDIUM -- semantic mismatch): `pre_fix_source` vs ledger
  base/head are different semantics.** v1 manifest's pre_fix_source is
  a hand-picked "bug-introducing ancestor"; ledger base/head is the
  REVIEWED DIFF RANGE. For ledger-derived entries the extractor must
  decide: the reviewed diff IS the eval diff (diff_state: pre-fix when
  the row is ESCAPED -- the bug was present in what was reviewed; for
  FIXED rows the reviewed diff CONTAINS the fix, so diff_state
  semantics invert). Planner must define diff_state derivation per
  terminal state; leaving it undefined corrupts the corpus.
- **D-14 (LOW -- mapping table): axis_claim free text vs axis enum.**
  Ledger axis_claim is free text ("manual" or finding-derived); corpus
  axis is an enum (verify/llm/machine). Extractor needs a mapping rule
  (exact-match table + fallback to... planner decides: skip with
  warning, or a default axis).

</decisions>

<code_context>
## Existing assets (re-verified on main @ 8bd01bc, code-graph 2026-08-21)

- `src/code_forge/ledger.py` (122 lines): LedgerRow schema v1 with
  base_sha/head_sha (docstring explicitly anticipates Phase 44
  re-extraction), TerminalState enum (FIXED/DISPROVED/DUPLICATE/
  ESCAPED), append_row (single-write O_APPEND atomicity), iter_rows,
  PIPE_BUF reasoning.
- `src/code_forge/eval/` (1572 lines): corpus.py (CorpusEntry,
  ExpectedFinding, load_corpus, valid_line_range), runner.py
  (replay_entry -- isolated temp repo per run, applies diff, invokes
  code-forge review), scorer.py (finding_hit, score_findings,
  Kuhn matching, EvalSummary). Findings-level scoring landed Aug 2026.
- `ledger mark --new` already accepts --file/--line/--axis-claim
  (7b6101a); --base-sha/--head-sha validated 40-hex, pair-or-none
  (c5d420d); dedup best-effort (fingerprint, terminal_state)
  (2495035); real-path SHA acceptance test (fab6d63).
- `.planning/eval-bank/v1/`: 11 hand-curated entries, manifest.yaml
  with diff_state: pre-fix + per-entry answers; findings-level
  question bank structure to target.
- MCP path hardcodes mode=Mode.CI (mcp_server.py:929); _run_ci has
  zero ledger references (re-verified 2026-08-21); _write_ledger_rows
  call sites all live in _finalize_local_terminal (LOCAL-only).
- Empty-ledger diagnosis (2026-07-30, 8 evidence files):
  .planning/reports/ledger-empty-diagnosis-20260730.md -- H1 CI-never-
  writes (dominant), H2 worktree-loss, H3 SHA-none narrow, H4
  CI-disposition-unreachable, H5 combination. All re-verified against
  current main before this phase.
- Existing tests: tests/test_ledger.py, test_cli_ledger.py,
  test_claim_type.py.

## Patterns to follow

- Append-only discipline: upgrades append new rows, never rewrite
  (mirrors ledger.py's design contract and 2495035's causal-chain
  rule).
- Atomic single-write rows under PIPE_BUF (D-07 preserves this).
- CLI subcommand pattern: ledger mark/list precedent in cli.py:780+;
  eval precedent at cli.py:758+.
- Real-path test pattern: fab6d63 (real git history end-to-end) over
  mock-only.

</code_context>

<canonical_refs>
- REQUIREMENTS.md EVAL-02 (Phase 44 section)
- ROADMAP.md v2.9 section, Phase 44 entry + dependency graph
- .planning/reports/ledger-empty-diagnosis-20260730.md (+ 8 evidence
  files in .planning/reports/ledger-diagnosis-evidence/)
- .planning/eval-bank/v1/manifest.yaml (target corpus format)
- src/code_forge/ledger.py, src/code_forge/eval/{corpus,runner,scorer}.py
- Prior-phase review discipline: cp-artifacts/ pattern from Phase 54
</canonical_refs>

<deferred>
## Deferred ideas (out of scope)

- DISPROVED-entry expected-answers semantics beyond the basic mapping
  (how to encode "review should NOT have reported this" at
  findings level) -- start with verdict-level scoring, findings-level
  refinement deferred until corpus has real DISPROVED rows.
- Streaming/export of UNADJUDICATED counts into doctor output
  (visibility nice-to-have, not blocking).
- Cross-repo ledger aggregation (multiple reviewed projects into one
  corpus) -- extractor takes one repo_root at a time for now.
- Phase 51 BASIS-DISCLOSE (falsification_survived +
  convergence_rounds surfacing) -- separate phase, prereq only Phase
  43 which is merged; may proceed in parallel.
</deferred>
