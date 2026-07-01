# MCP Server Setup

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

Run `forge_resolve_outlet` in the MCP client. The output includes an
"API key status" section showing which env var each backend expects
and whether it is currently set. If a key shows "NOT SET", check your
shell environment or pass/gpg-agent configuration.
