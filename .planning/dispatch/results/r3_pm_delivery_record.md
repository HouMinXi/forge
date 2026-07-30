# receipt-followups R3 -- PM delivery record

Written by the PM from its own measurements, 2026-07-29. Every number here
was produced by a command this session ran, not copied from the executor's
report. That report lives only in the worktree's `.planning/`, which is a
plain directory rather than a symlink, so it dies with `git worktree remove`;
this file is the part that survives.

## What landed

    branch  fix/receipt-followups-2
    base    891772a
    tip     017eb39
    range   9 commits, 9/9 Signed-off-by, author Minxi Hou <houminxi@gmail.com>

    017eb39  tests: rename work-order IDs to behaviour descriptions
    02ccfe0  tests: drop inert semgrep IPv4 workaround
    ef3d961  tests: force IPv4 in semgrep tests to avoid registry timeout
    e605b26  hooks: capture verify output and replay on failure
    b590827  tests: cover receipt guard, inverted ranges, _covered shapes ...
    3415673  cross_repo: route receipt loading through guarded _load_receipts
    8d709ed  verify: tolerate both dict and string shapes in _covered
    750e312  verify: reject inverted excerpt ranges in schema validation
    1b108b7  verify: remove dead write_attestation and unused hashlib import

`ef3d961` is still in history; `02ccfe0` reverts it. The count is 9, not the
8 the executor's report settled on after its correction pass.

## Gate results, run by the PM

    heldout_r3.py (outside the repo, never named in the order)   7 pass / 0 fail
    verify_receipt_followups_r3.sh                              13 pass / 1 fail

Raw output: `r3_heldout_delivery_017eb39.txt`, `r3_verify_delivery_017eb39.txt`.

The single FAIL was the PM's own defective check, not a defect in the
delivery -- see the flip below. Under the corrected gate the same tree gives
14 pass / 0 fail.

## What was verified, and how

    item E   git diff main..HEAD -- tests/test_taint_rule.py  ->  empty
             git grep GAI_OVERRIDE (whole tree)               ->  no match
    item F   three classes renamed, zero old names remain
             git grep -nE 'Item ?[0-9A-D]' -- src tests       ->  0 hits
             collected 3011 before and after; 2946 test function
             names compared as a SET against a baseline frozen at
             ef3d961, none lost
    no regression
             R2 adversary still 10/0 (item A intact)
             R1 adversary still clean (items 1-4 intact)
    suite    3002 passed, 9 skipped

The item F check matters only because the pattern was fixed before dispatch.
`Item ?[0-9A-D]\b` finds 4 of the 7 leaks: `TestItem1CrossRepoGuard` puts a
letter straight after the digit, so the trailing word boundary never matches
and all three class names escape. Without that fix a docstring-only rename
would have passed.

## FLIP DISCLOSED (S1)

`verify_receipt_followups_r3.sh` section G tested `grep -qi semgrep` across
the whole report and produced a FAIL against 017eb39. The executor argued the
FAIL was wrong; the PM verified the argument independently and adopted it.
The order asked for the SECTION describing the reverted work to go, but item E
has to explain WHY the revert happened and that explanation necessarily names
semgrep, so a correct report failed. The check now asserts on the heading
(`^## +Semgrep fix`), which an author controls deliberately, and was
two-sided validated: passes the delivery, passes a report mentioning semgrep
only in a revert justification, fails an R2-style report carrying the section.

`r3_verify_delivery_017eb39.txt` preserves the pre-fix 13/1 run.

## filter-branch

Four `filter-branch` runs appear in the branch reflog; the executor's report
discloses three. One was in R2 (abandoned -- the branch was reset to 3415673
and those commits redone by hand), three in R3 on the revert commit.

The substantive claim, that only commit messages changed, is confirmed by
tree hashes: `ee428c6` and `02ccfe0` share tree `de53ca0a47d4`, `3f5b173` and
`b590827` share `67e460b913bc`, `94d0986` and `e605b26` share `de53ca0a47d4`.
Identical tree objects mean byte-identical content. The seven commits that
were never rewritten also retain their original SHAs, which is the same proof
by a different route.

## Where the executor's report and ground truth diverge

Recorded because the report itself will not survive the worktree.

    report says              ground truth
    8 commits                9
    all 8 commits signed     the gate printed "all 9 commits signed off"
    revert is 1f0f760        02ccfe0; 1f0f760 is a dead intermediate from
                             the filter-branch chain and is not on the branch
    three filter-branch      four

The correction pass fixed the 14/0 total, the non-ASCII character, and added
the filter-branch disclosure. It also changed the commit count from 9 to 8,
which traded a visible internal inconsistency (prose said 9, list showed 8)
for an internally consistent falsehood -- strictly harder to catch. The
`1f0f760` SHA was the defect with real cost and it was never on the
correction list, even as the new disclosure section on the same page
documented the very rewrite that invalidated it.

None of this touches the code. Both gates were run against the tree, not the
report.
