# Phase 31: CN Backend Robustness - Research

**Researched:** 2026-06-27
**Domain:** LLM HTTP error handling, retry, provider error classification
**Confidence:** HIGH

## Summary

Phase 31 adds resilience to forge's LLM invocation layer against the error
diversity of five CN providers (DeepSeek, MiMo, Zhipu, MiniMax, Kimi).  Today
every HTTP error raises `LLMInvokeError` immediately with no retry, no
provider-specific classification, and no actionable message.  The phase adds
exponential-backoff retry in `_invoke_api`, provider-specific error code maps,
Retry-After header support, pass-level retry in `factories.py`, and actionable
error messages.

The codebase is well-structured for this change.  `_invoke_openai` and
`_invoke_anthropic` both catch `urllib.error.HTTPError` and read the body
excerpt before raising `LLMInvokeError`.  The retry loop wraps this catch block.
`LLMInvokeError` needs a `retryable: bool` attribute.  `factories.py` already
catches `LLMInvokeError` per pass and creates INFRA findings -- pass-level retry
wraps this existing catch.  `gate.yaml` already has a JSON schema and loader;
adding `retry.*` fields follows established patterns.

**Primary recommendation:** Implement retry as an inline loop in `_invoke_api`
(not a decorator), because Retry-After header context and body-based error
classification require access to the `urllib.error.HTTPError` object inside the
catch block.  Keep changes to four files: `llm_invoke.py` (retry loop + error
map), `factories.py` (pass-level retry), `gate_check.py` (retry config
validation), `gate.schema.json` (retry schema).

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-31-01:** Two-layer retry. HTTP-level retry in llm_invoke.py (handles
  429/5xx/network errors before the caller sees them) AND pass-level retry
  in factories.py (retries the whole pass once on LLMInvokeError, then marks
  INFRA finding and moves to next pass).
- **D-31-02:** HTTP retry parameters are configurable via gate.yaml
  (`retry.max_attempts`, `retry.initial_delay_s`).  Defaults: max 5 attempts,
  2s initial delay, exponential backoff x2, random jitter 0-500ms.  When
  Retry-After header is present (DeepSeek, Kimi), use max(computed_delay,
  header_value).  Total worst-case wait ~62s (2+4+8+16+32).
- **D-31-03:** Error code mapping hardcoded in llm_invoke.py (a dict keyed
  by provider name from BackendConfig.name).  Zhipu: only common codes
  (1302 balance, 1305 invalid key, 1308 concurrency limit); rest default
  to retryable.  MiniMax: 1002 rate limit (retryable), 1008 balance
  (non-retryable), 1039 token limit (retryable), 1041 conn limit
  (retryable), 2045 rate growth (retryable), 2056 usage limit
  (non-retryable).
- **D-31-04:** Body-based error detection: _invoke_openai/_invoke_anthropic
  check the parsed JSON for error indicator fields (Zhipu: `error.code`;
  MiniMax: `base_resp.status_code`) BEFORE attempting content extraction.
- **D-31-05:** Retryable HTTP statuses: 429, 500, 502, 503, 504.
  Retryable non-HTTP: TimeoutError, URLError (network).
  Non-retryable: 400, 401, 402, 403, 404, 422 + provider-specific
  non-retryable body codes (Zhipu 1302/1305, MiniMax 1008/2056).
- **D-31-06:** L1 passes stay serial (for loop in factories.py).
  No concurrency control.
- **D-31-07:** Retry exhaustion = fail-closed (INFRA finding + FAIL verdict).
- **D-31-08:** Error message format:
  `"code-forge: {provider} backend: {problem} ({HTTP code/body code}). {actionable suggestion}"`
- **D-31-09:** Retry progress printed to stderr during retry loop.

### Claude's Discretion
- Retry as decorator vs inline loop (recommendation: inline loop, see below)
- Exact placement of body-based detection within _invoke_openai / _invoke_anthropic
- How retry config is threaded from gate.yaml to llm_invoke (separate args vs dataclass)

### Deferred Ideas (OUT OF SCOPE)
- Provider fallback chain (switch backend on exhaustion)
- Circuit breaker pattern (fast-fail after N consecutive)
- Per-backend retry config override
- Zhipu full 21 sub-code mapping

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ROBUST-01 | HTTP 429 triggers retry with exponential backoff + jitter | Retry loop in `_invoke_api` wrapping the HTTPError catch; `LLMInvokeError.retryable` flag; error code map classifies 429 as retryable |
| ROBUST-02 | Retry-After header honored when present | `urllib.error.HTTPError.headers.get('Retry-After')` verified to return the value; parsed as int seconds; max(computed_delay, header_value) |
| ROBUST-03 | Provider-specific error codes mapped | Error code map dict in llm_invoke.py; Zhipu `error.code` (string, not int) + MiniMax `base_resp.status_code` (int) body detection |
| ROBUST-04 | L1 pass dispatch respects concurrency limit | D-31-06: serial dispatch + HTTP retry + backoff naturally avoids rate storms |
| ROBUST-05 | HTTP 402/403 fast-fail with clear error message | Non-retryable classification in error map; actionable message per D-31-08 |

</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| HTTP retry loop | llm_invoke.py (_invoke_api) | -- | Retry is transport-level; belongs where HTTP calls are made |
| Error classification | llm_invoke.py (error map) | -- | Provider error codes are wire-protocol details |
| Body-based detection | llm_invoke.py (_invoke_openai, _invoke_anthropic) | -- | Must happen before content extraction in the format-specific handlers |
| Pass-level retry | factories.py (build_l1_provider) | -- | Pass-level = one level above transport; factories owns L1 dispatch |
| Retry config parsing | gate_check.py (load_gate_config) | -- | Follows existing pattern for gate.yaml sections |
| Retry config schema | gate.schema.json | -- | Follows existing schema/loader agreement pattern |
| Retry config threading | cli.py -> build_l1_provider -> llm_invoke | -- | Same path as backend config today |

## Standard Stack

### Core
No new external dependencies.  All retry logic uses stdlib only.

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| urllib.request | stdlib | HTTP calls (existing) | Already in use; no change |
| urllib.error | stdlib | HTTPError handling (existing) | Already in use; needs Retry-After header access |
| time | stdlib | sleep for backoff (existing) | Already imported |
| random | stdlib | jitter (new import) | Stdlib; no dep weight |
| json | stdlib | Body parsing (existing) | Already imported |

No packages to install.  No Package Legitimacy Audit needed.

## Architecture Patterns

### System Architecture Diagram

```
gate.yaml retry:            cli.py _run()
  max_attempts: 5     ------>  loads retry config
  initial_delay_s: 2           passes to build_l1_provider
                                    |
                                    v
                            factories.py build_l1_provider
                              _provider() loop per pass
                                    |
                         [pass-level retry: 1 retry on LLMInvokeError]
                                    |
                                    v
                            llm_invoke.py llm_invoke()
                              _invoke_api()
                                    |
                         [HTTP-level retry loop: max_attempts with backoff]
                                    |
                    +---------------+----------------+
                    |               |                |
            _invoke_openai   _invoke_anthropic  _invoke_vertex
                    |               |                |
              [body-based     [body-based       [standard
               error detect:   error detect:     HTTP errors]
               Zhipu error.code  MiniMax
               base_resp]        base_resp]
                    |               |                |
                    v               v                v
              urllib.request.urlopen (HTTP call)
                    |
              [HTTPError caught -> classify retryable/non-retryable]
              [Retry-After header -> max(computed, header)]
              [retryable -> sleep + retry]
              [non-retryable -> raise LLMInvokeError(retryable=False)]
```

### Recommended Project Structure (changes only)

```
src/code_forge/
  llm_invoke.py        # +retry loop in _invoke_api, +error map, +body detect
  factories.py         # +pass-level retry (1 retry) in _provider()
  gate_check.py        # +validate_retry_config()
  gate.schema.json     # +retry section schema
  backend.py           # NO CHANGES (retry config separate from BackendConfig)
```

### Pattern 1: Inline Retry Loop (not decorator)

**What:** The retry loop is an inline while loop inside `_invoke_api`,
wrapping the format-specific dispatch call.

**When to use:** When retry logic needs access to exception attributes
(Retry-After header, HTTP status code, body content) that a generic decorator
cannot access.

**Why not a decorator:** A `@retry(max_attempts=N)` decorator cannot:
1. Read Retry-After header from `urllib.error.HTTPError.headers`
2. Classify retryable vs non-retryable from body-parsed error codes
3. Print per-attempt stderr progress with provider name
4. Compute `max(computed_delay, retry_after_value)`

**Example:**
```python
# Source: derived from codebase analysis (no external ref needed)
def _invoke_api(self, prompt, backend, timeout_s, expected_keys=None,
                max_attempts=5, initial_delay_s=2.0):
    import random as _random

    for attempt in range(max_attempts):
        try:
            # dispatch to _invoke_openai / _invoke_anthropic / _invoke_vertex
            ...
            return LLMResult(...)
        except LLMInvokeError as exc:
            if not exc.retryable or attempt == max_attempts - 1:
                raise
            computed = initial_delay_s * (2 ** attempt)
            jitter = _random.uniform(0, 0.5)
            delay = computed + jitter
            if exc.retry_after is not None:
                delay = max(delay, exc.retry_after)
            sys.stderr.write(
                "code-forge: retrying %s (%d/%d, waiting %.1fs)...\n"
                % (backend.name, attempt + 2, max_attempts, delay)
            )
            time.sleep(delay)
    # unreachable -- loop either returns or raises
```

### Pattern 2: Body-Based Error Detection

**What:** Before attempting `resp_data["choices"][0]["message"]["content"]`
(OpenAI) or `resp_data["content"][0]["text"]` (Anthropic), check for
provider-specific error fields in the parsed response JSON.

**When to use:** When a provider returns HTTP 200 but embeds an error
inside the response body (Zhipu `error.code`, MiniMax `base_resp.status_code`).

**Critical finding:** Zhipu `error.code` is a **string**, not an integer
(e.g., `"1302"` not `1302`). The error map must convert to int or compare
as string. [CITED: docs.z.ai/api-reference/api-code]

**Critical finding:** MiniMax `base_resp.status_code` is an **integer**.
However, when MiniMax is used via the anthropic format (which forge does),
errors come as standard HTTP errors, not embedded `base_resp`. The `base_resp`
check only applies in the OpenAI format path (`_invoke_openai`).
[CITED: platform.minimax.io/docs/api-reference/text-anthropic-api]

**Example:**
```python
# In _invoke_openai, after json.loads(response.read()):
# Check for Zhipu error.code (string) in response body
error_obj = resp_data.get("error")
if error_obj and isinstance(error_obj, dict):
    error_code = error_obj.get("code")
    if error_code is not None:
        _raise_provider_error(backend.name, str(error_code), error_obj.get("message", ""))

# Check for MiniMax base_resp.status_code (int) in response body
base_resp = resp_data.get("base_resp")
if base_resp and isinstance(base_resp, dict):
    status_code = base_resp.get("status_code", 0)
    if status_code != 0:
        _raise_provider_error(backend.name, str(status_code), base_resp.get("status_msg", ""))
```

### Pattern 3: Pass-Level Retry

**What:** In `factories.py` `_provider()`, when `LLMInvokeError` is caught
for a pass, retry the pass once before creating an INFRA finding.

**When to use:** When the HTTP-level retry in `_invoke_api` exhausted all
attempts but the failure might be transient at a higher level (e.g., a
different prompt might succeed, or the rate limit has lifted).

**Example:**
```python
# In _provider() loop, around the existing LLMInvokeError catch:
for pass_name, role in pass_configs:
    for pass_attempt in range(2):  # 1 retry
        try:
            result = llm_invoke(prompt, backend=backend)
            break  # success
        except LLMInvokeError as exc:
            if pass_attempt == 0 and exc.retryable:
                sys.stderr.write(
                    "code-forge: L1 pass '%s' failed, retrying...\n" % pass_name
                )
                continue
            # final attempt or non-retryable: create INFRA finding
            ...
```

### Anti-Patterns to Avoid

- **Decorator retry:** Cannot access HTTPError attributes (Retry-After, body).
  Use inline loop.
- **Retry on non-retryable errors:** 402 (balance exhausted) or 403
  (forbidden) must NEVER be retried.  Wasted time + identical outcome.
- **Silent retry:** Users in pre-commit hooks see no output for 60s.
  Always print progress to stderr.
- **Reading HTTPError body twice:** `exc.read()` can only be called once;
  second call returns `b""`.  Read once and preserve.
  [VERIFIED: Python stdlib test, this session]
- **Comparing Zhipu error.code as int:** The field is a string (`"1302"`).
  Convert before lookup or use string keys in the map.
  [CITED: docs.z.ai/api-reference/api-code]
- **Checking base_resp in _invoke_anthropic for MiniMax:** MiniMax's anthropic
  format endpoint returns standard HTTP errors, not embedded `base_resp`.
  The `base_resp` check belongs in `_invoke_openai` only.
  [CITED: platform.minimax.io/docs/api-reference/text-anthropic-api]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Exponential backoff | Custom scheduling | `initial * (2 ** attempt) + random.uniform(0, 0.5)` | Standard formula; no library needed |
| Retry-After parsing | Complex RFC 7231 parser | `int(headers.get('Retry-After', '0'))` | CN providers return seconds (int), not HTTP-date |
| HTTP error classification | Per-provider if/else chain | Dict lookup `PROVIDER_ERROR_MAP[provider][code]` | Clean, extensible, testable |
| JSON body error detection | Inline isinstance checks | Helper function `_classify_body_error(resp_data, provider)` | Reusable across _invoke_openai and _invoke_anthropic |

## Common Pitfalls

### Pitfall 1: HTTPError body can only be read once
**What goes wrong:** `exc.read()` is called in the retry loop to classify the
error, then later to build the error message.  The second read returns `b""`.
**Why it happens:** `urllib.error.HTTPError` wraps a file-like `fp` that is
consumed on first read.
**How to avoid:** Read body once into a variable before any classification
or message construction.
**Warning signs:** Empty body excerpts in error messages despite known API error.

### Pitfall 2: Zhipu error.code is a string
**What goes wrong:** The error map uses `int` keys (`{1302: "non-retryable"}`),
but Zhipu returns `"1302"` (string).  Lookup misses; error defaults to
retryable and retries a balance-exhaustion error 5 times.
**Why it happens:** Most APIs use int status codes; Zhipu is the exception.
**How to avoid:** Convert `error.code` to string before lookup, or use string
keys in the Zhipu error map section.
**Warning signs:** Balance-exhaustion errors being retried (60s wasted wait).

### Pitfall 3: Zhipu 1302 is rate limit (retryable), not balance
**What goes wrong:** CONTEXT.md D-31-03 lists Zhipu 1302 as "balance"
(non-retryable), but the official Zhipu docs show 1302 = "Rate limit reached
for requests" (HTTP 429, retryable).  Balance is code 1113 (HTTP 429,
non-retryable).
**Why it happens:** Error code mapping from the discuss-phase session used
stale or incorrect information.
**How to avoid:** Use the verified Zhipu error code table below.
Planner must reconcile with CONTEXT.md D-31-03.
**Warning signs:** CONTEXT.md D-31-05 says "Zhipu 1302/1305" are
non-retryable, but docs say 1302 and 1305 are both HTTP 429 (retryable).

### Pitfall 4: Retry vs outer timeout
**What goes wrong:** Retry loop waits ~62s total worst-case, but the outer
`FORGE_LLM_TIMEOUT_S` (default 120s) or `EXIT_TIMEOUT=6` circuit breaker
(5 consecutive timeouts) may fire first.
**Why it happens:** Multiple timeout layers interact: urllib timeout per
request, retry loop total, and circuit breaker threshold.
**How to avoid:** The retry loop's per-attempt timeout is the existing
`timeout_s` passed to `urllib.request.urlopen`.  The retry loop adds
inter-attempt delay (backoff).  These are additive: worst-case =
`max_attempts * timeout_s + sum(backoff_delays)`.  Document this clearly.
**Warning signs:** Circuit breaker firing before retry exhaustion.

### Pitfall 5: MiniMax base_resp only in OpenAI format
**What goes wrong:** Adding `base_resp` check to `_invoke_anthropic` causes
false positives or dead code, because MiniMax's anthropic endpoint returns
standard HTTP errors without `base_resp`.
**Why it happens:** MiniMax has two API endpoints (OpenAI and Anthropic)
with different error formats.
**How to avoid:** Check `base_resp` only in `_invoke_openai`.  Check
`error.code` (Zhipu pattern) in `_invoke_openai` only (Zhipu uses OpenAI
format).  The `_invoke_anthropic` path for MiniMax gets standard HTTP errors.
**Warning signs:** Dead code in `_invoke_anthropic` body-error checks.

## Verified Provider Error Code Reference

### Zhipu (GLM) -- OpenAI format
[CITED: docs.z.ai/api-reference/api-code]

| Body code | HTTP | Meaning | Retryable | Suggestion |
|-----------|------|---------|-----------|------------|
| 1113 | 429 | Insufficient balance | NO | "Top up at z.ai" |
| 1302 | 429 | Rate limit reached | YES | "Wait and retry" |
| 1305 | 429 | Service overloaded | YES | "Wait and retry" |
| 1308 | 429 | Usage limit per time unit | NO | "Wait for reset or upgrade plan" |
| 1309 | 429 | Coding plan expired | NO | "Renew subscription" |
| 1000 | 401 | Auth failed | NO | "Check API key" |
| 1001 | 401 | Auth param missing | NO | "Set api_key_env" |
| 1210 | 400 | Invalid parameter | NO | "Check request format" |
| 1301 | 400 | Unsafe content | NO | "Modify input" |

**Key correction vs CONTEXT.md D-31-03:** D-31-03 lists 1302 as "balance"
(non-retryable) and 1305 as "invalid key" (non-retryable).  Official docs:
1302 = rate limit (retryable), 1305 = service overloaded (retryable), 1113 =
balance (non-retryable).  The planner must use these corrected values.

**Format note:** Zhipu `error.code` is a **string** (e.g., `"1302"`), not int.

### MiniMax -- OpenAI format (base_resp)
[ASSUMED -- aggregated from WebSearch, official docs page was unreachable]

| Body code | Meaning | Retryable | Suggestion |
|-----------|---------|-----------|------------|
| 1002 | Rate limit | YES | "Reduce request frequency" |
| 1008 | Insufficient balance | NO | "Top up at minimax.io" |
| 1039 | Token limit exceeded | NO | "Reduce input length" |
| 1041 | Connection limit | YES | "Wait and retry" |
| 2045 | Rate growth limit | YES | "Wait and retry" |
| 2049 | Invalid API key | NO | "Check API key" |
| 2056 | Usage limit exhausted | NO | "Upgrade plan" |

**Format note:** MiniMax uses `base_resp.status_code` (integer) in its
OpenAI-format responses.  When accessed via the anthropic format (which forge
uses), errors come as standard HTTP status codes.

### DeepSeek -- OpenAI format
[ASSUMED -- based on training data, standard OpenAI error format]

Standard HTTP error codes.  Returns `Retry-After` header on 429.  No
proprietary body codes.  Error body follows OpenAI format:
`{"error": {"message": "...", "type": "...", "code": "..."}}`

### Kimi (Moonshot) -- OpenAI format
[ASSUMED -- based on training data]

Standard HTTP error codes.  May return `Retry-After` header on 429.  No
proprietary body codes documented.

### MiMo -- Anthropic format
[VERIFIED: forge gate.yaml + test fixtures in test_llm_invoke.py]

Uses Anthropic wire protocol.  HTTP errors are standard.  No proprietary
body codes.  Thinking blocks prepended before text block (already handled
by forge's existing `_invoke_anthropic` extraction).

## HTTP-Level Retry Classification

[Combination of CONTEXT.md D-31-05 + official docs verification]

| Source | Retryable | Non-retryable |
|--------|-----------|---------------|
| HTTP status | 429, 500, 502, 503, 504 | 400, 401, 402, 403, 404, 422 |
| Python exception | TimeoutError, URLError | -- |
| Zhipu body | 1302, 1305 | 1113, 1308, 1309, 1000, 1001, 1210, 1301 |
| MiniMax body | 1002, 1041, 2045 | 1008, 1039, 2049, 2056 |

## Code-Level Insertion Points

### 1. LLMInvokeError -- add `retryable` attribute

File: `src/code_forge/llm_invoke.py`, line 44-57

```python
class LLMInvokeError(Exception):
    def __init__(
        self,
        message: str,
        exit_code: int = -1,
        stderr: str = "",
        duration_s: float = 0.0,
        is_timeout: bool = False,
        retryable: bool = True,      # NEW
        retry_after: float | None = None,  # NEW: seconds from Retry-After
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr
        self.duration_s = duration_s
        self.is_timeout = is_timeout
        self.retryable = retryable        # NEW
        self.retry_after = retry_after    # NEW
```

**Note:** Default `retryable=True` means unknown errors are retried (safe
default -- fail-open to retry, fail-closed after exhaustion).  Known
non-retryable codes explicitly set `retryable=False`.

### 2. Provider error map -- module-level dict

File: `src/code_forge/llm_invoke.py`, after line 95 (after _REVIEW_ENVELOPE_KEYS)

```python
# Provider-specific error code classification.
# Keys: provider name (BackendConfig.name substring match).
# Values: dict of body error code (str) -> "retryable" | "non-retryable".
# Codes not in the map default to retryable (safe: retry unknown, fail-closed
# after exhaustion).
PROVIDER_ERROR_CODES: dict[str, dict[str, str]] = {
    "zhipu": {
        "1113": "non-retryable",  # insufficient balance
        "1302": "retryable",      # rate limit
        "1305": "retryable",      # service overloaded
        "1308": "non-retryable",  # usage limit per time unit
        "1309": "non-retryable",  # coding plan expired
        "1000": "non-retryable",  # auth failed
        "1001": "non-retryable",  # auth param missing
    },
    "minimax": {
        "1002": "retryable",      # rate limit
        "1008": "non-retryable",  # insufficient balance
        "1039": "non-retryable",  # token limit exceeded
        "1041": "retryable",      # connection limit
        "2045": "retryable",      # rate growth limit
        "2049": "non-retryable",  # invalid API key
        "2056": "non-retryable",  # usage limit exhausted
    },
}

# HTTP status codes classified as retryable.
RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
```

### 3. Body-based error detection -- in _invoke_openai

File: `src/code_forge/llm_invoke.py`, inside `_invoke_openai`, after
`resp_data = json.loads(response.read()...)` (line 511) and BEFORE the
`resp_data["choices"][0]["message"]["content"]` extraction (line 525).

```python
    # Body-based error detection: Zhipu error.code, MiniMax base_resp
    _check_body_error(resp_data, backend)

    # Extract content (existing code)
    try:
        content = resp_data["choices"][0]["message"]["content"]
```

And the helper:
```python
def _check_body_error(resp_data: dict, backend: BackendConfig) -> None:
    """Detect provider-specific errors in response body (HTTP 200 with error).

    Zhipu: {"error": {"code": "1302", "message": "..."}}
    MiniMax (openai format): {"base_resp": {"status_code": 1008, "status_msg": "..."}}

    Raises LLMInvokeError with retryable flag based on PROVIDER_ERROR_CODES.
    """
    # Zhipu: error.code (string)
    error_obj = resp_data.get("error")
    if isinstance(error_obj, dict) and error_obj.get("code") is not None:
        code_str = str(error_obj["code"])
        msg = error_obj.get("message", "")
        retryable = _is_body_code_retryable(backend.name, code_str)
        raise LLMInvokeError(
            "code-forge: %s backend: %s (code %s). %s"
            % (backend.name, msg, code_str, _suggestion(backend.name, code_str)),
            exit_code=0,
            retryable=retryable,
        )

    # MiniMax openai format: base_resp.status_code (int)
    base_resp = resp_data.get("base_resp")
    if isinstance(base_resp, dict):
        status_code = base_resp.get("status_code", 0)
        if status_code != 0:
            code_str = str(status_code)
            msg = base_resp.get("status_msg", "")
            retryable = _is_body_code_retryable(backend.name, code_str)
            raise LLMInvokeError(
                "code-forge: %s backend: %s (code %s). %s"
                % (backend.name, msg, code_str, _suggestion(backend.name, code_str)),
                exit_code=0,
                retryable=retryable,
            )
```

### 4. HTTP error classification -- in _invoke_openai / _invoke_anthropic catch blocks

File: `src/code_forge/llm_invoke.py`, lines 512-517 (_invoke_openai) and
559-564 (_invoke_anthropic).

The existing catch reads body and raises.  The change: read body once, parse
Retry-After, classify retryable, and set attributes on LLMInvokeError.

```python
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()  # read once (second read returns b"")
        body_excerpt = body_bytes.decode("utf-8", errors="replace")[:200]
        retry_after = _parse_retry_after(exc.headers)
        retryable = exc.code in RETRYABLE_HTTP_STATUSES
        raise LLMInvokeError(
            _format_error_message(backend.name, exc.code, body_excerpt),
            exit_code=exc.code,
            retryable=retryable,
            retry_after=retry_after,
        ) from exc
```

### 5. Retry loop -- wrapping the format dispatch in _invoke_api

File: `src/code_forge/llm_invoke.py`, in `_invoke_api` (line 402).

The retry loop wraps the entire format dispatch block (lines 433-462).
The existing `TimeoutError` catch at line 456 is inside the retry scope
(timeouts are retryable per D-31-05).

### 6. Pass-level retry -- in factories.py _provider()

File: `src/code_forge/factories.py`, in `_provider()` inner function,
around the `llm_invoke` call (line 288) and LLMInvokeError catch (line 304).

Wrap in `for pass_attempt in range(2)`.  On first LLMInvokeError where
`exc.retryable is True`, continue to retry.  On second failure or
non-retryable, fall through to existing INFRA finding creation.

### 7. gate.yaml retry config -- in gate_check.py

File: `src/code_forge/gate_check.py`, in `load_gate_config()` (line 39).

Add validation for optional `retry` section after existing section
validations (line 136):

```python
    # Validate optional retry section
    if "retry" in data:
        validate_retry_config(data["retry"])
```

### 8. gate.schema.json -- retry section

File: `src/code_forge/gate.schema.json`, in properties (after `canary`).

```json
"retry": {
  "type": "object",
  "description": "HTTP-level retry configuration for API backends.",
  "additionalProperties": false,
  "properties": {
    "max_attempts": {
      "type": "integer",
      "minimum": 1,
      "maximum": 10,
      "default": 5,
      "description": "Maximum number of attempts per API call (including the first)."
    },
    "initial_delay_s": {
      "type": "number",
      "minimum": 0.1,
      "maximum": 30,
      "default": 2,
      "description": "Initial delay in seconds before first retry. Doubles each attempt."
    }
  }
}
```

## Existing Test Patterns

### How tests mock llm_invoke (for retry test design)

From `test_llm_invoke.py` (84 tests) and `test_factories.py` (33 tests):

**HTTP error mocking:**
```python
http_error = urllib.error.HTTPError(
    "https://example.com", 429, "Rate limited", {}, None
)
http_error.read = Mock(return_value=b"rate limit exceeded")

with patch("urllib.request.urlopen", side_effect=http_error):
    with pytest.raises(LLMInvokeError, match="HTTP 429"):
        llm_invoke("prompt", backend=backend)
```

**TimeoutError mocking:**
```python
with patch("urllib.request.urlopen", side_effect=TimeoutError("read timed out")):
    with pytest.raises(LLMInvokeError, match="timed out") as exc:
        llm_invoke("prompt", backend=backend)
    assert exc.value.is_timeout is True
```

**L1 provider invoke-fail mocking:**
```python
with patch("code_forge.llm_invoke.llm_invoke") as mock_invoke:
    mock_invoke.side_effect = LLMInvokeError("timeout")
    provider = build_l1_provider("real", resolved)
    findings, excerpts, usage, duration = provider()

infra = [f for f in findings if f.source == "INFRA"]
assert len(infra) >= 1
```

### Test patterns needed for retry

1. **Retry on 429:** Mock urlopen to raise HTTPError(429) N-1 times then succeed
2. **No retry on 402:** Mock urlopen to raise HTTPError(402), verify immediate raise
3. **Retry-After honored:** HTTPError(429) with Retry-After header, verify sleep duration
4. **Retry exhaustion:** Mock urlopen to raise HTTPError(429) max_attempts times,
   verify final LLMInvokeError raised
5. **Body-based Zhipu:** Mock urlopen to return 200 with `{"error": {"code": "1113"}}`,
   verify non-retryable LLMInvokeError
6. **Body-based MiniMax:** Mock urlopen to return 200 with
   `{"base_resp": {"status_code": 1008}}`, verify non-retryable
7. **Pass-level retry:** Mock llm_invoke to raise LLMInvokeError(retryable=True)
   once then succeed, verify pass completes
8. **Stderr progress:** Capture stderr during retry, verify format matches D-31-09
9. **retryable attribute:** Verify LLMInvokeError.retryable is True/False correctly
10. **gate.yaml retry config:** Valid and invalid retry config parsed correctly

### Retry-After header creation for tests

```python
from email.message import Message
headers = Message()
headers['Retry-After'] = '5'
http_error = urllib.error.HTTPError(
    "https://example.com", 429, "Rate limited", headers, None
)
http_error.read = Mock(return_value=b"rate limit exceeded")
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.1.0 |
| Config file | pyproject.toml (pytest section) |
| Quick run command | `pytest tests/test_llm_invoke.py tests/test_factories.py tests/test_gate_check.py -x -q` |
| Full suite command | `pytest --ignore=.worktrees --ignore=.claude/worktrees -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ROBUST-01 | 429 triggers retry with backoff | unit | `pytest tests/test_llm_invoke.py -x -q -k retry_429` | Wave 0 |
| ROBUST-02 | Retry-After header honored | unit | `pytest tests/test_llm_invoke.py -x -q -k retry_after` | Wave 0 |
| ROBUST-03 | Provider error codes mapped | unit | `pytest tests/test_llm_invoke.py -x -q -k provider_error` | Wave 0 |
| ROBUST-04 | L1 serial dispatch completes | unit | `pytest tests/test_factories.py -x -q -k pass_retry` | Wave 0 |
| ROBUST-05 | 402/403 fast-fail | unit | `pytest tests/test_llm_invoke.py -x -q -k non_retryable` | Wave 0 |

### Wave 0 Gaps
- [ ] `tests/test_llm_invoke.py` -- new test class TestRetry (ROBUST-01/02/03/05)
- [ ] `tests/test_factories.py` -- new test class TestPassLevelRetry (ROBUST-04)
- [ ] `tests/test_gate_check.py` -- new tests for retry config validation
- [ ] `tests/test_schema_corpus.py` -- new snippets for retry schema

## Zhipu Error Code Correction (CONTEXT.md D-31-03 vs Official Docs)

**This is the most critical research finding.** The CONTEXT.md D-31-03
decision contains incorrect Zhipu error code mappings that must be corrected:

| Code | CONTEXT.md D-31-03 says | Official docs say | Correct action |
|------|-------------------------|-------------------|----------------|
| 1302 | "balance" (non-retryable) | Rate limit (retryable) | Fix: retryable |
| 1305 | "invalid key" (non-retryable) | Service overloaded (retryable) | Fix: retryable |
| 1308 | "concurrency limit" | Usage limit per time unit | Correct: non-retryable |
| 1113 | (not listed) | Insufficient balance | Add: non-retryable |

D-31-05 also says "Zhipu 1302/1305" are non-retryable.  Based on the official
docs, 1302 (rate limit) and 1305 (overloaded) are both retryable.  The
non-retryable balance code is 1113 (not 1302).

The planner should use the corrected values from the "Verified Provider Error
Code Reference" section above.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| No retry (immediate fail) | Phase 31 adds retry | This phase | Resilience |
| Single error message format | Provider-specific actionable messages | This phase | User experience |
| No body-based detection | Detect Zhipu/MiniMax errors in 200 body | This phase | Correctness |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | DeepSeek returns Retry-After header on 429 | Provider Error Reference | Low -- retry uses computed delay as fallback |
| A2 | Kimi returns Retry-After header on 429 | Provider Error Reference | Low -- same fallback |
| A3 | MiniMax error codes (1002/1008/1039/1041/2045/2056) correct | Provider Error Reference | Medium -- official docs page unreachable, aggregated from community |
| A4 | MiniMax anthropic endpoint does not return base_resp | Anti-Patterns | Low -- forge already works without base_resp check; no false positives |
| A5 | DeepSeek has no proprietary body error codes | Provider Error Reference | Low -- uses standard OpenAI error format |
| A6 | Kimi has no proprietary body error codes | Provider Error Reference | Low -- standard format, retries handle unknowns |

## Open Questions (RESOLVED)

1. **Zhipu 1302 retryable vs CONTEXT.md D-31-03** — RESOLVED
   - What we know: Official docs say 1302 = rate limit (retryable)
   - Resolution: Use official docs (retryable).  D-31-03 corrected.
     CONTEXT.md updated with correction annotation.  Plan 01 uses corrected
     values: 1302=retryable, 1305=retryable, 1113=non-retryable.

2. **MiniMax 1039 (token limit) classification** — RESOLVED
   - What we know: Token limit means input is too long -- retrying same input fails again
   - Resolution: Non-retryable.  Plan 01 maps MiniMax 1039 as non-retryable.
     CONTEXT.md D-31-03 updated.

3. **Retry config threading path** — RESOLVED
   - What we know: cli.py loads gate.yaml, passes backend to build_l1_provider
   - Resolution: Pass max_attempts and initial_delay_s as kwargs to llm_invoke,
     which forwards to _invoke_api.  cli.py _run() reads gate_config retry
     section and passes to build_l1_provider.  Plan 03 covers this wiring.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | -- |
| V3 Session Management | no | -- |
| V4 Access Control | no | -- |
| V5 Input Validation | yes | gate.yaml retry config validated (int range bounds) |
| V6 Cryptography | no | -- |

### Known Threat Patterns for retry logic

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Retry amplification (attacker triggers retries to increase API cost) | Denial of Service | max_attempts capped at 10; backoff increases delay; non-retryable on auth errors |
| Retry-After header injection | Tampering | Capped to reasonable range; negative/huge values fall back to computed delay |
| Sensitive data in error messages | Information Disclosure | Error messages use provider name + code; no API keys or request bodies in messages |

## Sources

### Primary (HIGH confidence)
- `src/code_forge/llm_invoke.py` -- current error handling, HTTPError catch,
  body reading patterns (lines 487-531, 534-583)
- `src/code_forge/factories.py` -- L1 pass dispatch, LLMInvokeError catch
  (lines 287-326)
- `src/code_forge/gate_check.py` -- config validation patterns (lines 39-138)
- `src/code_forge/gate.schema.json` -- existing schema structure
- `tests/test_llm_invoke.py` -- 84 tests, mock patterns for HTTP errors
- `tests/test_factories.py` -- 33 tests, mock patterns for invoke failures
- Python stdlib verification (this session) -- HTTPError.headers.get('Retry-After'),
  body read-once behavior

### Secondary (MEDIUM confidence)
- [Z.AI Error Codes](https://docs.z.ai/api-reference/api-code) -- Zhipu full
  error code table, JSON format (`error.code` is string)
- [MiniMax Error Codes](https://platform.minimax.io/docs/api-reference/errorcode)
  -- referenced but page was unreachable; codes aggregated from community reports
- [MiniMax Anthropic API](https://platform.minimax.io/docs/api-reference/text-anthropic-api)
  -- anthropic-format endpoint does not document base_resp in error responses
- [Zhipu 1302 rate limit](https://github.com/anomalyco/opencode/issues/14535) --
  confirms 1302 = rate limit, not balance

### Tertiary (LOW confidence)
- DeepSeek Retry-After behavior [ASSUMED from training data]
- Kimi Retry-After behavior [ASSUMED from training data]
- MiniMax body codes (community-sourced, official page unreachable) [ASSUMED]

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new deps, stdlib only
- Architecture: HIGH - insertion points verified in source code
- Error code mapping: MEDIUM - Zhipu verified via official docs, MiniMax from community
- Pitfalls: HIGH - verified via Python stdlib testing + official docs

**Research date:** 2026-06-27
**Valid until:** 2026-07-27 (provider error codes may change; re-verify if issues arise)
