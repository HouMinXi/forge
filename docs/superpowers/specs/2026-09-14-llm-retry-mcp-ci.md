# LLM invoke retry for MCP and CI

Status: draft
Date: 2026-09-14
Base: `f4b3773c36c96d670cd3cc76094eb833ae685fd4` (Gitea `origin/main`)

## Problem

Free Omni routes (example: `tkr-glm-5.3-free`) fail a review pass on a
single flaky call. The operator watching an MCP job or a CI run sees a
dead pass, not a retry. Two surfaces are named: MCP `forge_review` and
`--mode ci`.

HTTP retry already exists inside `_invoke_api`
(`llm_invoke.py:1540`, default `max_attempts=5`, 429/5xx/empty/no_json).
CLI `_run` already forwards `gate.yaml retry` into `build_l1_provider`.
MCP subprocess outlet is that CLI. So "no retry" is the wrong diagnosis.

What is actually missing:

1. Retry lines write `sys.stderr` and never flush. MCP
   `forge_job_status` tails a redirected stderr file. Python block-buffers
   that file. The operator never sees a retry while the job is running,
   and the last attempt has no "exhausted" line -- it just raises.
2. Socket `TimeoutError` is `retryable=False` (`llm_invoke.py:1648`).
   Connect timeouts (`URLError` wrapping `TimeoutError`) already retry.
   Free models hang then die on the read timeout. Paid 1800s backends
   must not spend 5 x 1800s unless asked.
3. MCP sampling outlet (`invoke_sampling`) has no retry loop. Empty /
   no_json from a free Copilot/IDE model fail the pass on first try.

## Non-goals

- Do not retry 401/403/credentials, truncation, Copilot stub models,
  or protocol-shaped falsify replies.
- Do not add a second retry loop around `StateMachine` rounds. CI still
  runs one round; retries stay per LLM call.
- Do not change `probe_backend_live` (`max_attempts=1` stays).
- Do not invent a new backend. This is invoke-layer.

## Literature

- OpenAI rate-limit guide: exponential backoff + jitter; honor
  `Retry-After`. Already in `_invoke_api`.
- Connect vs read timeout: connect already retries; read timeout is the
  gap. Retrying a 1800s read timeout is a wall-clock bomb unless opt-in.
- `progress.py` docstring already names the MCP stderr-file case as the
  reason every emit flushes.

## Decision

Keep the existing `_invoke_api` loop as the single retry owner for API
backends. Close the three gaps above.

`gate.yaml` / user config:

```yaml
retry:
  max_attempts: 5          # already valid, 1..10
  initial_delay_s: 2.0     # already valid, 0.1..30
  retry_timeouts: false    # NEW. default false. true = TimeoutError retries
```

Project `gate.yaml` wins over `~/.config/code-forge/config.yaml` for the
`retry` mapping (same "project wins by name" idea as backends, but for
the one retry block). Missing keys fill from the other side, then from
defaults.

Timeout retries stay off by default so a 1800s backend cannot silently
become a multi-hour review. Free-model operators set
`retry.retry_timeouts: true` (and a short `timeout_s` on that backend).

Logs (both MCP and CI, both success-retry and exhausted):

- Each retry: `progress.emit` so it flushes into the MCP stderr log.
  Text still contains `code-forge: retrying` plus attempt, delay, cause.
- Last failure: `progress.emit` with `code-forge: retry exhausted` plus
  attempt count and cause, then raise. Today this line does not exist.

Sampling: the same `max_attempts` / `initial_delay_s` / retry_timeouts
on `invoke_sampling`. Retry empty and no_json. Do not retry truncated
or stub_model. `asyncio.sleep` for the delay. Log the same two lines.

## Call sites (from graph + file)

API retry loop: `llm_invoke.py::_invoke_api` only.

Callers that already pass `max_attempts` from `retry_cfg`:
`cli.py` `build_l1_provider` / `build_grouped_l1_provider` (CI and LOCAL
and MCP subprocess).

Callers that use defaults (5 attempts) and need no signature change:
`falsify_real.py:148`, `cli.py::_spawn`, `contract_loader.py`.

Sampling: `factories.py:build_sampling_l1_provider` ->
`invoke_sampling`. MCP `_dispatch_sampling` must load `retry` from
gate.yaml the same way CLI `_run` does.

## Alternatives

1. Do nothing. HTTP retry already covers 429/5xx/empty. Cost: timeout
   and sampling still one-shot; MCP still silent. Rejected -- that is
   the reported failure.
2. Only flush logs. Smallest change. Cost: timeout and sampling stay
   one-shot. Rejected as incomplete for free models.
3. Make every TimeoutError retryable with no flag. Cost: 1800s x 5.
   Rejected.
4. This spec (flush + exhausted line + opt-in timeout retry + sampling
   loop). Chosen.

## Inversion

- Retrying 401 burns quota. Keep credentials `retryable=False`.
- Retrying 1800s timeout burns wall clock. Flag defaults false.
- A test that only checks `sys.stderr` and never injects a timeout
  would stay green if timeout retry is wired wrong. Inject at
  `retryable=False` on TimeoutError and at the sampling loop.

## Acceptance

- 429 then 200: log contains `code-forge: retrying`, MCP-style stderr
  capture sees it after the write (flush).
- 429 x max_attempts: log contains `code-forge: retry exhausted`, then
  `LLMInvokeError`.
- Timeout with `retry_timeouts=false`: one attempt, exhausted line names
  timeout, existing `test_timeout_not_retried_raises_immediately` still
  green (rename if the exhausted line is the new assertion).
- Timeout with `retry_timeouts=true`: N attempts, then raise.
- Sampling empty then JSON: retries; truncated never retries.
- CI and MCP subprocess share the loop (no second implementation).
- `probe_backend_live` still `max_attempts=1`.
