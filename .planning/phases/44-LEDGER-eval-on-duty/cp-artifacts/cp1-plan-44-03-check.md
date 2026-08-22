# CP1 Incremental Check -- 44-03-PLAN.md + D-23..D-27

Checked: 2026-08-22, FORCE stance, goal-backward against main @ 8bd01bc.
Scope: ONLY 44-03-PLAN.md and D-23..D-27 (44-01/44-02 already PASS B=0;
checked only where 44-03 couples to them).

## Dimension 1 -- Requirement coverage (D-23..D-27)

| Decision | Delivering task | Verdict |
|---|---|---|
| D-23 fingerprint suppression | Task 1 (`_suppress_known_findings` + `known_terminal_fingerprints`) | COVERED |
| D-24 rebuttal via adjudicate, single store | Task 1 test (b) (rebuttal via `ledger adjudicate <F> DISPROVED` suppresses) | COVERED |
| D-25 convergence + no fuzzy match | Task 1 test (d) (zero post-suppression CONFIRMED converges; drifted fingerprint blocks) | COVERED (see dim 5 for adequacy) |
| D-26 pinned_paths | Task 2 test (a)/(b) | COVERED with a WARNING (dim 3, dim 6) |
| D-27 style downgrade | Task 2 test (c)/(d) | COVERED with a WARNING (dim 6) |

No decision lacks a delivering task. OK.

## Dimension 2 -- False-green risk (CRITICAL)

Anchors re-grepped on main @ 8bd01bc:

- Verdict count: machine.py:526 `confirmed = self._count(Disposition.CONFIRMED)`;
  :528-532 `verdict = FAIL if confirmed > 0 or coverage_gaps > 0 else PASS`. REAL.
  The plan's insertion point (after collection, before `_count`) is correct and
  is the ONLY point that makes the verdict count post-suppression findings.
- Fingerprint recipe: reviewer_json.py:168-170 -- `sha256("%s:%d:%s" % (file, line, desc))[:16]`,
  computed at parse time, shared by Outlet A/C. REAL. Exact-match suppression is
  therefore well-defined and testable.

Conservative-rule audit -- can a LIVE finding be wrongly suppressed?

- Suppression set = fingerprints whose LATEST row is FIXED/DISPROVED/DUPLICATE
  (plan Task 1 action; "LATEST row" semantics match 44-01's
  `list --unadjudicated` latest-row vocabulary -- consistent).
- UNADJUDICATED does NOT suppress (test c pins it). ESCAPED does NOT suppress
  (Task 1 action text; acceptance criteria list it). Unknown fingerprint never
  suppresses (test c). Fuzzy/substring matching is explicitly rejected and
  bug-injection-pinned ("make suppression fuzzy/substring, watch test (d) FAIL").
- Bug-injection acceptance criteria (make UNADJUDICATED suppress -> test c FAIL;
  fuzzy match -> test d FAIL) pin both over-match directions. GOOD.

Residual false-green paths examined:

1. **Wrongly-adjudicated row (human error, not code)**: a human runs
   `ledger adjudicate <fp> FIXED` on a finding that is NOT fixed; next CI run
   suppresses a live bug. This is the designed trust model (D-24: the ledger IS
   the rebuttal registry; suppression follows the row). Acceptable -- the ruling
   is an explicit human act with an audit trail, not silent inference. NOT a
   plan defect.
2. **Fingerprint collision across findings**: 16-hex (64-bit) truncated sha256
   over file:line:desc. Two DIFFERENT live findings sharing one 64-bit
   fingerprint would suppress both. Birthday-bound at ~4B findings; irrelevant
   at forge scale. NOT a plan defect.
3. **LATEST-row regression race**: adjudicate FIXED, later the same fingerprint
   is re-CONFIRMED by a CI run -> 44-01 writes a new UNADJUDICATED row ->
   LATEST is UNADJUDICATED -> suppression lifts. Correct behavior (a re-found
   "fixed" bug resurfaces for re-adjudication). GOOD.
4. **Same-run suppression**: 44-01 writes UNADJUDICATED rows AFTER the verdict
   (44-01 key_link: "_write_ci_ledger_rows ... after _persist_state, before
   return verdict"), so a finding can never suppress itself in its own run.
   GOOD.

WARNING W-1 (test gap, not design gap): the suppression mechanism is
"set its disposition to a non-counting state OR move it out of the counted set"
(Task 1 action, two alternatives left open). The acceptance criteria assert the
verdict effect but never pin WHAT the suppressed finding looks like after the
run (which disposition, or is it removed from state.findings). An executor can
pass all tests while leaving suppressed findings in an ambiguous state that
downstream consumers (`active_findings`, the MCP result extractor at
mcp_server.py:996-997, state.json persistence at machine.py:541) then treat
inconsistently. Pick ONE mechanism in the plan (recommend: keep the finding,
set disposition to a named suppressed state or DISMISSED-with-note) and add an
assertion on the post-run disposition list.

False-green verdict: the conservative rule is testable and bug-injection-pinned
on both over-match axes; no code path found where a live finding is suppressed
without a real terminal row. PASS with W-1.

## Dimension 3 -- Interface accuracy

- machine.py:526-540 verdict count -- VERIFIED (see dim 2).
- machine.py:310 `def _run_ci` -- VERIFIED.
- machine.py:253 `self._advisories: list[AdvisoryFinding]` -- VERIFIED.
- machine.py:722-738 advisories populated (delta filter routes pre-existing L0
  findings to AdvisoryFinding) -- VERIFIED.
- machine.py:287-301 `_maybe_load_prior_state` / STATE-09 CI skip -- VERIFIED
  (comment + warning at :288-300; 44-03 test (e) pins it stays untouched).
- machine.py:232 `coverage_exempt_patterns: list` -- VERIFIED. BUT it is a
  StateMachine dataclass field fed from `.code-forge/coverage.yaml` via
  `coverage.py:load_coverage_exempt_patterns` (coverage.py:118) and wired in
  cli.py:3158-3240 -- NOT read from gate.yaml. The plan's phrase "following the
  coverage_exempt_patterns precedent" while putting `pinned_paths` in gate.yaml
  conflates two different config surfaces (gate.yaml is validated by
  gate_check.py:load_gate_config :39-100, which currently has ZERO ledger or
  path-list reads -- `grep -n "ledger" gate_check.py` returns nothing on main).
  The D-19 `ledger.enabled` read the plan says to sit "alongside" is itself a
  44-01 deliverable not yet on main. Workable (44-01 lands first per
  depends_on), but the executor must ALSO wire pinned_paths from the gate.yaml
  dict into the StateMachine (cli.py / mcp_server.py:928-943 constructs
  StateMachine and currently passes NO config-derived path list). Neither task
  names this wiring. WARNING W-2.
- advisory.py:26-36 AdvisoryFinding -- VERIFIED (class at :24-36; docstring
  confirms "NEVER block the review verdict"). NOTE: AdvisoryFinding has NO
  fingerprint field (deliberately excluded, advisory.py:31-34) -- Task 2's
  routing of style findings to advisories is compatible, but those findings
  leave the ledger-suppression universe entirely (no fingerprint -> can never
  be suppressed OR tracked). Consistent with D-27's intent. OK.
- gate_check.py:935-938 CI flag precedent -- VERIFIED (FORGE_SKIP_TESTS ignored
  in CI at :935-941; line drift ~3, within tolerance).
- ledger.py `iter_rows` :77, `TerminalState` :27 -- VERIFIED on main.
  `resolve_ledger_root` and `UNADJUDICATED` do NOT exist on main (grep clean)
  -- both are 44-01 Task 1 deliverables; see dim 4.

## Dimension 4 -- Coupling to 44-01

- `depends_on: [44-01]` declared in frontmatter. 44-03 consumes exactly what
  44-01 Task 1/2/3 deliver: `resolve_ledger_root` (44-01 Task 1, plan line
  154/171), UNADJUDICATED enum member (44-01 Task 1), CI-written UNADJUDICATED
  rows (44-01 Task 2), `ledger adjudicate` (44-01 Task 3). HONORED.
- "Base: main @ <44-01 merge sha>. Branch from main after 44-01 merges" --
  explicit sequencing. GOOD.
- Terminal-state vocabulary: 44-03 assumes FIXED/DISPROVED/DUPLICATE/ESCAPED +
  UNADJUDICATED -- exactly 44-01's vocabulary, nothing more. The verification
  section's coupling proof ("break 44-01's terminal-state vocabulary, watch a
  44-03 test FAIL") is a real cross-plan guard. GOOD.
- 44-03 assumes 44-01's `list --unadjudicated` "LATEST row" semantics carry
  over to `known_terminal_fingerprints` -- consistent with 44-01 plan lines
  314-315. No assumption beyond 44-01's contract. OK.

## Dimension 5 -- Does D-25 actually solve the pain point? (design adequacy)

Honest answer: PARTIALLY. This is the plan's biggest exposure.

- The scope analysis itself (44-SCOPE-EXTENSION-ANALYSIS.md:58) names the
  reworded-re-find case as the hardest sub-problem and says "needs topic-level
  matching, not just exact fingerprint". D-25 then REJECTS fuzzy/topic matching
  as a false-green risk and ships exact-match only.
- The fingerprint is sha256(file:line:description) (reviewer_json.py:168-170).
  It is sensitive to ALL THREE inputs: reworded description -> new fingerprint
  (blocks, by design); **line drift** (the same bug, same wording, after the
  file shifts by one line) -> new fingerprint -> also NOT suppressed. D-25
  only discusses the wording case; the line-drift case is equally common in a
  12-round review campaign and is silently lumped into "blocks again".
- What exact-match suppression DOES kill: the verbatim re-find -- same file,
  same line, same wording, round after round. On an unchanged diff (the
  classic CI re-run), wording AND line are stable, so the dominant 52% repeat
  mode (CI re-reporting the identical finding on a re-run of the same diff)
  IS suppressed. D-23/D-24 also close the "human already ruled it" case, which
  the pain-point report flags as the deepest insult (re-litigating adjudicated
  findings).
- What it does NOT kill: (a) reworded re-finds of the same underlying issue
  (R5/R7 case) -- these keep blocking, by explicit design choice; (b)
  line-drifted re-finds after the diff evolves. Both remain review-noise.
- Net: D-25 trades recall of dedup for zero false-green. Given the stated
  root cause (STATE-09 zero memory on identical re-runs) and that false-green
  is the unacceptable failure mode for a gate, the trade is DEFENSIBLE -- but
  the plan should say so. The objective ("52% repeat findings ... root cause
  STATE-09 CI zero-memory") oversells: an executor reading it could believe
  44-03 eliminates most repeats. It eliminates the identical-re-find class;
  the reworded/line-drifted class persists by design.

WARNING W-3: add one sentence to the objective or D-25 scoping the expectation
("suppresses verbatim re-finds of adjudicated/terminal findings on re-run;
reworded or line-drifted re-finds still block -- reviewer-behavior problem,
out of scope"). Otherwise the acceptance review of Phase 44 will re-litigate
"52% not solved" against a plan that never promised to solve it.

## Dimension 6 -- Scope reduction / unverifiable criteria

- Task 1: all five behaviors (a)-(e) are directly testable with ledger seeds +
  mocked L0/L1; bug-injection criteria are concrete. OK.
- Task 2 style classifier (D-27): "a finding whose axis/keywords mark it as
  style/test-assertion/naming/idiomatic" -- the classification RULE is deferred
  to the executor ("defined conservatively: only findings the review pipeline
  already tags as style-adjacent"). Verified ground truth: L1 findings carry
  NO axis field (reviewer_json.py:161-179 builds StateFinding with
  file/line/desc only; description is prefixed with the pass name). There is
  no existing style tag in the pipeline to key on. "Ambiguous stays CONFIRMED"
  is untestable without an enumerated keyword/axis set -- two executors will
  legitimately implement different classifiers and both can claim PASS.
  WARNING W-4: the plan must enumerate the downgrade set (e.g. specific L0
  rule-ids, or L1 pass names, or description keywords) or make the classifier
  table-driven from gate.yaml so the rule is data, not judgment.
- Task 2 pinned_paths: testable IF the gate.yaml -> StateMachine wiring is
  specified (W-2). The match semantics (glob? fnmatch? `**` support? relative
  to repo root?) are unspecified -- test (a) uses `"src/foo/**"`, which
  fnmatch does NOT handle (`**` needs `pathlib.PurePath.match` semantics or
  glob). Minor, but an executor can pick the wrong matcher and pass their own
  test. Fold into W-2.

## Findings

- W-1 (dim 2): suppression mechanism left as "disposition change OR removal"
  -- pick one, assert post-run disposition shape.
- W-2 (dim 3/6): pinned_paths wiring (gate.yaml dict -> StateMachine
  constructor at cli.py:3240 / mcp_server.py:928) and glob semantics
  unspecified; precedent cited (coverage_exempt_patterns) lives in a
  different config file (coverage.yaml) than the plan's target (gate.yaml).
- W-3 (dim 5): objective oversells repeat-finding reduction; scope D-25's
  expectation to verbatim re-finds.
- W-4 (dim 6): style classifier rule is executor-judgment, not an enumerated,
  testable set; "ambiguous stays CONFIRMED" unverifiable as written.

No BLOCKERS: anchors are real, the false-green conservative rule is sound and
bug-injection-pinned, coupling to 44-01 is honored exactly, and every decision
has a delivering task.

VERDICT: PASS
SCORECARD: B=0 W=4
