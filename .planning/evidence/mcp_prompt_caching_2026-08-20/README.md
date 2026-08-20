# MCP vs CLI prompt-caching experiment, 2026-08-20

Backend pinned: mimo-pro (mimo-v2.5-pro, anthropic format,
api.xiaomimimo.com). Same diff both runs: HEAD~1..HEAD on forge main
(4087b05e, 2 files: cli.py + test_post_image_window.py), --committed,
--allow-main. Run order: CLI first (09:4x), MCP second (moments later).

NOTE: an earlier draft of this README (README_v1_draft_superseded.md)
claimed the MCP path sends a near-empty prompt. That claim is WRONG and
is corrected below -- the low input-token numbers are a cache-hit
measurement artifact, not a broken prompt. The draft is kept verbatim
per Fleet Law S2b (evidence immutable), not as a live conclusion.

## Artifacts

| file | what it is |
|------|------------|
| cli_run1_mimo_full.log | full CLI-path review log (round 0) |
| mcp_run1_TIMEOUT_result.json | raw MCP job result (job cap TIMEOUT) |
| probe_mimo_cache_fields.py + _results.json | raw-API caching probe, 6 calls |

## CLI path (code-forge review --committed --backend mimo-pro --allow-main)

- Wrapper wall clock: 1146.8s (includes shell overhead); forge-internal
  wall 920.7s, LLM time 336.7s
- Cost line: 85426 tokens (50922 in + 34504 out), 3 passes
- Per-pass tokens (round 0): qodo 16974 in / 8605 out; adversarial
  16973 in / 9061 out; expert 16975 in / 16838 out
- Verdict: FAIL findings=4 confirmed=1 uncertain=2 dismissed=1

## MCP path (forge_review tool, backend=mimo-pro, committed=true, allow_main=true)

- job_id b5a5a7ec-d40a-4d8f-9165-c6cfa9c93d22
- Killed by the MCP job's own 900s cap while still mid-run (mutation
  baseline stage): verdict=TIMEOUT, exit_code=130, duration_s=900.10
- Per-pass tokens (round 1): expert 15 in / 13929 out; qodo 14 in /
  15148 out; adversarial 13 in / 32259 out
- Falsify stage ran to completion on real findings: cli.py:910
  CONFIRMED, cli.py:1023 UNCERTAIN, test_post_image_window.py:235
  CONFIRMED, :263 DISMISSED -- the model demonstrably had the diff

## Probe result (decisive, [KNOWN] from raw API responses)

mimo-pro performs AUTOMATIC prefix caching with NO cache_control sent:

| call | cache_control | input_tokens | cache_read_input_tokens | elapsed_s |
|------|---------------|--------------|-------------------------|-----------|
| A1 | no  | 4088 | 192  | 12.2 |
| A2 | no  | 56   | 4224 | 12.3 |
| A3 | no  | 56   | 4224 | 6.5  |
| B1 | yes | 56   | 4224 | 14.4 |
| B2 | yes | 56   | 4224 | 3.3  |
| B3 | yes | 56   | 4224 | 2.0  |

Two facts follow directly:

1. On a cache hit, `input_tokens` reports ONLY the uncached delta
   (56 of 4280 total). Explicit cache_control changes nothing -- the
   backend already caches the identical prefix automatically.
2. forge's _invoke_anthropic (llm_invoke.py) reads only
   input_tokens/output_tokens from usage and DISCARDS
   cache_read_input_tokens / cache_creation_input_tokens.

## Corrected interpretation of the MCP run's 13-15 input tokens

[COMPUTED] 16974 (CLI, uncached) vs 13-15 (MCP, same diff minutes
later) is exactly the A1-vs-A2 shape from the probe: the MCP run's
prompts hit the automatic prefix cache nearly in full because the CLI
run had just sent byte-identical prompts for the same diff. The 13-15
is the uncached residual, NOT a missing prompt. Corroborated by the
falsify stage operating on exact line numbers of the real diff.

So the v1 draft's "third hypothesis" (MCP dispatch truncates the
prompt) is falsified. What actually happened:

- Hypothesis (a), literal form ("no cache_control -> no caching"):
  FALSIFIED for mimo-pro. Caching engages automatically on an
  identical prefix, with or without cache_control.
- Hypothesis (b) (different backend/config): FALSIFIED here -- both
  runs hit mimo-pro (log's own backend field).
- The charter's original 17min-vs-6-7min gap: NOT REPRODUCED in this
  experiment. On mimo-pro, CLI took 920.7s wall and MCP was killed at
  its 900s cap while still running -- i.e. both paths cost roughly the
  same (~15 min). The 2.5x gap was measured on the office machine
  against oc-deepseek (zen gateway); whether THAT gateway forwards
  caching semantics remains unmeasured ([UNKNOWN], charter flags this
  too).

## Measurement artifacts documented along the way

- state.json cost.per_pass values are round-total // 3, not real
  per-pass telemetry (machine.py _execute_round). Real per-pass
  numbers live only in the stderr progress lines. Matches existing
  memory note on this artifact.
- The MCP run starting at "round 1" (not 0) is
  _continuation_round_index() avoiding receipt-filename collisions
  across CI invocations -- intentional, not a bug.

## What this means for the charter's Scope items 2-4

1. The 13-15-token "anomaly" that motivated deeper MCP-dispatch
   tracing is RESOLVED as a cache-hit artifact. No MCP-specific prompt
   bug exists on this path (outlet=subprocess shells out to the same
   CLI binary; both runs also produced equivalent review work).
2. Real gap to explain is on the ORIGINAL backend (oc-deepseek via
   zen gateway), where the 17min measurement was taken. Re-measuring
   CLI vs MCP there is the next experiment -- this session cannot run
   office backends from Z66.
3. Minimal fix that IS justified from this evidence: surface
   cache_read_input_tokens / cache_creation_input_tokens in forge's
   usage accounting and progress lines. Without it, every cached run
   looks like a near-empty prompt (this session misdiagnosed exactly
   that way), and cost lines underreport real prompt size on hits.
4. Parity acceptance (~20%): on mimo-pro the two paths are already at
   parity up to the 900s job cap; the cap, not the path, is what
   truncates MCP runs of this size.

## Office machine findings (2026-08-20 evening, via ssh yinhe-laptop)

The office machine (ThinkBook, where the 17min measurement was taken)
holds the authoritative record: skill reference
`forge-review-method-decision-2026-08-20.md` in hermes-kanban-
orchestration. Its own table: skill-inline ~4.5 min/round, CLI
(mimo-pro) 17+ min, forge MCP (mimo-pro) 17+ min -- i.e. NO CLI-vs-MCP
gap on the same backend. The charter's "CLI ~6-7 min" figure's
provenance was not found (kanban.db is an empty file).

### Office config differs from Z66 (same backend name, different API surface)

| key | Z66 | office |
|-----|-----|--------|
| format | anthropic | openai |
| base_url | .../anthropic | .../v1 |
| stream | false | true |
| timeout_s | 2400 | 900 |
| default | no | yes |

### OpenAI-surface probe (probe_mimo_openai_surface*)

A1 (first call): cached_tokens 4224/4280 -- a hit derived from the Z66
anthropic-surface probe ~45min earlier (identical filler prefix, same
account): the cache is account-scoped, shared across API surfaces,
TTL >= ~45min. C1/C2 (same prefix, different tails): still 4224 cached
-- real prefix caching, not exact-request replay. The office-side
diagnosis "mimo-pro has no prefix cache" is falsified for BOTH
surfaces.

### Real 17min decomposition (office_forge_r8_stage_timings.log)

CI-mode round 7 on the 11-commit diff, wall 565.5s:
- 3 passes CONCURRENT: t+0 -> t+113.4s (TTFT 37/42/61s; 43737 in / 3
  = 14579 in per pass -- full prefill each, cross-persona prefix never
  matches, consistent with Z66 CLI run)
- 6 falsify calls SERIAL: t+113 -> t+345 (18-50s each)
- mutation baseline (local pytest): t+345 -> t+565 (~220s)
- falsify prompts are TINY (falsify_real.py _PROMPT_PREFIX = 568 chars
  + one finding): their 18-50s each is model thinking time, NOT
  prefill -- caching cannot help them at all

## Final root-cause statement

1. There is NO MCP-vs-CLI code-path difference (outlet=subprocess
   shells out to the same binary); office's own record shows both at
   17+ min on mimo-pro. The charter's acceptance criterion (MCP within
   ~20% of CLI) is ALREADY met on this backend.
2. mimo-pro has automatic prefix caching on both API surfaces. What
   defeats it in real forge load: persona framing precedes the shared
   diff context, so the 3 passes never share a prefix (Z66 CLI run:
   16974/16973/16975 full-price inputs), and falsify prompts carry no
   context to cache.
3. But prompt caching is NOT the dominant cost: passes run
   concurrently (prefill overlaps), falsify prompts are ~1K tokens,
   and the wall is dominated by serial falsify thinking time (scales
   with finding count) + mutation pytest. A perfect caching fix saves
   roughly 2 of 3 passes' prefill, ~80-100s of 565s (~15%).
4. skill-inline is 4.5min because it skips falsify + mutation
   entirely, not because of caching.

## Implications for the charter

- Cache-field surfacing (visibility fix) is justified: BOTH the office
  "no prefix cache" misdiagnosis and this session's "near-empty
  prompt" misdiagnosis trace to forge discarding cached-token fields.
- The charter's expected outcome (caching -> 2.5x speedup -> parity)
  does not survive the decomposition. The honest fix scope is:
  (a) surface cache fields [small, clearly worth it];
  (b) optional prefix-stable prompt ordering [~15% ceiling];
  (c) the actual big levers -- serial falsify, mutation pytest --
    are review-semantics changes the charter lists as non-goals, so
    they need the architect's decision, not a unilateral fix.

## Concurrent identical-prefix probe (probe_mimo_concurrent_prefix*)

Decision experiment for the prefix-stable-reorder fix. Fresh prefix,
3 truly-concurrent calls (anthropic surface; the openai leg reused the
same prefix after the anthropic leg planted it, so it measures
cross-surface reuse only, not in-flight behavior):

- C1 miss (full prefill 6558 in)
- C2 HIT (cache_read 6720) -- its prefill started after C1's entry
  committed
- C3 miss (full prefill 6559)

In-flight prefix dedup exists but is racy (1 of 2 overlapping calls
benefited). Expected wall benefit of reordering persona framing behind
the shared diff context in forge's CONCURRENT passes: ~1 trailing pass
hits, saving ~15-20s of a ~565s round (~3%), below mimo's own latency
variance. Cross-round reuse already works at full-prompt granularity
with the current persona-first ordering (Z66 MCP run: 13-15 input
tokens). Decision: prefix-stable reorder CUT -- expected gain does not
justify changing prompt structure (review-output drift risk).

## Open questions

- Does the zen gateway (oc-deepseek) forward caching semantics?
  [UNKNOWN] -- needs the office machine.
- Do the three persona passes share a byte-identical prefix in
  forge's prompt construction? [INFERRED] yes (the cross-run,
  cross-persona cache hit at 13-15 residual implies the shared
  prefix, including persona framing, matched the CLI run's
  corresponding calls), but not directly verified at byte level.
