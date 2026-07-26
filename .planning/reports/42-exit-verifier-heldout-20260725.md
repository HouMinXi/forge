# Phase 42 rework -- EXIT verifier (HELD OUT)

**DO NOT hand this file to mimo-pro or include it in any phase context bundle.**
It lives in `.planning/reports/` rather than the phase directory for exactly
that reason. The work order is
`.planning/phases/42-cli-key-claim-type/42-REWORK-ORDER.md`; sections H1-H7
below are deliberately absent from it.

**Frozen:** 2026-07-25, BEFORE the reworked plan exists (pre-registration).
**Baseline:** planning-local snapshot `33356a56fd7c`.
**Why frozen now:** last round the PM's held-out checks were stated only in
conversation and never persisted, which is an S2 gap. A verifier written after
seeing the delivery cannot be distinguished from a verifier fitted to it.

## Part 1 -- mechanical gates (deterministic, stated in the order)

Run from repo root. Every one must hold.

    P=.planning/phases/42-cli-key-claim-type/42-02-PLAN.md

    # G1  the false claim is gone
    test $(grep -c 'ONLY test that catches' $P) -eq 0

    # G2  non-ASCII clean, both plans
    test $(grep -cP '[^\x00-\x7F]' $P) -eq 0
    test $(grep -cP '[^\x00-\x7F]' ${P/42-02/42-01}) -eq 0

    # G3  the duplicated injection collapsed to one statement
    test $(grep -c 're-add `axis_claim="review"`\|replace `derive_claim_type(f.source).type` back' $P) -eq 1

    # G4  a real-path test is actually specified, not just promised
    grep -q '_write_ledger_rows' $P && grep -qE 'test_machine_ledger|test_realpath_ledger|StateMachine\(' $P

## Part 2 -- held-out adversary checks (H1-H7, NOT in the work order)

### H1 -- the fix that does not fix (sharpest check)

The new real-path test must assert on BOTH an L0 and an L1 finding, and on
BOTH `axis_claim` and `version_sensitive`.

Rationale: the target mutation is `derive_claim_type(f.source)` ->
`derive_claim_type("L1")`. A real-path test that feeds only an L1 finding
still passes under that mutation, because L1 is what the mutation hardcodes.
Such a test would satisfy the letter of Item A while leaving the exact hole
Item A exists to close. Both existing ledger tests default to `source="L0"`,
so the L0 half is nearly free and the L1 half is the one likely to be skipped.

FAIL if: only one source value is exercised, or only `axis_claim` is asserted
while `version_sensitive` is not.

### H2 -- the new test must itself be proven

Golden Rule 2: a new assertion is unproven until the bug it targets is
injected, observed to FAIL, reverted, and observed to PASS. The plan must add
an injection step naming the NEW test and the `derive_claim_type("L1")`
mutation specifically.

FAIL if: the new test is added with no injection step, or the injection step
reuses the old Test 13 mutation (re-hardcoding the output string) -- that one
is already covered and proves nothing about the new test.

### H3 -- scope containment

    git diff 33356a56fd7c -- .planning/phases/42-cli-key-claim-type/ --stat

FAIL if any file other than `42-02-PLAN.md` changed, or if hunks in
`42-02-PLAN.md` fall outside Items A/B/C. Watch specifically for opportunistic
edits to the frontmatter, the threat model, or Task 1.

### H4 -- delta note against the real diff

Read the delta note, then read the actual diff. Every claimed change must
appear in the diff, and every diff hunk must appear in the note.

FAIL if: the note claims a change the diff does not contain (fabricated
completion -- the documented mimo-pro reporter failure mode), or the diff
contains a change the note omits (undisclosed edit).

### H5 -- Test 13 must survive

The order says Test 13 becomes supplementary, not removed. Confirm all three
assertions (a) import, (b) no literal `axis_claim="review"`, (c)
`version_sensitive` present are still specified.

FAIL if: Test 13 was deleted or reduced, i.e. the fix traded one guard for
another instead of adding one.

### H6 -- confirmation round evidence

Results must land in `cp-artifacts/`, not `/tmp`. Check each result file's
byte size before reading its verdict.

FAIL if: any result file is 0 bytes and was counted as clean. This exact
false-green already occurred in this phase (`p42-r3-kimi.md` and
`p42-r3-kimi-v2.md`, both empty; see `cp-artifacts/INDEX.md`). An empty file
is the absence of a review, never a clean verdict.

FAIL if: the confirmation prompt does not carry the three non-convergence
elements, or omits the note that kimi's "Test 13 correctly covers" is only
partially valid -- without it kimi re-confirms its prior position and the
round is worthless.

### H7 -- frontmatter drift

If the new test extends an existing file, `files_modified` must list it.

FAIL if: the plan modifies a file its own frontmatter does not name. This is
the same defect class kimi caught in R2 (`ledger.py` missing from
`files_modified`); recurrence means the fix did not generalise.

## Part 2b -- round 2 held-out checks (H8-H10, NOT in REWORK-ORDER-2)

Added 2026-07-25 after round 1, frozen before the round-2 delivery exists.
Round 1 outcome for the record: G1-G4, H2, H5, H7 passed; H1 failed exactly
as predicted (single-source test, mirror mutation survives); H4 caught a
delta-note line that the diff does not support; H6 failed on placement.

### H8 -- per-row assertion, not per-position

If both findings are written into one ledger, each row's claim must be
asserted against its own identity (fingerprint), not against a list index.

    rows = list(iter_rows(tmp_path))
    by_fp = {r.fingerprint: r for r in rows}     # correct
    assert rows[0].axis_claim == "lint"          # positional, fragile

Rationale: `iter_rows` order is a file-append artifact, not a guaranteed
contract. A positional test can pass for the wrong reason today and flip on
any reordering, and if both rows were somehow written with the same claim a
positional assertion could still land green on one of them. This is the same
class of defect as the single-source gap: an assertion that appears to cover
two cases while actually pinning one.

FAIL if: positional indexing is used where two rows carry different expected
claims, without an accompanying assertion that ties each row to its source.

**Scope the check to the new test, not the file.** `by_fp` already exists at
`tests/test_machine_ledger.py:109` inside an unrelated pre-existing test, so
a file-wide grep reports PASS no matter what the executor writes. This gate
was vacuous on its first known-answer run for exactly that reason. Extract
the behavioural test's own span first:

    awk '/def test_write_ledger_derives_claim_type/,0' tests/test_machine_ledger.py \
      | grep -cE 'by_fp|\{r\.fingerprint: r for r in'

Note also that the per-row idiom is already this file's local convention
(line 109), so satisfying H8 requires following sibling style rather than
inventing anything. That makes a positional assertion in the new test a
convention-adherence miss, not merely a stylistic preference.

### H9 -- the mirror injection must be proven, not just documented

Item D asks for a fourth injection (`derive_claim_type("L0")`) AND a real
run showing the L1 assertion red while the L0 assertion stays green.

FAIL if: Step 4 gains the fourth injection as text while the delivery
reports it as verified without pytest output, or with output that does not
show the asymmetry (L1 red, L0 green). That asymmetry is the entire claim --
output showing both red would mean something else broke.

PASS is also achievable by an explicit, reasoned "deferred to GREEN because
the wiring does not exist yet". The order pre-authorises that. Treat a
fabricated transcript and a missing one very differently: the second is
honest, the first is the documented mimo-pro failure mode.

### H10 -- did the delta note get re-checked after the final edit

Round 1's note was off by exactly 4 lines on both anchors, consistent with
edits made above them after the note was written, and claimed a
trailing-newline fix that never happened.

Re-verify every line number in the round-2 note against the final file, and
re-run `tail -c1 <file> | od -c` if the note again claims a newline fix.

FAIL if: any anchor is off, or any claimed change is absent from the diff.
A note written before the last edit is not a note of what shipped.

## Part 3 -- PM discipline for this verification pass

Two failures of mine this session, both the same shape: asserting that
something does not exist from a view that was truncated. First a `Read` window
that ended two lines before the guard; then a `find` piped through `head -40`
whose tail I read as the end of the list.

Rule for this pass: **no absence claim from a paginated, truncated, or limited
view.** Any "X is not there" must come from an unbounded enumeration, and the
command that produced it gets quoted alongside the claim.
