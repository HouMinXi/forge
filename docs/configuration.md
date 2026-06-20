# Configuration Reference

code-forge is configured through environment variables and an optional
`gate.yaml` file. Most users need only environment variables.

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
- **Precedence**: `--auth-timeout` CLI flag > `FORGE_AUTH_TIMEOUT` env >
  default (20s)

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

The proxy must return responses in the format specified by `format`.
Authentication is handled via `api_key_env` -- the env var holds whatever
token or key your proxy expects.

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

### Example: Local Claude CLI (pinned model)

```yaml
backends:
  local-claude:
    type: cli
    model: claude-opus-4-5
    command: claude
```

No API key needed -- uses your existing `claude auth` session.

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

Or use the CLI flag for a one-off:

```bash
code-forge review --auth-timeout 60
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

## Related Documentation

- [VS Code setup](setup-vscode.md) -- setting env vars in VS Code terminal
- [Cursor setup](setup-cursor.md) -- setting env vars in Cursor terminal
- [PyCharm setup](setup-pycharm.md) -- setting env vars in PyCharm
- [README Backend configuration](../README.md#backend-configuration) -- quick reference
