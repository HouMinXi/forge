# code-forge

[![PyPI version](https://img.shields.io/pypi/v/code-review-forge.svg?cacheSeconds=300)](https://pypi.org/project/code-review-forge/)
[![Python](https://img.shields.io/pypi/pyversions/code-review-forge.svg?cacheSeconds=300)](https://pypi.org/project/code-review-forge/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/HouMinXi/forge/blob/main/LICENSE)

A 5-step code review pipeline for AI coding assistants. Treats review as a
state machine: three independent passes per cycle, three consecutive clean
cycles required, any finding resets the counter. The minimum path to a
commit is 9 static review passes plus a runtime smoke test.

## Why

AI coding assistants ship code that compiles, runs, and looks right.
Single-pass review (Copilot, Cursor, CodeRabbit, etc.) catches the obvious
defects but misses two failure modes:

- **Author and reviewer collapse.** When the same model writes and reviews
  the change, it inherits its own blind spots. code-forge runs three
  independent review perspectives (qodo, expert, adversarial) and treats
  their findings as untrusted claims that must be reproduced before any fix.
- **Self-claimed completion.** Hooks that gate on "I finished" markers are
  bypassable by any agent that can write a string. code-forge gates on
  actual state: a real `pre-commit` hook running the test suite, a mutation
  runner proving the tests catch regressions, and a coverage heuristic
  detecting drift across components.

## Quick start

```bash
pip install code-review-forge
code-forge install-skill
```

The first command installs the CLI (Python >=3.12). The second copies the
6 review skills into `~/.claude/skills/`. Then in Claude Code, run the
full pipeline:

```
/code-forge
```

Or invoke individual passes:

```
/qodo-review          # change-aware pre-review (Pass 1)
/code-review-expert   # SOLID, architecture, security (Pass 2)
/adversarial-qe       # red-team QE, 12 attack dimensions (Pass 3)
/kernel-fp-verify     # false-positive verification (Step 3.5)
/smoke-test           # runtime verification (Step 4)
```

Other agent targets:

```bash
code-forge install-skill --target vscode      # <cwd>/.claude/skills/
code-forge install-skill --target universal   # <cwd>/.agents/skills/
code-forge install-skill --dest /path/to/dir  # explicit location
code-forge install-skill --skill code-forge   # one skill only
code-forge install-skill --force              # overwrite existing
```

## Backend configuration

By default, code-forge uses the `claude` CLI in your PATH with the session
model (no model pin). Three environment variables control the backend:

| Variable | Purpose | Default |
|---|---|---|
| `FORGE_BACKEND` | Select a named backend from `backends.yaml` | session-default |
| `FORGE_OUTLET` | Force outlet: `cli` or `inline` | auto-detected |
| `FORGE_LLM_MODEL` | Override model for CLI backends | `claude-sonnet-4-6` |

**Quick examples:**

```bash
# Use the default (claude CLI, session model)
code-forge review

# Pin a specific model for this run
FORGE_LLM_MODEL=claude-opus-4-5 code-forge review

# Use a named API backend from backends.yaml
FORGE_BACKEND=claude-api code-forge review

# Force inline outlet (no CLI subprocess)
FORGE_OUTLET=inline code-forge review
```

**Named backends** (optional) are defined in `~/.config/code-forge/backends.yaml`:

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

Full reference: [docs/configuration.md](docs/configuration.md)

Editor setup guides:
- VS Code: [docs/setup-vscode.md](docs/setup-vscode.md)
- Cursor: [docs/setup-cursor.md](docs/setup-cursor.md)
- PyCharm: [docs/setup-pycharm.md](docs/setup-pycharm.md)

## The pipeline

```
Code Change
     |
     v
[Step 0]  Syntax (0a) + Lint (0b) + Non-ASCII (0c)
     |
     v
[Cycle 1] Pass 1: qodo-review
          Pass 2: code-review-expert
          Pass 3: adversarial-qe
     |
     |  zero findings -> counter += 1
     |  any finding   -> fix, counter = 0, restart Cycle 1
     v
[Cycle 2] (same 3 passes)
     |
     v
[Cycle 3] (same 3 passes)
     |  counter = 3
     v
[Step 3.5] kernel-fp-verify (if fixes were applied during cycles)
     |
     v
[Step 4]   smoke-test (runtime verification)
     |
     v
[COMMIT GATE]  # post-review-c3
```

## What ships

| Skill              | Step      | Purpose                                                  |
|--------------------|-----------|----------------------------------------------------------|
| code-forge         | Orchestrator | Runs the full 5-step pipeline                         |
| qodo-review        | Pass 1    | Change-aware pre-review with feature-grouped walkthrough |
| code-review-expert | Pass 2    | SOLID, architecture, security analysis                   |
| adversarial-qe     | Pass 3    | Red-team QE with 12 attack dimensions                    |
| kernel-fp-verify   | Step 3.5  | 10-step false-positive verification protocol             |
| smoke-test         | Step 4    | Runtime verification with bash assertion primitives      |

## What code-forge does that others don't

- **Multi-pass convergence.** Three consecutive clean cycles from three
  independent perspectives. Any finding resets the counter to zero.
  Copilot, CodeRabbit, Cursor, and Devin are single-pass.
- **Anti-hallucination gates.** code-forge treats LLM review output as
  untrusted claims. Parser-deterministic findings auto-confirm; LLM
  findings require falsification before disposition; Step 4 runs the
  actual code. Prompt-only mitigations cap at 15% hallucination
  reduction; tool grounding reaches 65-80% (CodeAnt and Suprmind data,
  2026).
- **Real commit gate (R1).** A real `.git/hooks/pre-commit` that runs the
  test suite and blocks on NEW failures vs a baseline. Gates on diff
  content and test results, not a self-claimed marker. Closes the
  terminal-and-IDE bypass that PreToolUse hooks cannot reach.
- **Mutation-gated review (R2).** Diff-scoped mutation runs after static
  review and before the verdict. Each mutant introduced into the changed
  code is run against the test suite; a surviving mutant flags tests that
  cannot catch the change. Toothless tests block the same cycle that
  finds the defect.
- **Cross-component coverage heuristic (R3).** Detects diffs that span
  multiple source areas with a changed function signature. An opt-in
  components mapping raises an uncertain finding when a hub and a
  dependent both change in the same diff and no integration test under
  the dependent's paths matches the configured test patterns.

## Honest limitations

- **No cross-repo impact.** code-forge reviews a single repository.
  Multi-repo dependency analysis requires CodeRabbit-style tooling or
  Chromium's `Cq-Depend`.
- **No feedback learning.** code-forge does not adapt to dismissed
  findings or developer preferences. Each review is independent.
- **No long-term maintainability scoring.** code-forge does not assess
  technical debt accumulation. SonarQube's tech-debt tracking is the
  closest automated approximation.
- **No performance regression suite.** No benchmark harness equivalent to
  Rust's `perf.rust-lang.org`.
- **R3 is artifact-presence, not coverage proof.** The cross-component
  check confirms an integration test file exists under the expected path;
  it does not verify that the test exercises the specific code that
  changed. A present-but-stale test passes the gate.

Static review (3-cycle convergence) is one layer. code-forge learned
from its own Phase 2 experience where 9 static passes and 639 mock tests
missed 3 bugs that dynamic verification caught. Verification grounding
(test suite + mutation + e2e coverage check) is the thesis -- not a
passes count.

## Requirements

- Python 3.12 or newer
- `jq` for the bash smoke primitives
- Claude Code or a compatible AI coding assistant for skill invocation

## Installation alternatives

### git clone

```bash
git clone https://github.com/HouMinXi/forge.git
cd forge
./install.sh
```

Symlinks each of the 6 skills from `~/.claude/skills/<name>` to this
repo's `skills/<name>`. Hook installation is manual -- see
`hooks/README.md` and `hooks/settings-snippet.json`.

## Enabling the commit gate (R1)

`install-skill` and `./install.sh` install the review **skills** only -- they
do not set up enforcement. The R1 pre-commit gate -- the un-fakeable layer that
runs the test suite on every commit and blocks on new failures, regardless of
what the in-editor review claims -- is a separate, manual step:

1. Add a `test:` section to `.code-forge/gate.yaml`. Without it, `gate-check`
   exits with `gate.yaml must have a 'test' section`:

   ```yaml
   test:
     command: [pytest, -q]
     timeout_seconds: 900
   ```

   `command[0]` must be a known runner (`python3`, `python`, `pytest`, `cargo`,
   `go`, `make`, `npm`, `npx`, `node`); no shell metacharacters are allowed.

2. Install the hook:

   ```bash
   code-forge install-hooks
   ```

   This writes `.git/hooks/pre-commit` that runs `code-forge verify` (a receipt
   tamper check) and then `code-forge gate-check` (the test gate).

3. If `git config core.hooksPath` is set, `install-hooks` refuses to write to a
   custom hooks path and prints a manual fallback. Add these two lines to your
   existing pre-commit hook by hand:

   ```sh
   code-forge verify --quiet 2>/dev/null || exit 1
   exec code-forge gate-check
   ```

The skills give you the review passes; this gate is what makes a green verdict
mean the tests actually pass. Without it, an in-editor review that never ran can
still reach a commit. Commits that stage only non-code files (docs, config,
metadata such as `.md`, `.yaml`, `.toml`, `LICENSE`, `README`) are detected by
the hook and skip the gate automatically -- no receipts and no `--no-verify`
needed. Any staged file outside that set, including unknown extensions, re-arms
the gate for the whole commit.

## Hooks (reference implementations)

| Hook                          | Trigger               | Purpose                       |
|-------------------------------|-----------------------|-------------------------------|
| `check_worktree.sh`           | PreToolUse Edit/Write | Block edits in main worktree  |
| `check_non_ascii.sh`          | PreToolUse Write/Edit | Non-ASCII character detection |
| `check_read_before_edit.sh`   | PreToolUse Edit       | 1:1 read-before-edit ratio    |
| `check_review_tracker.sh`     | PostToolUse Bash      | Review cycle state machine    |
| `check_git_commit_review.sh`  | PreToolUse Bash       | Block unreviewed commits      |
| `check_git_push_review.sh`    | PreToolUse Bash       | Block unreviewed pushes       |

Some hooks contain environment-specific logic (Kerberos auth, pattern
matching) you will need to adapt. See `hooks/README.md`.

## Bash smoke primitives

`skills/smoke-test/test-library/shell/` ships 19 reusable bash assertion
functions with no dependencies beyond `jq`:

- `run_and_capture`, `run_concurrent`, `concurrent_wait`
- `assert_success`, `assert_failure`, `assert_exit_code`
- `assert_output_contains`, `assert_output_not_contains`
- `assert_stderr_contains`, `assert_stderr_empty`
- `assert_file_exists`, `assert_file_not_exists`, `assert_file_contains`
- `assert_json_valid`
- `assert_no_zombie`, `assert_temp_clean`
- `assert_no_command_exec`, `assert_no_command_exec_json`, `assert_no_path_traversal`

A backward-compatible symlink at `test-library/` points to
`skills/smoke-test/test-library/` for users migrating from
[bash-smoke-primitives](https://github.com/HouMinXi/bash-smoke-primitives).

## Documentation

- `evidence/cross-model-complementarity.md` -- why 3 different review passes
- `evidence/design-iterations.md` -- how the pipeline evolved
- `evidence/ground-truth-verification.md` -- why smoke tests must inject bugs
- `evidence/shell-assertion-footguns.md` -- 5 bash-specific traps
- `evidence/v9-model-coverage-matrix.md` -- 4-model coverage data
- `hooks/README.md` -- hook installation and adaptation guide

## Contributing

Issues and discussion: <https://github.com/HouMinXi/forge/issues>.

## License

Apache-2.0
