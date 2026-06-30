# Claude Code Setup Guide

Claude Code is code-forge's native habitat: the review passes are Claude Code
skills, and the default backend is the `claude` CLI you already have. This guide
gets forge running in Claude Code and explains the one thing that matters most --
not letting a model review its own code.

## Prerequisites

- Claude Code installed and authenticated (`claude auth status` succeeds)
- Python 3.12 or newer
- code-forge installed: `pip install code-review-forge`
- `jq` (for the smoke-test bash assertion primitives)

## Use `code-forge`, never `forge`

The CLI entry point is `code-forge`. A different, unrelated package installs a
`forge` command; typing `forge` runs that other tool, not this one. Always
`code-forge`.

```bash
which code-forge
```

## Install the review skills

```bash
code-forge install-skill
```

This copies the 6 review skills into `~/.claude/skills/`. Then, in Claude Code:

```
/code-forge            # full 5-step pipeline
/qodo-review           # Pass 1: change-aware pre-review
/code-review-expert    # Pass 2: SOLID, architecture, security
/adversarial-qe        # Pass 3: red-team QE, 12 attack dimensions
/kernel-fp-verify      # Step 3.5: false-positive verification
/smoke-test            # Step 4: runtime verification
```

Other targets (project-local, universal, explicit dir, single skill, overwrite):

```bash
code-forge install-skill --target vscode      # <cwd>/.claude/skills/
code-forge install-skill --target universal   # <cwd>/.agents/skills/
code-forge install-skill --dest /path/to/dir
code-forge install-skill --skill code-forge
code-forge install-skill --force
```

(From a git clone, `./install.sh` symlinks the 6 skills instead of copying.)

## The one thing that matters: surface vs substance

forge has two independent questions, and onboarding goes wrong when they are
confused.

- **Surface** -- where you trigger forge: the `/code-forge` skill, the MCP
  server, or the `code-forge` CLI.
- **Substance** -- who actually reviews the code:
  - *Inline / default*: the `/code-forge` skill (and the default cli backend)
    run the review in YOUR CURRENT Claude session. It is a real review
    procedure, but the model reviewing the diff is the same model that could
    have written it -- author and reviewer collapse, and it inherits its own
    blind spots. That is the exact failure forge exists to prevent.
  - *Independent*: route the review to a DIFFERENT backend -- a separate API
    model configured in `gate.yaml`, or the MCP server (which sends the review
    to the configured backend, so the calling model never reviews its own code).

Rule of thumb: the `/code-forge` skill alone gives you the procedure, not
independence. For a verdict you can trust, add one independent backend (below),
or lean on the deterministic gates, which use no LLM at all.

## Backend: zero-key default vs independent review

By default forge uses the `claude` CLI in your PATH with your session model --
no API key, nothing to configure. This is account-authenticated review: it
rides your existing Claude Code login. It is the fastest way to try forge, and
also self-review (see above).

```bash
code-forge review                              # default: your claude, session model
FORGE_LLM_MODEL=claude-opus-4-5 code-forge review
```

For independent review, define a named backend in `.code-forge/gate.yaml`, trust
it, and provide its key:

```bash
code-forge init                  # writes .code-forge/gate.yaml + gate.schema.json
# edit gate.yaml: add an api backend (see configuration.md for fields/examples)
code-forge trust                 # repo backends are untrusted until you trust them
export DEEPSEEK_API_KEY=...      # whatever the backend's api_key_env names
FORGE_BACKEND=<name> code-forge review
```

Confirm which backend will run before relying on it:

```bash
code-forge resolve-outlet        # names the backend, or says "key not set" / untrusted
```

Full backend reference -- API/CLI fields, OpenAI-compatible routers, and
account-authenticated tools via a local bridge -- is in
[configuration.md](configuration.md).

## Deterministic gates (no LLM, no quota -- start here)

These need no backend and no API key. They are the un-fakeable layer: a green
result means the tests actually ran, not that a model said so.

```bash
code-forge gate-check       # runs your test suite, blocks on NEW failures vs a baseline (R1)
code-forge mutation-check   # proves your tests catch changes to the diff
code-forge e2e-check        # cross-component coverage heuristic
```

`gate-check` compares results against a recorded baseline; until one exists it
warns and allows the commit. To make it enforce on every commit, see
[Enabling the commit gate (R1)](../README.md#enabling-the-commit-gate-r1) in the
README.

## MCP server (optional: native tools in Claude Code)

The MCP server exposes forge as tools, and routes reviews to the configured
backend -- so the calling model never reviews its own code (independence by
construction).

```bash
pip install code-review-forge[mcp]
claude mcp add forge -- code-forge-mcp
```

Launch `claude` from the repo root so the server finds `.code-forge/gate.yaml`.
Tools: `forge_review`, `forge_gate_check`, `forge_resolve_outlet`,
`forge_job_status`, `forge_init`, `forge_trust`. Verify with
`forge_resolve_outlet` -- it should name a backend, not "key not set". The
server still needs a configured + trusted backend with its key in the server
environment; without one, `forge_review` fails closed (same as the CLI).

## Commit gate (auto-fire)

```bash
code-forge install-hooks    # writes .git/hooks/pre-commit: verify + gate-check
```

Code commits run the gate; commits that stage only non-code files (`.md`,
`.yaml`, `.toml`, `LICENSE`, `README`) skip it automatically -- no `--no-verify`
needed. If `git config core.hooksPath` is set, `install-hooks` refuses to
overwrite it and prints a two-line manual fallback to add to your existing hook.

## Troubleshooting

### "code-forge: command not found", or `forge` runs the wrong tool

```bash
which code-forge
echo $PATH
```

Add the Python user bin to PATH if needed: `export PATH="$HOME/.local/bin:$PATH"`.
Never invoke bare `forge` -- that is a different package.

### "must run inside a linked git worktree"

forge refuses to review in a main worktree. Create a linked one:

```bash
git worktree add .worktrees/review <branch>
```

`.code-forge/gate.yaml` is gitignored, so a fresh worktree will not have it and
`resolve-outlet` will say "No review backend configured". Symlink it from the
main tree into the worktree's `.code-forge/` (symlink the file only, not the
directory).

### resolve-outlet says "key not set" or the repo is untrusted

Export the backend's key, and run `code-forge trust` in the repo. Until trusted,
repo-supplied backend URLs are ignored (a supply-chain guard, not a bug).

### Review is slow

Cross-Pacific CN backends are slow under three review passes. Raise
`FORGE_LLM_TIMEOUT_S` (see configuration.md).

## Related Documentation

- [configuration.md](configuration.md) -- full env var + gate.yaml reference
  (account-auth, local bridge, canary, model selection)
- README [Enabling the commit gate (R1)](../README.md#enabling-the-commit-gate-r1)
  -- test baseline + hooks
- [setup-vscode.md](setup-vscode.md) / [setup-cursor.md](setup-cursor.md) /
  [setup-pycharm.md](setup-pycharm.md) -- other editors
