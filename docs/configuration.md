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
  Requires the `claude` binary in PATH and an authenticated session.
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
| `base_url` | yes (anthropic/openai) | API base URL (including version path for OpenAI) |
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

### CLI Backend Fields

| Field | Required | Description |
|---|---|---|
| `type` | yes | Must be `"cli"` |
| `model` | no | Model to pass to the CLI (empty = session default) |
| `command` | no | CLI binary name or path (default: `"claude"`) |
| `max_tokens` | no | Output token cap (default: 16384) |
| `default` | no | If `true`, use this backend when no override is set |

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

## Related Documentation

- [VS Code setup](setup-vscode.md) -- setting env vars in VS Code terminal
- [Cursor setup](setup-cursor.md) -- setting env vars in Cursor terminal
- [PyCharm setup](setup-pycharm.md) -- setting env vars in PyCharm
- [README Backend configuration](../README.md#backend-configuration) -- quick reference
