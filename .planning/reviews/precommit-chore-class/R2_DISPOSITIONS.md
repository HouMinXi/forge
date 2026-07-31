# R1 dispositions -> R2 review packet

New diff for round 2: r2.diff (supersedes r1.diff). Ground truth:
/home/houminxi/code/forge/.worktrees/precommit-chore-class, staged changes.

GM round 1: dispatch failed at the transport layer (Error parsing
response: 'choices'), no findings produced. Retried in this round.

## kimi findings

**MINOR setup-vscode.md orphaned "versus the baseline."** -- CONFIRMED.
The inserted section cut item 4's sentence in half. Fixed by moving the
declared-class section below the complete numbered list (setup-vscode.md
now reads "...no new failures versus the baseline." then the new section).

**MINOR presubmit-preservation untested for declared commits.** --
CONFIRMED, same finding as ds MINOR 2 (count once). Added test (o)
test_declared_commit_still_runs_presubmit: generated hook installed
with a presubmit entry whose command always fails; a declared commit
is blocked. Bug-injection verified: moving declared-exit before
presubmit turns exactly that test red; restored.

**NIT AI-vocab gate untested for declared commits.** -- CONFIRMED.
Added test (q) test_declared_commit_still_hits_ai_vocab_gate: a banned
word in a staged .py diff blocks the declared commit.

**NIT docs/wip values and chain variant untested.** -- CONFIRMED, both
cheap. Added test (s) docs-class value declares, and test (r) chain
variant contains the declared blocks.

## deepseek findings

**MINOR 1 review-skip untested.** -- CONFIRMED. Added test (p)
test_declared_commit_skips_review_block: stub passes verify, fails
review; undeclared code commit is blocked (so review does run in the
full path), declared commit passes (so declared skips it).
Bug-injection: moving declared-exit after review turns exactly that
test red; restored.

**MINOR 2 presubmit-preservation untested.** -- CONFIRMED, dup of kimi's
second MINOR; disposition above.

**MINOR 3 env inheritance risk.** -- PARTIALLY ACCEPTED. A FORGE_
COMMIT_CLASS exported into a shell profile widens the skip to later
logic-bearing commits from that shell with no trace. We will not add a
mechanism (commit-message coupling was rejected in design phase as
over-coupled; env is the API). Mitigation landed: docs now explicitly
warn never to export it, and the generator comment states "one value,
one commit". The echo already prints the class per commit. Residual
risk accepted: the staged-diff text gates and presubmit linters still
run on every commit and cannot be disabled by this variable.

**NIT 4 echo omits chain.** -- CONFIRMED. Echo now reads
"skipping verify/review/chain/gate-check".

**NIT 5 "same vocabulary" imprecise.** -- CONFIRMED. Comment now reads
"the class names the session-side trailing # class marker convention
uses" (4 classes, not all 6 session markers).

## Verification delta since R1

- tests: 96 passed across test_install_hooks, test_hook_failclosed,
  test_hook_carveout (was 91).
- Bug-injection total: 4 sites, each with exactly-one-test red on
  injection, restored byte-identical after.
- Full suite rerun in progress.

## For round 2

Confirm fixes; look for anything new introduced by r2.diff itself
(test helpers/fixtures/docs wording); re-issue MAJOR/MINOR/NIT counts.
Round 2 also: please sanity-check docs/setup-vscode.md context around
the moved section.
