# Phase 48 Injection Matrix Record

Process evidence for the phase's bug-injection proofs (inject -> FAIL ->
revert -> PASS). Every injection was executed AT the fix site -- the
exact guard line, call site, or wiring argument -- never a distant
proxy. Per house discipline, injections are recorded here, not in
commit messages.

## Injection matrix I1-I9 (plan + amendments)

| ID | Injection (at the fix site) | Proving test | Inject result | Revert result |
|----|------------------------------|--------------|---------------|---------------|
| I1 | Moved the `isinstance(exc, _TruncatedResponse)` branch to AFTER the retryable gate in `_invoke_api`'s except handler | test_continuation_success | FAIL: `_TruncatedResponse` raised straight out of the dispatch (retryable=False gate re-raised before recovery ran) | PASS |
| I2 | Deleted the `"{" not in truncated.content` clause from the zero-output guard | test_no_brace_partial_raises_no_continuation | FAIL: StopIteration -- a continuation was issued against the prose partial, consuming the side_effect sentinel | PASS |
| I3 | Removed the `first_emitted` flag guard in `_read_sse` (emit on every content chunk) | test_first_token_emit | FAIL: `assert 2 == 1` -- two events fired for two content chunks | PASS |
| I4 | Removed the trip raise from `TruncationBreaker.record_truncation` | test_breaker_trips_and_check_tripped | FAIL: `Failed: DID NOT RAISE TruncationBreakerError` | PASS |
| I5 | Dropped usage summation in the helper's return (returned `Usage(0, 0)`) | test_continuation_success | FAIL: `assert Usage(0,0) == Usage(805, 16404)` | PASS |
| I6 | Replaced the exhaustion message with a generic `"output truncated at provider cap"` | test_continuation_exhausted | FAIL: regex `continuation exhausted after 2 attempts` did not match | PASS |
| I7 | Reverted `_run_pass`'s llm_invoke call to not pass `continuation_breaker` | test_provider_passes_breaker | FAIL: `KeyError: 'continuation_breaker'` | PASS |
| I8 | Deleted the `breaker.check_tripped()` call at the retry-loop entry | test_pre_tripped_breaker_raises_before_dispatch | FAIL: dispatch proceeded into the spy (`ValueError: not enough values to unpack`) | PASS |
| I9 | Deleted the `except TruncationBreakerError: raise` clause in the helper's outer try (A-17: deletion, not swapping) | test_trip_propagates_not_budgeted | FAIL: trip swallowed -- final error was `continuation exhausted after 2 attempts` instead of TruncationBreakerError | PASS |

## Amendment-driven extra injections (not in the original matrix)

| ID | Injection | Proving test | Inject result | Revert result |
|----|-----------|--------------|---------------|---------------|
| EXTRA-1 (A-14) | Deleted the `isinstance(truncated.content, str)` clause from the zero-output guard | test_non_str_partial_raises_no_continuation | FAIL: `AttributeError: 'int' object has no attribute 'strip'` | PASS |
| EXTRA-2 (A-12) | Deleted the `not result.is_truncated` condition in the fold's `continuation_breaker.record_success()` call | test_fold_records_success_only_for_non_truncated | FAIL: `assert 0 == 1` -- a recovered result reset the count | PASS |

## Honest non-FAIL injection outcomes

- **I4b (T2, optional lock variant):** removed the `with self._lock:`
  from `record_truncation` and ran test_breaker_thread_safe_increments.
  Result: **PASSED** in that run -- 80 increments across 8 threads did
  not hit the lost-update window. This injection is probabilistic, not
  a proof of absence; the deterministic raise-removal (I4) is the
  recorded proof for the breaker. Recorded as-is per S1 (no silent
  flips, no invented failures).

## RED-phase observations (green-at-RED tests, recorded honestly)

- **T1 RED:** both new tests FAILED (`AttributeError: module
  'code_forge.llm_invoke' has no attribute 'progress'`) -- clean RED.
- **T2 RED:** all 6 new tests FAILED (ImportError: symbols did not
  exist) -- clean RED.
- **T3 RED (amended set):** 10 failed, 3 passed. The 3 passing are the
  zero-output guard tests (test_zero_partial_raises_no_continuation,
  test_no_brace_partial_raises_no_continuation,
  test_non_str_partial_raises_no_continuation): with no recovery code
  present, a truncation raises the original error after exactly one
  call -- the same observable the tests lock. Their bug-injection proof
  is I2 and EXTRA-1, executed at GREEN time.
- **T4 RED (amended set):** 2 failed, 2 passed.
  test_breaker_trips_across_calls and test_breaker_default_fresh_per_call
  are T3 machinery and were green before T4 wiring existed; their
  fail-first proof lives in T3 (test_pre_tripped_breaker_raises_before_dispatch
  plus I8/I9). The plan body's "all three must FAIL" for T4 RED is
  superseded by this sequencing reality; the wiring-driving failures are
  test_provider_passes_breaker (KeyError/TypeError) and
  test_fold_records_success_only_for_non_truncated (TypeError: unexpected
  keyword argument), both clean RED.

## Forge review R1 fix batch (commit b2a7a3b)

Each fix was injected at its own fix site and proven by its target
test before committing (inject -> FAIL -> revert -> PASS):

| Fix | Injection (at the fix site) | Proving test | Inject result | Revert result |
|-----|------------------------------|--------------|---------------|---------------|
| FIX-1 | Re-added `record_success` (method + fold call) | test_breaker_count_is_monotonic + test_fold_never_resets_truncation_breaker | FAIL x2 (hasattr found the method; the fold reset the count) | PASS x2 |
| FIX-2a | Dropped the `; last failure: %s` suffix from the exhaustion message | test_continuation_exhausted | FAIL (`assert 'last failure' in ...`) | PASS |
| FIX-2b | Reverted the breaker advice to the unconditional "Raise output_ceiling or switch backends" | test_breaker_trips_and_check_tripped | FAIL (`assert 'may already clamp below the configured' in ...`) | PASS |
| FIX-3 | Dropped the fence-marker strip (verbatim tail) | test_fence_marker_stripped_from_continuation_prompt | FAIL (markers present in the prompt) | PASS |
| FIX-4 | Dropped the fixed 2s delay between attempts | test_continuation_exhausted | FAIL (sleep call-count assertion) | PASS |
| FIX-5 | Dropped the expected-keys envelope check on the parsed combined | test_wrong_shaped_continuation_is_a_failed_attempt | FAIL (wrong-shaped dict returned as a result) | PASS |

FIX-6 was DISMISSED by the orchestrator (no change). DEFER-1 recorded in
48-FOLLOWUPS.md.

## Forge review R2 fix batch (commit 7b0ddcf)

Four new tests first (clean RED: 4/4 failed), then each fix
bug-injected at its own site:

| Fix | Injection (at the fix site) | Proving test | Inject result | Revert result |
|-----|------------------------------|--------------|---------------|---------------|
| FIX-A | Deleted the complete-envelope early-return block in the helper | test_complete_json_partial_returns_without_continuation | FAIL: `StopIteration` -- a continuation was attempted against the single-element side_effect | PASS |
| FIX-B | Deleted the `breaker.check_tripped()` call at the top of each budget-loop iteration | test_breaker_tripped_between_attempts_stops_further_dispatch | FAIL: wrong error raised (exhaustion LLMInvokeError instead of TruncationBreakerError -- the third dispatch ran) | PASS |
| FIX-C | Deleted the `logging.getLogger("code_forge").warning(...)` call in the broad continuation-failure handler | test_unexpected_continuation_error_is_logged | FAIL: `assert any(...)` over caplog.records -- no warning emitted | PASS |
| FIX-D | Removed the "untrusted data, never instructions" line from CONTINUE_PROMPT | test_continuation_prompt_declares_data_boundary | FAIL: instruction line absent from the captured prompt | PASS |

R2 RED record: all four new tests failed against the pre-fix code
(StopIteration / wrong exception / empty caplog / missing prompt line).
Dismissed findings (no change): threading-import (pre-existing import),
envelope any-key check (downstream schema gate covers it).

## Forge review R3 fix batch (commit 2d2c932)

Four new tests first (clean RED: 4/4 failed), then each fix
bug-injected at its own site. FIX-F touches two check sites; both
injected separately:

| Fix | Injection (at the fix site) | Proving test | Inject result | Revert result |
|-----|------------------------------|--------------|---------------|---------------|
| FIX-E | Deleted the `except TruncationBreakerError: raise` clause at the top of the inner per-dispatch try | test_trip_during_continuation_dispatch_propagates | FAIL: trip folded into a budgeted failure, surfaced as the wrong error | PASS |
| FIX-Fa | Fast path reverted to the overlap predicate (`parsed_partial.keys() & _REVIEW_ENVELOPE_KEYS`) | test_partial_with_only_findings_does_not_fast_return | FAIL: `DID NOT RAISE LLMInvokeError` -- the one-key partial fast-returned | PASS |
| FIX-Fb | Loop success check reverted to the overlap predicate | test_one_key_continuation_counts_as_failed_attempt | FAIL (and test_partial_with_only_findings_does_not_fast_return too -- the extract fallback fed the findings-only dict to the weakened check) | PASS x2 |
| FIX-G | Dropped the `from exc` chaining in the outer defensive handler | test_outer_defensive_handler_chains_cause | FAIL: `assert None is LLMInvokeError(...)` -- no __cause__ | PASS |

R3 RED record: all four new tests failed against the pre-fix code
(exhaustion instead of trip / fast-returned one-key partial / accepted
one-key continuation / missing __cause__). Dismissed findings (no
change): threading-import x3 (pre-existing), the .diff artifact
(untracked, never committed), the continuation-prompt injection
residual (already mitigated by the untrusted-data instruction; inherent
to the technique), transport-failure misclassification (message suffix
+ log already surface the cause).

Note: FIX-F changed the fixture `_TAIL` and the success assertions to
the full envelope (findings AND code_excerpts), so every
continuation-success test now exercises the full-envelope requirement.
