# CI Setup Guide

forge runs in CI two ways, and they are not equal in cost or reliability:

- **Deterministic gate (`gate-check`)** -- runs your test suite, blocks on new
  failures. No API key, no LLM, no quota, no network. This is the CI surface to
  lead with: a green result means the tests actually ran.
- **LLM review (`review --mode ci`)** -- the full pipeline against a backend,
  emitting SARIF. Needs a backend key as a CI secret and some setup (below).
  Useful as advisory annotations, not as the hard gate.

## The deterministic gate (recommended)

```bash
pip install code-review-forge
code-forge gate-check        # exit 1 if staged/changed tests newly fail, else 0
```

`gate-check` requires a `test:` section in `.code-forge/gate.yaml` that names the
test runner command (e.g. `command: [pytest]`). Without it, gate-check does not
know how to run your tests. It compares results against
`.code-forge/test_baseline.json`. **Without a baseline it fails open** (warns,
exits 0) -- so commit a baseline or the gate passes everything. Generate it once
and commit it:

```bash
code-forge gate-check        # first run records state; commit .code-forge/test_baseline.json
```

`gate-check` does not require a linked worktree and needs no key, so it runs in a
plain `actions/checkout`. Use `--quiet` to suppress warnings.

### GitHub Actions example

```yaml
name: forge gate
on: [pull_request]
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install code-review-forge
      - run: code-forge gate-check
```

## Exit codes

forge uses the exit code as the gate signal:

| Code | Meaning |
|------|---------|
| 0 | PASS |
| 1 | FAIL (findings, or new test failures) |
| 2 | CLI_ERROR (bad invocation, no reachable backend) |
| 3 | BUSY |
| 4 | ESCALATED (review escalated for human decision) |
| 5 | DELEGATED (inline outlet -- review not enforced by CLI) |
| 6 | TIMEOUT |
| 7 | UNRELIABLE (canary check failed -- reviewer missed planted defects) |

A CI step fails the job on any non-zero, which is what you want for a gate.

## LLM review in CI (advanced)

`code-forge review` emits SARIF to stdout and a human summary to stderr when not
attached to a TTY; it auto-selects CI mode there, or force it with `--mode ci`.
Redirect stdout to capture the SARIF report. Two things make it fiddly in CI:

1. **It needs a backend key.** Put it in CI secrets and export it for the step
   (e.g. `DEEPSEEK_API_KEY`), and make sure the repo's `gate.yaml` backend is
   trusted. Reviews are billed and slow (cross-Pacific, three passes), so this
   is advisory, not a per-PR hard gate.
2. **It refuses to run in the main tree.** `review` requires a linked worktree
   (it errors when git-dir equals git-common-dir, which is what a plain checkout
   is). In CI, create one first:

```yaml
      - run: git worktree add .worktrees/ci HEAD
      - run: code-forge review --mode ci --committed > review.sarif
        working-directory: .worktrees/ci
        env:
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
```

`.code-forge/gate.yaml` is gitignored, so it is absent in a fresh checkout --
provide it in the job (write it from a secret or a committed template) or the
review fails closed with "No review backend configured". Upload `review.sarif`
to code scanning if you want inline annotations.

Because of the key, cost, and worktree friction, keep `gate-check` as the
blocking gate and treat LLM review as optional advisory output.

## Related Documentation

- [configuration.md](configuration.md) -- backends, gate.yaml, keys, baseline
- README [Enabling the commit gate (R1)](../README.md#enabling-the-commit-gate-r1)
  -- the same gate as a local pre-commit hook
- [setup-mcp.md](setup-mcp.md) -- the MCP server for in-editor review
