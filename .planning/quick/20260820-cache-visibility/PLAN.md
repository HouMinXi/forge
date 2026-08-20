---
status: in-progress
created: 2026-08-20
type: quick
---

# Quick Task: surface prompt-cache token fields in usage accounting

## Problem

Backends report cache-hit tokens in usage (anthropic:
`cache_read_input_tokens`; openai: `usage.prompt_tokens_details.cached_tokens`),
but `llm_invoke.py` extracts only input/output tokens and discards the
cache fields. Consequence measured twice on 2026-08-20: a cache-hit run
reports near-zero input tokens and looks like a broken/empty prompt
(Z66 session misdiagnosis), and a cache-miss workload looks like "the
backend has no cache" (office diagnosis). Both wrong for the same
reason: the diagnostic field is dropped on the floor.

Evidence: `.planning/evidence/mcp_prompt_caching_2026-08-20/` (probe
results on both mimo API surfaces + corrected README).

## Change (pure observability, no review-semantics change)

1. `llm_invoke.py` `Usage`: add `cached_input_tokens: int = 0`.
2. Extract at four caller sites:
   - openai: `(usage_data.get("prompt_tokens_details") or {}).get("cached_tokens", 0)`
   - anthropic: `usage_data.get("cache_read_input_tokens", 0)`
   - vertex: same as anthropic (vertex responds in anthropic format)
   - sampling unwrap (`claude -p` inner-result): same as anthropic
3. `factories.py`: accumulate `total_cached`; aggregate into the
   round `Usage`; per-pass stderr line appends `(N cached)` when > 0.
4. `machine.py` + `state.py`: persist `total_cached_tokens` in
   state.json cost (default 0, backward compatible).
5. `sarif.py` tokenCost: add `cachedTokens`.

## Tests

- `tests/test_llm_invoke.py`: openai response with
  prompt_tokens_details.cached_tokens -> Usage.cached_input_tokens;
  anthropic response with cache_read_input_tokens -> same; absent
  fields -> 0.
- State round-trip: state.json with total_cached_tokens loads;
  without the key loads as 0.
- Progress line: cached > 0 renders "(N cached)", 0 renders without.
- Bug-injection proof: delete one extraction line -> test FAILS ->
  restore -> PASSES.

## Non-goals

- No cache_control breakpoints, no prompt reordering (measured ~3%
  ceiling under concurrent passes; evidence probe_mimo_concurrent_prefix*).
- No falsify/mutation timing changes.
