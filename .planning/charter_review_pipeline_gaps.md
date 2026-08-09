# Charter: review pipeline self-attestation gaps (RATIFIED 2026-08-08)

Slot: unassigned. Does not overlap the v3.x sketch lane (45-50,
learning-loop flywheel) or the ENV-GROUNDING lane (51-53b) -- checked by
grep across ROADMAP.md, v2.8tail-v3-DISPATCH-SCHEDULE.md,
v2.9-V3-GROUNDTRUTH-SCHEDULE.md, MILESTONES.md, 2026-08-01: no existing
phase claims l2_runner/e2e_runner wiring, the mutation baseline timeout,
or the run_verify coverage floor. Numbering (which lane, which slot) is
a scheduling decision left to the user -- do not assign a phase number
without that decision (STATE.md's own precedent: "scheduling sovereignty,
not a map decision").

**Ratified 2026-08-08 by user.** Scope (items 1-10), the process change
(silent-failure signature at plan time, adopted in forge phase planning
first per the charter's own promotion route -- not promoted to
~/CLAUDE.md global until it catches something in a second project beyond
forge and OmniRoute), and the review proportionality rules in the
Process section below are all accepted. Phase number unassigned, pending
user scheduling decision.

## Backlog mapping (added at ratification)

The charter's 10 items overlap the existing todo list (#45-#62) in
places and stand alone in others. This table is the contract between the
charter and the todos so a later session can see which item a todo serves
without re-deriving it.

| charter item | todo #        | status                |
|--------------|---------------|-----------------------|
| 1 (mutation receipt.py timestamp)  | (none -- on defects/receipt-timestamp branch) | branch exists, unmerged |
| 2 (e2e_runner CLI wiring)          | (none) | not started |
| 3 (mutation baseline timeout)      | (none) | not started |
| 4 (source_hash cache-replay, investigative) | #53, #58 (partial -- these are symptoms; item 4 is the root cause) | #53 pending, #58 pending |
| 5 (negative test for timestamp rejection) | (none) | not started |
| 6 (check 6 coverage-floor calibration) | (none) | not started |
| 7 (backend routing for security reviews) | #54, #60 | #54 pending [BLOCKS DEPLOYMENT], #60 pending (now unblocked) |
| 8 (detach mutation run)            | #56 | in_progress |
| 9 (excerpt line numbers from hunk header) | #59, #51 (done) | #59 pending, #51 completed |
| 10 (checks 2-4 read every cycle)   | (none) | not started |

Todos NOT in this charter (independent or separate chains):

| todo # | subject                          | belongs to                  |
|--------|----------------------------------|-----------------------------|
| #49    | stale git hook detection         | independent (onboarding DX) |
| #50    | claude -p fallthrough            | independent (verdict trust) |
| #55    | canary/runtime annotated diff    | #51 extension               |
| #57    | gate.schema.json field drift     | independent -> #61 chain    |
| #61    | api-only fields null semantics   | #57 chain                   |

Origin: 2026-07-31/08-01, main-session verification of the fix-receipt-ts
delivery (branch defects/receipt-timestamp) and adjacent work on
defects/mutation-gate-nowait. Full evidence trail: memory
feedback_gate_yaml_split_brain.md (5 sections) and
.planning/reviews/fix-receipt-ts/panel-local-r{1,2}/. User decision
2026-08-01: accept the delivery on direct evidence (source read +
bug-injection + standalone pytest 87/87), log the deferred items here
instead of chasing them inline, and route ALL further backlog through a
new phase rather than ad hoc.

## Scope-challenge

(a) Does this need to exist?
    Yes for items 1-3 (concrete, reproduced defects with a named
    mechanism); item 4 is investigative (root cause not yet known,
    charter records the question, not a fix); items 5-6 are small,
    single-site, low-risk.

(b) Real consumers:
    1. Any future defects/* branch touching receipt.py/verify.py that
       needs a PYTHONPATH-redirected panel review of itself -- hits the
       same chicken-and-egg (section 4 + 5 of the memory file) every
       time without item 1.
    2. Any CLI local-mode review that should exercise E2E checks --
       currently silent no-op, same shape l2_runner was before this
       session's fix (item 2).
    3. Any repo whose test suite runs 120-900s (this repo: 269-297s) --
       R2 mutation gating is permanently a soft-skip for it regardless
       of gate.yaml's configured timeout_seconds (item 3).
    4. Whoever next trusts a `consecutive_clean_rounds >= 3` verdict at
       face value -- item 4 is why that trust is currently unproven in
       general, not just for this one delivery (item 4).
    5/6. Future editors of check 4 / check 6 in verify.py.

(c) Cost of do-nothing:
    Items 1-3: the exact failure modes in memory section 5 recur on the
    next receipt/verify-touching delivery, each time paying the same
    manual audit cost (direct source read + bug-injection + standalone
    pytest) this session paid once. Item 4: the review pipeline's core
    convergence claim (3 independent clean cycles) stays unverified
    wherever it is not manually checked, repo-wide, not delivery-specific.
    Items 5-6: small, no urgency.

## Proposed scope (unratified -- needs user sign-off before a plan)

1. **Mutation-gate engine's own receipt.py**: port the single-timestamp-
   per-round fix (defects/receipt-timestamp) onto defects/mutation-gate-
   nowait, or merge fix-receipt-ts to main first and rebase mutation-gate
   onto it -- the latter avoids a second copy of the same fix living on
   two branches. Decide which before planning.

2. **e2e_runner CLI wiring**: same shape as the l2_runner fix
   (cli.py _run_hold_loop never passes e2e_runner=build_e2e_checker()).
   Bug-injection pattern already proven reusable (test_cli_l2_wiring.py
   is a template for test_cli_e2e_wiring.py).

3. **Mutation baseline timeout**: mutation.py:180 hardcodes `timeout=120`
   in `_run_baseline`; gate.yaml's `test.timeout_seconds` (read by
   gate_check.py:981 for the L1 path) never reaches it. Thread it through,
   or document why L2 baseline intentionally uses a different, shorter
   timeout than L1 (a flaky-guard budget vs a correctness budget may
   legitimately differ -- this needs a decision, not an assumed bug).

4. **source_hash / intra-run cache-replay mechanism** (INVESTIGATIVE):
   root cause still unknown. Confirmed facts only: (a) section 2 of the
   memory file showed cross-invocation replay on an unchanged
   source_hash; (b) section 5 (2026-08-01) showed the SAME shape
   happening INTRA-run, across cycles 2-3 of a single LOCAL-mode call,
   with byte-identical code_excerpts and cache-speed durations (0.02-
   0.07s vs cycle 0's 5.6s). Not yet determined: OmniRoute-side response
   caching, a forge-side memoization keyed on (source_hash, axis), or
   something else. Needs an experiment (Golden Rule 6), not another
   inference pass.

5. **Negative test for timestamp rejection**: verify.py's check 4 has no
   test proving it REJECTS an out-of-order timestamp set (only that it
   accepts a valid one, per this session's E2E addition). Raised by r2's
   own [test-assertion] axis and independently confirmed true.

6. **Check 6 coverage-floor calibration for test-heavy diffs**: flagged,
   not resolved. A diff that is 83% new test code (r2's case: 149/180
   lines) structurally cannot clear a 60%-of-all-changed-lines excerpt
   floor without a reviewer quoting the test's full body near-verbatim.
   Unclear whether this is working as intended (forces thorough test
   review) or miscalibrated (penalizes exactly the diffs the project
   wants more of). Needs a decision on intent before it is "fixed."

7. **Backend routing for security-adjacent reviews** (added 2026-08-01,
   after it blocked this session's own commit). The default
   `gemini-omniroute` backend resolves to a Flash model, which returns
   empty content on review prompts about security-relevant code and sends
   forge into a retry loop until the run dies. Measurements and the
   working alternative are in memory feedback_gate_yaml_split_brain.md
   section 6b. Open questions this leaves: whether the project should ship
   a Pro-class default for its own reviews, whether forge should surface
   "backend returned empty N times" as an infra_error rather than only a
   retry line on stderr, and whether the upstream OmniRoute fix
   (LEGIT_EMPTY_OPENAI_FINISH missing content_filter) is worth carrying
   locally rather than waiting on.

8. **Detach the mutation run instead of joining it** (added 2026-08-01,
   from the 19-round panel on the mutation-gate branch: five findings,
   three independent axes, all pointing the same way). The daemon=False
   fix makes CI mode block on exit until the mutation run finishes, about
   two minutes in this repo and roughly twelve where the suite fits inside
   the baseline budget. `subprocess.Popen(start_new_session=True)` removes
   the wait instead of paying it, but it also moves the pid the result
   file records from the CLI's to the child's, so whatever reads that pid
   has to move with it. Landed the smaller fix first because the gate was
   measuring nothing at all until it did.

9. **Excerpt line numbers taken from the hunk header** (added 2026-08-01,
   cost this session a whole panel). Given `@@ -46,7 +47,13 @@ from
   .exit_codes import (`, the reviewing model read line 46 as holding
   `from .exit_codes import (` -- the old-side start number plus the
   trailing context annotation, treated as a line of source -- and counted
   down from there, so every line in that excerpt was off by four. Check 5
   caught it, which is the system working; the waste is that it surfaces
   only after the full run. Fifteen of sixteen excerpts were fine and the
   same diff's other hunk was quoted correctly, so this is occasional
   rather than systematic. Worth one sentence in the excerpt prompt, and
   possibly a cheap pre-flight that re-checks excerpt alignment against
   the post-image before the round is committed rather than at verify.

10. **Checks 2-4 read every cycle while check 1 reads the last three**
    (added 2026-08-01). Noticed while fixing the infra-anchor defect, left
    alone deliberately: for the hash check, reading every receipt is
    arguably right, since a diff that changed mid-run should invalidate
    the whole run. But the asymmetry is undocumented and it is what turned
    a transient failure into a permanent one. Decide whether it is a
    contract or an accident, then write it down either way.

## Proposed process change: name the silent-failure signature at plan time

Different in kind from the ten items above, which are forge defects. This
one is about how phases get planned, so it is written out in full rather
than as a line item: a cold session should be able to judge it without the
conversation that produced it.

### What happened

Three defect branches opened inside forty-eight hours, on 2026-07-31.
Against 118 completed phase items the raw count is unremarkable; what makes
them worth a process change is that all three landed in the same subsystem,
and that subsystem is the gate.

- **defects/mutation-gate-nowait.** The mutation run was launched on a
  daemon thread, so the interpreter killed it the moment the review process
  returned. Every later round read a dead pid, recorded the gate as
  skipped, and launched a replacement that died the same way. The mutation
  gate had never measured anything.
- **Same branch, second fix.** `_run_hold_loop` built its StateMachine
  without `l2_runner`, so the machine fell back to its own default, which
  returns an empty finding list. That is byte-identical to a mutation run
  that found no surviving mutants. Every CLI review reported an R2 gate it
  had never run. The MCP path passed the real runner on the same
  construction; only the CLI path was missing it.
- **Same branch, third fix.** A gate.yaml whose test config was unusable
  was swallowed rather than recorded.
- **defects/receipt-timestamp.** write_receipts offset each pass's
  timestamp by its index, so a converged review failed its own attestation
  on non-monotonic timestamps.
- **defects/infra-anchor-poison.** A failed backend call is recorded as a
  finding naming "<llm-invoke>". That sentinel went into the receipt
  anchors, which verify reads as paths that must appear in the diff, so
  three transient 502s in the opening rounds of a nineteen-round run left
  it permanently unattestable.

### The shape they share

The first three fail by doing nothing while reporting success. A gate that
crashes is fixed within the hour. A gate that returns "clean" without
having run is invisible by construction, and every green build afterwards
reinforces the belief that it works. The l2_runner case is the pure form:
the default and the real runner return the same value for "nothing found",
so no observer anywhere in the system can tell the two apart.

The last two fail loudly, but at the wrong place and for a reason the
message does not name, which costs a full review to diagnose.

### Why the existing pipeline did not catch them

Four reasons, each structural rather than a lapse:

1. **Plans state what to build, never what a silent failure would look
   like.** CP1 and CP1b check that a plan is correct and complete. Neither
   asks what would be observed if the thing built quietly did nothing. A
   plan item reading "wire the mutation runner into the state machine" has
   a provable done-condition, and it stayed provable while the CLI path had
   no wiring at all, because the MCP path did.
2. **Review is diff-scoped, and these live between components no single
   diff contained.** factories emits a sentinel path, receipt copies every
   finding into anchors, verify requires anchors to be real diff paths.
   Each is correct on its own and each was reviewed on its own. No review
   pass has "the composition" as its window.
3. **Mocks encode the author's belief about the collaborator.** A mocked
   mutation runner returns what its author thinks the real one returns. It
   cannot report that the real one never started.
4. **The instrument cannot measure itself.** Demonstrated live while fixing
   the last of these: the review of the anchor fix hit a backend timeout
   and would have been voided by the very defect it was reviewing, had the
   engine not been pinned to the fixed code.

Worth stating plainly so the trend is not misread: the discovery rate rose
because the instruments got sharper, not because quality fell. Bug
injection is what proved the daemon fix; verify's check 5 caught an
excerpt misnumbering; check 7 caught a set of test cycles that were too
similar to each other. Most of what is being found predates the discipline
finding it.

### The proposal, and the alternatives it beat

Proposed: any plan item that adds a gate, a check, or a measurement states
its **silent-failure signature** -- what the output looks like when it runs
and finds nothing, beside what it looks like when it never ran. If those
two are identical, that is a design defect to resolve before implementing,
not a test to add afterwards.

Alternatives considered:

- **Do nothing; the bug-injection rule already covers it.** Partly true:
  injecting at the fix would have caught the l2_runner default. But
  injection happens at implementation time, after the design that made the
  two states indistinguishable is already committed to. It catches the
  instance, not the class.
- **A mechanical lint instead of a checklist item.** Flag a default
  implementation returning an empty collection where the real one returns
  findings. Un-skippable, but narrow: it would have caught l2_runner and
  neither of the others.
- **The stronger form: make "never ran" unrepresentable as success.** The
  real cause is that "ran, found nothing" and "never ran" share an
  encoding. Fixing that is a type change, not a process rule -- and forge
  already has the pattern: receipts carry `pass_status`
  (completed/timeout/...) precisely so an L1 pass that never reached the
  model is distinguishable from one that returned nothing. The mutation
  gate and the L2 runner have no equivalent. This is the better fix and it
  should be scoped as its own item; the checklist line is the cheap
  approximation that covers the cases the type change has not reached yet.

Both are worth doing, and the ordering was settled by an accident while
this section was being written rather than by argument. The check gating
this very document was written as

    grep -nP '[^\x00-\x7F]' $C | head -3 && echo FAIL || echo "OK clean"

which reports FAIL unconditionally, because a pipeline exits with the
status of its last stage and `head` returns 0 on empty input. It reported
a non-ASCII failure on a file containing none.

That footgun was already documented, in an indexed memory file, phrased in
terms of this exact check, with a prior instance recorded. Being written
down did not prevent it, and the author of the write-up was the one who
tripped it. Three correct versions of the same check appear minutes
earlier in the same session, so the difference was not knowledge.

So: the type-level change first, wherever a gate is being designed, and
the plan-time question only where no type change is available. A written
rule is the weakest enforcement there is, and this section now contains
its own counterexample. Where a wrong shape recurs, prefer making it
mechanically detectable -- for this one, a PreToolUse hook matching a
pipeline that ends in head/tee/cat/sort and feeds an `&&`/`||` branch.

### Scope question, unresolved

The evidence is entirely from forge. The rule as written would go into the
global Implementation Hygiene Checklist in ~/CLAUDE.md, which currently
checks artifacts -- branch refs, stray files, memory sync -- and nothing
about observability. Promoting a rule to global on one project's evidence
is the over-generalisation this workspace's own rules warn about. Suggested
route: adopt it in forge's phase planning first, and promote it only after
it has caught something in a second project, or been shown to cost nothing
when it catches nothing.

### The scope question answered the same day, from a second codebase

The condition above was "promote it only after it has caught something in a
second project". That happened within hours, and the sharper version of the
signature it produced is worth more than the count.

Two more instances turned up while debugging why a review could not converge:

Forge. `derive_pass_outcomes()` works out which L1 passes completed and which
timed out or errored, and `write_receipts` stores the answer in every receipt
as `pass_status`. Measured on disk: 204 receipts, all carrying the field, 15
of them recording a pass that failed. Two places needed it and neither read
it -- `verify.py` attested cycles containing a pass that never ran, and the
round loop kept spending rounds on a run whose clean-round counter was being
zeroed every round by that same failure. Alongside it,
`TimeoutCircuitBreaker.record_other_error()` is literally `pass`: a method
that exists, is called from three sites, and does nothing, so the breaker
whose own message says "review cannot converge" cannot see the most common
way a review fails to converge.

OmniRoute, a different codebase with different authors.
`rateLimitManager.ts` rewrites a queue-expiry error specifically so it will
not be "misdiagnosed as a provider outage", and tags it
`code = "RATE_LIMIT_QUEUE_TIMEOUT"` -- the comment says the tag is there for
classification. Nothing classifies on it. The error surfaces as a 503, 503 is
in the set that trips the provider circuit breaker, and so the proxy records
its own queue giving up as the provider failing.

### The sharper signature: the producer shipped, the consumer did not

The first three instances were described as "reports success while doing
nothing". These four are more specific and easier to look for:

    a signal is computed, named, and persisted -- and no consumer reads it

That shape is worse than a missing signal, because the code *looks* handled.
Someone did the hard part. The field is in the receipt, the tag is on the
error, the method is on the class and is being called. Review reads that as
covered. Only the question "who reads this?" separates the two, and it is a
question no reviewer asks unprompted, because the presence of the producer is
what makes it feel answered.

It is also cheap to check mechanically, which the earlier framing was not.
For a field or an error code: grep the name and look at whether every hit is
a write. For a method: read the body. Both are seconds of work, and both
found real bugs today in code that had already passed review.

Two consequences for the proposal above. The scope question is answered:
evidence now spans two independent codebases, so the rule is not a
forge-shaped rule. And the plan-time question gets a second, more concrete
form to sit beside "what does this look like when it silently does nothing":

    if this change produces a signal, name its consumer -- and if the
    consumer is a later phase, say so, because a signal with a scheduled
    consumer and a signal with no consumer look identical on disk

### Watch this rule for its own failure mode

The rule cannot be allowed to become a demand that every value have a reader
before it is written. Recording a field for later, or for a human reading
receipts, is legitimate; `covered_line_ranges` in the receipt schema is
exactly that and its comment says so. The distinction is whether something
*depends* on the signal being acted on. `pass_status` had a gate that was
wrong without it. `covered_line_ranges` does not. Asking "who reads this"
should surface that difference, not punish the answer "nobody, deliberately".

## Non-overlap with other lanes (checked 2026-08-01)

Grepped e2e_runner/l2_runner/build_e2e_checker and timeout/mutation
strings across ROADMAP.md, v2.8tail-v3-DISPATCH-SCHEDULE.md,
v2.9-V3-GROUNDTRUTH-SCHEDULE.md, MILESTONES.md. Only hit: MILESTONES.md:87
describes l2_runner's FOUNDING design ("wired after L1"), not a live
claim on this gap. No collision.

## Process

Items 1-3 are logic-bearing (control flow / gate wiring) -> full 3-cycle
review + bug-injection per this project's own gate, same as this
session's l2_runner fix. Item 4 is investigation-only until it produces a
mechanism; then re-scope as its own item. Items 5-6 are small enough for
R2 proportionality judgment at dispatch time.

## Not doing (explicit)

- Not re-running the fix-receipt-ts panel a third time on the same
  engine/diff pairing (debugging circuit breaker, memory section 5) --
  item 1 above is the prerequisite for a clean re-run, not the re-run
  itself.
- Not assuming item 3's fix is "make it 900s" -- the charter records the
  question (should L2's flaky-guard budget equal L1's correctness
  budget?), not a predetermined answer.
