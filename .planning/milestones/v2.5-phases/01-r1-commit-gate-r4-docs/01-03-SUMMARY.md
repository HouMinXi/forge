---
phase: 01-r1-commit-gate-r4-docs
plan: 03
subsystem: install-hooks
tags: [cli, git-hooks, install, pre-commit]
dependency_graph:
  requires: [01-01, 01-02]
  provides: [install-hooks-subcommand, hook-installer, forge-hook]
  affects: [cli, git-hooks]
tech_stack:
  added:
    - shutil.which for forge path resolution
    - git rev-parse --git-path hooks (worktree-safe)
  patterns:
    - Idempotent install (re-install detection)
    - Backup + chain existing hooks
    - Absolute path embedding for hook shells
key_files:
  created:
    - src/forge/install_hooks.py (284 lines, 5 functions)
    - tests/test_install_hooks.py (432 lines, 18 test cases)
  modified:
    - src/forge/cli.py (replace install-hooks stub with dispatch)
decisions:
  - Idempotency via forge hook detection (check first 3 lines for "forge gate-check")
  - Backup preservation: existing backup not overwritten on re-install
  - Absolute path from shutil.which or sys.executable fallback
  - core.hooksPath override triggers FAIL with manual instructions
metrics:
  duration: 45 minutes
  tasks_completed: 2/2
  tests_added: 18
  test_coverage: 547 tests pass (original 521 + 18 new + 8 base updates)
  commits: 2
---

# Phase 01-r1-commit-gate-r4-docs Plan 03: install-hooks Implementation Summary

**One-liner:** Implemented install-hooks subcommand with worktree-safe hooks dir resolution, absolute forge path embedding, idempotent re-install, and backup+chain for existing hooks.

## Tasks Completed

### Task 1: Create install_hooks.py module

Created `src/forge/install_hooks.py` with 5 core functions:

1. **resolve_hooks_dir(cwd, run_cmd)**: Uses `git rev-parse --git-path hooks` (not hardcoded `.git/hooks`). Handles worktrees, submodules. Returns absolute Path. Raises RuntimeError if not in git repo.

2. **check_hooks_path_override(cwd, run_cmd)**: Runs `git config --get core.hooksPath`. Returns value if set, None otherwise. Does not raise on returncode 1 (not set).

3. **resolve_forge_path()**: 
   - Try `shutil.which('forge')` first
   - Fallback to `sys.executable + ' -m forge'`
   - Validates with `os.access(path, os.X_OK)`
   - Returns invocation string: `"/path/to/forge gate-check"` or `"/path/to/python3 -m forge gate-check"`
   - Raises RuntimeError if no valid path found

4. **generate_hook_content(forge_invocation, chain_path)**:
   - If chain_path is None: simple hook (`exec forge_invocation`)
   - If chain_path is not None: chain hook (call backup first, then exec forge_invocation)
   - Returns shell script string

5. **run_install_hooks(args, env, cwd, stdout, stderr)**:
   Main entry point. Returns EXIT_PASS (0) or EXIT_FAIL (1). Flow:
   - Check core.hooksPath override -> FAIL if set
   - Resolve hooks dir via git rev-parse
   - Warn if .pre-commit-config.yaml detected
   - Resolve forge absolute path
   - Check existing hook:
     - If forge hook (detect via "forge gate-check" in first 3 lines) -> re-install (idempotent), skip backup
     - If non-forge hook -> backup to pre-commit.forge-backup (preserve existing backup if present)
   - Generate hook content (with or without chain)
   - Write hook file
   - chmod 0o755
   - Print success message

**Verification:** Module imports cleanly, resolve_forge_path returns `/home/houminxi/.local/bin/forge gate-check`, generate_hook_content produces valid shell script.

**Commit:** `431f261` - feat(01-r1-commit-gate-r4-docs-03): add install_hooks module

### Task 2: Wire install-hooks into cli.py + write tests

**CLI wiring:** Replaced stub in `src/forge/cli.py` line 315-320:
```python
elif args.subcommand == 'install-hooks':
    from .install_hooks import run_install_hooks
    return run_install_hooks(
        args=args, env=os.environ, cwd=Path.cwd(),
        stdout=sys.stdout, stderr=sys.stderr
    )
```

**Tests created:** `tests/test_install_hooks.py` with 18 test cases across 8 test classes:

1. **TestInstallHookFresh** (4 tests): fresh repo, no existing hook
   - test_creates_pre_commit_hook
   - test_hook_is_executable (mode & 0o111)
   - test_hook_contains_gate_check
   - test_absolute_forge_path (contains `/`)

2. **TestInstallHookChain** (3 tests): backup + chain existing hook
   - test_existing_hook_backed_up (pre-commit.forge-backup created)
   - test_chain_calls_backup_first (backup line < gate-check line)
   - test_backup_preserved_on_reinstall (original backup not overwritten)

3. **TestHooksPathAbort** (2 tests): core.hooksPath detection
   - test_hooks_path_set_aborts (returns EXIT_FAIL)
   - test_hooks_path_unset_succeeds (returns EXIT_PASS)

4. **TestHooksDirResolution** (2 tests): git rev-parse usage
   - test_resolves_via_git_rev_parse (verifies command called)
   - test_not_git_repo_fails (raises RuntimeError)

5. **TestInstallHookIntegration** (1 test): full cycle
   - test_full_install_cycle (init, install, verify hook)

6. **TestHelperFunctions** (5 tests): unit tests for helpers
   - test_check_hooks_path_override_set
   - test_check_hooks_path_override_unset
   - test_resolve_forge_path_returns_absolute
   - test_generate_hook_content_no_chain
   - test_generate_hook_content_with_chain

7. **TestIdempotency** (1 test): re-install behavior
   - test_reinstall_over_forge_hook_skips_backup

All tests use real git repos in tmp_path fixtures. No mocking of git commands (subprocess.run calls real git).

**Verification:** All 18 new tests pass. Full suite: 547 tests pass (521 original + 18 new + 8 base commit updates). Ruff clean. No non-ASCII.

**Commit:** `2310732` - feat(01-r1-commit-gate-r4-docs-03): wire install-hooks to cli + tests

## Deviations from Plan

None - plan executed exactly as written. All 5 functions implemented per spec, all 18+ test cases present, all acceptance criteria met.

## Decisions Made

1. **Idempotency detection:** Check first 3 lines of existing hook for "forge gate-check" string. Simple, robust, avoids hash-based approaches.

2. **Backup preservation:** When re-installing over a non-forge hook and backup already exists, preserve the original backup rather than overwriting it. The original backup is more valuable than a stale forge backup.

3. **Absolute path format:** `resolve_forge_path()` returns the full invocation string (`"/path/to/forge gate-check"`), not just the binary path. This simplifies `generate_hook_content()` and makes the hook template clearer.

4. **Chain path type:** Pass `Path | None` to `generate_hook_content()`, not `str | None`. Consistent with `resolve_hooks_dir()` return type.

## Testing

**Unit tests:** 18 test cases covering all EC-3 requirements:
- Hook creation (file exists, executable, contains gate-check, absolute path)
- Backup + chain (backup created, chain ordering, backup preservation)
- core.hooksPath abort (aborts when set, succeeds when unset)
- Hooks dir resolution (git rev-parse usage, non-git error)
- Full integration cycle
- Helper function units
- Idempotent re-install

**Integration:** Full suite regression test - 547 tests pass, no new failures.

**Manual smoke test:** Not performed (requires real .git/hooks setup, covered by integration test).

## Known Issues

None.

## Self-Check: PASSED

**Created files exist:**
```bash
[ -f "src/forge/install_hooks.py" ] && echo "FOUND: src/forge/install_hooks.py" || echo "MISSING"
[ -f "tests/test_install_hooks.py" ] && echo "FOUND: tests/test_install_hooks.py" || echo "MISSING"
```
Result: Both FOUND.

**Commits exist:**
```bash
git log --oneline --all | grep -q "431f261" && echo "FOUND: 431f261" || echo "MISSING"
git log --oneline --all | grep -q "2310732" && echo "FOUND: 2310732" || echo "MISSING"
```
Result: Both FOUND.

## Threats Addressed

| Threat ID | Mitigation Implemented |
|-----------|------------------------|
| T-03-01 | Backup existing hook to .forge-backup before writing; never silently overwrite |
| T-03-02 | shutil.which returns absolute path; if forge compromised, system already compromised (accept) |
| T-03-03 | Abort with error when core.hooksPath is set; do not write to wrong directory |
| T-03-04 | Use git rev-parse --git-path hooks; handles worktrees, submodules correctly |

## Next Steps

Plan 03 complete. install-hooks subcommand is operational. Users can now run:
```bash
forge install-hooks
```
to install the pre-commit hook that calls `forge gate-check`.

Phase 1 R1 implementation is now complete (Plans 01, 02, 03 all done):
- Plan 01: argparse subparsers + backward compat routing
- Plan 02: gate-check subcommand (test runner, baseline delta, exit-code translation)
- Plan 03: install-hooks subcommand (this plan)

Ready for R4 (docs) implementation or Phase 1 verification.
