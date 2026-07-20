# Phase 29 Planning -- Ground-Truth Review Verdict

Reviewer: main session (independent ground-truth verifier).
Method: read all 6 artifacts + real source (cross_repo_impact.py, graph_triage.py)
+ real graph.db. Every briefing claim treated as a CLAIM, verified against source.

## Verdict in one line

EXECUTABLE, NOT yet AUTHORIZE-READY. The plans (29-01, 29-02) are correct,
buildable, line-accurate, and would produce working code. But the phase's
JUSTIFICATION in CONTEXT/RESEARCH is stale (contradicts the plans + ground
truth), and one locked detector behavior can drop live callers. Fix the
justification and decide the scope on the true basis before /gsd:execute-phase.

## What the briefing got RIGHT (verified, not trusted)

- All 6 artifacts exist; line counts 303/243/219/162/397/577 and byte sizes
  match the briefing exactly. main @ ce12a0e confirmed. .planning gitignored.
- The 3-round cross-model review was REAL and substantive, not narrated. Proof:
  R1's claimed "sys.version_info limited to <" fix actually landed in
  29-01 Task 1 step 5 (vs the stale blanket substring in 29-RESEARCH:211).
  The rounds caught real tactical bugs (SC#3 test placement, compound-condition
  guard, C #else handling, exact-pattern grep to avoid docstring match).
- Plan line numbers are ACCURATE against real source: cross_repo_impact.py
  134-147 SQL + 150-166 loop; graph_triage.py 260-272 / 274-285 (site B) and
  393-405 / 409 (site C). The wiring surgery targets the correct lines.
- The 3 inline SQLs ARE byte-identical (read all three) -> a single shared
  _live_callers is the correct dedup. SC#3 has real standalone value.

## FINDING A -- MEDIUM -- stale justification (cross-doc; affects authorize/scope)

GROUND TRUTH (verified):
- All 3 advisory axes surface CALLS-edge callers ONLY. Source:
  cross_repo_impact.py:136, graph_triage.py:262, graph_triage.py:395 all
  `WHERE c.kind = 'CALLS'`; IMPORTS_FROM appears only inside the EXISTS
  disambiguation subquery. cross_repo_impact.py:110 docstring confirms
  "CALLS + IMPORTS_FROM disambiguation".
- The cited "observed FPs" machine.py:32 / cli.py:24-25 are IMPORTS_FROM edges.
  Real graph.db: no CALLS edge exists at line <=35 in machine.py or cli.py
  (query returned empty). So those imports can NEVER surface as findings.
- Conclusion: there is no demonstrated current CALLS-edge dead-code FP. The
  filter is PREVENTIVE infrastructure (+ the SQL dedup, SC#3).

THE PLANS SAY THIS HONESTLY -- 29-01 Task 2 TestRealPathSmoke (lines 227, 244-245):
"machine.py:32 does NOT produce a CALLS-edge FP ... the filter is preventive
infrastructure ... validates the detector, not the full pipeline."

BUT THESE STILL ASSERT THE OLD (stale) JUSTIFICATION:
- 29-CONTEXT D-05 (line 64): "cover the ONLY observed live FPs (machine.py:32,
  cli.py:24-25)"
- 29-CONTEXT canonical_refs (lines 155-157): "confirmed live dead-code edges:
  machine.py:32 ..., cli.py:24-25"
- 29-CONTEXT specifics (lines 203-206): smoke "confirm machine.py:32 dead
  importer appears WITHOUT filter, drops WITH it" (impossible -- no CALLS edge)
- 29-RESEARCH (line 11): "confirmed live false positives ... provide real-path
  smoke test targets"; (line 49) SPEC-01 "Research confirms live FPs exist"

WHY IT MATTERS: the authorize decision (is this phase worth doing now?) rests on
the stated value. CONTEXT frames it as "fix observed FPs"; the truth is
"preventive infra + SQL dedup, no FP currently occurs." Both may justify the
phase, but the user should decide on the TRUE basis (the scope-challenge rule).
This is the textbook forge "Cross-Plan Consistency" failure: the R2 ground-truth
revision propagated into the PLANS but not into CONTEXT/RESEARCH-summary. No
single-doc review round (even 4 models x 3) can see a cross-doc contradiction.

FIX: rewrite CONTEXT D-05 / canonical_refs / specifics and RESEARCH lines 11 & 49
to the honest "preventive; no currently-surfaced FP; primary concrete win is
SC#3 SQL dedup across 3 sites" framing, matching the plans. Then re-confirm the
scope is still wanted on that basis.

## FINDING E -- MEDIUM -- sys.version_info detector can DROP LIVE callers

29-01 Task 1 step 5 (locked): a Python line is dead if its if-condition contains
b"sys.version_info", has no b"and"/b"or", and contains b"<".

PROBLEM: this never compares the version tuple to the running interpreter.
`if sys.version_info < (3, 13):` is a normal forward-compat idiom; on Python
3.11/3.12 that block is LIVE. The detector sees "<" and returns dead -> any CALLS
caller inside it is DROPPED. That is the one direction D-06 forbids ("a
wrongly-dropped live caller ... is worse"). The rounds fixed the >=/compound
direction but not the magnitude.

Realistic trigger: a target/sibling repo with `sys.version_info < (3, X)` where
X > host minor version AND a call site inside the block. Moderate-low frequency,
but it is a charter violation when it fires.

DECIDE (user's call -- "Add version guard" was the user's choice, DISCUSSION-LOG
line 33): (a) drop the version-guard detector entirely -- most D-06-consistent,
since host-dependent reachability is undecidable without the target interpreter;
(b) actually evaluate the comparison against the running sys.version_info; or
(c) keep it but document the assumption "compared version <= running interpreter"
in the honest ceiling, accepting the residual drop-live risk.

## FINDING I -- LOW -- CONTEXT D-02 signature is stale vs the plan

CONTEXT D-02 (line 43): `_live_callers(cursor, caller_qualifieds)`.
Plan 29-01 Task 1 step 8 + 29-02 interfaces: `_live_callers(cursor, target_name,
module_name)` (self-documented as a deliberate D-02+D-08 merge). Plan governs;
update CONTEXT D-02 to match so the contract is single-sourced.

## NOTE -- real-path smoke proves less than forge golden rule #3 ideal

Because no real FP exists, TestRealPathSmoke can only (a) unit-test the detector
on machine.py:32 and (b) no-crash the pipeline; it never shows the filter
removing a real finding. This is an honest necessity, and the plan handles it
correctly: 29-01 Task 2 line 239 builds a fixture graph.db with an actual CALLS
edge from a dead-block caller, so the bug-inject (SC#2) does exercise the full
CALLS-edge pipeline synthetically. Acceptable -- just be aware the hand-built
fixture is doing all the proof work; verify at impl-review that it really
constructs the CALLS edge (not a vacuous pass).

## Not blockers (checked)

- No execution blocker found: SQL extraction is sound, signatures consistent
  (target bound twice as the original 3-param form), SC#3 grep correctly uses
  the exact pattern to avoid the docstring false match (29-02 Task 1 line 132).
- Bug-inject monkeypatch target (dead_code._is_dead_call_site) works iff
  _live_callers calls it by module-global name -- standard; verify at impl-review.

## Recommendation

1. Fix FINDING A (CONTEXT + RESEARCH justification) -- required before authorize;
   re-affirm scope on the preventive+dedup basis.
2. Decide FINDING E (version-guard detector) -- pick (a)/(b)/(c).
3. Fix FINDING I (one-line CONTEXT D-02 sync).
4. Then authorize /gsd:execute-phase 29. The plans themselves need no structural
   change -- 29-01/29-02 are buildable as-is once the detector decision is made.

These are planning-doc corrections, not a re-plan. The engineering is sound; the
justification narrative drifted from the verified ground truth.

## RESOLUTION (2026-06-26, applied by main session per user decision)

User decision: edit CONTEXT/RESEARCH directly; FINDING E -> option (b). All three
findings fixed in the planning docs.

- FINDING A (stale justification) FIXED: reframed to "preventive infra + SQL
  dedup (SC#3); machine.py:32 / cli.py:24-25 are IMPORTS_FROM, not surfaced"
  across 29-CONTEXT (D-05, canonical_refs, specifics) and 29-RESEARCH (summary,
  SPEC-01, State-of-the-Art).
- FINDING E (drop-live version guard) FIXED via (b): the sys.version_info
  detector now EVALUATES the comparison against the running interpreter (regex
  parse op + tuple -> operator.{lt,le,...}(sys.version_info, tuple); dead iff the
  guard is False here; compound / unparseable -> fail-safe live). Written into
  29-CONTEXT D-01, 29-RESEARCH Pattern 1 (code + verified-behavior), and 29-01
  Task 1 step 1 (imports) + step 5 (logic) + Task 2 (test + fixture) + frontmatter
  must_have. Empirically verified 6/6 on Python 3.14.5, incl. the regression
  `if sys.version_info < (3, 99):` -> LIVE (not dropped) and `>= (3, 99)` -> dead.
- FINDING I (D-02 signature) FIXED: canonical `_live_callers(cursor, target_name,
  module_name)` in 29-CONTEXT D-02; consequent drifts cleared in 29-01 step 8 and
  the 29-RESEARCH architecture diagram.

Verification: non-ASCII clean on all 3 files; (b) logic 6/6 empirical PASS;
cross-doc stale sweep clean except 29-DISCUSSION-LOG.md:29 (frozen audit trail,
intentionally preserved). No 9-pass review run -- these are planning docs, not
executable code; the logic-bearing (b) spec was proven empirically instead. The
generated dead_code.py gets the 3-cycle review + SC#2 bug-inject at execute time.

Files touched: 29-CONTEXT.md, 29-RESEARCH.md, 29-01-PLAN.md.
Not touched (deliberate): 29-02-PLAN.md (call sites already consistent),
29-PATTERNS.md (no stale ref), 29-DISCUSSION-LOG.md (audit trail).
Planning snapshot: planning-local @ 79106e3 (2026-06-26T02:05:02Z; 478/478 files,
main untouched at ce12a0e). Status: ready to authorize /gsd:execute-phase 29.
