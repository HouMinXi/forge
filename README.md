# Forge

5-step code review pipeline for AI coding assistants. Minimum 9 static review
passes before any commit is allowed.

Forge treats code review as a state machine: three review cycles, each with
three independent passes. Any finding resets the counter to zero. Only after
three consecutive clean cycles does the code proceed to runtime smoke testing
and the commit gate.

## Pipeline

```
Code Change
     |
     v
[Step 0] Syntax (0a) + Lint (0b) + Non-ASCII (0c)
     |
     v
[Cycle 1]  Pass 1: /qodo-review
           Pass 2: /code-review-expert
           Pass 3: /adversarial-qe
     |                              |
     | zero findings                | findings -> fix -> counter=0
     | counter=1                    |            restart Cycle 1
     v
[Cycle 2]  (same 3 passes)
     |
     v
[Cycle 3]  (same 3 passes)
     |
     | counter=3
     v
[Step 3.5] /kernel-fp-verify (if findings were fixed during cycles)
     |
     v
[Step 4] /smoke-test (runtime verification)
     |
     v
[COMMIT GATE]  # post-review-c3
```

## Skills

| Skill | Pipeline Step | Purpose |
|-------|-------------|---------|
| forge | Orchestrator | Runs the full 5-step pipeline |
| qodo-review | Pass 1 | Change-aware pre-review with feature-grouped walkthrough |
| code-review-expert | Pass 2 | SOLID, architecture, security analysis |
| adversarial-qe | Pass 3 | Red-team QE with 12 attack dimensions |
| kernel-fp-verify | Step 3.5 | 10-step false-positive verification protocol |
| smoke-test | Step 4 | Runtime verification with bash assertion primitives |

## Hooks (Reference Implementation)

| Hook | Trigger | Purpose |
|------|---------|---------|
| check_worktree.sh | PreToolUse Edit/Write | Block edits in main worktree |
| check_non_ascii.sh | PreToolUse Write/Edit | Non-ASCII character detection |
| check_read_before_edit.sh | PreToolUse Edit | 1:1 read-before-edit ratio |
| check_review_tracker.sh | PostToolUse Bash | Review cycle state machine |
| check_git_commit_review.sh | PreToolUse Bash | Block unreviewed commits |
| check_git_push_review.sh | PreToolUse Bash | Block unreviewed pushes |

Hooks are reference implementations. Some contain environment-specific logic
(Kerberos auth, Chinese pattern matching) that you will need to adapt.
See `hooks/README.md` for details.

## Quick Start

```bash
git clone https://github.com/HouMinXi/forge.git
cd forge
./install.sh
```

The installer creates symlinks from `~/.claude/skills/<name>` to this repo's
`skills/<name>` for each of the 6 skills.

Hook installation is manual -- see `hooks/README.md` and
`hooks/settings-snippet.json`.

After installation, invoke the full pipeline in Claude Code:

```
/forge
```

Or invoke individual skills:

```
/qodo-review
/code-review-expert
/adversarial-qe
/smoke-test
```

## Bash Smoke Primitives

The `skills/smoke-test/test-library/shell/` directory contains 19 reusable
bash assertion functions with zero dependencies beyond `jq`:

- `run_and_capture` / `run_concurrent`
- `assert_success` / `assert_failure` / `assert_exit_code`
- `assert_output_contains` / `assert_output_not_contains`
- `assert_stderr_contains` / `assert_stderr_empty`
- `assert_file_exists` / `assert_file_not_exists` / `assert_file_contains`
- `assert_json_valid` / `assert_json_field`
- `assert_no_zombie` / `assert_temp_clean`
- `assert_no_command_exec` / `assert_no_path_traversal`

A backward-compatible symlink at `test-library/` points to
`skills/smoke-test/test-library/` for users migrating from
[bash-smoke-primitives](https://github.com/HouMinXi/bash-smoke-primitives).

## Evidence

The `evidence/` directory documents design decisions and the rationale behind
key pipeline mechanisms:

- `cross-model-complementarity.md` -- why 3 different review passes
- `design-iterations.md` -- how the pipeline evolved
- `ground-truth-verification.md` -- why smoke tests must inject known bugs
- `shell-assertion-footguns.md` -- 5 bash-specific traps that evade static analysis
- `v9-model-coverage-matrix.md` -- real-world 4-model coverage data from kernel patch review

## License

MIT
