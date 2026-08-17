# Phase 48: LLM stream TTFT + truncation continuation - Research

**Researched:** 2026-08-16
**Domain:** LLM invocation adapter (streaming observability + truncated-output recovery)
**Confidence:** HIGH (all code paths read from disk; detection semantics verified against OpenAI docs; external prior art cross-checked via web search)

## Summary

Phase 48 fixes two defects in `src/code_forge/llm_invoke.py`:

1. **STREAM-VISIBLE (TTFT).** `_read_sse` assembles the SSE stream into a response dict but emits nothing while bytes arrive (llm_invoke.py:375-437). All API backends run `stream: false` (gate.yaml:143,164,192,283,293,322,332,348) EXCEPT bonsai (gate.yaml:434, `stream: true`), so the TTFT gap is LIVE in production on bonsai, whose passes stall silently until the body is fully assembled. Fix: emit one progress event on the first content delta, via `code_forge.progress.emit`.

2. **TRUNCATION-RECOVER (continuation).** `finish_reason=length` is already detected and already raises `LLMInvokeError(kind="truncated", retryable=False)` at llm_invoke.py:1310-1356 (openai), 1443-1456 (anthropic), 1605-1618 (vertex). What does not exist is recovery: a truncated JSON dies immediately as an INFRA finding (factories.py:347-369). sn-deepseek-flash clamps at ~16385 output tokens regardless of the configured 65536 (gate.yaml:141; corroborated by tests/test_llm_invoke.py:1508 "16384 = the SenseNova-family hard clamp"), so the default backend hits this in production. Fix: bounded continuation - a short fresh request that continues the cut-off JSON, concatenation, then the existing `_extract_json_from_text` + `validate_reviewer_json` pipeline.

**Primary recommendation:** Keep truncation *detection* exactly where it is (per-format helpers, post-SSE-assembly), convert the raise into a payload-carrying exception subclass, and add one `_continue_truncated` helper invoked from `_invoke_api`'s `except LLMInvokeError` handler with its own budget of 2 (orthogonal to `max_attempts`). Add a dedicated small circuit-breaker class (pattern-mirror of `TimeoutCircuitBreaker`, not a reuse). Emit TTFT inside `_read_sse` on the first content delta.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| TTFT progress event emission | In-process broker (forge review run, stderr) | - | `progress.emit` writes to stderr; consumed by human/CLI and MCP job-status tail (progress.py:8-20). Not a network tier concern. |
| finish_reason detection | API adapter (`_invoke_openai`/`_invoke_anthropic`/`_invoke_vertex`) | - | The response envelope is format-shaped; detection already lives at the assembled-dict level (llm_invoke.py:1310, 1443, 1605). |
| Continuation loop | API dispatch (`_invoke_api`) | - | Owns the retry loop, budgets, and error classification; continuation must compose with it (llm_invoke.py:1065-1197). |
| JSON parse / envelope extraction | API dispatch (existing) | - | `_strip_fences` + `json.loads` + `_extract_json_from_text` (llm_invoke.py:1140-1158). |
| Circuit breaker state | Run orchestration (cli.py) | API dispatch | Breaker is per-run state; constructed in cli.py:3004 beside the timeout breaker, threaded into `llm_invoke` like `max_attempts`. |
| Token accounting | API dispatch | - | `Usage` summing happens where usage_data is in hand (llm_invoke.py:1072-1091). |

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STREAM-VISIBLE | Streaming passes emit a first-token progress event via `code_forge.progress.emit` | Section "Design decision 0 (TTFT)"; test seam `_sse_lines` (tests:3276-3282); progress.emit is lock+flush protected (progress.py:56-72). |
| TRUNCATION-RECOVER | finish_reason=length treated as truncation, recovered via bounded continuation; zero-usable-content raises; max 2 continuation attempts + circuit breaker; exhaustion raises distinguishing `LLMInvokeError` | Section "Design decisions 1-5"; existing detection sites llm_invoke.py:1310-1356; existing zero-output regression test tests:1726-1743. |
</phase_requirements>

## 1. Current State (code walk)

### 1.1 Stream path: `_read_sse` (llm_invoke.py:375-437)
- Iterates SSE `data:` lines, decodes with `errors="replace"`, skips non-data lines (395-397), breaks on `[DONE]` (399-400), tolerates bad JSON chunks (401-404).
- Collects `delta.content` into `content_parts` (415-418); deliberately drops `reasoning_content` (419).
- Captures `finish_reason` from the final chunk's choice (420-421), `usage` (413-414), `model` (411-412).
- Returns a dict normalized to the non-stream OpenAI shape: `{"choices": [{"message": {"content": joined}, "finish_reason": fr}], "usage": ...}` (427-437). Error-only chunks are returned as-is for `_check_body_error` (424-425, 407-409).
- **No progress emission exists anywhere in llm_invoke.py** (grep for `progress` returns zero hits). The stream is fully silent until the whole body is assembled.
- Enforces a total deadline per line (389-394) but no per-chunk timer.
- Only caller: `_invoke_openai` stream branch (llm_invoke.py:1256-1261). anthropic/vertex stream raise `CliError` upfront (1368-1372, 1488-1492). The live streaming backend is bonsai (gate.yaml:434; openai format, `stream: true`).

### 1.2 The API envelope's finish_reason is visible today - and already raises
- `_invoke_openai`: `choice = resp_data["choices"][0]`; `finish = choice.get("finish_reason", "")` (1296, 1310). If `"length"`, three raise variants: clamped-below-config-cap (1319-1331, the sn-deepseek-flash case), at-ceiling (1332-1342), no-usable-cap (1343-1356). All are `kind="truncated", retryable=False`.
- `_invoke_anthropic`: `stop_reason == "max_tokens"` (1443-1456). `_invoke_vertex`: same (1605-1618). Sampling path: `stopReason == "maxTokens"` (1688-1694).
- Because `_read_sse` normalizes stream output into the same dict shape, the stream path already flows through the same detection - detection location is NOT the gap.
- The raise **discards the partial content**: only in_tok/out_tok/resolved_cap reach the message. Nothing carries the cut-off JSON out of the helper.
- `LLMResult.is_truncated` (llm_invoke.py:48) exists but is never set True anywhere in src (grep: only the default and test assertions, tests:3442). No consumer.

### 1.3 Retry loop: `_invoke_api` (llm_invoke.py:1065-1197)
- `for attempt in range(max_attempts)`: dispatch by format (1068-1095) -> empty-content check `kind="empty"` (1114-1128) -> `_strip_fences` + `json.loads` + `_extract_json_from_text` fallback, `kind="no_json"` on failure (1140-1158) -> `break` on success (1197).
- `except LLMInvokeError`: `if not exc.retryable or attempt == max_attempts - 1: raise` (1169-1170); else exponential backoff capped at 60s + jitter, Retry-After floor (1171-1177), stderr line, `time.sleep`, `continue` (1190-1196).
- The JSON parse inside the loop is commit f91605b ("llm: retry a pass whose response is not valid JSON", 2026-08-15): an unparseable HTTP-200 reply draws a fresh attempt.
- `_read_with_deadline` (273-372): daemon-thread read + join; `_IDLE_READ_TIMEOUT_S = 900` (270); raises `is_timeout=True, retryable=False` on expiry.

### 1.4 What happens today on a truncated response
- Detected truncation (finish_reason=length): helper raises `kind="truncated", retryable=False` -> the loop re-raises immediately (1169) -> `factories.py` catches `LLMInvokeError` and folds it into an INFRA `StateFinding` (`source="INFRA"`, factories.py:347-369). `breaker.record_other_error()` if not a timeout (370-374). **No retry, no continuation, actionable message but dead pass.**
- Undetected truncation (provider reports `stop` with cut JSON): parse fails -> `kind="no_json"` retryable=True -> retried with the SAME prompt and SAME budget up to `max_attempts` times (default 5, factories.py:300-307) -> each retry re-truncates -> dies as no_json after burning the budget. This is the "retries burn the same budget" path from the design doc.

## 2. Gap Analysis

1. **TTFT invisible:** `_read_sse` accumulates but never reports (1.1). Live today on bonsai (gate.yaml:434, `stream: true`): a bonsai pass shows zero progress events until the whole body is assembled.
2. **Truncated JSON dies:** detection works, but the raise is a dead end (1.4). The missing piece is not detection - it is a *carrier* for the partial content plus a recovery path inside `_invoke_api` that runs before the `retryable` check (1169).
3. **Zero-output truncation must not continue:** today it raises with the generic at-ceiling message (1332-1342). With continuation added, the zero-usable-content case must keep raising without issuing a continuation (OpenCode lesson: continuing a nothing-output truncation replays the whole chain and truncates again - design doc lines 38-40; also tests:1726-1743 asserts exactly one call for this shape today).
4. **No run-level memory of truncations:** each call is stateless; a systematically under-capped backend repeats the same failure per pass with no escalation.

## 3. Design Decisions with Alternatives

### Decision 0: TTFT event placement and naming
**Recommend:** inside `_read_sse`, before the loop `t_start = time.monotonic()`; on the first chunk where `delta.get("content")` is non-empty, call `progress.emit("backend %s: first token" % backend_name)` exactly once (flag-guarded). The emit already stamps `t+Ns` (progress.py:69), so the message carries no timestamp of its own.
- Alternatives: (a) periodic byte counter on later chunks (design doc's "optional") - defer, LOW value, more stderr noise; (b) threading a pass-name parameter into `llm_invoke` for a `pass <name>:` label - rejected for this phase: `factories.py:296` already emits `pass %s: calling %s` immediately before the call, so the preceding line supplies the pass name and events interleave readably; a label parameter would touch every `llm_invoke` call site (cli.py:900,993,2127; factories.py:300; falsify_real.py:44; daemon_state.py:409,450; contract_loader.py:279).
- Justification for monotonic clock: progress.py already uses `time.monotonic()` (52-53); the TTFT-pitfall literature (hermes-webui PR #6044) flags wall-clock distortion under NTP - already aligned.
- `_read_sse` import of `progress` is cycle-free (progress.py imports only stdlib).
- Thread-safety: emit is lock-protected and swallow-errors (progress.py:56-72), safe from parallel-pass worker threads (factories.py:334-345).

### Decision 1: Where to detect finish_reason
**Recommend:** keep detection at the assembled-dict level in the three format helpers (existing llm_invoke.py:1310/1443/1605) - the stream path already normalizes to this shape (427-437). **The change is a payload-carrying exception, not a new detection site.**
- Introduce `class _TruncatedResponse(LLMInvokeError)` with fields `content: str`, `usage_data: dict`, `resolved_cap: int`; the three raise sites construct it instead of the plain `LLMInvokeError` (all existing `TestTruncationDetection` assertions - kind, retryable, message substrings - keep passing because the subclass inherits the same kwargs).
- Alternative rejected: per-chunk detection inside `_read_sse` (`choice.get("finish_reason")` per event) - finish_reason arrives only on the final chunk, so per-chunk logic duplicates what the assembled dict already yields and splits the raise into two paths.

### Decision 2: Continuation loop shape and budget composition
**Recommend:** a helper `_continue_truncated(prompt, backend, api_key, timeout_s, truncated, expected_keys, budget=2, breaker)` invoked from `_invoke_api`'s `except LLMInvokeError` handler **before** the `retryable` check, when `isinstance(exc, _TruncatedResponse)` and the partial passes the zero-output guard (Decision 3).
- **Continuation does NOT consume `max_attempts`.** Rationale: `max_attempts` retries are for transient failures (429/5xx/empty/no_json); truncation is deterministic per prompt. Letting continuation eat attempts would, on exhaustion, replay the original truncating prompt with no possible benefit - the exact failure the design doc names. Continuation gets its own budget of 2; exhaustion raises `LLMInvokeError(kind="truncated", retryable=False)` with the distinguishing message `"output truncated at provider cap; continuation exhausted after N attempts"` (message distinguishes; kind stays "truncated" so the mcp_server fallback whitelist at mcp_server.py:959-961 keeps working if this ever routes there).
- Continuation request shape (per design doc): a **fresh short request**, same model+backend: `"continue the JSON output from where it was cut off; emit ONLY the continuation; no recap; no preamble"` + the tail of the partial content (bounded, e.g. last ~2000 chars). On success: `combined = partial + continuation`, then `_strip_fences` + `json.loads` + `_extract_json_from_text(expected_keys)`; parse success -> return combined + summed usage; parse failure of combined counts as a failed continuation attempt (budget--, breaker records).
- Alternative rejected (multi-turn continuation): `messages: [original prompt, assistant: partial, user: "continue"]` - better context continuity but resends the full original prompt (a review diff can be 100KB+), whose affordability depends on provider prefix caching that forge's gateway routes do not guarantee; models also tend to explain rather than continue when the assistant turn ends mid-JSON. The short-request shape bounds continuation input tokens to the partial tail.
- Alternative rejected (Layer 1, raise max_tokens): the design doc keeps it minimal and I recommend **dropping it entirely**. Every current backend already pins max_tokens at or above its provider's real cap (deepseek 65536 vs 16385 clamp; oc-ds-flash-free 16384; deepseek-direct 384000 - gate.yaml:141,190,346), and llm_invoke.py:1319-1331 already detects the clamped-below case and tells the user raising will not help. A heuristic "raise the cap" layer could only fire when the operator deliberately set a low cap - in which case raising it contradicts the operator. YAGNI; the continuation is the real fix for the observed case.

### Decision 3: "Usable partial content" heuristic (zero-output guard)
**Recommend:** usable iff `content.strip()` is non-empty AND `"{"` is in the partial. Forge envelopes are dicts; `_extract_json_from_text` scans `{` only (llm_invoke.py:711-712, 721-726). Zero-output (empty/null content, or all-whitespace, or no `{`) -> re-raise the original truncation error unchanged, **no continuation issued**.
- Alternative rejected: also accepting `[` - bare arrays never validate as forge envelopes (dicts with expected_keys), so a `[`-shaped partial is not recoverable toward a valid result.
- Regression anchor: tests:1726-1743 (`test_null_content_with_length_still_reports_truncated`, asserts `call_count == 1`) becomes the guard for this branch.

### Decision 4: Circuit breaker - dedicated counter, not a TimeoutCircuitBreaker reuse
**Recommend:** a dedicated small class (e.g. `TruncationBreaker`) in llm_invoke.py mirroring the pattern of machine.py:75-99 (`threshold`, `record_truncation`, `record_success`, `count`), but raising its own error instead of `TimeoutBreaker`.
- Why not reuse `TimeoutCircuitBreaker`: (a) its raise (machine.py:84-89) hardcodes a timeout-specific message ("Raise FORGE_LLM_TIMEOUT_S or switch to a faster backend") that cli.py prints verbatim - misleading for truncation; (b) `TimeoutBreaker` carries "review cannot converge" run-abort semantics owned by machine.py, while truncation exhaustion is a call-abort owned by llm_invoke; (c) the existing breaker is only touched in the main-thread fold (factories.py:370-425) and has **no lock**, while a continuation breaker is touched inside ThreadPoolExecutor worker threads (factories.py:334-345) - the dedicated class must take a `threading.Lock` (this is a real difference, not a copy of the pattern).
- Wiring: per-run instance constructed in cli.py next to the timeout breaker (cli.py:3004), threaded through `build_l1_provider`/factories into `llm_invoke(..., continuation_breaker=breaker)`; parameter default `None` -> fresh per-call instance, keeping direct callers stateless (daemon_state.py:409,450; contract_loader.py:279; falsify_real.py:44).
- Semantics: `record_truncation()` on every truncation event (recovered or not); `record_success()` only on a call that completed without truncating. Rationale: the breaker's purpose is to detect a *systematically* under-capped backend, which is visible in the truncation rate, not just in recovery failures; a recovered pass still costs ~2x tokens. (Both readings are defensible; this one is the recommendation - see Open Questions.)

### Decision 5: Token accounting
**Recommend:** `_TruncatedResponse` carries the original `usage_data`; `_continue_truncated` accumulates every continuation call's `usage_data`; the returned `Usage` is the sum. Note the continuation's input tokens approximate the partial-tail length (bounded by the provider output cap, ~16k), so a recovered pass costs up to ~2x a clean pass, and the 2-attempt bound caps the worst case at ~3x. factories.py:302-311 automatically prints the summed usage - no consumer change needed.

## 4. Test Strategy

Existing seams to reuse (all in tests/test_llm_invoke.py):
- `_mock_body(payload)` / `_openai_body(content, finish)` (1657-1669): build a mock urlopen response (read -> JSON bytes, `__enter__`/`__exit__`).
- Multi-call sequences: `patch("urllib.request.urlopen", side_effect=[resp1, resp2, ...])` with a trailing sentinel to catch over-eager retries (pattern at 1715-1724).
- Retry-loop tests at the `_invoke_api` level patch `code_forge.llm_invoke._invoke_openai` with a side_effect function (2673-2740) - ideal for continuation tests that need call counting without HTTP mock plumbing.
- `_sse_lines(*chunks)` (3276-3282) builds SSE bytes iterators for `_read_sse`; `TestReadSSE` (3285+) is the STREAM-VISIBLE home.
- `_make_api_backend(name, fmt)` (2410-2414), `_empty_content_backend` (1648-1654), `_mock_ok_response` (2417-2429).
- `patch("time.sleep")` to skip backoffs (standard in this file).
- Zero-output regression already present: `test_null_content_with_length_still_reports_truncated` (1726-1743).

New fixtures needed:
1. `_truncated_openai_body(partial='{"findings": [{"fil')` -> finish="length", usage with completion_tokens ~16384 (mirrors the SenseNova clamp shape already used at 1505-1510).
2. `_continuation_body(tail='...}]}', finish="stop")` -> second response in a side_effect list.
3. Zero-content truncation (content null/"" + finish=length) - reusable from `_openai_body(None, finish="length")` (1726 already does this).
4. SSE chunk fixture with content deltas -> assert one TTFT event (capture via `patch("code_forge.llm_invoke.progress.emit")` or `capsys` stderr) and that the event fires after the first content chunk, never before.

New tests (behavior map):
- TRUNCATION-RECOVER: continuation success (combined parses; `call_count == 2`; `result.usage == sum`); continuation truncates again -> exhaustion raise with distinguishing message, `kind="truncated"`, `retryable=False`; zero-content -> raise, `call_count == 1`; combined-parse-failure counts as a failed continuation; anthropic `stop_reason=max_tokens` carrier path (extend TestTruncationDetection: assert the error exposes `.content`); shared breaker trips across calls -> hard stop; breaker resets on a clean call.
- STREAM-VISIBLE: `_read_sse` first-token emit; no emit when stream yields only reasoning_content then errors; emit absent for non-stream path (no regression).

Run commands: quick `python3 -m pytest tests/test_llm_invoke.py -x -q`; full suite from repo root must add `--ignore=.worktrees --ignore=.claude/worktrees` to avoid double-collection of nested worktrees (ROADMAP.md:674 note). Python 3.14.6 and pytest 9.1.0 verified present.

## 5. Risks and Mitigations

1. **Cost blowup (continuation doubles tokens per pass).** The continuation prompt is short (instruction + bounded tail), never the original diff; budget 2; breaker caps run-level repeat; usage summed for honest accounting. Worst case ~3x a clean pass, then the breaker or exhaustion stops it.
2. **Prompt-cache interaction.** With the short-request shape the original prompt is not resent, so prefix caching is not a dependency. Two rules if a future multi-turn shape is adopted: keep the original prompt byte-identical (any reformat kills provider prefix-cache hits), and insert the partial verbatim (no re-encoding) into the continuation prompt so the JSON fragment the model must continue is not corrupted.
3. **Oscillation (continuation returns the same truncation).** Hard budget (2) bounds it. OpenCode #18108's lesson applies: per-attempt output varies, so similarity-based doom-loop detectors fail - forge's fixed counter does not rely on output similarity. Note: a continuation whose output is again `finish_reason=length` counts against the same counter.
4. **Stacking with the f91605b json-parse retry.** Detected truncations raise before parse (helper level), so they never reach the no_json retry. Undetected truncations (provider reports `stop` on cut JSON) still burn `max_attempts` re-truncating retries and die as no_json - continuation never fires because detection never fired. Known gap; extension point (out of scope): after no_json retries exhaust, classify `{`-prefixed content as truncation-suspect and attempt continuation. Keep out of the first cut.
5. **Zero-output continuation replays.** Guarded by Decision 3; existing regression test anchors it.
6. **Breaker races.** The dedicated breaker is touched from parallel-pass worker threads; needs a lock (Decision 4). The existing TimeoutCircuitBreaker avoids the problem only by being main-thread-only - do not copy that property.
7. **TTFT event spam.** Emit exactly once per call, flag-guarded; periodic byte counters deferred.
8. **Prompt injection via partial content.** The continuation prompt re-feeds model-generated text into a model. Fence the partial with delimiters and instruct that the fenced snippet is data to continue, not instructions (see Security Domain).

## Standard Stack

No new libraries. The phase is pure stdlib + existing `code_forge` modules (`llm_invoke`, `progress`, `machine` pattern). No version pinning, no installation step.

## Package Legitimacy Audit

Not applicable - the phase installs no external packages (all implementation uses Python stdlib and existing in-repo modules). slopcheck gate and registry verification are skipped for this reason; nothing to audit.

## Architecture Patterns

### Pattern 1: Exception-as-carrier
Truncation raises carry the partial content and usage_data in an `LLMInvokeError` subclass, so the recovery layer at `_invoke_api` never re-parses or re-reads anything. Mirrors how `LLMInvokeError.kind` already carries machine-readable failure class for dispatch decisions (llm_invoke.py:70-77).

### Pattern 2: Budget separation
Continuation budget (2) is orthogonal to the retry budget (`max_attempts`). Mixing them misroutes deterministic failures into transient-failure retries and produces misleading exhaustion messages.

### Pattern 3: Single normalization point
`_read_sse` already normalizes the stream to the non-stream dict shape (llm_invoke.py:427-437); all downstream logic (body-error check, truncation detection, content extraction) stays on one shape. Do not add stream-specific detection.

### Anti-Patterns to Avoid
- **Detecting finish_reason per SSE chunk:** finish_reason exists only on the final chunk; per-chunk detection is redundant and splits the raise path.
- **Continuation via full-conversation replay:** resends the whole diff prompt; cost depends on unverifiable gateway caching; models explain instead of continue when the assistant turn ends mid-JSON.
- **Letting continuation consume `max_attempts`:** on exhaustion the loop replays a deterministically truncating prompt - cost with zero success probability.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Truncation recovery | Custom JSON auto-repair / repair-suffix guessing | Concatenate partial + continuation, then the existing `_strip_fences` + `_extract_json_from_text` (llm_invoke.py:666-730) | JSON repair is unbounded complexity; envelope extraction already exists and is tested (f91605b, F1/F2 fixes documented at 691-717). |
| Run-level truncation escalation | Ad-hoc counters inside `_invoke_api` | Dedicated breaker class mirroring machine.py:75-99 | State ownership and reset semantics must be explicit and testable. |
| TTFT timing | Wall-clock `time.time()` | `progress.emit` (monotonic clock, progress.py:52-53) | NTP/suspend distortion; emit also handles flush and lock. |

**Key insight:** this phase is itself a "don't hand-roll blindly" case - the recovery pattern follows documented prior art (Claude Code query.ts layered recovery; OpenCode continuation PRs) rather than a novel mechanism.

## Common Pitfalls

### Pitfall 1: Raising the truncation error before the continuation can run
**What goes wrong:** the `except LLMInvokeError` handler checks `retryable` first; a `_TruncatedResponse` with `retryable=False` re-raises before recovery logic sees it.
**Why it happens:** the existing handler order (llm_invoke.py:1168-1170) is correct for every other error class.
**How to avoid:** the continuation branch must be checked in the except handler *before* the `retryable` gate.
**Warning signs:** continuation tests pass at the helper level but `llm_invoke` still raises truncated.

### Pitfall 2: Zero-output truncation entering the continuation loop
**What goes wrong:** empty partial -> continuation -> another empty truncation -> budget burned, then a confusing exhaustion error.
**Why it happens:** treating `finish_reason=length` uniformly.
**How to avoid:** the `{` + non-whitespace guard runs before any continuation call (Decision 3); the existing test at tests:1726-1743 asserts `call_count == 1`.

### Pitfall 3: Breaker counting from worker threads without a lock
**What goes wrong:** lost increments or double-trips under parallel passes.
**Why it happens:** copying TimeoutCircuitBreaker, which is safe only because it is main-thread-only (factories.py:370-425).
**How to avoid:** dedicated class takes a `threading.Lock`.

### Pitfall 4: Continuation prompt corrupting the partial tail
**What goes wrong:** re-encoding or truncating the partial mid-escape produces a continuation of the wrong JSON fragment, and the concatenation can never parse.
**Why it happens:** the tail is itself partial JSON with live escapes.
**How to avoid:** insert the tail verbatim; fence it as data.

## Code Examples

### Continuation prompt shape (recommended)
```python
CONTINUE_PROMPT = (
    "The JSON output below was cut off by an output token limit. "
    "Continue the JSON output from where it was cut off. "
    "Emit ONLY the continuation; no recap; no preamble.\n"
    "<partial>\n%s\n</partial>"
)
# partial_tail = truncated.content[-2000:] inserted verbatim
```
Source: design doc (.planning/todos/pending/stream-ttft-truncation-continuation-20260816.md:47-50), adapted with fencing per Security Domain.

### TTFT emit point inside `_read_sse` (concept)
```python
t_start = time.monotonic()
first_emitted = False
for raw_line in response:
    ...
    for choice in chunk.get("choices", []):
        delta = choice.get("delta", {})
        if delta.get("content"):
            if not first_emitted:
                first_emitted = True
                progress.emit("backend %s: first token" % backend_name)
            content_parts.append(delta["content"])
```
No functional change to the assembly loop; one flag, one emit.

### Mock seam for continuation tests (existing pattern, tests:1715-1724)
```python
responses = [
    _mock_body(_openai_body('{"findings": [{"fil', finish="length")),
    _mock_body(_openai_body('...}]}', finish="stop")),
]
with patch("urllib.request.urlopen", side_effect=responses) as mock_open, \
     patch("time.sleep"):
    result = llm_invoke("p", backend=backend, max_attempts=3)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| finish_reason=length treated as normal completion (OpenCode prompt.ts:698-699, issue #18108; #40146 "no code branches on length") | length = truncation -> detection + recovery | OpenCode PRs #21688/#37220; forge already has the detection half (llm_invoke.py:1310) | Forge now adds the recovery half. |
| TTFT per-turn metric (codex.turn_ttft) | Per-request TTFT latched on first `response.output_item.added` | openai/codex PR #30883 | Reference for STREAM-VISIBLE; forge analog is per-call first-content-delta. |
| OpenAI `max_tokens` | `max_completion_tokens` (o-series compatible; max_tokens deprecated) | OpenAI API reference | Forge already handles both via `field_selects_key` (llm_invoke.py:176-195). |

**Deprecated/outdated:** treating `finish_reason=length` as a completion is the anti-pattern forge must never reintroduce - the continuation path must always classify it as truncation first.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | sn-deepseek-flash reports `finish_reason=length` on its ~16385 clamp (detection fires). Source: task-input memory note + design doc. | 1.4 | If the gateway reports `stop` on clamped output, detection never fires and the continuation is dormant for the default backend - mitigation: the no_json extension point (Risk 4) + a one-call diagnostic experiment during execution. |
| A2 | OpenCode #13102's lesson is "never discard partial data; preserve partial + continuation on success" (design doc:54-55). Not independently fetched in this session (search verified #17471/#18108/#26167/#40146 only). | 5, Decisions | Low - the design doc's own requirement already states the behavior regardless of the citation. |
| A3 | The breaker should count truncation *events* (recovered or not), not only failed recoveries (Decision 4 semantics). | Decision 4 | If the intent was failure-only counting, a recovered-but-expensive backend would never trip - discuss-gate can flip this in one line. |
| A4 | Layer 1 (raise request max_tokens) adds nothing for any current backend (gate.yaml caps all at/above provider limits). | Decision 2 | If a future backend configures max_tokens below its provider cap, Layer 1 would be the cheap first recovery - noted as a possible later addition. |
| A5 | Passing-name label for TTFT is unnecessary because factories.py:296 emits the pass-name line first. | Decision 0 | Cosmetic only - if MCP job-status consumers need pass names inside events, add the optional label parameter later. |

## Open Questions (all RESOLVED by PLAN.md, 2026-08-16; full rationale in its Decisions section)

1. **Breaker semantics (A3):** count all truncation events, or only recovery failures? Recommendation: all events (detects systematically small caps even when recovery succeeds). **(RESOLVED -> D-1: count all events, threshold 5; record_success resets)**
2. **Exhaustion kind:** keep `kind="truncated"` (mcp fallback-eligible, mcp_server.py:959-961) with a distinguishing message, or introduce `kind="truncation_exhausted"` (not fallback-eligible)? Recommendation: keep "truncated" + message; the CLI path does not branch on kind (factories.py:347-369 checks only `is_timeout`). **(RESOLVED -> D-2: keep "truncated" + distinguishing message)**
3. **Does sn-deepseek-flash actually report finish_reason=length on its clamp?** Recommend a one-call diagnostic at execution start (inspect the envelope of one truncated call) before relying on the continuation path for the default backend (A1). **(RESOLVED -> D-3: probe included as plan task T0, non-blocking; A1 is grounded by gate.yaml's 2026-08-11 measurement, the probe is a drift check)**
4. **Periodic byte-counter progress:** design doc says "optional". Recommendation: defer; first-token only for this phase. **(RESOLVED -> D-6: deferred this phase)**
5. **Anthropic/vertex continuation coverage:** implement the carrier + continuation for all three formats (same code path), or openai-only first? Recommendation: all three - the helper is format-agnostic and re-invokes the same per-format function; test cost is one extra fixture. **(RESOLVED -> D-4: all three formats, one shared helper)**

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3 | Implementation + tests | Yes | 3.14.6 | - |
| pytest | Test suite | Yes | 9.1.0 | - |
| pytest-asyncio | Existing async tests | Declared | pyproject.toml:36 (pytest-asyncio>=1.0) | - |

No new external dependencies. LLM backends are exercised only via mocks in tests; no live service required for this phase's verification.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.1.0 (pyproject.toml [tool.pytest.ini_options], line 65) |
| Config file | pyproject.toml |
| Quick run command | `python3 -m pytest tests/test_llm_invoke.py -x -q` |
| Full suite command | `python3 -m pytest -q --ignore=.worktrees --ignore=.claude/worktrees` (ROADMAP.md:674 double-collection note) |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STREAM-VISIBLE | First content delta emits exactly one progress event | unit | `python3 -m pytest tests/test_llm_invoke.py::TestReadSSE::test_first_token_emit -x` | No - Wave 0 |
| TRUNCATION-RECOVER | finish_reason=length with partial JSON + continuation tail -> combined content parses; usage summed | unit | `python3 -m pytest tests/test_llm_invoke.py::TestTruncationRecover::test_continuation_success -x` | No - Wave 0 |
| TRUNCATION-RECOVER | Zero-usable-content truncation -> raise, no continuation (call_count==1) | unit | existing `test_null_content_with_length_still_reports_truncated` (tests:1726) | Yes - doubles as regression |
| TRUNCATION-RECOVER | Continuation exhaustion -> distinguishing LLMInvokeError, kind/retryable | unit | `python3 -m pytest tests/test_llm_invoke.py::TestTruncationRecover::test_continuation_exhausted -x` | No - Wave 0 |
| TRUNCATION-RECOVER | Breaker trips across calls -> hard stop | unit | `python3 -m pytest tests/test_llm_invoke.py::TestTruncationRecover::test_breaker_trips_across_calls -x` | No - Wave 0 |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/test_llm_invoke.py -x -q`
- **Per wave merge:** full `tests/test_llm_invoke.py` (no `-x`)
- **Phase gate:** full suite with worktree ignores, green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_llm_invoke.py::TestTruncationRecover` - new class + fixtures `_truncated_openai_body`, `_continuation_body` (reuse `_mock_body`/`_openai_body` at 1657-1669)
- [ ] `tests/test_llm_invoke.py::TestReadSSE::test_first_token_emit` - extends existing `_sse_lines` fixture (3276-3282)
- [ ] Breaker unit tests (threshold/reset/lock behavior) - mirror `tests/test_machine_local.py:704-788` style
- [ ] No framework install needed (pytest present)

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Unchanged (credential handling untouched) |
| V3 Session Management | No | Unchanged |
| V4 Access Control | No | Unchanged |
| V5 Input Validation | Yes | Provider output is untrusted input; continuation re-feeds model-generated text into a prompt. Delimit the partial with markers and instruct the model the fenced snippet is data to continue, not instructions. JSON parsing stays on the existing `_extract_json_from_text` path. |
| V6 Cryptography | No | Unchanged |

### Known Threat Patterns for this change

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Prompt injection via truncated partial content (attacker-influenced review target text that reaches the model, then flows into the continuation prompt as "instructions") | Spoofing / Elevation | Fence partial as `<partial>...</partial>` data; continuation instruction explicitly constrains output ("emit ONLY the continuation"). |
| Malformed/oversized partial tail consumed by continuation prompt | DoS | Tail bounded (~2000 chars); budget 2; breaker. |
| SSE chunk parsing abuse | Tampering | Unchanged existing `_read_sse` guards (decode errors="replace", JSONDecodeError skip, llm_invoke.py:395-404). |

## Sources

### Primary (HIGH confidence)
- src/code_forge/llm_invoke.py - full read (all line refs in-text)
- src/code_forge/progress.py - full read
- src/code_forge/machine.py:64-99 - TimeoutCircuitBreaker / TimeoutBreaker
- src/code_forge/factories.py:212-425 - pass runner, error folding, breaker calls
- tests/test_llm_invoke.py - harness seams (1410-1646 truncation; 1648-1771 empty-content; 2410-2429 helpers; 2673-2740 no_json retry; 3270-3351 SSE)
- .code-forge/gate.yaml - backends section (stream flags, max_tokens values)
- .planning/todos/pending/stream-ttft-truncation-continuation-20260816.md - design doc (source of truth for scope)
- developers.openai.com API reference via Context7 (/websites/developers_openai_api_reference) - finish_reason "length" = "maximum token limit was reached" [VERIFIED]

### Secondary (MEDIUM confidence)
- openai/codex PR #30883 "emit per-request TTFT completion telemetry" - per-request TTFT latched on first response.output_item.added (via WebSearch)
- openai.com/index/unrolling-the-codex-agent-loop - Codex event stream series (direct fetch blocked; content via WebSearch summary)
- anomalyco/opencode issues #17471, #18108, #26167, #40146 and PRs #21688, #37220 - truncation-detection/continuation lessons (via WebSearch; issue bodies not fetched directly)
- Commit f91605b - json-parse-inside-retry-loop (git show)

### Tertiary (LOW confidence / unverified this session)
- OpenCode #13102 content (A2) - cited by design doc, not independently fetched
- The exact clamp behavior of sn-deepseek-flash (16385 vs 16384) - task-input memory note + tests:1508 comment

## Metadata

**Confidence breakdown:**
- Current state walk: HIGH - every claim carries a file:line from this session's reads
- Standard stack: HIGH (trivial - no new packages)
- Architecture/design decisions: MEDIUM-HIGH - grounded in the actual control flow; semantics choices (A3, A4) flagged for the discuss gate
- Pitfalls: MEDIUM-HIGH - local ones verified in code; external lessons (OpenCode/Codex) at MEDIUM via search snippets
- Test strategy: HIGH - fixtures and seams exist and were read

**Research date:** 2026-08-16
**Valid until:** 2026-08-30 (stable domain; gate.yaml backend caps can change without notice)
