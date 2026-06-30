# MCP Server Setup Guide

forge ships a local stdio MCP server, `code-forge-mcp`, that exposes the review
pipeline as tools any MCP-capable editor can call (Claude Code, VS Code, Cursor,
PyCharm). This guide sets it up and explains the one reason to prefer MCP over
the `/code-forge` skill: independence.

## Why MCP (surface vs substance)

The `/code-forge` skill runs the review inside your current editor session --
the same model that wrote the diff reviews it (author and reviewer collapse).
The MCP server is different in substance, not just surface: `forge_review`
routes the diff to the backend configured in `gate.yaml` and returns that
backend's verdict. The calling model never reviews its own code. That external
backend is the whole point -- so the setup below is mostly about making sure the
server can actually reach one.

If the server has no reachable backend, `forge_review` fails closed -- it does
not silently fall back to self-review. Independence by construction.

## Prerequisites

- code-forge with the MCP extra: `pip install code-review-forge[mcp]`
- `code-forge-mcp` on your PATH (`which code-forge-mcp`)
- A workspace with a trusted backend: `.code-forge/gate.yaml` defining an API
  backend, `code-forge trust` run in it, and the backend's API key available
  (see [configuration.md](configuration.md)). Without this the tools still load
  but `forge_review` fails closed.

## The key problem, and the wrapper

The server reads the backend API key from its own environment. The CLI
(`claude` launched from a shell) inherits your exported variables, but **GUI
editors inherit neither your shell environment nor your PATH** -- so VS Code and
PyCharm launch the server with no key (review fails closed) and often cannot
find a command that lives in `~/.local/bin`.

One small launcher solves all of it, and keeps the key out of every config
file. Example with `pass` (adapt to your secret store and to your backend's
`api_key_env`):

```bash
#!/usr/bin/env bash
# ~/.local/bin/code-forge-mcp-pass
set -eu
export PATH="$HOME/.local/bin:$PATH"          # so the server can exec code-forge
DEEPSEEK_API_KEY="$(pass show path/to/deepseek-key)"
export DEEPSEEK_API_KEY
exec code-forge-mcp "$@"
```

```bash
chmod +x ~/.local/bin/code-forge-mcp-pass
```

Rules that save an hour:

- Point every editor at this wrapper, not bare `code-forge-mcp`.
- In GUI editors (VS Code, PyCharm) reference it by **absolute path** -- the GUI
  PATH usually does not include `~/.local/bin`.
- Keep your secret store unlocked (for `pass`, gpg-agent) so the wrapper can
  decrypt at launch.
- Never put the raw key in an MCP config file; configs are easy to commit.

## Claude Code

Launched from a shell, so it inherits PATH -- the bare wrapper name is fine:

```bash
claude mcp add forge -s user -- code-forge-mcp-pass
```

`-s user` makes it available in every project (it fails closed where no trusted
backend exists). Launch `claude` from the workspace root so the server finds
`.code-forge/gate.yaml`. New tools appear after the next session start; verify
with `/mcp`.

## VS Code (1.102+)

User-level config at `~/.config/Code/User/mcp.json` (applies to every workspace,
never lands in a repo). Use the absolute wrapper path:

```json
{
  "servers": {
    "forge": {
      "type": "stdio",
      "command": "/absolute/path/to/code-forge-mcp-pass",
      "cwd": "${workspaceFolder}"
    }
  }
}
```

Root key is `servers` and the field is `type: stdio` -- VS Code's own schema,
different from the `mcpServers` form below.

## PyCharm (2025.2+)

PyCharm has two unrelated MCP panels. You want AI Assistant as an MCP *client*
(connecting out to forge), NOT "Settings | Tools | MCP Server" (which exposes
the IDE's own tools to other clients).

Requires the JetBrains AI Assistant plugin and an active JetBrains AI
subscription. There is no documented config file, so use the GUI:

1. Settings | Tools | AI Assistant | Model Context Protocol (MCP)
2. Click Add, choose JSON configuration, and paste (absolute wrapper path):

```json
{
  "mcpServers": {
    "forge": {
      "command": "/absolute/path/to/code-forge-mcp-pass"
    }
  }
}
```

3. Set Working directory to your project root (so the server finds
   `.code-forge/gate.yaml`), and Server level to global or project as you prefer.
4. Apply, then fully restart PyCharm -- it reads MCP config only at startup.

Root key here is `mcpServers` (the Claude Desktop form), not VS Code's `servers`.

## Verify

In the editor, call `forge_resolve_outlet`. It should name a backend (e.g. a
`subprocess` outlet on a model), not "key not set" or "No review backend
configured". From the CLI you can confirm the same resolution:

```bash
code-forge resolve-outlet      # with the key exported; prints the outlet
```

Then call `forge_review` on a real diff.

## forge_review needs a linked worktree

`forge_review` runs the same pipeline as `code-forge review`, which refuses to
run in a main worktree (exit 2, "must run inside a linked git worktree"):

- `forge_resolve_outlet` and `forge_gate_check` work from anywhere.
- `forge_review` needs the editor opened on a linked worktree:
  `git worktree add .worktrees/review <branch>`.
- `.code-forge/gate.yaml` is gitignored, so a fresh worktree lacks it. Symlink
  the file (only the file) from the main tree:
  `ln -s ../../../.code-forge/gate.yaml .worktrees/review/.code-forge/gate.yaml`.
  Trust still holds (it resolves the real path of the main-tree gate.yaml).

## The tools

| Tool | Purpose |
|------|---------|
| `forge_review` | Review the current git diff (inline if fast, else a job_id) |
| `forge_gate_check` | Pre-commit gate on staged changes |
| `forge_resolve_outlet` | Show which backend forge will use (read-only) |
| `forge_job_status` | Poll a long-running review by job_id |
| `forge_init` | Create `.code-forge/` in the workspace |
| `forge_trust` | Trust the gate.yaml backends |

## Troubleshooting

### `forge_resolve_outlet` says "key not set"

The server is not getting the key. Confirm the editor points at the wrapper
(not bare `code-forge-mcp`), that the wrapper path is absolute in GUI editors,
and that your secret store is unlocked. GUI editors never see your shell exports.

### `forge_resolve_outlet` says "No review backend configured"

The server's cwd has no `.code-forge/gate.yaml`, or the repo is untrusted. Open
the editor on the workspace root, run `code-forge trust` there, and (in a
worktree) symlink gate.yaml as above.

### `forge_review` exits 2 / "must run inside a linked git worktree"

Expected in a main worktree. Open a linked worktree (above). `forge_gate_check`
has no such restriction.

### Tools do not appear

MCP config is read at startup. Restart the editor (for PyCharm, fully quit and
reopen -- no hot reload).

## Related Documentation

- [configuration.md](configuration.md) -- backends, gate.yaml, account-auth, keys
- [setup-claude-code.md](setup-claude-code.md) -- the skill path (inline review)
- [setup-vscode.md](setup-vscode.md) / [setup-cursor.md](setup-cursor.md) /
  [setup-pycharm.md](setup-pycharm.md) -- CLI + skill setup per editor
