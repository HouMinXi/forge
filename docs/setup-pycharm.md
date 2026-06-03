# PyCharm Setup Guide

This guide covers how to configure code-forge environment variables in
PyCharm. Three methods are available depending on whether you run
code-forge from a Run Configuration, a terminal, or via a .env file.

## Prerequisites

- PyCharm (Community or Professional) installed
- code-forge installed: `pip install code-review-forge`
- Python interpreter configured in PyCharm

## Option 1: Run/Debug Configurations (Per-Configuration)

This method sets environment variables for a specific Run Configuration.
It is the most precise option when you want separate configurations for
different backends or models.

**Steps:**

1. Open **Run > Edit Configurations...** (or click the run config dropdown
   at the top and choose **Edit Configurations...**)
2. Select an existing configuration or click **+** to create a new one
   (e.g., **Python** or **Shell Script**)
3. In the configuration editor, find the **Environment variables** field
4. Click the folder icon next to the field to open the environment
   variables dialog
5. Click **+** to add a variable:
   - Name: `FORGE_LLM_MODEL`
   - Value: `claude-opus-4-5`
6. Add additional variables as needed:
   - `FORGE_BACKEND` = `claude-api`
   - `ANTHROPIC_API_KEY` = `sk-ant-api03-XXXXXXXXX`
7. Click **OK** to save

**WARNING: PyCharm stores Run Configuration env vars in `.idea/` XML files.
Do not commit `.idea/workspace.xml` or similar files that contain API keys
to version control.** Add `.idea/` to `.gitignore`, or use Option 2 (EnvFile
plugin) to keep secrets out of `.idea/` entirely.

**Example: Shell Script configuration for code-forge**

- Script path: `code-forge` (or full path from `which code-forge`)
- Script parameters: `review`
- Environment variables: `FORGE_LLM_MODEL=claude-opus-4-5`

Click the green **Run** button to run the configuration.

## Option 2: EnvFile Plugin (.env File)

The **EnvFile** plugin (available in the JetBrains Marketplace) lets you
attach a `.env` file to any Run Configuration. This keeps secrets out of
`.idea/` XML files and is the recommended approach for teams.

**Install the plugin:**

1. Open **Settings > Plugins** (Cmd+, on macOS, Ctrl+Alt+S on Linux/Windows)
2. Search for **EnvFile** and install it
3. Restart PyCharm

**Create a .env file** in your project root:

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

**Attach the .env file to a Run Configuration:**

1. Open **Run > Edit Configurations...**
2. Select your configuration
3. Click the **EnvFile** tab (appears after plugin installation)
4. Check **Enable EnvFile**
5. Click **+** and select your `.env` file
6. Click **OK**

The variables from `.env` are now available when the configuration runs.

## Option 3: PyCharm Terminal Settings

If you run code-forge from PyCharm's built-in terminal rather than a
Run Configuration, set environment variables at the terminal level.

**Method A: System shell RC file (recommended)**

Configure variables in your shell's startup file so PyCharm's terminal
inherits them automatically. This is the same as VS Code Option 1.

For bash (`~/.bashrc`):

```bash
# code-forge configuration
export FORGE_LLM_MODEL=claude-opus-4-5
export ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXX
```

For zsh (`~/.zshrc`):

```bash
export FORGE_LLM_MODEL=claude-opus-4-5
export ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXX
```

Restart PyCharm to pick up the new shell environment.

**Method B: PyCharm terminal environment settings**

1. Open **Settings > Tools > Terminal**
2. In the **Environment variables** field, add:
   `FORGE_LLM_MODEL=claude-opus-4-5;FORGE_BACKEND=claude-api`
   (separate multiple variables with semicolons)
3. Click **OK** and reopen the terminal

**WARNING: Do not store API keys in PyCharm's terminal environment settings
if they are synced to JetBrains settings storage or a shared settings
repository.**

## Running code-forge

**From the PyCharm terminal:**

```bash
code-forge review
```

**From a Shell Script Run Configuration:**

Set up a configuration as described in Option 1 and click the green
**Run** button.

**Verify the configuration:**

```bash
echo $FORGE_LLM_MODEL
echo $FORGE_BACKEND
code-forge review --version
```

## Troubleshooting

### "code-forge: command not found" in Run Configuration

PyCharm Run Configurations may use a different PATH than your shell.
Specify the full path to code-forge in the script path field:

```bash
which code-forge   # in the terminal, find the full path
```

Then use that path in the Run Configuration (e.g., `/home/user/.local/bin/code-forge`).

### Environment variables not visible in PyCharm terminal

PyCharm's terminal may not source `~/.bashrc` automatically on some
systems. Open **Settings > Tools > Terminal** and check the **Shell path**
setting. For bash, use:

```
/bin/bash --login
```

The `--login` flag forces bash to read `~/.bash_profile` (which typically
sources `~/.bashrc`).

### EnvFile plugin not loading .env

- Verify **Enable EnvFile** is checked in the Run Configuration
- Verify the `.env` file path is correct (absolute paths are safer)
- Restart PyCharm after installing the plugin
- Check that the `.env` file has no syntax errors (each line: `KEY=VALUE`)

### API key visible in .idea/ XML

If you used Option 1 and the key is now in a `.idea/` file:

1. Remove the variable from the Run Configuration environment fields
2. Switch to Option 2 (EnvFile plugin) with a `.env` file
3. Revoke and rotate the exposed key

## Related Documentation

- [Full configuration reference](configuration.md) -- all env vars and backends.yaml
- [VS Code setup](setup-vscode.md) -- for VS Code
- [Cursor setup](setup-cursor.md) -- for Cursor
