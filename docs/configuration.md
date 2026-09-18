# Configuration Reference

code-forge is configured through environment variables, user-level defaults,
and an optional repository-level `gate.yaml` file. Most users need only
environment variables.

## Configuration Scope & Inheritance

Configuration resolves across two tiers (ADR-0009):

1. **User-level configuration** (`~/.config/code-forge/config.yaml`):
   Global defaults, machine-local API keys, and default backend selections.
   Inherited automatically by all projects on the host via `_merge_user_into`.
2. **Project-level configuration** (`.code-forge/gate.yaml`):
   Repository-specific quality gates, rulepacks, and project backend overrides.
   Overrides user-level settings on key collision.

```
Resolution Precedence:
  CLI Flags (--backend, --mode)
    > Environment Variables (FORGE_BACKEND, FORGE_OUTLET)
      > Project gate.yaml (.code-forge/gate.yaml)
        > User config.yaml (~/.config/code-forge/config.yaml)
          > Built-in defaults
```

### Trust and repository backends

A repository can name any endpoint and any credential environment variable,
so a `gate.yaml` arriving over `git pull` could otherwise redirect a review to
a host of its author's choosing. code-forge therefore hashes the
credential-bearing fields of the `backends` block and stores the hash keyed by
the file's real path. A block whose hash has moved is dropped, with one line
on stderr:

```
code-forge: trust invalidated: credential-related fields changed in
gate.yaml. Run 'code-forge trust' to re-authorize.
```

The review still runs -- it falls back to whatever else resolves, which on
most hosts means the user-level config. That is the failure worth knowing
about: editing `base_url` or `api_key_env` and not re-authorizing leaves a
review that looks fine and used a different backend than the one you edited.
Re-authorize after every such edit:

```bash
code-forge trust                 # in the repository
scripts/forge-provider.py trust  # audit every gate.yaml on the host
```

`scripts/forge-provider.py` re-seals automatically after any change it makes.

---

## Environment Variables

### FORGE_BACKEND

Selects a named backend defined in the `backends:` key of `.code-forge/gate.yaml`.

- **Default**: `session-default` (uses the `claude` CLI with the active
  session model -- no model pin)
- **Precedence**: `--backend` CLI flag > `FORGE_BACKEND` env > `default: true`
  entry in `gate.yaml` > session-default

```bash
export FORGE_BACKEND=claude-api    # use a named API backend
export FORGE_BACKEND=local-claude  # use a named CLI backend
```

If the named backend does not exist in `gate.yaml`, code-forge exits
with an error listing the configured backend names.

Setting `FORGE_BACKEND` to an empty string (`""`) falls through to the
config-file default or session default -- it does not cause an error.

---

### FORGE_OUTLET

Forces the review outlet. Three outlets are available:

- `subprocess` -- spawns a fresh `claude` subprocess for each review pass.
  Requires the `claude` binary in PATH and an authenticated session
  (or a `command` override in gate.yaml -- see "Third-Party API Proxies"
  below).
- `inline` -- Outlet B: runs the merged review skill inside the current
  AI session. No subprocess, no reachability probe.
- `subagent` -- spawns a fresh Agent per pass; works inside the current
  session without a subprocess.

- **Default**: auto-detected based on backend reachability probe.
  Reachable -> `subprocess`. Unreachable -> error (FAIL CLOSED, no silent
  fallback to inline).
- **Precedence**: `--outlet` CLI flag > `FORGE_OUTLET` env > `outlet` field
  in `gate.yaml` > reachability probe.

```bash
export FORGE_OUTLET=subprocess   # always use CLI subprocess
export FORGE_OUTLET=inline       # always use inline (no subprocess)
export FORGE_OUTLET=subagent     # always use subagent
```

When `FORGE_OUTLET=inline`, code-forge never runs the reachability probe.
This is useful when you know the backend is available and want to skip
the probe latency.

> **"Implicit claude -p is disabled" warning**: This warning fires only
> when no backend is explicitly configured and code-forge would fall back
> to the implicit `claude` CLI default. The implicit fallback is disabled
> because it nests a subprocess and bills the main Anthropic account.
> Setting `command: claude` (or a proxy binary path) in a gate.yaml
> `backends` entry is an explicit configuration and does NOT trigger the
> warning. If you see this warning, either add a `backends` section to
> gate.yaml, or set `FORGE_OUTLET=inline` to run review inside the
> current session.

---

### FORGE_LLM_MODEL

Overrides the model used by CLI backends. Has no effect on API backends
(which use the model configured in `gate.yaml` or the API default).

- **Default**: `claude-sonnet-4-6`
- **Applies to**: `type: cli` backends only

```bash
export FORGE_LLM_MODEL=claude-opus-4-5    # use Opus for reviews
export FORGE_LLM_MODEL=claude-sonnet-4-6  # back to default
```

Useful when you want to run reviews with a more capable model without
creating a full `gate.yaml` backends entry.

---

### FORGE_AUTH_TIMEOUT

Sets the timeout (in seconds) for the backend reachability probe.

- **Default**: `20` seconds
- **Maximum**: `120` seconds
- **Precedence**: `FORGE_AUTH_TIMEOUT` env > default (20s)

```bash
export FORGE_AUTH_TIMEOUT=45   # increase for slow networks
export FORGE_AUTH_TIMEOUT=5    # decrease for fast local setups
```

The probe runs `claude auth status --json` (not an inference call -- zero
token cost). Successful results are cached for 5 minutes, so the probe
overhead is incurred at most once per 5-minute window.

Values less than 1 or greater than 120 are rejected with a clear error.

---

### FORGE_LLM_TIMEOUT_S

Sets the timeout (in seconds) for each LLM invocation during review.

- **Default**: `120` seconds
- **Precedence**: explicit `timeout_s` argument > `FORGE_LLM_TIMEOUT_S` env > `120`
- **Resolved per call** (not frozen at import), so the override takes effect
  even when the env var is set after the process starts.

```bash
export FORGE_LLM_TIMEOUT_S=300   # cross-region or reasoning backends
```

An unset, malformed, or non-positive value falls back to `120`. Raise this when
a healthy backend call is aborted mid-flight by the default 120s ceiling (slow
cross-region APIs, reasoning models). Distinct from `FORGE_AUTH_TIMEOUT`, which
bounds the zero-cost reachability probe, not the review inference call.

> **Update note**: `FORGE_LLM_TIMEOUT_S` requires forge v2.4 or later. If
> you are running an older installation, update via
> `pip install --upgrade code-forge` or reinstall from source. On older
> builds, the per-call timeout is hardcoded to 120 seconds and cannot be
> overridden.

---

## gate.yaml backends block

The `backends:` key in `.code-forge/gate.yaml` defines named backends.
The file is created by `code-forge init` in the project root under
`.code-forge/gate.yaml`. If the file does not exist, code-forge uses the
session-default backend (the `claude` CLI with the active session model).

Backends are a **dict** keyed by name -- not a list. The key is the backend
name used with `FORGE_BACKEND`.

### File Format

```yaml
backends:
  <backend-name>:
    type: api | cli
    # ... type-specific fields
```

The first entry with `default: true` is used when `FORGE_BACKEND` is not set
and no other override applies. If no entry has `default: true`, the
session-default backend is used.

### API Backend Fields

| Field | Required | Description |
|---|---|---|
| `type` | yes | Must be `"api"` |
| `format` | yes | API format: `"anthropic"`, `"openai"`, or `"vertex"` |
| `base_url` | yes (anthropic/openai) | API base URL (see format-specific notes below) |
| `api_key_env` | yes (anthropic/openai) | Name of the env var that holds the API key |
| `project_id` | yes (vertex) | GCP project ID |
| `region` | no (vertex) | GCP region (default: global) |
| `credentials_path` | no (vertex) | Path to service account JSON key file |
| `model` | no | Model ID (leave empty to use API default) |
| `max_tokens` | no | Output token cap (default: 16384) |
| `default` | no | If `true`, use this backend when no override is set |

**Security note**: Never put an API key directly in `gate.yaml`. Use
`api_key_env` to specify the name of an environment variable, and set
the actual key in your shell or secrets manager. code-forge rejects any
backend entry that contains an `api_key` field.

**base_url format differences**: The `format` field determines how
code-forge constructs the request URL from `base_url`:

- `format: anthropic` -- code-forge appends `/v1/messages` to the
  configured `base_url`. For example, if `base_url` is
  `https://proxy.example.com/anthropic`, the actual request goes to
  `https://proxy.example.com/anthropic/v1/messages`. Do NOT include
  `/v1/messages` in `base_url` for anthropic-format backends.
- `format: openai` -- code-forge appends `/chat/completions` to
  `base_url`. Include the version path but NOT the endpoint
  (e.g. `https://api.openai.com/v1`, not
  `https://api.openai.com/v1/chat/completions`).
- `format: vertex` -- `base_url` is not used; the URL is constructed
  from `project_id` and `region`.

### CLI Backend Fields

| Field | Required | Description |
|---|---|---|
| `type` | yes | Must be `"cli"` |
| `model` | no | Model to pass to the CLI (empty = session default) |
| `command` | no | CLI binary name or path (default: `"claude"`) |
| `max_tokens` | no | Output token cap (default: 16384) |
| `default` | no | If `true`, use this backend when no override is set |

### Third-Party API Proxies

code-forge supports third-party LLM providers and proxy services through
two patterns:

**CLI outlet with a proxy binary** (`type: cli`): Replace the default
`claude` binary with a proxy that accepts the same stdin/stdout contract.
Set `command` to the path of your proxy binary:

```yaml
backends:
  my-proxy:
    type: cli
    command: /usr/local/bin/my-llm-proxy
```

The proxy binary must accept the same arguments and produce the same
output format as `claude -p`. No API key is needed in gate.yaml -- the
proxy handles its own authentication.

**API outlet with a proxy URL** (`type: api`): Point `base_url` at a
proxy server that implements the OpenAI or Anthropic API format:

```yaml
backends:
  proxy-api:
    type: api
    format: openai
    base_url: https://my-proxy.example.com/v1
    api_key_env: MY_PROXY_API_KEY
```

A gateway that caches temperature-0 responses (OmniRoute's semantic
cache is on by default) will replay every LOCAL round after the first.
Forge's OpenAI-format invoker sends `temperature: 0`, which is exactly
the cache gate. Disable it per request:

```yaml
backends:
  omniroute:
    type: api
    format: openai
    base_url: https://192.168.100.10:20128/v1
    api_key_env: OMNIROUTE_API_KEY
    model: your-combo-name
    headers:
      x-omniroute-no-cache: "true"
      x-omniroute-no-memory: "1"
      x-omniroute-compression: "off"
```

`max_tokens: 65536` belongs on the same backend. Omni's thinking
models spend the first part of the budget on hidden reasoning; a
smaller cap returns an empty or truncated JSON envelope and forge
parses zero findings.

Without `x-omniroute-no-cache: "true"`, identical L1 and falsify
prompts come back as cache hits. LOCAL cannot converge or HOLD; it
walks toward `max_total_rounds`. The other two headers keep the
gateway from compressing the review prompt or injecting memory
into it. Startup warns when the resolved backend URL looks like
OmniRoute and the no-cache header is missing.

The proxy must return responses in the format specified by `format`.
Authentication is handled via `api_key_env` -- the env var holds whatever
token or key your proxy expects.

This covers hosted routers such as OpenRouter, Together, or a self-hosted
LiteLLM / one-api gateway: point `base_url` at the router, set `format` to
the API it speaks (`openai` for OpenRouter), and put the router's key in
`api_key_env`. See the OpenRouter example below.

> **Note**: The "authenticated session" mentioned in the `subprocess`
> outlet description refers to the default `claude` CLI using the user's
> own Anthropic account. Third-party proxies use their own authentication
> via `api_key_env` (for API outlets) or internal credentials (for CLI
> proxy binaries).

### Example: Anthropic API

```yaml
backends:
  claude-api:
    type: api
    format: anthropic
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
    model: claude-opus-4-5
    default: true
```

Set the key in your shell before running code-forge:

```bash
export ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

### Example: OpenAI-Compatible API

```yaml
backends:
  openai-compatible:
    type: api
    format: openai
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
```

```bash
export OPENAI_API_KEY=sk-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

### Example: OpenRouter (or any OpenAI-compatible router)

OpenRouter speaks the OpenAI API, so it is a plain `format: openai` backend
with the router's URL and a prefixed model id. The same shape works for
Together, DeepInfra, or a self-hosted LiteLLM / one-api gateway -- only the
`base_url`, `model`, and `api_key_env` change.

```yaml
backends:
  openrouter:
    type: api
    format: openai
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    model: anthropic/claude-sonnet-4.6
    default: true
```

```bash
export OPENROUTER_API_KEY=YOUR_API_KEY_HERE
```

code-forge sends only `Authorization: Bearer <key>` and `Content-Type`; it
does not send OpenRouter's optional `HTTP-Referer` / `X-Title` ranking
headers, which are not required for reviews.

### Example: Local Claude CLI (pinned model)

```yaml
backends:
  local-claude:
    type: cli
    model: claude-opus-4-5
    command: claude
```

No API key needed -- this uses your existing `claude auth` session, so it is
also the simplest way to review with an account-authenticated model (a
subscription / Pro login rather than an API key).

If you normally drive `claude` through a shell wrapper that scrubs the
environment to pin a specific account (for example a function that `unset`s
`ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` / `CLAUDE_CODE_USE_VERTEX` before
exec), two things matter: point `command` at the `claude` **binary** (a shell
function or alias is not resolvable by `which`), and start the code-forge / MCP
process with the same scrubbed environment -- the spawned `claude` inherits
code-forge's environment, so a stray `ANTHROPIC_BASE_URL` would send it to the
wrong endpoint.

### Example: Account-authenticated tools via a local bridge

Some assistants log in with OAuth / a subscription session rather than an API
key, and their CLIs do not speak code-forge's CLI contract
(`-p <prompt> --output-format json`), so they cannot be a `type: cli` backend
directly. The portable way to review with them is a local bridge: run a small
proxy that holds the tool's authenticated session and exposes an OpenAI- or
Anthropic-compatible endpoint on `localhost`. code-forge then consumes it as an
ordinary API backend -- the same shape as the OpenRouter example, just pointed
at the bridge:

```yaml
backends:
  via-bridge:
    type: api
    format: openai           # or anthropic -- whichever the bridge speaks
    base_url: http://localhost:8080/v1
    api_key_env: BRIDGE_KEY   # most bridges ignore the key; set any value
    model: <the model id the bridge exposes>
```

```bash
export BRIDGE_KEY=unused
```

You provide and run the bridge process; code-forge does not ship one. From
code-forge's side it is a plain HTTP backend, so no special support is needed --
if the bridge speaks the OpenAI or Anthropic API, it works.

### Example: Multi-Backend Setup

```yaml
backends:
  claude-api:
    type: api
    format: anthropic
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
    default: true

  openai-compatible:
    type: api
    format: openai
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY

  local-claude:
    type: cli
    model: claude-opus-4-5
    command: claude
```

With this config:
- Default: `claude-api` (API call with Anthropic key)
- `FORGE_BACKEND=openai-compatible`: use OpenAI API
- `FORGE_BACKEND=local-claude`: use local claude CLI with Opus

### gate.schema.json

`code-forge init` writes `gate.schema.json` alongside `gate.yaml` in the
`.code-forge/` directory. The schema file enables IDE tooling that reads
yaml-language-server directives. VS Code and Cursor honor the `$schema`
directive automatically; PyCharm ignores it and requires manual schema
registration via Settings -> Languages -> JSON Schema Mappings.

---

## Kernel observations

`kernel_context` adds bounded observations from changed patch lines and one
explicitly named configuration file. It is off by default. It does not run
Kconfig, merge fragments, preprocess device trees, or infer effective build
configuration.

```yaml
kernel_context:
  enabled: true
  defconfig: arch/arm64/configs/defconfig
  max_rows: 40
  max_chars: 4000
```

Only single-repository CLI subprocess review supports this source. Enabled
requests reject non-empty `siblings`, inline/subagent outlets, and MCP sampling.
MCP subprocess delegates to the CLI. Disabled requests keep existing dispatch.

Run `code-forge trust` after enabling the source or changing its path. File-read
authorization is independent of backend trust and binds the enabled flag,
normalized relative path, and real workspace root. Another workspace cannot
reuse it through a shared `gate.yaml` symlink. Budget changes do not require
renewed authorization. `trust --revoke` removes the whole authorization entry.
A repository containing only a valid `kernel_context` section can be approved;
user-level backends do not need to be copied into the repository.

Review uses trust-filtered repository configuration: an untrusted section is
inactive. `gate-check` validates the raw file instead, so it reports malformed
sections even before trust is granted. A valid but unapproved enabled section
produces an authorization warning in `gate-check`; it does not read the file.

Paths must stay below the workspace root and contain no `..` component. Only
regular files are read. Symlinks and unsupported safe-open platforms produce an
unknown diagnostic, not a fallback read. Files must be UTF-8 and at most 1 MiB.
No configuration candidate means no file read. `max_rows` accepts integers
1..200; `max_chars` accepts integers 512..32000. Booleans are not valid budgets,
and unknown keys are errors.

Values are declarations, never effective configuration. Duplicate or malformed
declarations are unknown. Changed guards, device-tree lines, and binding
fragments retain old/new positions. One cached source instance supplies grouped
review. Diagnostic rows take priority when budgets run out; warnings report
omissions. No additional review pass is added.

The source footer identifies its own read bytes by SHA-256. It does **not**
authenticate the existing diff, post-image, or complete prompt. Those older
channels can contain neighboring declarations or content from another read
time. Check both the old channels and this fragment against the selected
backend's data policy before authorizing a request.

## Retry

API backends and MCP sampling retry transient failures. Omit the block
to keep the built-in defaults (5 attempts, 2 s initial delay, socket
timeouts not retried).

The same `retry:` mapping is valid in both files:

- `~/.config/code-forge/config.yaml` (host default)
- `.code-forge/gate.yaml` (project override)

On a key collision the project value wins; the user file fills keys the
project does not set. A malformed `retry` block is ignored and the
review still runs with defaults -- a bad config must not take a review
down.

```yaml
retry:
  max_attempts: 5
  initial_delay_s: 2.0
  retry_timeout: false
```

### Fields

| Field | Type | Range | Default | Meaning |
|---|---|---|---|---|
| `max_attempts` | int | 1..10 | 5 | Attempts per call, including the first. |
| `initial_delay_s` | number | 0.1..30 | 2 | Delay before the first retry. Each later wait doubles, capped at 60 s, plus 0-0.5 s jitter. A `Retry-After` header, if present, is the floor. |
| `retry_timeout` | bool | -- | `false` | When `true`, a socket `TimeoutError` is retried like 429/503. Leave `false` unless the backend flakes by hanging: a hung call at `timeout_s` (often 1800-2400) times five stalls a review. |

Unknown keys are kept (forward-compatible) but ignored.

### What is retried

HTTP API (`type: api`): status 429, 500, 502, 503, 504; a 200 body that
is not JSON; an SSE stream where a JSON body was expected; empty or
non-JSON model content. Vendor body codes that the gateway maps as
retryable (rate-limit / overload) also retry.

MCP sampling (`code-forge-mcp` `createMessage`): empty text, and a
reply with no parseable JSON.

Not retried: HTTP 4xx other than 429; missing credentials; truncated
output (`stopReason=maxTokens`); Copilot CLI stub models
(`copilotcli/...`); `type: cli` backends (they are not HTTP). Socket
timeouts stay unretriable unless `retry_timeout: true`.

### Logs

Each retry writes one flushed line to stderr so MCP `forge_job_status`
and CI logs see it while the wait is still in progress:

```
[forge] t+12.3s retrying review-default (2/5, waiting 2.1s) after <cause>
[forge] t+45.0s retry failed review-default after 5 attempts: <cause>
```

The name is the backend name, or `sampling` on the MCP sampling path.
`<cause>` is the exception text, collapsed to one line and capped at
400 characters. The delay is printed *before* the sleep: the gap
between two lines is the sleep plus the next attempt, not the duration
of the failed call.

---

## Authentication

### CLI Backends (type: cli)

Authentication is handled by the `claude` CLI. Two options:

**Option 1: claude auth login** (recommended for interactive use)

```bash
claude auth login
```

code-forge verifies authentication by running `claude auth status --json`.
This is not an inference call -- it has zero token cost.

**Option 2: ANTHROPIC_API_KEY env var**

If `ANTHROPIC_API_KEY` is set, the `claude` CLI uses it automatically.
No `claude auth login` needed.

```bash
export ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

### API Backends (type: api)

Set the env var named by `api_key_env` in your shell:

```bash
# For a backend with api_key_env: ANTHROPIC_API_KEY
export ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXXXXXXXXXXXXXXXXXXXXX

# For a backend with api_key_env: OPENAI_API_KEY
export OPENAI_API_KEY=sk-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

The reachability probe for API backends checks only that the env var is
set (non-empty). It does not make a network request or validate the key.
A set but invalid key will fail at review time, not at probe time.

### Increasing Probe Timeout

If the reachability probe times out (especially on slow networks or when
the `claude` binary is slow to start):

```bash
export FORGE_AUTH_TIMEOUT=60   # wait up to 60 seconds
```

---

## Model Selection

code-forge's review pipeline sends multiple LLM prompts per review run.
Each of the 3 review passes (qodo, expert, adversarial) sends the full
diff plus review context to the backend, and the pipeline runs multiple
rounds until convergence. A typical review run produces 9 or more LLM
calls, each containing the full diff.

Reasoning models (claude-opus-4-5, claude-sonnet-4-6 with extended
thinking, mimo-pro, deepseek-reasoner) add substantial thinking overhead
per call. Multiplied across 9+ passes, this overhead dominates total
review time and cost.

**Recommendation**: Use a fast non-reasoning model as the primary review
backend. Reserve reasoning models for single-pass deep analysis or
targeted falsification of specific findings.

| Category | Models | Multi-pass review | Single-pass analysis |
|---|---|---|---|
| Fast (recommended for review) | haiku, mimo, deepseek-chat, glm-4 | Good | Adequate |
| Balanced | claude-sonnet-4-6, gpt-4o | Acceptable | Good |
| Reasoning (use sparingly) | claude-opus-4-5, mimo-pro, deepseek-reasoner | Slow and expensive | Best depth |

For cross-region backends (e.g. CN-hosted APIs accessed from outside
China), also raise `FORGE_LLM_TIMEOUT_S` to account for network latency
(300-600 seconds is typical).

---

## Backend Troubleshooting

Common backend errors and their solutions:

| Symptom | Likely Cause | Fix |
|---|---|---|
| L1 pass always times out | Backend too slow or unreachable | Raise `FORGE_LLM_TIMEOUT_S` (e.g. 300 for cross-region) or switch to a faster model |
| "unexpected response structure" | Backend returned non-JSON or truncated response | Check `max_tokens` setting in gate.yaml -- ensure it is at least 16384. Reasoning models need higher limits |
| "Implicit \`claude -p\` is disabled" | No backend configured; implicit fallback refused | Add a `backends` section to gate.yaml, or set `FORGE_OUTLET=inline` |
| "LLM subprocess failed" | CLI binary not found or crashed | Verify the `command` path exists and is executable (`which claude` or `which <proxy>`) |
| "schema validation failed" | Backend returned valid JSON but wrong structure | Verify the backend supports the API format specified by `format` in gate.yaml |
| "lock busy" | Another code-forge process is running on this project | Wait for it to finish, or check for stuck processes (`ps aux \| grep code-forge`) |
| Exit code 6 (TIMEOUT) | 5 consecutive L1 timeouts tripped the circuit breaker | The backend cannot keep up. Reduce diff size, raise `FORGE_LLM_TIMEOUT_S`, or switch to a faster (non-reasoning) backend |

Exit code 6 is distinct from exit code 1 (review found unfixed issues).
The circuit breaker trips after 5 consecutive L1 timeouts to prevent
the review from running indefinitely on an unreachable or overloaded
backend. The counter resets on any successful L1 call, so transient
single-request timeouts do not trip the breaker.

---

## Canary (inline outlet)

An opt-in objective laziness check for the inline review outlet. When
enabled, forge plants semantic defects into an isolated copy of the diff
and gates on how many the reviewer catches. A rubber-stamp reviewer that
returns empty findings is detected and flagged UNRELIABLE (exit 7).

### gate.yaml canary: block

Add a `canary:` block to `.code-forge/gate.yaml`:

```yaml
canary:
  enabled: true              # bool -- required for opt-in
  n: 5                       # int, 3..5 -- canaries to plant per review
  threshold_ratio: 0.6       # float, >0.0..1.0 -- catch ratio to pass
```

### Field descriptions

| Field | Type | Range | Default | Description |
|---|---|---|---|---|
| `enabled` | bool | -- | -- | Activates the canary check. Equivalent to passing `--canary` on the CLI. Required for opt-in. |
| `n` | int | 3..5 | 5 | Number of canary mutations to plant. |
| `threshold_ratio` | float | >0.0..1.0 | 0.6 | Minimum fraction of canaries the reviewer must catch. The actual threshold is `ceil(threshold_ratio * n)` (e.g. 0.6 * 5 = 3). A value of 0.0 is rejected because it would produce a threshold of 0, which is meaningless. |

### Behavior

- With no opt-in (no `--canary` flag, no `canary:` block, or
  `canary.enabled: false`), the inline outlet is unchanged -- it returns
  `DELEGATED` (exit 5) as before.
- When fewer than 2 verified canaries can be generated, the check is
  skipped with a notice (graceful degradation, not a hard failure).
- If the canary dispatch fails (LLM timeout, network error), the check
  degrades to `DELEGATED` -- it never crashes the review.
- The canary result never alters outlet or model selection (D-16).
- Planted defects are never written to the working tree or git history.
  They exist only in the isolated review copy passed to the fresh-context
  reviewer.
- Currently Python diffs only; non-Python diffs skip the canary with a
  notice.

### CLI alternative

The `--canary` flag on `code-forge review` achieves the same opt-in with
default `n=5` and `threshold_ratio=0.6`, without requiring a gate.yaml
`canary:` block.

---

## Related Documentation

- [VS Code setup](setup-vscode.md) -- setting env vars in VS Code terminal
- [Cursor setup](setup-cursor.md) -- setting env vars in Cursor terminal
- [PyCharm setup](setup-pycharm.md) -- setting env vars in PyCharm
- [README Backend configuration](../README.md#backend-configuration) -- quick reference
- [Retry](#retry) -- HTTP and MCP sampling retry block
