# Presubmit Linter Presets (Community Examples)

**These are community examples, not endorsed configurations.** forge makes no
promise of upstream parity with any linter. Copy, adapt, and test in your own
workflow. If a linter changes its CLI flags or invocation, update your
gate.yaml accordingly. No upstream-parity promise; copy and adapt.

---

## How to Use

Add a `presubmit:` section to your `.code-forge/gate.yaml`. Each entry
specifies a linter command that runs before each commit. A non-zero exit code
blocks the commit.

Schema fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `command` | list[str] | yes | Command and arguments to run |
| `applies_to` | glob string | yes | Only trigger when staged files match this glob |
| `on` | `"diff"` or `"patch"` | yes | What to pipe to stdin (`diff` = unified diff, `patch` = full patch) |
| `when_exists` | path string | no | Skip this entry if this path does not exist in the repo |

---

## Preset 1: checkpatch.pl (Linux kernel style)

```yaml
presubmit:
  - command: ["scripts/checkpatch.pl", "--strict", "--max-line-length=80", "-"]
    applies_to: "*.c"
    on: diff
    when_exists: scripts/checkpatch.pl
```

**How it works:**

- The trailing `"-"` tells checkpatch.pl to read the unified diff from stdin.
- `when_exists: scripts/checkpatch.pl` means this entry only activates when
  the script is present in the repository root. In a non-kernel tree this
  entry is silently skipped.
- `applies_to: "*.c"` prevents the entry from triggering on unrelated file
  types (documentation-only commits, for example).
- Adjust `--max-line-length` to match your project's convention. The Linux
  kernel netdev tree enforces 80; other subsystems may allow more.

---

## Preset 2: go vet

```yaml
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: diff
```

**How it works:**

- `go vet ./...` runs on the entire module, not just the changed files. The
  `applies_to: "*.go"` filter ensures the entry only triggers when Go files
  are staged; it does not restrict vet to those files.
- No `when_exists` needed: if Go files are staged, `go` is expected to be
  present. A missing `go` binary causes a framework-level failure (the
  configured-but-broken linter is never a silent pass).
- To add golangci-lint on top, add a second entry with
  `command: ["golangci-lint", "run"]` and the same `applies_to`.

---

## Preset 3: clippy (Rust)

```yaml
presubmit:
  - command: ["cargo", "clippy", "--", "-D", "warnings"]
    applies_to: "*.rs"
    on: diff
    when_exists: Cargo.toml
```

**How it works:**

- `-D warnings` treats all clippy warnings as errors; any warning blocks the
  commit. Remove this flag if you prefer advisory-only warnings.
- `when_exists: Cargo.toml` skips this entry in repos without a Cargo
  manifest (mixed-language monorepos, for example).
- clippy runs on the whole crate, not just the staged files. The
  `applies_to: "*.rs"` filter prevents triggering when only non-Rust files
  are staged.

---

## Preset 4: eslint (JavaScript / TypeScript)

```yaml
presubmit:
  - command: ["npx", "eslint", "--ext", ".js", "."]
    applies_to: "*.js"
    on: diff
```

**IMPORTANT -- requires adaptation:** The `on: diff` field pipes the unified
git diff to the linter's stdin. eslint does NOT read diff format -- it
expects file paths or directory arguments. This snippet triggers eslint on
the entire project directory (`.`) whenever JS files are staged; it
effectively ignores stdin.

For a true staged-file-only scan, replace this snippet with a wrapper script
that extracts filenames from the diff and passes them as arguments:

```bash
#!/bin/sh
# staged-eslint.sh
git diff --cached --name-only --diff-filter=ACMR | grep '\.js$' | xargs npx eslint
```

Then reference the wrapper in gate.yaml:

```yaml
presubmit:
  - command: ["bash", "staged-eslint.sh"]
    applies_to: "*.js"
    on: diff
```

For TypeScript, change `--ext .js` to `--ext .ts` and `applies_to: "*.ts"`.

---

## Combining Presets

Multiple entries in one `presubmit:` list run sequentially. The hook
short-circuits on the first failure -- later entries do not run.

```yaml
presubmit:
  - command: ["go", "vet", "./..."]
    applies_to: "*.go"
    on: diff

  - command: ["cargo", "clippy", "--", "-D", "warnings"]
    applies_to: "*.rs"
    on: diff
    when_exists: Cargo.toml

  - command: ["scripts/checkpatch.pl", "--strict", "--max-line-length=80", "-"]
    applies_to: "*.c"
    on: diff
    when_exists: scripts/checkpatch.pl
```

In this example: a commit that stages only Go files triggers go vet but
skips the Rust and kernel entries. A commit that stages C files skips go vet
and clippy (no Go or Rust files staged) and runs checkpatch only if
scripts/checkpatch.pl exists.

---

## Failure Behavior

forge never silently passes a configured-but-broken linter. If a `presubmit:`
entry is present in gate.yaml and:

- the binary in `command` is not found, or
- the command exits with a non-zero status,

the commit is blocked. Remove the entry from gate.yaml to disable a linter,
rather than relying on a missing binary to skip it quietly.

`when_exists` is the correct way to make an entry conditional on tool
presence -- it is checked before the command runs and skips cleanly when the
path is absent.
