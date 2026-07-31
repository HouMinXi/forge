# Review order: forge verify gate, three defects on one branch

You are reviewing a git diff. Your output is a review, not a patch. Do not
edit files. Do not run git commands that change state.

## Scope fence

Review ONLY the diff in `diff.patch` (branch `defects/verify-gate` vs its
merge base). Two files:

    src/code_forge/verify.py     118 lines changed
    tests/test_verify.py         365 lines changed

Everything else in the repository is out of scope. If a problem you see lives
in an unchanged file, say so explicitly and mark it OUT-OF-SCOPE rather than
counting it as a finding against this diff.

## What the code is

`code_forge/verify.py` is a commit gate. Before a commit is allowed, a
reviewer model must have produced nine "receipts" (3 review cycles x 3
passes). Each receipt carries `code_excerpts`: quoted source lines the
reviewer claims it read, each tagged with `file`, `start_line`, `end_line`,
`content`. `run_verify` checks those receipts against the actual diff. Its
purpose is anti-fabrication: proving the reviewer really looked at the code
rather than inventing plausible commentary.

The checks run in order, and ANY failure fails the whole attestation:

    1. all nine cycle/pass slots present
    2. every receipt's diff hash matches the diff being committed
    3. anchor files appear in the diff
    4. timestamps monotonic
    5. STEP A: each hunk is witnessed by >=1 overlapping excerpt
       STEP C: each excerpt's content matches the diff post-image
    6. coverage floor

## The three changes under review

**(a) Which cycles count.** Previously verify demanded cycles numbered
exactly 1, 2, 3. A review that ran four cycles (because cycle 3 surfaced a
finding and reset the counter) had receipts c1..c4 and failed with "missing
cycle/pass combinations" even though three consecutive clean cycles existed.
The change accepts the LAST three consecutive cycles.

**(b) Out-of-hunk excerpts.** Previously one excerpt whose lines fell outside
any hunk failed the entire attestation.

**(c) Excerpt anchor tolerance -- STEP C.** This is the change I am least
sure of and want attacked hardest.

Measured problem: on a real review of a real diff, 21 of 78 excerpts failed
STEP C. Inspecting them, the content was genuine file content, but
`start_line` named a line 1-2 above where the quoted block actually began.
Cause: the reviewer sets `start_line` to where the CHANGE starts and quotes
`content` from where the readable code starts; the blank lines that separate
Python definitions sit between the two. Nothing in any prompt ever stated
that the two must be the same anchor.

The change: instead of comparing only at `start_line`, try shifts
(0, 1, -1, 2, -2, 3, -3) and accept the excerpt if the WHOLE quoted block
matches contiguously at any one of them.

The anti-fabrication argument for accepting this, which you should try to
break: fabricated content matches at NO shift, because the whole block must
match line-for-line against the post-image; a model that can reproduce real
file content has read the file, which is the property STEP C exists to
establish; the bounded window only makes the pointer slightly imprecise.

## Ground truth already established (do not re-derive, but you may challenge)

    pytest full suite         2994 passed, 4 skipped
    ruff                      clean
    non-ASCII gate            clean
    bug injection at (c):
      shift set -> (0,)                 "accepted" test FAILS   (window load-bearing)
      shift set -> (0,1,-1,2,-2,3,-3,6,-6)
                                        "beyond window" test FAILS (bound load-bearing)
    known-answer run on 78 real excerpts:
      old STEP C  57/78 pass
      new STEP C  63/78 pass
      residual 15 failures are a DIFFERENT cause (the reviewer double-escapes
      backslashes when serializing source lines into JSON). Out of scope here.

## What I want from you

Attack the diff. In priority order:

1. Can a FABRICATED excerpt now pass STEP C that would have failed before?
   Construct the concrete input if you think so.
2. Does change (a) admit a receipt set that is not actually three
   consecutive clean cycles? Cycle numbering, gaps, duplicates, ordering.
3. Does change (b) open a path where an attestation passes with no real
   coverage?
4. Correctness bugs in the new code: control flow, off-by-one, dead code,
   unreachable branches, error messages that name the wrong line.
5. Do the new tests actually bite? A test that passes for the wrong reason
   is worse than no test. Name any assertion that would stay green if the
   code under it were broken.

## Output contract

For each finding:

    SEVERITY: BLOCKER | MAJOR | MINOR | NIT
    FILE:LINE: <path>:<line in the NEW file>
    CLAIM: one sentence, what is wrong
    FAILURE: concrete input or sequence -> concrete wrong outcome
    EVIDENCE: quote the exact lines from the diff you are relying on

End with a single line:

    SCORECARD: blocker=<n> major=<n> minor=<n> nit=<n>

## Honest failure is pre-authorized

Zero findings is an acceptable and expected answer. Do not manufacture
findings to look thorough. If you cannot construct a concrete failing input
for a suspicion, report it as a QUESTION section at the end, not as a
finding. If you did not or could not examine something, say so plainly.
Inventing a file:line that does not exist in the diff is the worst possible
outcome and will be checked.
