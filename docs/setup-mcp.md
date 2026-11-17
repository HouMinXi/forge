# MCP Server Setup

## Workspace Model

One MCP server instance serves one project. The server resolves its
workspace once at startup (priority: `FORGE_PROJECT_DIR` env var, then
walking up from `cwd` to find `.code-forge/gate.yaml`) and uses that
directory for all tool calls. It does not re-resolve per call.

If you work on multiple projects, configure a separate MCP server entry
per project. Claude Code's `.mcp.json` and VS Code's `.vscode/mcp.json`
are per-project by design, so this maps naturally. Claude Code also reads a
user-level `~/.claude.json`, whose `mcpServers` block applies to every project
and therefore needs an explicit `FORGE_PROJECT_DIR` on each entry.

### GUI client command and PATH

Graphical clients may inherit a different PATH from your interactive shell.
Use the absolute path to an executable wrapper that selects your existing
Forge installation. This POSIX example puts that installation's bin directory
first while preserving inherited tool paths:

```sh
#!/bin/sh
FORGE_BIN="/absolute/path/to/existing-forge/bin"
export PATH="$FORGE_BIN${PATH:+:$PATH}"
exec "$FORGE_BIN/code-forge-mcp" "$@"
```

Save the wrapper as an executable file at the absolute path used below, and
replace both placeholder paths with your actual paths. Include any other
required tool directories in the wrapper's PATH if the GUI client does not
inherit them. Do not rely on shell expansion of `$HOME` or `$PATH` inside JSON
strings; the examples use literal, already-expanded paths. The wrapper's shell
expands its own variables. Keep `FORGE_PROJECT_DIR` explicit for each project.

### Multi-project example (Claude Code)

```json
{
  "mcpServers": {
    "forge-projectA": {
      "command": "/absolute/path/to/forge-mcp-wrapper",
      "env": { "FORGE_PROJECT_DIR": "/home/user/code/projectA" }
    },
    "forge-projectB": {
      "command": "/absolute/path/to/forge-mcp-wrapper",
      "env": { "FORGE_PROJECT_DIR": "/home/user/code/projectB" }
    }
  }
}
```

### Wrong-workspace symptoms

| Symptom | Cause |
|---------|-------|
| `forge_review` reviews the wrong diff | Server cwd is not your project |
| `forge_trust` trusts the wrong gate.yaml | Trust applies to the server's workspace |
| "No review backends configured" despite gate.yaml existing | Server found a different (or no) gate.yaml |
| `forge_resolve_outlet` shows unexpected workspace path | `FORGE_PROJECT_DIR` not set or pointing elsewhere |

Run `forge_resolve_outlet` to see the resolved workspace, gate.yaml
path, and backend list. If wrong, fix `FORGE_PROJECT_DIR` and restart
the server.

## Configuration

Set `FORGE_PROJECT_DIR` in your MCP server configuration to point to
your project root (the directory containing `.code-forge/`). MCP
processes typically start with `cwd=~`, so this env var is required
for the server to locate your gate.yaml.

Example user-level `~/.claude.json` entry. Because this file applies to every
project, each server entry needs its own `FORGE_PROJECT_DIR`. A project-level
`.mcp.json` reaches the same result only when the client launches the server with
the project as its working directory; set the variable explicitly if it does not.

```json
{
  "mcpServers": {
    "code-forge-mcp": {
      "command": "/absolute/path/to/forge-mcp-wrapper",
      "env": {
        "FORGE_PROJECT_DIR": "/home/user/code/myproject"
      }
    }
  }
}
```

## Review and gate-check timeout

`forge_review` and `forge_gate_check` accept an optional `timeout_s` argument
to set a wall-clock deadline for that one CLI run. The value must be a finite
number greater than zero; booleans are invalid. Omitting it, or passing
`None` in Python (`null` in JSON), keeps the server's existing timeout
behavior.

```python
forge_review(project_dir="/home/user/code/myproject", timeout_s=7200)
forge_gate_check(project_dir="/home/user/code/myproject", timeout_s=7200)
```

The deadline starts before CLI setup and process launch, so that time uses the
same allowance as the foreground wait and any background work after handoff. A
foreground wait is capped at 20 seconds or the remaining deadline, whichever is
shorter. A still-running command then returns a job ID; setup, process launch
and scheduling can add latency. Poll it with `forge_job_status` as usual (the
suggested polling interval remains 10 seconds). Handoff does not restart the
timeout. When the deadline is reached, Forge starts terminating and reaping the
CLI process; that cleanup can take additional time.

A background job has **no total wall-clock cap by default**. A LOCAL review
runs the mutation baseline in every round and may take up to
`FORGE_MAX_TOTAL_ROUNDS` rounds, so no fixed total fits every review. Instead,
an inactivity watchdog kills the job when it prints no new progress (its
stderr log, where Forge writes its `[forge]` lines, stops growing) for the
idle window:

    idle window = max(backend invoke timeout, longest silent test step) + 600 s

The longest silent test step is the larger of `test.timeout_seconds` (default
120; one run of the test command) and `test.mutation_timeout_seconds`, but only
when a `test.command` is configured. These are read from the same trusted gate
the review uses: `FORGE_TEST_CONFIG_ROOT` when set, otherwise the HEAD
(baseline) copy of `.code-forge/gate.yaml`, never an uncommitted edit to it.
With `timeout_seconds: 2400` and a CLI backend (300 s invoke timeout), the
window is 2400 + 600 = 3000 s, and a review of any length keeps running as
long as it reports progress within that window.

A hard total cap applies only when set explicitly: `FORGE_MCP_JOB_TIMEOUT_S`
(server-wide; a non-integer or non-positive value is ignored with a warning),
or a per-call `timeout_s`. An explicit `timeout_s` overrides both for that call
and replaces the watchdog with that deadline. It controls the total CLI run,
not an individual LLM invocation. It also cannot extend the MCP client's own
request timeout, which is configured by the client.

## Troubleshooting

### Zombie processes after /mcp reconnect

Claude Code's `/mcp reconnect` spawns a new server process without
reaping the previous one. After reconnecting, check for stale
processes:

```bash
pgrep -f code-forge-mcp
```

Kill stale ones if found:

```bash
pkill -f code-forge-mcp
```

Then reconnect again. This is a client-side behavior that the server
cannot control.

### API key not found

The MCP server inherits its environment at startup. Keys exported in
your shell after the server started are invisible to it. Set API keys
in the MCP server config `env` block (or a wrapper script), then
restart the server.

Run `forge_resolve_outlet` to check. If a backend's key is missing,
the server now reports the exact env var name and where to set it.

### Old gate.yaml from a previous init

If `code-forge init` was run before v2.7, the generated gate.yaml may
have `outlet: subprocess` active with no backends configured. The
server now rejects this at startup with an actionable error and
remediation steps. Follow the instructions in the error message to
add a backend. The `sampling` outlet has been removed; it is not an option.
