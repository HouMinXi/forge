# VS Code Setup Guide

This guide covers how to configure code-forge environment variables so
the VS Code integrated terminal and the Claude Code extension can find
your API keys and backend settings.

## Prerequisites

- VS Code installed
- code-forge installed: `pip install code-review-forge`
- Claude Code extension installed (for `/code-forge` skill invocation)
- `claude` CLI in PATH (for CLI backends)

## Option 1: Shell RC File (Recommended)

Add environment variables to your shell initialization file. This is the
simplest approach and works across all terminal applications, not just VS Code.

**For bash** (`~/.bashrc` or `~/.bash_profile`):

```bash
# code-forge configuration
export FORGE_LLM_MODEL=claude-opus-4-5          # optional: pin model
export ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXX # if using API backend
```

**For zsh** (`~/.zshrc`):

```bash
# code-forge configuration
export FORGE_LLM_MODEL=claude-opus-4-5
export ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXX
```

After editing, restart VS Code (or open a new terminal tab) so the
updated shell environment is loaded.

**Why this works**: VS Code's integrated terminal inherits the login
shell environment on startup. Variables set in `~/.bashrc` or `~/.zshrc`
are visible to all tools run in the terminal, including code-forge.

**Verify**:

```bash
echo $FORGE_LLM_MODEL
code-forge review --version
```

## Option 2: VS Code Terminal Environment Settings

You can inject environment variables into VS Code terminals without
modifying your shell RC file. This keeps code-forge settings isolated
to the editor.

Open **Settings** (Cmd+, on macOS or Ctrl+, on Linux/Windows) and edit
`.vscode/settings.json` or the user `settings.json`:

```json
{
  "terminal.integrated.env.linux": {
    "FORGE_LLM_MODEL": "claude-opus-4-5",
    "ANTHROPIC_API_KEY": "sk-ant-api03-XXXXXXXXX"
  },
  "terminal.integrated.env.osx": {
    "FORGE_LLM_MODEL": "claude-opus-4-5",
    "ANTHROPIC_API_KEY": "sk-ant-api03-XXXXXXXXX"
  },
  "terminal.integrated.env.windows": {
    "FORGE_LLM_MODEL": "claude-opus-4-5",
    "ANTHROPIC_API_KEY": "sk-ant-api03-XXXXXXXXX"
  }
}
```

Use the key that matches your OS: `linux`, `osx`, or `windows`.

**WARNING: Do not commit `.vscode/settings.json` if it contains API keys.**

Add to your project's `.gitignore`:

```
.vscode/settings.json
```

Or keep API keys in user settings (`File > Preferences > Settings > User`)
instead of workspace settings, so they are never part of the repository.

**Verify**: Open a new VS Code terminal and run:

```bash
echo $FORGE_LLM_MODEL
```

## Option 3: .env File (Manual Source)

Create a `.env` file in your project root with code-forge settings:

```bash
# .env -- do NOT commit this file
FORGE_LLM_MODEL=claude-opus-4-5
FORGE_BACKEND=claude-api
ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXX
```

Add `.env` to `.gitignore`:

```
.env
```

Source it in each terminal session before running code-forge:

```bash
source .env
code-forge review
```

Or create a shell alias to auto-source:

```bash
# in ~/.bashrc or ~/.zshrc
alias forge='source .env && code-forge'
```

This option requires a manual step each session. Options 1 and 2 are
generally more convenient for daily use.

## Using code-forge from VS Code

### From the integrated terminal

```bash
code-forge review
```

### From the Claude Code extension

In a Claude Code chat window, type the skill name:

```
/code-forge
```

This invokes the full 5-step review pipeline using Outlet B (inline)
by default when running inside Claude Code. To force CLI subprocess mode:

```bash
# in the terminal before starting Claude Code
export FORGE_OUTLET=cli
```

Or set it in your VS Code terminal environment:

```json
{
  "terminal.integrated.env.linux": {
    "FORGE_OUTLET": "cli"
  }
}
```

## Troubleshooting

### "code-forge: command not found"

Make sure the Python bin directory is in PATH:

```bash
which code-forge
python3 -m pip show code-review-forge | grep Location
```

Add the bin directory to PATH in your shell RC file:

```bash
export PATH="$HOME/.local/bin:$PATH"  # typical pip user install
```

### "ANTHROPIC_API_KEY not set"

If using an API backend, the env var must be set in the terminal where
code-forge runs. Check:

```bash
echo $ANTHROPIC_API_KEY
```

If empty, see Option 1 or 2 above to set it persistently.

### "claude binary not found"

The CLI backend requires the `claude` binary:

```bash
which claude
```

If missing, install Claude Code CLI:
<https://docs.anthropic.com/en/docs/claude-code>

Then authenticate:

```bash
claude auth login
```

### VS Code terminal does not see newly set variables

VS Code terminals do not reload the shell RC automatically. Either:
- Open a new terminal tab (Ctrl+Shift+`)
- Restart VS Code
- Run `source ~/.bashrc` (or `~/.zshrc`) in the current terminal

## Related Documentation

- [Full configuration reference](configuration.md) -- all env vars and backends.yaml
- [Cursor setup](setup-cursor.md) -- for Cursor editor
- [PyCharm setup](setup-pycharm.md) -- for PyCharm
