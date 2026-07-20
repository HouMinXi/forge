# ADR-0005: Provider-aware sampling and reasoning parameter passthrough

**Date**: 2026-06-29
**Status**: accepted
**Deciders**: Minxi Hou
**Relates to**: [ADR-0003](0003-customer-supplied-backend.md) (supersedes its Open Item 1, "sampling-parameter passthrough"); [ADR-0004](0004-account-authenticated-backends.md)
**Source**: trinity-router (`~/code/trinity-router`) `workers.yaml` + `worker_pool.py` + its ADR-0005, validated on LiveCodeBench; plus exa research of each provider's live API docs (2026-06-29).

## Context

forge today sends a near-empty request body on all three formats (grounded
`llm_invoke.py`): openai = `{model, messages, temperature:0, max_tokens}`;
anthropic = `{model, max_tokens, messages}`; vertex = `{anthropic_version,
max_tokens, messages}`. It sends **no** `thinking`, `reasoning_effort`,
`stream`, or `max_completion_tokens`. Modern reasoning models need these or
they underperform or error:

- Reasoning depth lives in `thinking:{type,budget_tokens?}` and/or
  `reasoning_effort`. Without them DeepSeek/GLM/Claude-4.6 run at a default
  effort that is not the validated optimum (trinity measured DeepSeek Think
  Max 93.5% vs High 88.4% on LCB).
- `temperature` is **ignored or rejected in reasoning mode** on most current
  models: DeepSeek ignores it, MiMo forces 1.0, Kimi K2.7 cannot modify it,
  Claude Opus 4.7+ returns 400 for it. forge hardcoding `temperature:0` on the
  openai path is therefore both inflexible and, on some providers, wrong.
- Long thinking behind a proxy with a short gateway timeout dies unless the
  request streams (trinity's GLM via the c2846 proxy died at a 2-min gateway
  timeout; `stream:true` keeps the connection alive).
- The output-cap key splits: `max_completion_tokens` (OpenAI, MiMo, MiniMax
  new, Kimi via trinity) vs `max_tokens` (DeepSeek, GLM, Anthropic/Vertex,
  Kimi docs). No single key works for all openai-format providers.

The requirement (operator): forge should support the parameters each model
accepts, ship a sane default per provider, and let the customer adjust --
the same config model trinity-router uses for its LCB baseline.

## Decision

Adopt trinity-router's validated pattern: **typed config fields for the
structural reasoning controls, plus a generic passthrough for the long tail,
every field sentinel-defaulted to "do not send."**

### 1. Typed fields on the backend config (ported from trinity `WorkerConfig`)

| field | sentinel (omit from body) | meaning |
|-------|---------------------------|---------|
| `temperature: float` | `-1.0` | `>=0` -> send `temperature` |
| `max_completion_tokens: int` | `0` | `>0` -> output cap (mapped to the per-format key, below) |
| `thinking_type: str` | `""` | `"enabled"`/`"adaptive"`/`"disabled"` -> send `thinking.type` |
| `thinking_budget: int` | `0` | `>0` -> add `thinking.budget_tokens` |
| `reasoning_effort: str` | `""` | non-empty -> send `reasoning_effort` |
| `stream: bool` | `false` | `true` -> SSE request, reassembled to one response |
| `timeout_s: int` | `0` | `>0` -> per-backend timeout override (reasoning needs 1800) |

### 2. Generic `params` passthrough (the long tail)

An optional `params` dict merged verbatim into the request body, for keys
forge does not type: `top_p`, `stop`, `verbosity` (GPT-5.x), `reasoning_split`
(MiniMax), `clear_thinking` (GLM), `frequency_penalty`, `response_format`, or a
provider-specific output-cap key. Protected structural keys are rejected at
parse: `model`, `messages`, `stream`, `anthropic_version`, and the keys the
typed fields own (`temperature`, `thinking`, `reasoning_effort`,
`max_completion_tokens`, `max_tokens`).

### 3. Per-format body mapping (grounded in trinity `_build_*`)

- **openai**: `max_completion_tokens` KEY (or fall back to current
  `max_tokens` field); `thinking:{type,budget_tokens?}`; `reasoning_effort`;
  `temperature` if `>=0`; `stream`. (DeepSeek/GLM want the `max_tokens` KEY on
  the openai path -- the customer sets that via `params`.)
- **anthropic**: `max_tokens` KEY; `thinking`; `temperature` if `>=0`.
  (MiniMax thinking is server-side; forge already strips the leading `<think>`
  block, `llm_invoke.py:773`.)
- **vertex**: `max_tokens` KEY; `thinking:{type,budget_tokens?}`;
  `reasoning_effort`; `temperature` if `>=0`. (trinity validated top-level
  `reasoning_effort` on Vertex Claude; native Anthropic may instead use
  `output_config.effort` -- the implementer verifies per endpoint.)

### 4. Defaults: code default = "do not send"; shipped examples carry the values

The **code** default for every field is its sentinel, so an unconfigured
backend is byte-identical to today (except the openai path keeps `temperature:0`
as forge's chosen default for backward compatibility -- overridable). The
**validated per-provider values** ship as commented example backends in
`init_template.py` / `configuration.md` (mirroring trinity's `workers.yaml`):
DeepSeek `thinking_type:enabled, reasoning_effort:high`; Claude-4.6
`thinking_type:adaptive, reasoning_effort:high`; MiMo `stream:true,
max_completion_tokens:65536`; etc. "We ship a default, you adjust it."

### 5. Streaming is in scope (Phase 1)

Per operator decision (the training side adds streaming in parallel), `stream`
+ an SSE reassembler (port trinity `_read_sse`) land in the first
implementation, not deferred. Needed for reasoning models behind
gateway-timeout proxies.

### 6. Output-cap key: forge owns the key name, never double-sends (OpenRouter-validated)

The output-cap key splits across openai-format providers (`max_completion_tokens`
for OpenAI/MiMo/MiniMax-new; `max_tokens` for DeepSeek/GLM). The first draft
exposed `max_tokens` via the generic `params` escape hatch, but that path
DOUBLE-SENDS: the openai builder unconditionally writes `max_completion_tokens`
(cap falls back to the `max_tokens` field default 16384, never omittable), so a
DeepSeek backend would carry BOTH `max_completion_tokens:16384` AND the params
`max_tokens` -- a contradictory double cap that either 400s (strict endpoints)
or silently uses the wrong (small) value and truncates the review.

How OpenRouter (a 100+ provider aggregator) solves it, exa-researched 2026-06-29
(openrouter.ai/docs reasoning-tokens + parameters):
- It accepts both `max_tokens` and `max_completion_tokens` as aliases but treats
  `max_completion_tokens` as canonical (`max_tokens` marked deprecated), and the
  PLATFORM normalizes to whatever the downstream provider wants -- the caller
  never picks the provider's key name.
- Reasoning budget is a SEPARATE `reasoning:{effort|max_tokens}` object,
  bidirectionally translated (effort<->budget_tokens), never conflated with the
  output cap.
- Hard invariant it enforces: "`max_tokens` must be strictly higher than the
  reasoning budget to ensure there are tokens available for the final response
  after thinking." (Same root cause as trinity's "kimi returns empty text at
  max_tokens 4096" -- thinking eats the whole budget.)

DECISION (revises section 3's escape-hatch sketch; amended 2026-06-30 to
key-follows-field): forge sends exactly ONE output-cap key, and the KEY FOLLOWS
THE POPULATED FIELD so neither the new nor the legacy cap key needs per-backend
config:
- openai format: if the customer set `max_completion_tokens` (>0) -> send the
  `max_completion_tokens` key; otherwise send the `max_tokens` key. An OpenAI
  o-series backend sets `max_completion_tokens` and a DeepSeek/GLM backend sets
  `max_tokens`, each getting the wire key it needs with zero extra config.
- anthropic/vertex format: ALWAYS the `max_tokens` key (the Anthropic wire schema
  has no `max_completion_tokens`); if the customer set the `max_completion_tokens`
  field, forge maps its VALUE onto `max_tokens`. The field selects the key on the
  openai format only.
- `outcap_key` survives as an explicit per-backend OVERRIDE for the rare endpoint
  whose key name does not match the field-derived default; it is no longer the
  primary mechanism. `max_tokens` STAYS a protected key (never a `params` escape
  hatch).

The cap VALUE is unchanged (`max_completion_tokens` field if set, else the
`max_tokens` field). `_apply_params` writes the cap under exactly one resolved
key, never both. This keeps the OpenRouter invariant (one authoritative cap, the
platform never double-sends) and ADR-0004's no-per-vendor-code rule (the customer
declares intent by which field they fill -- no base_url/model sniffing), while
removing the config friction the format-default approach imposed on DeepSeek/GLM
(the most common forge openai backends). Superseded sketch: "default
`max_completion_tokens` on openai, `max_tokens` on anthropic/vertex; customer
overrides via `outcap_key`." Sections 3-4 mentions of the per-format-default
mapping are superseded by this paragraph.

## Provider parameter matrix (exa-researched 2026-06-29)

| model | format | thinking | effort values | out-cap key | temperature |
|-------|--------|----------|---------------|-------------|-------------|
| Claude Opus/Sonnet 4.6 | vertex/anthropic | `{type:adaptive}` (budget_tokens deprecated) | low/med/high(def)/xhigh/max | `max_tokens` | works 0-1; **Opus 4.7+ 400** |
| Claude Haiku 4.5 | vertex/anthropic | `{type:enabled,budget_tokens}` | n/a | `max_tokens` | works |
| GPT-5.5 (GA flagship) | openai (Responses preferred) | implicit reasoning | none/minimal/low/med(def)/high/xhigh | `max_completion_tokens` (`max_output_tokens` on Responses) | Chat 0-2; reasoning prefers Responses |
| GPT-5.6 Sol/Terra/Luna | openai (Responses) | reasoning + "ultra" multi-subagent | 5.5 family + ultra | `max_completion_tokens` | LIMITED PREVIEW (2026-06-26): ~20 US-gov-approved partners only, NOT generally available -- use GPT-5.5; do not rely on 5.6 |
| DeepSeek V4 flash/pro | openai or anthropic | `{type:enabled/disabled}` (default enabled, via extra_body in SDK) | high/max (low/med->high, xhigh->max) | `max_tokens` (<=384K) | **ignored in thinking mode** |
| MiniMax M3 | anthropic (pref) / openai | `{type:adaptive/disabled}` (omit=on) | n/a (auto) | `max_tokens`(legacy)/`max_completion_tokens`(new) | 0-2 def 1; penalties ignored |
| GLM 5.2 | openai (z.ai /paas/v4) | `{type:enabled}` (auto-decides) | none/minimal/low/med/high/xhigh/max(def) | `max_tokens` (out<=128K) | def 1.0, top_p 0.95 |
| Kimi K2.7-code | openai (moonshot/kimi.ai) | always-on, do NOT send `thinking` | n/a | `max_tokens` >=16K (trinity uses max_completion_tokens) | **cannot modify** |
| MiMo V2.5-pro | openai (xiaomimimo) | `{type:enabled}` via extra_body | n/a | `max_completion_tokens` | **forced 1.0 in thinking** |

Cross-cutting: (a) `thinking:{type[,budget_tokens]}` is near-universal; forge
sends it top-level in the raw JSON body (the `extra_body` wrap is an OpenAI-SDK
detail forge does not use). (b) `temperature` is mostly inert/forbidden in
reasoning mode -> "do not send" default is the safe and correct choice. (c) The
out-cap key is genuinely mixed -> forge owns the key name per format with a
per-backend `outcap_key` override (see section 6); never via `params`, never
double-sent.

## Alternatives Considered

### Alternative 1: Default temperature 0 on all formats
Rejected. Most current reasoning models ignore or reject `temperature`
(DeepSeek/MiMo/Kimi/Claude-4.7+). Defaulting 0 would be inert at best and a
400 at worst. The sentinel ("do not send") is both zero-regression and correct.

### Alternative 2: Per-provider adapters inside forge
Rejected (same reason as ADR-0004). Eight providers with drifting params is
unbounded maintenance. Typed fields cover the shared reasoning controls; the
`params` passthrough covers the rest without forge knowing each provider.

### Alternative 3: Pure generic passthrough, no typed fields
Rejected. `thinking:{type,budget_tokens}` needs structured object construction
that differs per format; a flat dict cannot express it cleanly, and the
defaults could not be sentinel-managed. Typed fields for the structural ones,
passthrough for the flat long tail.

## Consequences

### Positive
- forge reviews on reasoning models run at the validated optimum (thinking +
  effort + adequate output budget), not a silent default.
- Zero regression: every field defaults to its sentinel (omit); existing
  backends behave identically.
- forge stays provider-agnostic: typed fields + passthrough, no per-vendor code.

### Negative
- Streaming adds an SSE reassembler to `llm_invoke.py` (forge is single-shot
  today) -- real new code and test surface, accepted as Phase 1 scope.
- The out-cap key and effort-key-location differences require the implementer
  to verify a couple of per-endpoint specifics (noted inline).

### Risks
- A miswired `params` key reaches the provider verbatim and may 400. Mitigation:
  reject protected/structural keys at parse; the provider's own error surfaces
  the rest (forge fails closed on a bad response, never silent-green).

## G1-G5 impact

Strengthens G1 ("real backend is the default review engine"): forge now drives
each backend at its validated parameters, so the real-backend path is not just
reachable but tuned. G2-G5 unchanged.
