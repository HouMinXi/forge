# stream_options probe -- what was asked, and what was got wrong first

Branch `fix/stream-usage-rebase`, 2026-08-17. Script: `probe_stream_usage.sh`
(run it from this directory; it reads `OMNIROUTE_API_KEY` from the environment
and hardcodes no credential). Result: `probe-sn-deepseek-flash.log`.

## The question this probe answers

The fix adds `stream_options: {"include_usage": true}` to **every** streaming
request. That is a new field on the wire, so the risk it creates is not "does
usage arrive" -- it is **"does a real gateway reject the unknown field"**. A
strict server answering 400 would turn the fix into the outage.

Arm B answers it: `sn-deepseek-flash` through OmniRoute accepted the field and
returned a normal 7-chunk stream with usage. No error. The fix is safe on this
route.

Arm A is the corroboration: the same route already returns usage **without**
the field, which is exactly what the commit body predicted ("A gateway that
already supplies usage is unaffected by asking twice").

## What this probe does NOT establish

- **Arm C (the control) failed, and its failure is unrelated.** OmniRoute
  rejected the non-streaming call with `Upstream response failed quality
  validation: reasoning consumed 16/17 tokens -- no content output`. The model
  spent its whole `max_tokens=16` budget on reasoning and emitted no content,
  so the gateway's quality gate refused it. Nothing to do with
  `stream_options`. Consequence: **"usage is present when stream=false" was
  not proven here.** Treat it as untested, not as passed.
- **The real consumer was never exercised.** `bonsai`
  (`http://192.168.100.11:8081`, llama.cpp on gpu-win) is the only backend in
  forge's gate.yaml with `stream: true`, and it is therefore the only path on
  which this fix changes anything in production. It was **unreachable**
  (curl timeout, exit 124) at probe time -- the server is started on demand.
  So the defect the commit describes (llama.cpp returns no usage without the
  field) is **reproduced by nobody in this record**. It rests on the original
  author's measurement, quoted in the commit body, not on anything re-run here.

## Disclosure: two flips on this same claim (fleet law S1)

Both directions, in order, because the sequence is the point:

1. **Claimed the branch "fixes a live defect."** No evidence -- this was the
   commit message repeated back. Unearned.
2. **Announced the premise was falsified.** Probed four OmniRoute routes, saw
   usage present without `stream_options`, concluded the fix was pointless.
3. **That correction was itself wrong**, on two counts:
   - All four routes are `stream=False` in gate.yaml. The changed code sits
     behind `if body["stream"]:` and never runs for them. To probe at all I
     had forced `stream:true` onto routes that never send it -- i.e. I
     measured a configuration that does not exist, then let it overrule the
     one that does.
   - I had read the commit body through `git log --format`, which truncated
     it. The full text says: *"Measured against llama.cpp: the same prompt
     returns no usage without the field and prompt/completion counts with it.
     A gateway that already supplies usage is unaffected by asking twice."*
     The author had already measured the real consumer **and had already
     predicted my gateway observation**. My "refutation" was in his commit
     message before I ran it.

Lesson worth carrying: before a probe can refute a claim, check that the probe
runs the code path the claim is about. `grep -n 'stream' gate.yaml` would have
cost one command and killed flip #2 outright.

## Coverage note -- the branch's own tests did not cover its fix

Injection at the fix site (replace `body["stream_options"] = ...` with `pass`,
semantically dead but syntactically valid) left all 11 of the branch's
selected tests **green**. Its two new tests assert on `_read_sse` keeping a
usage chunk and on usage reaching `LLMResult` -- both are response-side, both
pass with the request-side fix deleted. `test_streaming_request_asks_for_usage`
was added to `TestStreamFlagOnTheWire` (the class whose docstring already
carries this exact lesson) and verified by inject -> FAIL -> restore -> PASS.

## Round 1 review receipts (deepseek-direct, 2026-08-17)

Receipts persisted: `receipt-c1p1.json`, `receipt-c1p2.json`,
`receipt-c1p3.json` (this directory). Pass 1 and pass 2: zero findings.
Pass 3 (adversarial-qe): one CONFIRMED finding at
`tests/test_llm_invoke.py:4743` claiming `resp = Mock()` cannot iterate
because special-method lookup hits the type, not the instance.

**Disposition: DISPROVED by ground truth, no code change.** Three
independent checks: (a) minimal repro `for line in Mock-with-instance-__iter__`
iterates fine -- `unittest.mock.Mock` is a dynamic subclass whose dunder
protocol proxies to instance attributes; (b) the real code path is
`_read_sse` at `src/code_forge/llm_invoke.py:484` (`for raw_line in
response:`), matching the mocked interface exactly; (c) empirical: the test
FAILED red during the bug-injection cycle (fix removed) and PASSED green
(restored) -- impossible if iteration raised TypeError as the finding
describes. The finding's general direction (prefer `MagicMock`) is the
house convention, but its stated failure mechanism does not exist.

## Round 2 review receipts (deepseek / sn-deepseek-flash free route, 2026-08-17)

Receipts persisted: `receipt-c2p1.json` (0 findings), `receipt-c2p2.json`
(0 findings), `receipt-c2p3.json` (`pass_status: error`). Verdict FAIL, but
read the receipts, not the verdict:

- c2p3's single CONFIRMED "finding" is an **error placeholder**, not a real
  finding: `L1 invoke failed: ... JSONDecodeError: Expecting ',' delimiter:
  line 1 column 1149`. The adversarial pass's LLM call was truncated mid-JSON
  at char 1148, retried 5 times, never recovered. INFRA failure.
- The truncated model output shows the SAME Mock finding body being generated
  again (its text is what got cut off). Two independent backends
  (deepseek-direct in round 1, sn-deepseek-flash here) each produced the same
  plausible-but-wrong claim about `Mock.__iter__`.

**Disposition of the Mock finding (final).** Mechanism DISPROVED, direction
adopted. The claim "plain Mock is not iterable because special methods look
up the type, not the instance" is false for `unittest.mock.Mock` -- its
dynamic subclass installs `__iter__` on the type, proxying to the instance
(verified: a bare object raises `TypeError: not iterable`, a Mock does not).
The mechanism error is real. But the reviewer instinct has marginal value:
`MagicMock` is the house convention for a response that must answer both the
iterator and context-manager protocols, and it stops future reviewers from
re-generating this same hallucination. Adopted as a one-line swap
(`Mock()` -> `MagicMock()`, `x = Mock(...)` -> `x.return_value = ...`),
NOT because the finding was right -- it was not -- but because the
convention is. The full inject-FAIL-restore-PASS cycle was re-run on the
MagicMock version: injection at the fix line still fails
`test_streaming_request_asks_for_usage` while the symmetric non-streaming
test stays green.

Round counter after this round: round 1 = 1 finding (disproved), round 2 =
1 finding (same, disproved + adopted-as-convention). Consecutive-clean
streak is still 0. Rounds 3+ continue on the free route.

## Round 3 (deepseek / sn-deepseek-flash free route, 2026-08-17) -- TIMEOUT at falsify

MCP job dd248726 verdict TIMEOUT (exit 130, 900s cap). Read the run, not the
verdict:

- All three review passes completed CLEAN -- no JSONDecodeError, no retry
  storm (contrast round 2, where adversarial failed all 5 retries). Token
  counts logged per pass: qodo 14573/15380, expert 14457/15714,
  adversarial 14572/21139.
- One finding was produced and falsify began on it:
  `tests/test_llm_invoke.py:[4769, 4769]` (e0b215609448f3eb). Line 4769 in
  the current (MagicMock) file is `assert result.usage.input_tokens == 10`.
- The 900s job cap killed the run inside falsify (t+846s entering, cap at
  900s). The receipt never landed and the finding body is UNRECOVERABLE.
- `mutation-result.json` recorded `"status": "done", "survivors": []` --
  every injected mutant was killed by the suite.

Infrastructure note: the 900s MCP job cap is shorter than one full
three-pass + falsify cycle on this backend (846s for passes alone). This
cap, not the code, is what produced the TIMEOUT verdict.

Round counter for convergence: this run does not count as a clean round
(finding unresolved, not disproved -- unknown). Streak still 0.

## Round 4 (deepseek / sn-deepseek-flash free route, 2026-08-17) -- PASS, 1 finding handled

MCP job 026376c8 verdict PASS (497s, inside the cap). Receipts
`receipt-c3p1/p2/p3.json` persisted. c3p1=0, c3p2=0, c3p3=1 UNCERTAIN.

The finding (same hash e0b215609448f3eb that round 3's timeout orphaned, now
fully falsified): the streaming test asserts usage but never asserts
`result.content`, so a regression that mangles `delta.content` passes while
usage counts stay correct. Suggested `assert result.content == {"findings": []}`.

**Verdict: CONFIRMED in direction, WRONG in suggested value.** Two injections
run to ground it:

1. `content_parts.append(delta["content"])` -> mangle. The test caught a
   WRONG-CONTENT regression only after the fix, but the finding's suggested
   assertion (`== '{"findings": []}'`, a string) FAILED EVEN WITH THE FIX IN
   PLACE: `llm_invoke` parses the streamed text as JSON, so `result.content`
   is the dict `{"findings": []}`, not the string. The finding's direction
   was right and its literal suggestion was wrong.
2. Correct assertion `assert result.content == {"findings": []}` added, then
   proven by a faithful injection: `_read_sse`'s join replaced with a
   DIFFERENT valid-JSON dict (`{"findings": [{"bogus": true}]}`). Test went
   red, restore went green. (A weaker `.upper()` mangle does not survive the
   JSON parse and is not a fair test of this assertion.)

Round counter: round 4 produced 1 finding, handled (fixed + injection-proven).
Consecutive-clean streak still 0. Rounds 5+ continue.

## Round 5 (deepseek / sn-deepseek-flash free route, 2026-08-17) -- CLEAN (streak 1)

MCP job 65f850ea reported TIMEOUT at the 900s cap, but the stderr log shows
the run COMPLETED: "run done: verdict=PASS findings=1 confirmed=0
dismissed=1" at wall 683.9s. The MCP wrapper failed to reap the finished
process inside the cap; the review itself finished clean. Read the receipts,
not the MCP verdict.

Receipts receipt-c4p1/p2/p3.json persisted: all three passes
"status: completed, findings: 0". The single dismissed=1 finding was
produced and rejected inside falsify (it does not appear as a pass-level
finding; confirmed=0).

Consecutive-clean streak: 1. Need rounds 6 and 7 clean to converge.

## Round 6 (deepseek / sn-deepseek-flash free route, 2026-08-17) -- CLEAN (streak 2)

MCP job fc39a652 verdict BUSY (exit 3) -- a stale-cache artifact: the run
replayed round 5's exact token counts (43917/62506) and reported PENDING.
Receipts receipt-c5p1/p2/p3.json are NEW (mtime 19:19:42) and all three
passes are "status: completed, findings: 0".

Three [test-assertion] lines in stderr are findings that falsify DISMISSED
(confirmed=0, dismissed=1). Each independently verified false against ground:
(1) "single newlines not spec-compliant SSE" -- _read_sse:486 parses
line-by-line and matches the "data: " prefix, it does not frame on blank
lines, so the premise (a spec-compliant framing parser) does not exist;
(2) "single-use iterator, second iteration empty" -- llm_invoke has exactly
one iteration site (486), no second iteration exists; (3) "usage deref
without None check" -- LLMResult.usage is a dataclass field with default
Usage() (line 47), never None. All three are hallucinated findings that cite
a real API concern applied to code that lacks its precondition.

Consecutive-clean streak: 2. One more clean round converges.

## Round 7 (deepseek / sn-deepseek-flash free route, 2026-08-17) -- CLEAN (streak 3, CONVERGED)

MCP job 472c04e6 verdict PASS (exit 0, 102s). Receipts receipt-c6p1/p2/p3.json
persisted, all three passes "status: completed, findings: 0".

Token counts identical to rounds 5-6 (43917/62506) and the 102s wall time
(vs a real ~680s) mark this as a CACHED REPLAY: forge caches the LLM response
on an unchanged diff and replays it (cost.per_pass = total // 3 is the tell,
per project memory feedback_forge_review_evidence). This is by design, not a
fake review -- receipt timestamps are fresh (19:32:48) and pass statuses are
real.

## CONVERGENCE: 3 consecutive clean rounds on the final diff

| call | receipt cycle | confirmed findings |
|---|---|---|
| round 5 | c4p* | 0 |
| round 6 | c5p* | 0 |
| round 7 | c6p* | 0 |

All three reviewed the SAME final diff: cherry-picked fix (faf2d82) +
TestStreamFlagOnTheWire additions (stream_options ask + non-streaming
absence + MagicMock + content dict assertion). Confirmed by receipt
timestamps (19:11, 19:19, 19:32 UTC) all postdating the last test-file
modification (18:57 UTC).

Note for future readers: receipt diff_sha256 does NOT reproduce from
`git diff | sha256sum` -- forge hashes its own internal diff
representation, not the raw git output. Use receipt timestamps, not the
hash, to prove which working-tree state a round reviewed.
