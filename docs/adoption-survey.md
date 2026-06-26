# Adoption Survey: ~/code/ Repos

Surveyed 2026-06-26 for code-forge gate-check deployment readiness.

## Daily-Use Repos

| Repo | Language | Test Runner | Existing Hook | Gate-Check Ready | Notes |
|------|----------|-------------|---------------|------------------|-------|
| forge | Python | pytest | YES (planning-leak) | YES | gate.yaml + test_baseline.json already configured |
| surflare-watchdog | Shell | bash smoke tests | no | NO | Needs gate.yaml with test_command override |
| ashare-lab | Python | pytest | no | Partial | Has pyproject.toml + tests/; needs `code-forge init` + baseline |
| mhou_workspace | Mixed (scripts) | none | no | NO | Config/dotfiles repo, no test suite |
| code-review-graph | Python | pytest | no | Partial | Has pyproject.toml + tests/; needs `code-forge init` + baseline |

## Other Repos (low or zero recent activity)

| Repo | Language | Notes |
|------|----------|-------|
| sashiko | Rust | 0 recent commits; would need cargo test command override |
| hermes-agent | Python/Node | Upstream project, 0 recent commits |
| linux-net-next | C | Kernel repo, uses kselftests/make; not suitable for forge gate |
| pynfs | Python | Upstream project, custom test runner |

## Deployment Order

1. **forge** (already configured) -- gate.yaml and test_baseline.json exist;
   run `code-forge install-hooks` to deploy. The planning-leak guard is
   auto-enabled when forge detects its own repo.

2. **ashare-lab** (high activity, pytest) -- natural fit since it already
   has pyproject.toml and a tests/ directory.
   ```bash
   cd ~/code/ashare-lab
   code-forge init          # creates .code-forge/gate.yaml
   code-forge baseline      # generates test_baseline.json
   code-forge install-hooks
   ```

3. **surflare-watchdog** (high activity, needs override) -- shell-only repo
   whose tests run via `bash tests/run_smoke_tests.sh`. Requires a
   test_command override in gate.yaml.
   ```bash
   cd ~/code/surflare-watchdog
   code-forge init
   ```
   Then edit `.code-forge/gate.yaml` to set:
   ```yaml
   test:
     command: [bash, tests/run_smoke_tests.sh]
     source_patterns: ["*.sh"]
   ```
   ```bash
   code-forge baseline
   code-forge install-hooks
   ```

4. **code-review-graph** (low activity, pytest) -- same process as ashare-lab.
   Low priority given minimal recent commits.

## Repos to Skip

- **mhou_workspace** -- config/dotfiles repo with no test suite. Gate-check
  adds no value without tests to run.
- **Upstream/kernel repos** -- use their own CI pipelines (kselftests,
  patchwork, Beaker). Forge gate-check is not appropriate for these.

## ADOPT-03 Coverage

The ADOPT-03 requirement ("pre-commit hook in >=1 target repo blocks a commit
introducing a new test failure") is satisfied by forge alone. The remaining
repos are stretch goals for broader adoption.
