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
export FORGE_OUTLET=subprocess
```

Or set it in your VS Code terminal environment:

```json
{
  "terminal.integrated.env.linux": {
    "FORGE_OUTLET": "subprocess"
  }
}
```

## Committing with code-forge hooks installed

`code-forge install-hooks` writes two real Git hooks into `.git/hooks` --
a `pre-commit` hook and a `commit-msg` hook. They run on every commit no
matter how you commit: the VS Code Source Control panel and the integrated
terminal both shell out to `git`, so there is no editor-GUI bypass.

Install them once per repository:

```bash
code-forge install-hooks
```

If `core.hooksPath` is set to a custom directory, install-hooks refuses and
tells you to wire the gate in manually -- it will not overwrite a custom
hooks path.

### Non-code commits skip review automatically

The `pre-commit` hook compares the staged file list against a non-code
carve-out. If every staged path matches, the hook exits without running
review. The carve-out covers:

```
.md  .txt  .yaml  .yml  .json  .toml  .cfg  .ini  .conf
.gitignore  .editorconfig  .env.example
LICENSE  README  CHANGELOG  Makefile  Dockerfile  .dockerignore
```

A docs-only or config-only commit goes straight through. You do not
hand-mark these commits -- the file extensions decide.

### Code commits run the gate

If any staged file is outside the carve-out, the `pre-commit` hook runs,
in order:

1. **Receipt check** -- `code-forge verify --quiet`. A code commit with no
   completed review is blocked with `receipt verification failed`; run
   `code-forge review` first to produce the receipts.
2. **Staged-diff scan** -- rejects non-ASCII characters and AI-vocabulary
   introduced in the diff.
3. **Presubmit linters** -- any external linters configured under
   `presubmit:` in `.code-forge/gate.yaml`, run on the staged diff
   (fail-closed: a configured-but-missing linter blocks the commit).
4. **Test gate (R1)** -- the test suite must introduce no new failures
   versus the baseline.

### The commit message is always checked

The `commit-msg` hook runs for every commit -- including non-code ones,
because messages must stay clean. It blocks the commit if the message
contains AI-vocabulary or non-ASCII characters. Non-ASCII detection has
two modes, set with `non_ascii:` in `.code-forge/gate.yaml`:

- **`ai-smell`** (default): blocks only confusable typographic characters
  -- em dash (U+2014), en dash (U+2013), smart quotes (U+2018, U+2019,
  U+201C, U+201D), ellipsis (U+2026), right arrow (U+2192), and
  non-breaking space (U+00A0). Accented letters, CJK, and emoji pass.
- **`strict`**: blocks every non-ASCII byte. Opt in with `non_ascii: strict`.

### When a commit is blocked

The hook prints which check failed. Fix the cause -- run a review, install
the missing linter, or clean the diff or commit message. Do not pass
`git commit --no-verify`: it skips the hooks entirely and defeats the gate.

For the complete `.code-forge/gate.yaml` schema -- `presubmit:` entries and
the `non_ascii` mode -- see the [configuration reference](configuration.md).

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

- [Full configuration reference](configuration.md) -- all env vars and gate.yaml
- [Claude Code setup](setup-claude-code.md) -- for Claude Code CLI
- [Cursor setup](setup-cursor.md) -- for Cursor editor
- [PyCharm setup](setup-pycharm.md) -- for PyCharm
