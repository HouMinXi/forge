# Discussion Log - Phase 44: EVAL-ON-DUTY

**Date:** 2026-08-21
**Method:** adaptive questioning + fable (external reviewer) constraints + territory exploration

## Area 1: H1 -- CI mode never writes ledger

**Finding:** ledger-empty-diagnosis-20260730.md (8 evidence files) proved
CI mode (MCP sampling path + all non-interactive subprocesses = forge's
actual primary usage) never calls _write_ledger_rows. Re-verified on
main @ 8bd01bc: _run_ci has zero ledger refs; all call sites live in
_finalize_local_terminal (LOCAL-only); mcp_server.py:929 hardcodes
mode=Mode.CI.

**Question:** Fix H1 in this phase, or defer?
**Options presented:** (a) consume-only + manual entry, defer H1;
(b) fix H1 in-phase (CI writes rows); (c) minimal hook only.
**User selection:** "那必须要打通CI模式写ledger" -- H1 must be fixed
in-phase. (b).
**Fable constraint:** D-05 -- H1 fix and H2 (worktree persistence) are
one atomic task; either alone is ineffective.

## Area 2: Terminal-state -> eval-entry mapping

**Question:** Which terminal states map to eval entries?
**Options:** all three / ESCAPED only / ESCAPED+FIXED.
**User selection:** "按照推荐" -- all three (ESCAPED->missed-bug,
FIXED->hit, DISPROVED->false-positive).

## Area 3: Dead-SHA handling

**Question:** Rows whose base/head SHAs died in the 07-10 filter-branch
rewrite (13/19 sampled ROADMAP SHAs dead): skip? quarantine? manifest
field?
**User selection:** "这个要实现" -- must be implemented (not just
documented).
**Recommended shape ratified (D-03):** skip + stderr warning + stale
count + source_status field in generated manifest. Two layers.

## Area 4: Extractor CLI shape

**Question:** ledger export-eval subcommand / eval --from-ledger inline /
append into v1 manifest?
**User selection:** "你觉得应该是什么?" -- asked for recommendation.
**Recommendation ratified (D-04):** `ledger export-eval` subcommand
emitting an independent manifest+diffs+answers directory; coexists with
hand-curated v1; independently reviewable/re-runnable.

## Area 5: Fable external review (user: "召唤fable，肥波你怎么看")

Fable ratified all four recommendations and added five binding
constraints (D-05..D-09 above in CONTEXT.md):
1. D-01+H2 atomic pairing (worktree loss makes CI-write pointless)
2. TerminalState enum naming honesty (rename or rewrite docstring)
3. Row size under PIPE_BUF (per-finding granularity; PASS rows with
   axis_claim="clean")
4. UNADJUDICATED dedup key includes diff identity
5. Extractor resolves against row.repo_root, not cwd

## Area 6: Territory exploration (user: "肥波请你按照舰队法和疆域拓展法则，再看看还有什么灰区和未知么")

Code-graph updated to 8bd01bc (5938 nodes) first. Exploration surfaced
five additional gray areas, all recorded as D-10..D-14:
- G-1/D-10 (HIGH): adjudication/upgrade path absent from CLI --
  contract gap, chain dead on arrival without it
- G-2/D-11 (HIGH): --git-common-dir unreliable outside git repo;
  needs cwd-local fallback
- G-3/D-12 (HIGH): fingerprint has no CI-mode source (STATE-09 keeps
  state ephemeral); compute in-process at terminal time with the same
  recipe, do NOT weaken STATE-09
- G-4/D-13 (MEDIUM): pre_fix_source vs base/head semantics differ;
  diff_state derivation per terminal state must be defined (FIXED
  rows invert it -- the reviewed diff CONTAINS the fix)
- G-5/D-14 (LOW): axis_claim free text vs axis enum mapping rule

## Review Round 1 (kanban t_6e58fa74, 2026-08-22, reviewer/gemini3.6)

SCORECARD B=2 H=5 M=4 L=2. Architect adjudication against real code:

- B-1 (D-03 self-contradiction: skip vs manifest-entry): CONFIRMED.
  Resolved in D-03 revision -- skipped rows are listed in the export
  summary report; no manifest entry for unmaterializable diffs.
- B-2 (D-10 metadata loss on re-mark): CONFIRMED at cli.py:1643-1654
  and extended -- re-marking today can write file=""/line=0/
  axis_claim="manual". Resolved via D-10 inheritance requirement.
- H-1 (UNADJUDICATED behavior in export undefined): CONFIRMED -> D-15.
- H-2 (uncapped evidence breaks PIPE_BUF guard): CONFIRMED -> D-07
  extension (<=500 chars + serialized-size test).
- H-3 (FIXED/ESCAPED diff polarity inversion): CONFIRMED -- LOCAL
  terminal-time SHAs may reference post-fix diffs. D-13 upgraded to
  HIGH with the "SHAs at confirmation time" write-path rule.
- M-1 (UNADJUDICATED flooding): accepted-inherent, documented in D-16.
- M-2 (repo_root portability): CONFIRMED -> D-09 --repo-root override.
- M-3 (D-12 framing wrong): CONFIRMED at reviewer_json.py:153-181 --
  fingerprints already in memory. D-12 corrected.
- M-4 (dedup race): accepted best-effort per 2495035; extractor dedups
  on read (D-08 extension).
- M-5 (replay toolchain isolation): CONFIRMED -> D-17.
- L-1 (old readers reject UNADJUDICATED): refuted-as-crash, confirmed
  as silent-skip via iter_rows tolerance -- acceptable, documented D-06.
- L-2 (absolute path PII in manifests): CONFIRMED -> D-09 basename
  rewrite.
- H-5 (scope ~750+ LOC, split): accepted -> D-18 two-plan split.

## Review Round 2 (reviewer t_2cc0f297 + devops t_a5c16f5a, 2026-08-22)

reviewer SCORECARD B=1 H=1 M=3 L=0; devops SCORECARD B=0 H=4 M=3 L=1.
Part A: all 14 round-1 adjudications verified RESOLVED. Part B + devops
axes adjudicated by architect against real code:

- reviewer B (D-15 double-count): CONFIRMED -> D-15 extended with
  mutually-exclusive counter precedence.
- reviewer H (D-13 polarity assertion wrong): CONFIRMED --
  resolved_review SHAs snapshot at run start (machine.py:201,218,
  1316-1317), so LOCAL FIXED rows already record PRE-fix SHAs. D-13
  rewritten to VERIFIED/no-change.
- reviewer M (D-16 O(N) cost): existing D-16 wording sufficient,
  quantified ~3600 rows/6mo -> ms-range parse. No change.
- reviewer M (D-17 gate.yaml collision): CONFIRMED ->
  eval/runner.py:757-768 generates gate.yaml; D-17 extended with merge
  rule.
- reviewer M (D-18 coupling): CONFIRMED -> D-18 extended: 44-02 tests
  consume real 44-01-produced rows.
- devops DO-01 (CI write no OSError isolation) + DO-08 (no
  kill-switch): CONFIRMED -> D-19.
- devops DO-04 (no --unadjudicated filter) + DO-06 (--git-common-dir
  ambiguity): CONFIRMED -> D-20.
- devops DO-02 (truncated-row pollution): -> D-21.
- devops DO-07 (re-export overwrite semantics): -> D-22.
- devops DO-03 (network-fs atomicity note) + DO-05 (adjudicate context
  echo): folded into D-20/D-21 narrative; DO-03 accepted as doc note.
- scribe t_8c0b9154 fact-check: scribe exhausted its iteration budget
  (known 30/30 ceiling); architect performed the incremental fact-check
  directly -- every R2 citation (reviewer_json.py:153-181,
  cli.py:1643-1654, machine.py:1316-1317, mcp_server.py:929, commits
  14328bb/7b6101a/c5d420d/2495035/fab6d63) verified against real code
  and git history. All PASS.

## Deferred ideas captured

- DISPROVED findings-level expected-answers semantics (start
  verdict-level)
- UNADJUDICATED counts in doctor output
- Cross-repo ledger aggregation
- Phase 51 may proceed in parallel (prereq only Phase 43, merged)
