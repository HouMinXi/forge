# Check 6 exemption mechanism (backlog, filed 2026-08-15)

Not implemented -- filed from the charter-6 decision
(43.1-DECISIONS-20260815.md). The 60% review-evidence floor stays as
is, test lines included; this item adds the industry-standard relief
valve for structurally expensive diffs.

## What

A per-run reason field (footer on the review or a gate.yaml key) that
lowers or waives the check-6 floor for named categories, modeled on
Chromium's Low-Coverage-Reason (code_coverage_in_gerrit.md):
TRIVIAL_CHANGE, TESTS_ARE_DISABLED, TESTS_IN_SEPARATE_CL,
HARD_TO_TEST, COVERAGE_UNDERREPORTED, LARGE_SCALE_REFACTOR,
EXPERIMENTAL_CODE, OTHER.

## Why this shape, not a lower number

- A fixed lower number for test lines is an invisible, unauditable
  bypass -- every diff quietly gets the discount.
- A named-reason exemption is explicit and reviewable: the reason
  travels with the run and shows up in verify output.
- Precedent: Chromium keeps the default gate honest and the exception
  visible; Google's SWE book warns fixed numeric bars become ceilings.

## Implementation notes (for whoever picks this up)

- verify.py check 6 currently reads only the receipts; the reason must
  be plumbed from the review invocation (CLI flag -> state/receipt) so
  verify can see it.
- Decide the categories first; do not ship an open-text field (that is
  the invisible bypass again).
- Failure surface: when the exemption applies, say so in the verify
  result instead of silently skipping -- "check 6 waived: HARD_TO_TEST"
  must be visible in the same place the FAIL would have been.
