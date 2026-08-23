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

## CP1 Plan-Check Round 1 (deleg_2bd6fbc3, 2026-08-22, FORCE stance)

VERDICT: FAIL, SCORECARD B=3 W=7. Architect verified each finding against
real code, then revised both plans. All three blockers CONFIRMED:

- B-1 (D-17 no implementable mechanism): CONFIRMED. The plan said a
  "review-only stub merges with _create_gate_yaml" but never named the
  transport (CorpusEntry/load_corpus/replay_entry have no gate-config
  channel), AND missed the real hazard -- a foreign diff carrying its own
  gate.yaml with test.command gets merged (existing keys win,
  runner.py:537-556) and EXECUTED at replay. FIXED in 44-02 Task 2: D-17
  is delivered by STRIPPING the foreign gate.yaml from the materialized
  diff before replay, not by a carried stub. Hostile-test.command fixture
  test added (marker file must be absent).
- B-2 (expected_verdict never specified + format conflict): CONFIRMED.
  load_corpus raises ValueError when expected_verdict is missing/not
  HOLD/PASS (corpus.py:106-111), and eval-bank v1 (no expected_verdict)
  was wrongly cited as "target format". FIXED: Task 1 derives
  expected_verdict (catch->HOLD, no-catch->PASS); Task 2 emits the
  load_corpus shape with eval-bank-compat fields as ignored extras
  (load_corpus reads only known keys, corpus.py:108-128).
- B-3 (read-side path resolution not wired): CONFIRMED and would ship
  broken silently (worktree reviews are mandated; adjudicate/list/export
  from a worktree would read an empty worktree-local ledger while
  temp-dir tests pass). FIXED: shared resolve_ledger_root added to
  44-01 Task 1 (in ledger.py, importable by machine/cli/export); 44-01
  Task 2/3 and 44-02 Task 3 all route reads through it; worktree
  read-side proof tests added to all three.

Warnings adjudicated and folded in:
- W (run_tests.sh does not exist in forge): CONFIRMED (it's hermes-agent's;
  forge uses python -m pytest). All 8 verify/verification commands fixed.
- W (44-02 interfaces axis "enum" wrong): CONFIRMED (axis_tags is
  list[str] free-text, corpus.py:76). Corrected in interfaces + Task 2.
- W (D-07 mark --evidence uncapped, cli.py:1647): CONFIRMED -> 44-01 Task
  3 applies _truncate_evidence to the mark path too.
- W (clean-row fingerprint unspecified): CONFIRMED (iter_rows KeyError-
  skips rows lacking fingerprint, ledger.py:103) -> 44-01 Task 2 gives the
  clean row a diff-scoped fingerprint sha256("clean:<base>:<head>")[:16].
- W (D-19 kill-switch read failure modes): CONFIRMED (load_gate_config
  raises FileNotFoundError/ValueError) -> config read moved INSIDE the
  same try/except as the write in 44-01 Task 2.
- W (repo_root field = disposable worktree): CONFIRMED -> 44-01 Task 2
  sets repo_root = resolve_ledger_root(cwd) (main root), not cwd.
- W (D-16 name-dropped): -> landing site = _write_ci_ledger_rows
  docstring in 44-01 Task 2.
- W (44-02 Task 3 test-file load): noted; monitor at execution.

## CP1 Plan-Check Round 2 (deleg_d92059a6, 2026-08-22)

VERDICT: PASS, SCORECARD B=0 W=3. All three round-1 blockers RESOLVED
(B-1 strip-before-replay implementable; B-2 expected_verdict derived +
load_corpus-shape emission verified against corpus.py:106-128; B-3 shared
resolve_ledger_root wired into all three consumers with worktree proof
tests). No new blockers.

3 round-2 warnings adjudicated:
- W1 (D-17 strip = outcome not algorithm): FIXED -- 44-02 Task 2 now names
  emission-side patch filtering (drop the .code-forge/gate.yaml file
  section from the emitted patch text, from its `diff --git` header to the
  next header/EOF).
- W2 (strip forbids gate-config-is-the-diff class silently): FIXED --
  documented-tradeoff note added to 44-02 Task 2 (export.py docstring).
- W3 (env-var kill-switch placement cosmetic): skipped -- checker itself
  marked it harmless; acceptance grep pins the load_gate_config call site.

CP1 CLOSED. Proceeding to CP1b external panel (forge rule: aicc
kimi/gemini/deepseek, gemini via manual relay; after 0/0/0/0 the user does
a final human review before execution).

## Scope extension (pain-points work order) + 44-03 (2026-08-22)

User merged the review pain-points work order (12 rounds/39 receipts, 52%
repeat findings, STATE-09 CI zero-memory) into Phase 44. Added D-23..D-27
(read-side convergence) and wrote 44-03-PLAN.md (2 tasks, depends_on
44-01). Architect adjudication during planning:
- D-25 deliberately REJECTS fuzzy/topic matching (false-green risk); only
  exact-fingerprint suppression of terminal-ruled findings. Wording-drift
  and line-drift re-finds still block -- documented in the objective.

CP1 incremental check of 44-03 (deleg_0510b5f8): VERDICT PASS, B=0 W=4.
False-green rule sound + bug-injection-pinned; coupling to 44-01 honored.
4 warnings adjudicated:
- W-1 (suppression mechanism two options): FIXED -- pinned to disposition
  change (KEEP finding, set DISMISSED, NOT removal) + post-run disposition
  assertion, so active_findings/MCP extractor/state.json stay consistent.
- W-2 (pinned_paths wiring + glob semantics + wrong-config-file precedent):
  FIXED -- pinned_paths + style_downgrade wired into BOTH StateMachine
  constructors (cli.py:3303 + mcp_server.py:928); match via
  pathlib.PurePath.match relative to repo root; interfaces corrected
  (coverage_exempt_patterns lives in coverage.yaml, pinned_paths in
  gate.yaml, same flow).
- W-3 (objective oversells 52%): FIXED -- objective now scopes the fix to
  verbatim re-finds; reworded/line-drifted re-finds documented as
  out-of-scope reviewer-behavior problems.
- W-4 (style classifier = executor judgment, untestable): FIXED -- D-27
  now table-driven from gate.yaml style_downgrade (pass_names +
  desc_keywords); L1 findings carry no style tag (reviewer_json.py:161-179),
  so the table is the only source and is identical across executors.

## CP1b external panel -- kimi leg (t_ef4fa893, 2026-08-22)

kimi SCORECARD B=3 H=3 M=7 L=3. The three blockers were all CONFIRMED
against real code by the architect (CP1 had missed all three):

- B-1 (D-27 style downgrade ejects findings from the data model):
  CONFIRMED -- AdvisoryFinding structurally excludes fingerprint
  (advisory.py:33-37), so rerouting style findings to advisories makes them
  unwritable/unadjudicable/unexportable/uncountable. FIXED: D-27 + 44-03
  Task 2 revised -- style findings STAY StateFindings with their
  fingerprint and get a non-blocking disposition, never rerouted.
- B-2 (kill-switch/pinned_paths read via load_gate_config which refuses
  review-mode gate.yaml): CONFIRMED -- load_gate_config raises ValueError
  without a test: section (gate_check.py:64-71); review-only mode is
  exactly that case (outlet_resolver.py:132 avoids it). FIXED: D-19 + 44-01
  Task 2 + 44-03 Task 2 all switched to a tolerant raw-YAML read
  (yaml.safe_load + dict.get).
- B-3 (mutation-survivor terminal at machine.py:360 returns before the
  :541 ledger write): CONFIRMED -- the "tests are weak" FAIL never reached
  the ledger. FIXED: 44-01 Task 2 funnels EVERY CI terminal exit (both
  :541 and :360) through the single write call.

(deepseek t_9ea4ec60 and gemini t_1ba32600 legs crashed "pid not alive" x2
-- to be re-dispatched after the blocker fixes land.)

## CP1b external panel -- deepseek + gemini legs (2026-08-22)

Both legs recovered and completed (reclaimed after the crash). deepseek
SCORECARD B=1 H=2 M=3 L=3; gemini SCORECARD B=2 H=4 M=3 L=2. Architect
adjudication against real code:

- gemini B-1 (pinned_paths does not suppress COVERAGE findings): CONFIRMED
  -- _count_coverage_gaps (machine.py:1635-1643) counts source=="COVERAGE"
  findings with disposition != DISMISSED separately, so a pinned COVERAGE
  finding keeps coverage_gaps > 0 -> FAIL even when pinned. FIXED in 44-03
  Task 2: suppression pass also sets pinned-path COVERAGE findings to
  DISMISSED (test b2 + acceptance).
- gemini B-2 (ledger mark --new ESCAPED yields base==head -> empty diff ->
  permanent MISSED false-green): CONFIRMED -- cli.py:1620-1622 defaults
  base_sha=head_sha=_git_head(cwd) when both omitted. FIXED in 44-02 Task 1:
  extractor skips base_sha==head_sha / empty diffs under a dedicated
  empty-diff counter (test f).
- deepseek H-1 (DUPLICATE mapped to expect-no-catch penalizes finding a
  real bug): CONFIRMED -- a DUPLICATE means the bug WAS real. FIXED: D-02 +
  D-13 + 44-02 Task 1 revised -- DUPLICATE rows are EXCLUDED from export
  under their own counter, not emitted as expect-no-catch.
- deepseek B (D-27 naive substring match brittle): covered by the earlier
  D-27 table-driven revision (match on pass_name prefix, not description
  substring); noted in 44-03 Task 2.
- deepseek H (_truncate_evidence may split multibyte UTF-8): valid -- noted
  for 44-01 Task 1 implementation (truncate on a char boundary, then
  validate the row still json-round-trips; the size-guard test already
  asserts <2048 bytes).

CP1b all three legs adjudicated. Plan set is now at: 44-01 (write),
44-03 (read-side convergence), 44-02 (export), 27 decisions D-01..D-27
with revisions. Pending: re-run CP1 on the revised 44-01/44-02/44-03 to
confirm convergence to 0 blockers, then user final human review.

## CP1 convergence check (deleg_905c7ae1, 2026-08-22)

VERDICT: PASS, SCORECARD B=0 W=5. All 6 CP1b findings RESOLVED with live
evidence. 5 warnings (prose/contract staleness), all adjudicated + fixed:
- W-1 (44-03 frontmatter/key_links still described the rejected
  AdvisoryFinding rerouting): FIXED -- frontmatter truths/artifacts/
  key_links rewritten to the keep-StateFinding design; advisory.py dropped
  from files_modified (no change needed); interfaces advisory.py note
  updated.
- W-2 (44-02 counter lists not extended for the 2 new skip counters):
  FIXED -- precedence chain extended to "unadjudicated > stale-sha >
  duplicate-excluded > empty-diff > dedup-collapse" in must_haves, Task 1
  test (d), Task 3 test (a) summary.
- W-3 (stale DUPLICATE->no-catch strings at 44-02:21,:174): FIXED -- both
  rewritten to DUPLICATE-excluded.
- W-4 (false active_findings visibility claim): FIXED -- 44-03 Task 1 prose
  corrected: DISMISSED findings are filtered OUT of active_findings
  (machine.py:240-243); the audit trail is in state.findings/state.json/
  infra_errors, not active_findings.
- W-5 (cross-plan write-scope contradiction for style findings): FIXED --
  44-01 Task 2 now specifies the CI write scope: CONFIRMED + style-downgraded
  findings get UNADJUDICATED rows; suppressed-DISMISSED findings are NOT
  re-written; the shared row-build takes the per-finding terminal-state
  decision as an INPUT (never the LOCAL DISMISSED->DISPROVED mapping,
  machine.py:1330-1334).

CP1 fully converged: 44-01/44-02 PASS (round 2), 44-03 PASS (incremental),
all CP1b findings resolved, convergence check PASS B=0. Ready for the
user's final human review, then execution (44-01 -> 44-03 -> 44-02).

## Final multi-role convergence review (2026-08-22, user-requested)

Four role-perspective reviews (kanban): reviewer/logic t_54fd29f4,
devops/ops t_20d612c8, scribe/facts t_a61a65f8, coder/exec t_8b59a4e6.
ALL FOUR returned NO OBJECTION with 0 blockers, and each independently
confirmed all 6 CP1b blockers CONFIRMED-FIXED against live code.

Residual M/L findings adjudicated:
- reviewer M-1 (kill-switch except OSError misses yaml.YAMLError from a
  malformed gate.yaml -- an uncaught exception is a crash = verdict change,
  violating D-19): CONFIRMED (yaml.YAMLError is not an OSError subclass;
  safe_load on empty file returns None -> .get AttributeError). FIXED in
  44-01 Task 2: except tuple broadened to OSError + yaml.YAMLError +
  AttributeError/TypeError, isinstance(data, dict) guard, fail-open on read
  failure; malformed + empty gate.yaml bug-injection tests added.
- coder M-1 (resolve_ledger_root referenced before 44-01 lands):
  informational only -- depends_on: [44-01] declared in frontmatter; no
  change needed.
- scribe L-1 (44-03 Task 2 <files> still listed advisory.py): FIXED --
  removed advisory.py from files + Task 2 name corrected to "non-blocking
  disposition".

CONSENSUS REACHED across all four roles. Plan set final: 44-01 (write),
44-03 (read-side convergence), 44-02 (export); 27 decisions D-01..D-27.
Next: user final human review, then execution.

## Deferred ideas captured

- DISPROVED findings-level expected-answers semantics (start
  verdict-level)
- UNADJUDICATED counts in doctor output
- Cross-repo ledger aggregation
- Phase 51 may proceed in parallel (prereq only Phase 43, merged)
