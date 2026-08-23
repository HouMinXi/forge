# CP1 Convergence Check -- Phase 44 (44-01 / 44-02 / 44-03, revised per CP1b)

**Checker:** GSD plan-checker (CP1 convergence round)
**Date:** 2026-08-22
**Scope:** Verify the 5 CP1b blocker fixes (+ deepseek H-1) in the revised plans
are resolved with ground-truth evidence, sweep for regressions, confirm
convergence. Ground truth re-grepped against src/code_forge/{machine, ledger,
gate_check, coverage, advisory, reviewer_json, cli, outlet_resolver,
eval/corpus}.py -- cited line numbers verified live, none trusted from citation.

---

## Per-blocker verdicts

### 1. kimi B-1 -- D-27 style downgrade ejected findings from the data model
**Verdict: RESOLVED (with stale frontmatter text -- see W-1)**

Ground truth: advisory.py:26-36 `AdvisoryFinding` docstring explicitly lists
`fingerprint` under "Fields intentionally excluded" (verified: line 34
"- fingerprint: advisory findings are not deduplicated against blocking").
Rerouting to advisories would indeed eject style findings from the
ledger/adjudicate/export path.

Fix evidence:
- 44-CONTEXT.md D-27 revised (lines 241-254): style findings "STAY
  StateFindings (keep their fingerprint) but are recorded with a NON-BLOCKING
  disposition, NOT rerouted to AdvisoryFinding".
- 44-03 Task 2 test (d) (lines 226-234): finding matching the table "STAYS a
  StateFinding with its fingerprint but is recorded with a NON-BLOCKING
  disposition ... it is NOT rerouted to AdvisoryFinding".
- 44-03 Task 2 action (lines 263-274) + acceptance (lines 287-291): fingerprint
  asserted present, finding asserted still in self._state.findings, "still
  written to the ledger / adjudicable / exportable"; bug-injection pins the
  wrong behavior (route to AdvisoryFinding -> test (d) FAIL).
- Non-blocking confirmed: verdict counts `_count(Disposition.CONFIRMED)` +
  `_count_coverage_gaps` only (machine.py:526-531); a DISMISSED/STYLE
  disposition is excluded from both.

### 2. kimi B-2 -- kill-switch/pinned_paths via load_gate_config dies on review-mode gate.yaml
**Verdict: RESOLVED**

Ground truth: gate_check.py:64-71 -- `load_gate_config` raises
`ValueError("gate.yaml needs an active 'test' section...")` when `"test" not in
data` (verified live). outlet_resolver.py:132 comment confirms the precedent:
"Does NOT call load_gate_config (avoids the 'test section required'
constraint)" with `yaml.safe_load` at :143.

Fix evidence:
- 44-01 Task 2 test (g) (lines 228-234) + action (lines 266-271) + acceptance
  (lines 289-303): kill-switch config read is `yaml.safe_load + dict.get`, NOT
  load_gate_config; bug-injection pins it; acceptance includes "grep: NO
  load_gate_config call in the kill-switch path".
- 44-03 Task 2 action (lines 240-244) + acceptance (lines 285-286):
  pinned_paths/style_downgrade read via the SAME tolerant raw-YAML read.
- 44-CONTEXT.md D-19 revised (lines 171-183) documents the loader constraint.
- Remaining `load_gate_config` mentions in the plans (44-01:131,207;
  44-03:114,204) are interface/read_first context or negations, never a live
  instruction in the kill-switch/pinned_paths path. Grep-clean.

### 3. kimi B-3 -- mutation-survivor FAIL terminal returns before the ledger write
**Verdict: RESOLVED**

Ground truth: machine.py:351-360 -- `status == "done"` + survivors ->
`verdict = Verdict.FAIL; converged = False; infra_errors.append(...);
_persist_state(); return Verdict.FAIL` (verified live at :351-360). The normal
CI terminal with the ledger-write insertion point is at :538-542
(`self._state.verdict = verdict; ... _persist_state(); return verdict`). The
:360 return bypasses :541. Real.

Fix evidence: 44-01 Task 2 test (g2) (lines 235-239), action (lines 251-256:
"Call it from EVERY CI terminal exit ... the normal terminal at :541 AND the
mutation-survivor FAIL return at :360 -- funnel both through the single write
call"), acceptance (lines 285-286). PENDING returns (:381/:391) correctly
excluded as non-terminal.

### 4. gemini B-1 -- pinned_paths did not suppress COVERAGE findings
**Verdict: RESOLVED**

Ground truth: machine.py:1635-1645 `_count_coverage_gaps` counts
`f.source == "COVERAGE" and f.disposition != Disposition.DISMISSED` SEPARATELY
from CONFIRMED (verified live); coverage.py:100-113 generates COVERAGE findings
as `disposition=Disposition.UNCERTAIN` (verified live), so a pinned-path
COVERAGE finding would stay UNCERTAIN and keep coverage_gaps > 0 -> FAIL.
Setting DISMISSED removes it from the count (the `!= DISMISSED` predicate).

Fix evidence: 44-03 Task 2 test (b2) (lines 217-220) + action CRITICAL note
(lines 252-262: cites machine.py:1635-1643 and coverage.py:100-115, mandates
setting pinned-path COVERAGE findings to DISMISSED or routing pinned_paths into
the coverage exempt filter) + acceptance (lines 283-284). Mechanism is
consistent with Task 1's suppression (same DISMISSED disposition, same pass).

### 5. gemini B-2 -- `ledger mark --new ESCAPED` defaults base_sha=head_sha -> empty diff -> permanent MISSED
**Verdict: RESOLVED**

Ground truth: cli.py:1619-1622 (verified live): when both SHAs omitted,
`head_sha = _git_head(cwd); base_sha = head_sha`. An ESCAPED row so created
materializes `git diff X..X` = empty; replaying an empty diff yields PASS while
expected_verdict=HOLD -> MISSED forever. Real.

Fix evidence: 44-02 Task 1 test (f) (lines 162-168): extractor skips
base_sha==head_sha / empty-diff rows under a dedicated empty-diff counter
("check base_sha != head_sha AND the produced diff is non-empty -- never emit
an expect-catch entry for an empty diff"). Counter integration gap noted as
W-2 below.

### 6. deepseek H-1 -- DUPLICATE mapped to expect-no-catch penalizes finding a real bug
**Verdict: RESOLVED in CONTEXT + task body; STALE TEXT in two spots (W-3)**

Fix evidence (correct):
- 44-CONTEXT.md D-02 revised (lines 40-47): "DUPLICATE -> EXCLUDED from export
  ... skipped under their own counter". D-13 revised (line 126): "DUPLICATE ->
  excluded from export".
- 44-02 Task 1 test (a) (lines 144-149): "DUPLICATE rows are EXCLUDED from
  export ... skipped under their own counter, not emitted as expect-no-catch".

Stale (contradicting) text:
- 44-02-PLAN.md line 21 (must_haves truth): "DISPROVED/DUPLICATE rows export as
  expect-no-catch entries" -- still the pre-fix mapping.
- 44-02-PLAN.md line 174 (Task 1 action): "Classification per D-02/D-13
  (FIXED/ESCAPED -> expect-catch ...; DISPROVED/DUPLICATE -> expect-no-catch)"
  -- the action prose contradicts its own test (a) five lines earlier.
No other DUPLICATE->no-catch mapping found anywhere in the three plans or
CONTEXT (full grep). 44-03's use of DUPLICATE as a suppressing terminal state
(D-23/D-24) is a different, correct axis.

---

## Regression sweep

**Decision coverage (all 27 decisions have a task):**
- 44-01: D-01 T2; D-05 T1/T2; D-06 T1; D-07 T1/T3; D-08 T2; D-10 T3; D-11
  T1/T2; D-12 T2; D-16 T2 (docstring); D-19 T2; D-20a T3; D-20b T1; D-21 T1. COMPLETE.
- 44-02: D-02 T1; D-03 T1; D-04 T3; D-09 T2/T3; D-13 T1; D-14 T2; D-15 T1/T3;
  D-17 T2; D-22 T3. D-18 coupling honored (Task 1 fixture = 44-01 real CI-write
  path, verification coupling proof). COMPLETE.
- 44-03: D-23/D-24/D-25 T1; D-26/D-27 T2. COMPLETE.

**WARNING W-1 -- 44-03 frontmatter/key_links still describe the OLD AdvisoryFinding rerouting.**
- 44-03-PLAN.md:23 (must_haves truth): "A finding classified as
  style/test-assertion/naming/idiomatic is emitted as an AdvisoryFinding
  (never blocks) instead of a CONFIRMED StateFinding (D-27)" -- the pre-fix
  behavior, directly contradicting Task 2 test (d)/action/acceptance.
- 44-03-PLAN.md:35-37 (artifacts): advisory.py "provides: style-finding
  classification hook" / contains "AdvisoryFinding" -- under the revision the
  classifier is table-driven in machine.py/gate.yaml; advisory.py needs no
  change at all (files_modified still lists it, Task 2 <files> still lists it).
- 44-03-PLAN.md:47-50 (key_links): "style-classified findings routed to
  self._advisories not self._state.findings" -- exactly the rejected design.
Task body is correct and its bug-injection pins the right behavior, so an
executor following tasks lands correctly; but must_haves/key_links are the
verifier-facing contract and currently assert the OLD design. Prose-level
contradiction, not test-breaking. Severity: WARNING.

**WARNING W-2 -- 44-02 summary/counter lists not extended for the two new skip counters.**
D-15's invariant: "The export summary always reports: entries emitted /
unadjudicated skipped / stale-SHA skipped / dedup-collapsed, summing to total
rows read" (CONTEXT:141-143) with precedence "unadjudicated > stale-sha >
dedup-collapse". The CP1b fixes add TWO new counters -- duplicate-excluded
(test a) and empty-diff (test f) -- but:
- 44-02-PLAN.md:24 (must_haves), :157 (test d precedence), :300 (Task 3 test a
  summary string) all still enumerate only the old four counters / three skip
  reasons.
- An empty-diff row whose SHAs resolve passes cat-file validation (the SHAs
  exist), so it is not stale-sha; without a listed counter the "sums to total
  rows read" invariant is unachievable as written. Test (d)'s exclusivity test
  also doesn't cover the new counters' precedence position.
The fix behaviors themselves are fully specced; the accounting contract is
stale. Severity: WARNING (the implementing executor must extend the summary;
acceptance line 188 "exclusive counters all green" implicitly covers it, but
the enumerated lists mislead).

**WARNING W-3 -- stale DUPLICATE->expect-no-catch mapping (see blocker 6).**
44-02-PLAN.md:21 and :174. The task's own test (a) and acceptance line 189
("Classification ... green against a 44-01-real-path fixture") pin the correct
behavior, so this is a must_haves/action-prose contradiction, not a behavioral
gap. Severity: WARNING.

**WARNING W-4 -- 44-03 Task 1 action prose claims DISMISSED findings "stay visible to active_findings"; the real accessor excludes DISMISSED.**
machine.py:240-243 (verified live):
`active_findings = [f for f in self._state.findings if f.disposition !=
Disposition.DISMISSED]`. 44-03-PLAN.md:168-171 claims the suppressed finding
"stays visible to active_findings / the MCP result extractor / state.json".
False for active_findings (and the MCP result extractor, which reads
active_findings) -- DISMISSED findings are filtered OUT. The acceptance
criteria (:190-193) assert only "remains in self._state.findings with
disposition == DISMISSED", which IS consistent with reality, so the tests will
pass; but the rationale prose misdescribes the mechanism, and the same
false claim implicitly covers Task 2's pinned/style DISMISSED findings
(suppressed findings disappear from the MCP result view entirely -- arguably
desired, but not what the prose says). Severity: WARNING (prose vs. accessor
contradiction; acceptance is correct).

**WARNING W-5 -- cross-plan write-scope contradiction for style findings (kimi B-1 fix side effect).**
44-03's revised D-27 promises style findings "stays in the ledger ...
written by CI, adjudicable, exportable" (CONTEXT:250-254; 44-03:272-273,
290-291). But 44-01 scopes CI writes to CONFIRMED findings only
(44-01-PLAN.md:20, :214, :257: "Per-finding UNADJUDICATED rows for CONFIRMED
findings"). A style finding carrying a non-blocking disposition
(DISMISSED/STYLE) is not CONFIRMED, so the 44-01 write path as specced never
emits it. Compounding: 44-01 directs REUSE of the LOCAL row-build logic
(44-01:249-251), and the LOCAL writer keys on FIXED/DISMISSED
(machine.py:1330-1334, mapping DISMISSED -> TerminalState.DISPROVED) -- a naive
shared-writer reuse would either (a) drop style findings (violating D-27's
promise) or (b) emit suppressed/DISMISSED findings as DISPROVED terminal rows,
which would then self-suppress on the next run via D-23 and pollute export
classification. Neither plan states which dispositions the CI writer emits for
non-blocking style findings. The gap is a direct consequence of the kimi B-1
revision and needs one sentence in 44-01 Task 2 (e.g. "CI writes UNADJUDICATED
rows for CONFIRMED and style-downgraded findings; suppressed DISMISSED findings
are NOT re-written"). Severity: WARNING (borderline BLOCKER -- unresolvable by
the executor without a choice the plans don't make, but both resolutions are
small and neither invalidates the fixes).

**Consistency check -- DISMISSED mechanism:** 44-03 Task 1 suppression and Task
2 pinned-path COVERAGE suppression use the SAME mechanism (set DISMISSED, keep
in findings, infra_errors note), consistent with both count predicates
(_count:1630-1633, _count_coverage_gaps:1635-1645). CONSISTENT.

**Stale-reference grep sweep:**
- "load_gate_config" in kill-switch context: only negations/read-first
  (44-01:230,233,268,290,298,303; 44-03:242-243,286). CLEAN.
- "AdvisoryFinding" as style destination: 44-03:23,47-50 (frontmatter, W-1);
  task body references are all negations/correct. STALE in frontmatter only.
- DUPLICATE->no-catch: 44-02:21,174 (W-3). CLEAN elsewhere.

**New unverifiable acceptance criteria introduced by fixes:** none found. All
new tests (44-01 g/g2, 44-02 a/f, 44-03 b2/d) are concrete, automated, and
have bug-injection pins.

---

## Summary

All 6 CP1b findings are RESOLVED in the binding layer (CONTEXT decisions + task
behavior/action/acceptance), each verified against live ground truth. The
revisions introduced NO new blockers but left FIVE warnings, all prose/contract
staleness: 44-03 frontmatter + key_links still describe the rejected
AdvisoryFinding rerouting (W-1); 44-02 counter/summary enumerations not
extended for duplicate-excluded and empty-diff (W-2); two stale
DUPLICATE->no-catch strings (W-3); a false active_findings visibility claim
(W-4); and an underspecified CI write scope for non-blocking style findings
(W-5). W-5 is the only one that forces an executor judgment call; the rest are
one-line text fixes. None of the warnings break the fixes' tests as written.

VERDICT: PASS

SCORECARD: B=0 W=5
