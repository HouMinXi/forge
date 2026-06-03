# Configuration Reference

code-forge is configured through environment variables and an optional
`backends.yaml` file. Most users need only environment variables.

## Environment Variables

### FORGE_BACKEND

Selects a named backend defined in `backends.yaml`.

- **Default**: `session-default` (uses the `claude` CLI with the active
  session model -- no model pin)
- **Precedence**: `--backend` CLI flag > `FORGE_BACKEND` env > `default: true`
  entry in `backends.yaml` > session-default

```bash
export FORGE_BACKEND=claude-api    # use a named API backend
export FORGE_BACKEND=local-claude  # use a named CLI backend
```

If the named backend does not exist in `backends.yaml`, code-forge exits
with an error listing the configured backend names.

Setting `FORGE_BACKEND` to an empty string (`""`) falls through to the
config-file default or session default -- it does not cause an error.

---

### FORGE_OUTLET

Forces the review outlet. Two outlets are available:

- `cli` -- Outlet A: spawns a `claude` subprocess for each review pass.
  Requires the `claude` binary in PATH and an authenticated session.
- `inline` -- Outlet B: runs the merged review skill inside the current
  AI session. No subprocess, no reachability probe.

- **Default**: auto-detected based on backend reachability probe.
  Reachable -> `cli`. Unreachable -> error (FAIL CLOSED, no silent
  fallback to inline).
- **Precedence**: `--outlet` CLI flag > `FORGE_OUTLET` env > `outlet` field
  in `gate.yaml` > reachability probe.

```bash
export FORGE_OUTLET=cli      # always use CLI subprocess
export FORGE_OUTLET=inline   # always use inline (no subprocess)
```

When `FORGE_OUTLET=inline`, code-forge never runs the reachability probe.
This is useful when you know the backend is available and want to skip
the probe latency.

---

### FORGE_LLM_MODEL

Overrides the model used by CLI backends. Has no effect on API backends
(which use the model configured in `backends.yaml` or the API default).

- **Default**: `claude-sonnet-4-6`
- **Applies to**: `type: cli` backends only

```bash
export FORGE_LLM_MODEL=claude-opus-4-5    # use Opus for reviews
export FORGE_LLM_MODEL=claude-sonnet-4-6  # back to default
```

Useful when you want to run reviews with a more capable model without
creating a full `backends.yaml` entry.

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

## backends.yaml

`backends.yaml` is an optional configuration file that defines named backends.
It is located at `~/.config/code-forge/backends.yaml` by default.

If the file does not exist, code-forge uses the session-default backend
(the `claude` CLI with the active session model).

### File Format

```yaml
backends:
  - name: <backend-name>
    type: api | cli
    # ... type-specific fields
```

The `backends` key is a list. Order matters: the first entry with
`default: true` is used when `FORGE_BACKEND` is not set and no other
override applies. If no entry has `default: true`, the first entry is used.

### API Backend Fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Unique identifier used by `FORGE_BACKEND` |
| `type` | yes | Must be `"api"` |
| `format` | yes | API format: `"anthropic"` or `"openai"` |
| `base_url` | yes | API base URL (including version path for OpenAI) |
| `api_key_env` | yes | Name of the env var that holds the API key |
| `model` | no | Model ID (leave empty to use API default) |
| `default` | no | If `true`, use this backend when no override is set |

**Security note**: Never put an API key directly in `backends.yaml`. Use
`api_key_env` to specify the name of an environment variable, and set
the actual key in your shell or secrets manager. code-forge rejects any
`backends.yaml` entry that contains an `api_key` field.

### CLI Backend Fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Unique identifier used by `FORGE_BACKEND` |
| `type` | yes | Must be `"cli"` |
| `model` | no | Model to pass to the CLI (empty = session default) |
| `command` | no | CLI binary name or path (default: `"claude"`) |
| `default` | no | If `true`, use this backend when no override is set |

### Example: Anthropic API

```yaml
backends:
  - name: claude-api
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
  - name: openai
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
  - name: local-claude
    type: cli
    model: claude-opus-4-5
    command: claude
```

No API key needed -- uses your existing `claude auth` session.

### Example: Multi-Backend Setup

```yaml
backends:
  - name: claude-api
    type: api
    format: anthropic
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
    default: true

  - name: openai
    type: api
    format: openai
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY

  - name: local-claude
    type: cli
    model: claude-opus-4-5
    command: claude
```

With this config:
- Default: `claude-api` (API call with Anthropic key)
- `FORGE_BACKEND=openai`: use OpenAI API
- `FORGE_BACKEND=local-claude`: use local claude CLI with Opus

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
