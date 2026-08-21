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

## Deferred ideas captured

- DISPROVED findings-level expected-answers semantics (start
  verdict-level)
- UNADJUDICATED counts in doctor output
- Cross-repo ledger aggregation
- Phase 51 may proceed in parallel (prereq only Phase 43, merged)
