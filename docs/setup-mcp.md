# MCP Server Setup

## Workspace Model

One MCP server instance serves one project. The server resolves its
workspace once at startup (priority: `FORGE_PROJECT_DIR` env var, then
walking up from `cwd` to find `.code-forge/gate.yaml`) and uses that
directory for all tool calls. It does not re-resolve per call.

If you work on multiple projects, configure a separate MCP server entry
per project. Claude Code's `.mcp.json` and VS Code's `.vscode/mcp.json`
are per-project by design, so this maps naturally.

### Multi-project example (Claude Code)

```json
{
  "mcpServers": {
    "forge-projectA": {
      "command": "code-forge-mcp",
      "env": { "FORGE_PROJECT_DIR": "/home/user/code/projectA" }
    },
    "forge-projectB": {
      "command": "code-forge-mcp",
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

Example `.claude.json` MCP entry:

```json
{
  "mcpServers": {
    "code-forge-mcp": {
      "command": "code-forge-mcp",
      "env": {
        "FORGE_PROJECT_DIR": "/home/user/code/myproject"
      }
    }
  }
}
```

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
add a backend or switch to `outlet: sampling`.
