# llm-parse-retry -- review evidence, 2026-08-15

Branch fix/llm-parse-retry (worktree .worktrees/llm-parse-retry), two
wip commits on origin/main @ 48dea95:

- 0999d79 llm: retry a pass whose response is not valid JSON
  (llm_invoke.py parse moved inside the retry loop, kind="no_json",
  + 3 tests in tests/test_llm_invoke.py::TestBadJsonRetry)
- 835115d machine: STATE-09 warning names what CI statelessness costs
  (text-only)

## Bug-injection (closed loop)

Injected `retryable=False` on the no_json raise (equivalent to the old
parse-outside-the-loop shape at the observable level): 2 of 3 tests
FAIL (retries-and-succeeds, exhausts-attempts), the prose-wrapped
rescue test stays PASS (its path never raises). Reverted: 3/3 PASS.
The injection pins the one property the fix adds: a bad-parse reply is
retried instead of raising straight out.

## Review rounds

Required_cycles in gate.yaml is 1, so each round below is one cycle of
three passes.

- R1 (gemini-omniroute): 3 passes returned 91/60/91 out tokens (no
  room for any finding), smoke axis 502/503 across 5 retries. Recorded
  as INFRA -- not a review.
- R1-gemini-pro (agy/gemini-3.1-pro-high): the gateway compacts the
  review prompt; the model answered "Prompt cut off at `[CCR retrieve
  hash=... chars=2590]`. Missing diff." on every attempt. The no_json
  retry fired 4 times on real output, which is the fix working on the
  real path, but the route cannot review. INFRA.
- R1b/R2/R3/R4 (gemini-omniroute): byte-identical 91/60/91 tokens at
  0.2-0.3s. Cache-replay. Experiments: clearing forge-side
  receipts/state.json changed nothing; a temperature: 0.1 added to the
  backend config changed nothing; --focus does not enter the L1 prompt
  (input tokens identical). Conclusion: the replay is served by the
  OmniRoute gateway (192.168.100.10:20128), keyed on the prompt text,
  across at least two backends -- this is cross-run evidence for the
  charter-4 cache-replay question (the intra-run half is still open).
- R5 (deepseek = sn-deepseek-flash via OmniRoute): real review, 18.2s,
  741/1794/230 out tokens, findings 0/0/0. PASS on findings.
- R6 (deepseek, same route): byte-identical replay at 0.2s -- the
  gateway cache also covers this backend. Not counted as a round.
- R7 (deepseek-direct, api.deepseek.com, no gateway): real review,
  17.4s, 1625/1648/1175 out tokens, findings 0/0/0. The direct route
  was blackholed on the morning of 2026-08-15 (memory
  project_forge_gray_areas_20260814.md) and is reachable again
  (probe: /v1/models 200 in 0.09s).
- R8 (deepseek-direct): real review, 11.3s, 1127/862/1109 out tokens
  (different from R7 -- no replay on the direct route), findings 0/0/0.

Convergence: three real rounds (R5 sn-deepseek-flash, R7 + R8
deepseek-direct), nine passes, zero confirmed findings across two
model families. The only finding in every round is the L2
mutation-baseline-timeout advisory, dismissed as environment
contention (see below). No fixes required; the two wip commits carry
final messages already.

## L2 mutation advisory (all rounds)

mutation-baseline-timeout (flaky guard), dismissed every round. Cause
diagnosed: the review's detached mutation run (baseline pytest on
machine.py) overlapped the full-suite pytest run, and the baseline
timed out under contention. Environment, not the diff. The detached
runner itself (Popen start_new_session=True) was observed live on the
real path, which is C2 of the 43.1 charter working as designed.

## Full-suite pytest incident (self-inflicted, disclosed)

The full-suite run started at 08:45 with HEAD at 48dea95, then the two
wip commits landed mid-run. The 18.1 test-isolation guard diffs the
.git snapshot at sessionstart against sessionfinish, saw HEAD move
48dea95 -> 835115d, and aborted with "Real .git has been altered".
Not a code failure -- the guard did exactly its job. The full suite
must be re-run after all commits are final.

## Disposition

Awaiting R8. Expected wrap-up: amend the two wip commits to their final
messages, run the full suite once with a stable HEAD, and hand the
branch to the user for push.
