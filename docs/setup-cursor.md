# Cursor Setup Guide

This guide covers how to configure code-forge environment variables in
Cursor editor. Cursor's integrated terminal inherits your shell environment,
so the setup is similar to VS Code.

## Prerequisites

- Cursor installed
- code-forge installed: `pip install code-review-forge`
- `claude` CLI in PATH (for CLI backends)

## Note on Cursor's Built-in AI

Cursor's built-in AI features (Composer, Chat, inline completions) use
Cursor's own configuration and API connection -- they are separate from
code-forge. The setup in this guide controls code-forge's backend when
you run `code-forge review` from Cursor's integrated terminal.

## Option 1: Shell RC File (Recommended)

Add environment variables to your shell initialization file:

**For bash** (`~/.bashrc` or `~/.bash_profile`):

```bash
# code-forge configuration
export FORGE_LLM_MODEL=claude-opus-4-5
export ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXX   # if using API backend
```

**For zsh** (`~/.zshrc`):

```bash
# code-forge configuration
export FORGE_LLM_MODEL=claude-opus-4-5
export ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXX
```

After editing, restart Cursor (or open a new terminal tab) to pick up
the new environment.

**Verify**:

```bash
echo $FORGE_LLM_MODEL
code-forge review --version
```

## Option 2: .env File

Create a `.env` file in your project root:

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

Source the file at the start of each terminal session:

```bash
source .env
code-forge review
```

## Cursor Terminal vs VS Code settings.json

Unlike VS Code, Cursor does not have a `terminal.integrated.env.*` setting
in its UI that is as straightforward to use. The recommended approach for
Cursor is Option 1 (shell RC file). If you need project-scoped env vars
without the shell RC, use the .env file (Option 2).

If you also use VS Code and prefer the `terminal.integrated.env` approach,
see the [VS Code setup guide](setup-vscode.md#option-2-vs-code-terminal-environment-settings)
for the settings.json method. Cursor respects VS Code-compatible
`settings.json` files in the `.vscode/` directory for many settings,
including `terminal.integrated.env.*`. However, this behavior may vary
with Cursor versions, so Option 1 remains the most reliable choice.

**WARNING: Do not commit `.vscode/settings.json` or `.env` files that
contain API keys to version control.**

## Using code-forge from Cursor

From the Cursor integrated terminal (Ctrl+` to open):

```bash
# Default review with session model
code-forge review

# Pin a specific model for this run
FORGE_LLM_MODEL=claude-opus-4-5 code-forge review

# Use a named API backend
FORGE_BACKEND=claude-api code-forge review
```

To use code-forge in combination with Cursor's AI chat, run it from the
integrated terminal. Ensure the `claude` CLI is authenticated and
FORGE_OUTLET is not set (auto-detection uses CLI mode when the backend
is reachable):

```bash
# Check CLI auth status
claude auth status
```

## Committing with code-forge hooks installed

If you installed the code-forge Git hooks (`code-forge install-hooks`),
they run on every commit -- Cursor's Source Control panel shells out to
`git`, so the hooks fire there too, with no IDE bypass. Non-code commits
skip review by file extension; code commits run receipts, linters, and
the test gate; and every commit message is checked for non-ASCII and
AI-vocabulary. See the VS Code guide's
[commit section](setup-vscode.md#committing-with-code-forge-hooks-installed)
for the full behavior and how to respond when a commit is blocked.

## Troubleshooting

### "code-forge: command not found"

```bash
which code-forge
echo $PATH
```

If code-forge is installed but not found, add the Python user bin
directory to PATH in your shell RC:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

### Environment variables not visible in Cursor terminal

Cursor terminals source your shell RC on startup. If you recently added
variables to `~/.bashrc` or `~/.zshrc`, open a new terminal tab or run:

```bash
source ~/.bashrc   # or ~/.zshrc
```

### API key not recognized

```bash
echo $ANTHROPIC_API_KEY
```

If empty, the variable is not set in the current session. Use Option 1
to set it persistently or source your `.env` file.

## Related Documentation

- [Full configuration reference](configuration.md) -- all env vars and gate.yaml
- [Claude Code setup](setup-claude-code.md) -- for Claude Code CLI
- [VS Code setup](setup-vscode.md) -- for VS Code, including terminal.integrated.env
- [PyCharm setup](setup-pycharm.md) -- for PyCharm

## See also

- [docs/configuration.md](configuration.md) -- full environment variable and backend reference,
  including the `gate.yaml` backends block and field tables.
- The `gate.schema.json` schema file (written by `code-forge init` alongside `gate.yaml`)
  enables IDE tooling that reads yaml-language-server directives. VS Code and Cursor honor
  the `$schema` directive automatically.
