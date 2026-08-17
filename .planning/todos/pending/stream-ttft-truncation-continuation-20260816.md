# Stream TTFT + truncated-output continuation (user order, 2026-08-16)

Two defects, one llm_invoke design pass. User: "stream 流下的首 token 吐出
仍然没有，被供应商限制 max_tokens 的模型没有接续 review 的能力。forge
必须要完整的 json 这很伤。" Research sources: Claude Code query.ts
(harness-books ch.6), OpenCode #17471/#18108/#13102, Codex event stream
(openai.com unrolling-the-codex-agent-loop).

## Defect 1: stream 流无首 token 可见性

_read_sse (llm_invoke.py) parses the SSE stream but emits nothing until
the whole body is assembled. All current backends run stream: false, so
this is dormant -- but a streaming backend would inherit a silent
20-minute stall with zero progress events. Fix: on the first content
chunk, progress.emit("pass <name>: first token at t+Ns"); optionally a
periodic byte counter on subsequent chunks. Codex's TTFT/TTFM-as-
first-class-metric is the reference.

## Defect 2: provider-capped max_tokens kills the review (no continuation)

sn-deepseek-flash clamps at 16385 whatever max_tokens says; a long pass
output (JSON findings) truncates mid-JSON, parse fails, retries burn the
same budget, the pass dies as INFRA. Forge demands one-shot complete
JSON; it has no continuation mechanism.

Design (layered recovery, Claude Code query.ts model adapted to forge):

1. DETECT: in _invoke_api (and _read_sse), inspect the response envelope
   for finish_reason == "length". This is TRUNCATION, not completion --
   never treat it as a normal result (OpenCode's root-defect lesson).

2. CLASSIFY:
   - partial content contains usable JSON prefix (non-whitespace bytes,
     at least one '{' or '[' or content in the expected shape) ->
     CONTINUE path.
   - zero usable content (all thinking budget spent, empty body) ->
     NO continuation; raise LLMInvokeError describing the truncation
     (OpenCode: continuing a nothing-output truncation replays the
     whole chain and truncates again).

3. CONTINUE (Layer 2, only when Layer 1 is unavailable):
   - Layer 1 (cheap): if backend.max_tokens < the provider's real cap
     and the caller did not pin it, raise the request max_tokens for the
     retry and re-run the same call. (forge pins max_tokens in backend
     config; Layer 1 applies only when the config value was not the
     provider cap -- detectable only heuristically, so keep it minimal.)
   - Layer 2 (escalation): continuation request -- same model+backend,
     prompt = "continue the JSON output from where it was cut off;
     emit ONLY the continuation; no recap; no preamble" plus the tail
     of the partial content for context. Concatenate partial +
     continuation, then run the existing envelope extraction
     (_extract_json_from_text) + validate_reviewer_json.
   - BOUNDS: at most N continuation attempts (start: 2), a per-call
     continuation counter, and a run-level circuit breaker reusing the
     existing TimeoutCircuitBreaker pattern. A continuation that again
     truncates counts against the same counter (doom-loop guard).
   - NEVER discard partial data: the partial + continuation are both
     preserved in the LLMResult content on success (OpenCode #13102).

4. FAILURE: after the bound is exhausted, raise LLMInvokeError with a
   message distinguishing truncation-exhausted from other failures
   ("output truncated at provider cap; continuation exhausted after N
   attempts"), retryable=False. The pass then lands as INFRA with an
   actionable description instead of a mystery schema failure.

5. TOKENS: usage must sum the continuation requests' tokens so cost
   accounting stays honest.

Not in scope: changing any backend's stream setting; raising provider
caps; JSON auto-repair beyond the existing envelope extraction.

## Placement

llm_invoke.py: _invoke_api + _read_sse + a small _continue_truncated
helper. Logic-bearing -> own branch, TDD, forge review, per the
house discipline. The eval-bank corpus (eval-bank/v1) supplies known
answer keys if a regression eval for this feature is wanted later.

## Acceptance sketch (preliminary)

- A backend stub returning finish_reason=length with partial JSON +
  a continuation returning the tail -> the assembled content parses
  and the pass succeeds (unit test with mocked HTTP).
- Truncation with zero usable content -> LLMInvokeError, no
  continuation issued.
- Continuation exhaustion -> LLMInvokeError with the distinguishing
  message.
- stream: true stub emitting chunks -> first-token progress event
  observed.
- Circuit breaker: N consecutive truncation continuations across
  calls -> hard stop.
