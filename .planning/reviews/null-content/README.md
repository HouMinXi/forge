# Review evidence -- llm_invoke null-content + stream-flag fixes

Merged to main as a47d888 (stream flag) and 695f739 (null content),
fast-forward from 933032d on 2026-07-30.

## What is here

`receipt-c1p1.json` .. `receipt-c4p3.json` (12 files)
  The null-content review. Four rounds, CI mode, deepseek backend, all
  bound to diff_sha256 536a7c92. Rounds 1 and 2 raised five findings I
  accepted and fixed. Rounds 3 and 4 raised nine more; I rejected all of
  them, two by direct experiment (the anthropic/vertex KeyError claim and
  the isinstance short-circuit claim were both run and both false).

  Read these receipts as a record of findings raised, not as a clean
  attestation. `findings_count` is 2-7 on every receipt and c4p3 carries
  `pass_status: error`. The adjudication that made the change shippable
  was mine and is not in the receipts.

  These receipts describe an earlier revision of the code. The fixes from
  rounds 1 and 2 landed after they were written, so their excerpt content
  no longer matches the committed file. That is the anti-tamper design
  working, not corruption.

`review-f1-stream-run.log`
  The stream-flag review, three rounds, LOCAL mode, bounded with
  FORGE_MAX_TOTAL_ROUNDS=3. Nine passes, 257771 tokens, 77.6s wall. The
  five test-assertion advisories at the end were read and not acted on;
  the one with substance (asking for a stream=True case) was already
  covered by test_stream_true, which predates this change.

## What is missing, and why

The nine receipts that log describes are gone. They lived in
`.worktrees/null-content/.code-forge/receipts/` and went with the
worktree when it was removed after the merge. Only the run log survives.

Those nine were the only receipt set in this work that verified cleanly
against final code, so their loss is the real gap here: what is archived
is the messier evidence, and the tidy evidence is what did not survive.
Copy receipts out of a worktree before removing it.

## Related

The stream-flag review was also where forge's own attestation gate
rejected a sound receipt set twice. Both defects are filed as separate
work with a dispatch order under `.planning/dispatch/`.
