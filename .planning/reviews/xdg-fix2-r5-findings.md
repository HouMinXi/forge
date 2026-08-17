# xdg-fix2 R5 review -- INFRA FAIL + test-assertion findings (2026-08-14/15)

Backend: deepseek-direct. Job cdf0821e-6c11-47d2-b78c-9aebf2cdfd2f,
duration 2960s wall.

## Outcome

FAIL, but zero L1 code findings: all three passes died as INFRA --
"deepseek-direct backend exceeded total read deadline" after 2400s
with 0 tokens returned (model deepseek-v4-flash per tokenCost block).
The diff grew past R4's (1012 insertions + post-image of every changed
file) and the paid direct backend never answered a single prompt.
Advisory axes (RUNTIME/DAEMON-STATE) ran and produced 12 advisories,
all descriptive of the intentional changes; the runtime smoke summary
reports 0/7 surfaces verified (no smoke receipts for this diff).

Disposition: rerun on the fallback backend (sn-deepseek-flash, per the
standing fallback rule for INFRA trouble with deepseek-direct).

## test-assertion findings (deterministic, valid regardless of LLM state)

1. TestCIContinuation asserts _continuation_round_index() in isolation;
   never exercises the CI path that calls
   _execute_round(round_index=self._continuation_round_index()) and
   never asserts receipt files survive across separate invocations.
   An off-by-one in the caller would pass these helper-only assertions.
2. The HOLD-loop test only adjusts the load_state mock count for the
   extra cost-line load; it never asserts the new PENDING behavior
   (cost line emitted, PENDING continues into HOLD). A regression that
   skips the cost line or returns immediately on PENDING goes unseen.
3. Misnumbering coverage only exercises a negative offset (-1).
   _constant_offset searches the full range and the message uses %+d
   for positive offsets, so +N detection is untested.
4. The no-backend static-rules test asserts static findings and the
   skipped finding but does not assert llm_invoke is never called;
   also missing backend=None + empty-diff (returns [] before the
   backend branch).
5. The OpenAI length-below-ceiling test checks the message text but not
   the structured fields callers branch on (kind='truncated',
   retryable=False); missing out_tok == cap and out_tok == 0
   boundaries.

All five are test-strengthening changes; fixed with bug-injection in
the R5-fixup pass, then a rerun on sn-deepseek-flash.

## R5-fixup disposition (all fixed, all bug-injected)

1. Added test_ci_run_does_not_overwrite_a_foreign_diff_receipt: full
   machine.run() with a foreign cycle-2 receipt on disk; asserts the
   foreign file survives byte-for-byte and this run writes c3p{1,2,3}.
   Injected: _run_ci hardcoded round_index=0 -> test fails. 14/14.
2. Added TestPendingContinuesToHold: PENDING -> cost line printed to
   stderr AND run_hold_ui called. Injected: the if-True regression
   restored -> hold_called assertion fails. 5/5.
3. Added test_misnumbered_excerpt_reports_positive_offset (claims 1-2,
   content is lines 2-3, expects "+1" in reason). Injected: offset
   search clamped to range(lo, 0) -> test fails. 139/139.
4. Static-rules test now patches llm_invoke and asserts not called;
   added test_no_backend_empty_diff_returns_empty_without_llm ([] + no
   call + no infra_errors). Injected: llm_invoke(q1_prompt) leaked
   into the no-backend branch -> assert_not_called fails. 27/27.
5. Clamp test now asserts kind=="truncated" and retryable is False;
   added at-ceiling and zero-output boundary tests asserting the
   generic message. Injected: upper bound 0<out_tok<=cap -> at-ceiling
   fails; lower bound 0<=out_tok<cap -> zero-output fails. 249/249.
