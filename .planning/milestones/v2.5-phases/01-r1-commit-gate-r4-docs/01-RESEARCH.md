# Phase 1: R1 (commit gate) + R4 (docs) - Research

**Researched:** 2026-05-25
**Domain:** Python subprocess orchestration, git hook management, CLI subparsers, test execution gates
**Confidence:** HIGH

## Summary

Phase 1 implements a real commit gate that runs tests and blocks commits on new failures, plus fills documentation gaps in CLAUDE.md. This phase closes forge's marker-trusting blind spot by gating on actual test results instead of a self-claimed marker.

The implementation builds on existing forge patterns: subprocess.run with list arguments (runner.py security model), yaml.safe_load for config (registry.py pattern), pure functions with injected dependencies (mode_resolver.py testability), and module-per-concern structure (exit_codes.py, lock.py). The technical stack is straightforward: argparse subparsers for CLI routing, subprocess for test execution, git rev-parse for hook path resolution, and pytest as the test runner.

**Primary recommendation:** Follow the SPEC v3.2 design exactly -- it already survived 3 review rounds and host ground-truth verification. The major execution-level findings (FAIL-OPEN guard, exit 4/5 translation, absolute forge path in hooks, CI detection via FORGE_MODE) are all documented and ready to implement.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-01: argparse subparsers**
- forge gains three subcommands: `forge review` (existing pipeline, behavior unchanged), `forge gate-check` (new: parse gate.yaml, run tests, translate exit codes, baseline delta), `forge install-hooks` (new: write .git/hooks/pre-commit)
- cli.py currently has no subparsers; requires restructuring argparse setup
- Existing `forge` (no subcommand) behavior must remain as `forge review` for backward compat

**D-02: gate.yaml source_patterns field**
- gate.yaml gains `source_patterns` field for file filtering
- gate-check reads `git diff --cached --name-only` and checks against source_patterns to decide if tests should run
- For forge itself: `source_patterns: ["*.py"]`
- Separate from tools.yaml file_patterns (which control which linters run on which files)

**D-03: R4 docs sequencing discipline**
- R4 docs must distinguish LIVE vs PLANNED at time of writing
- Phase 1 delivers R4, but R2 (mutation) and R3 (e2e) do not exist yet
- Writing them as "already present" contradicts forge's anti-hallucination thesis
- Mark mutation/e2e as PLANNED, not LIVE
- As R2/R3 land in Phases 2-3, update R4 to promote items from PLANNED to LIVE
- This is a living document, not one-shot write

**D-04: Phase 1+ merge discipline**
- Phase 0 committed to main before host verification (acceptable for config-only)
- Phase 1+ (logic code) MUST follow: sub-session implements in worktree -> sub-session reports -> host ground-truth verification -> pass -> THEN merge to main
- gate-check logic with a bug landing on main is high cost
- Worktree stays until host accepts

### SPEC-Locked Decisions (from SPEC v3.2 -- NOT re-discussed)

All of the following are locked by SPEC v3.2 (3 rounds, 5-model review). Downstream agents MUST read the SPEC for full detail:

- **Exit-code translation:** test 0->allow, 1/4/5/timeout->BLOCK, 2-3->warn
- **FAIL-OPEN guard:** gate-check own config/parse errors -> dedicated BLOCK path, isolated from test exit codes (EXIT_CLI_ERROR=2 must not be mistranslated to ALLOW)
- **install-hooks:** git rev-parse --git-path hooks, absolute forge path, backup + chain existing hook, ABORT on core.hooksPath
- **CI detection:** FORGE_MODE=ci + CI/GITHUB_ACTIONS/GITLAB_CI/JENKINS_URL/BUILD_URL
- **Baseline:** blocks only NEW failures vs .forge/test_baseline.json; no baseline -> allow+warn; absent-but-FAILS -> BLOCK; absent-but-PASSES -> fold into baseline
- **Pre-commit gates on DIFF CONTENT** (not marker); PreToolUse hook KEPT (orthogonal, CC-only)
- **FORGE_SKIP_TESTS=1 -> warn+allow (local); CI mode always runs regardless**
- **Command safety:** test.command[0] must be known runner, no shell metachar

### Claude's Discretion

- Internal module layout for gate_check.py / install_hooks.py
- Test file organization for new gate-check tests
- Specific error message wording
- Whether to add yamllint to pyproject.toml dev deps (currently pip installed inline in Phase 0)

</user_constraints>

<phase_requirements>
## Phase Requirements

Phase 1 exit criteria (10 items from ROADMAP.md):

| ID | Description | Research Support |
|----|-------------|------------------|
| EC-1 | Step 0: ruff clean, non-ASCII grep clean on every changed file | Standard forge workflow (already in project) |
| EC-2 | forge gate-check: parse gate.yaml, run tests, translate exit codes, FAIL-OPEN guard | yaml.safe_load (registry.py pattern), subprocess.run list args (runner.py security), exit_codes.py constants |
| EC-3 | Real .git/hooks/pre-commit gates on source files; forge install-hooks CLI | git rev-parse --git-path hooks, shutil.which for absolute path, backup+chain pattern |
| EC-4 | CI detection: FORGE_MODE=ci + platform vars; FORGE_SKIP_TESTS ignored in CI | mode_resolver.py existing pattern extended |
| EC-5 | Baseline: blocks only NEW failures vs .forge/test_baseline.json | Phase 0 delivered baseline schema, gate-check reads and compares |
| EC-6 | Full suite green + NEW unit tests for gate-check / exit-code translation / install-hooks | pytest framework already in use (521 existing tests) |
| EC-7 | Bug-inject (teeth): break exit-code translation -> test FAILS; revert -> PASS | Standard forge verification discipline |
| EC-8 | Real-dependency smoke: red tree -> hook BLOCKS; terminal commit -> still gated; broken gate.yaml -> BLOCK | Drive real hook, not mock |
| EC-9 | Forge's own 3-cycle review reaches zero findings | Standard forge merge requirement |
| EC-10 | R4: fill CLAUDE.md:287-288 "What Forge Covers" + "What Forge Is Missing" sections | D-03 sequencing: LIVE (R1) vs PLANNED (R2/R3) |

</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| CLI routing (subparsers) | CLI / Frontend | (none) | argparse owns command dispatch before backend logic |
| Test command execution | Backend / Subprocess | (none) | subprocess.run isolation, timeout, exit code capture |
| Gate config parsing | Config Layer | (none) | yaml.safe_load pattern from registry.py |
| Exit code translation | Backend Logic | (none) | Pure function mapping test codes to hook codes |
| Baseline delta computation | Backend Logic | Filesystem | Compare in-memory test results vs .forge/test_baseline.json |
| Hook file generation | Filesystem / Install | (none) | Write .git/hooks/pre-commit with templated content |
| Hook chaining | Filesystem / Install | (none) | Backup existing hook, exec chain pattern |
| CI mode detection | Environment / Config | (none) | Extend mode_resolver.py with platform env vars |
| Source file filtering | Diff / Git | (none) | git diff --cached --name-only + fnmatch patterns |
| R4 docs authoring | Documentation | (none) | Fill empty CLAUDE.md sections (human-facing text) |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| argparse | stdlib (3.12+) | CLI subparser routing | Built-in, zero deps, proven subparser backward compat pattern [VERIFIED: Python 3.12 stdlib] |
| subprocess | stdlib (3.12+) | Test command execution | Secure by default (list args, no shell=True), timeout support [VERIFIED: Python 3.12 stdlib] |
| pyyaml | 6.0+ | gate.yaml parsing | Already forge dependency, yaml.safe_load prevents code injection [VERIFIED: pyproject.toml line 18] |
| pytest | 8.0+ | Test framework (forge's own tests) | Already in use (521 tests), -q/-x flags standard [VERIFIED: npm view pytest version -> 9.0.3, 2026-05-25] |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| shutil.which | stdlib (3.12+) | Resolve forge absolute path for hooks | Hook PATH is minimal, must embed full path [VERIFIED: Python 3.12 stdlib] |
| fnmatch | stdlib (3.12+) | source_patterns glob matching | D-02 requirement, stdlib sufficient for *.py patterns [VERIFIED: Python 3.12 stdlib] |
| pathlib | stdlib (3.12+) | .git/hooks path manipulation | Already used throughout forge (Path objects) [VERIFIED: Python 3.12 stdlib] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| argparse subparsers | click multi-command | click adds external dep; argparse subparsers sufficient and already familiar to Python devs |
| subprocess.run | subprocess.Popen | Popen is lower-level; run() covers gate-check needs (capture stdout/stderr, timeout, check=False) |
| fnmatch | pathlib.match or re | fnmatch is stdlib glob matcher used by tools.yaml already; consistency wins |
| yaml.safe_load | json | gate.yaml is YAML (Phase 0 delivered); changing format breaks backward compat |

**Installation:**

All core dependencies already present in forge (pyproject.toml) or stdlib. No new packages needed.

**Version verification:**

```bash
# Verified 2026-05-25
npm view pytest version  # 9.0.3 (latest)
python3 --version        # Python 3.12+ required per pyproject.toml
python3 -c "import yaml; print(yaml.__version__)"  # 6.0+
```

## Architecture Patterns

### Recommended Project Structure

```
src/forge/
  cli.py                  # [MODIFIED] add subparsers
  gate_check.py           # [NEW] gate-check subcommand
  install_hooks.py        # [NEW] install-hooks subcommand
  exit_codes.py           # [REUSED] constants for exit code translation
  mode_resolver.py        # [MODIFIED] add CI platform detection
  registry.py             # [REUSED] yaml.safe_load pattern
  runner.py               # [REUSED] subprocess security pattern
  baseline.py             # [REUSED] baseline patterns (different domain)

.forge/
  gate.yaml               # [MODIFIED D-02] add source_patterns
  test_baseline.json      # [REUSED] Phase 0 output
  tools.yaml              # [UNCHANGED]

tests/
  test_gate_check.py      # [NEW] gate-check logic tests
  test_install_hooks.py   # [NEW] hook installer tests
  test_cli_subparsers.py  # [NEW] argparse subparser backward compat
  test_mode_resolver.py   # [MODIFIED] add CI detection tests
```

### Pattern 1: Subparser with Backward Compat

**What:** argparse subparsers that default to "review" subcommand when no subcommand given

**When to use:** Maintaining backward compatibility when adding subcommands to existing single-command CLI

**Example:**

```python
# Source: Python argparse docs + stackoverflow common pattern
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forge", ...)
    
    # Add subparsers with dest to capture which subcommand
    subparsers = parser.add_subparsers(dest='subcommand')
    
    # review subcommand (existing forge behavior)
    review_parser = subparsers.add_parser('review', help='...')
    review_parser.add_argument('--mode', ...)
    review_parser.add_argument('--baseline', ...)
    # ... all existing flags
    
    # gate-check subcommand (new)
    gate_parser = subparsers.add_parser('gate-check', help='...')
    # gate-check has minimal flags, most config from gate.yaml
    
    # install-hooks subcommand (new)
    hooks_parser = subparsers.add_parser('install-hooks', help='...')
    
    return parser

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    
    # Backward compat: if no subcommand, default to 'review'
    if args.subcommand is None:
        args.subcommand = 'review'
    
    if args.subcommand == 'review':
        return _run_review(args)
    elif args.subcommand == 'gate-check':
        return _run_gate_check(args)
    elif args.subcommand == 'install-hooks':
        return _run_install_hooks(args)
```

### Pattern 2: Subprocess Security (No Shell Injection)

**What:** Always use subprocess.run with list arguments, never string + shell=True

**When to use:** Any subprocess invocation, especially with user-controlled data

**Example:**

```python
# Source: forge runner.py existing pattern (VERIFIED: secure pattern)
# GOOD: list argument, shell=False (default), env dict merge
result = subprocess.run(
    test_command,  # ["python3", "-m", "pytest", "tests/", "-q"]
    env={**os.environ, **test_env},  # PYTHONPATH: "src"
    timeout=test_timeout,
    capture_output=True,
    text=True,
    check=False,  # we inspect returncode manually
)

# BAD: DO NOT DO THIS (shell injection vector)
# cmd_str = "python3 -m pytest tests/ -q"
# result = subprocess.run(cmd_str, shell=True)  # NEVER

# Command safety validation BEFORE subprocess.run:
known_runners = {"python3", "python", "pytest", "cargo", "go", "make"}
if test_command[0] not in known_runners:
    raise ValueError(f"Unsafe test command: {test_command[0]}")
if any(c in ' '.join(test_command) for c in '|;&$><`'):
    raise ValueError("Shell metacharacters not allowed in test.command")
```

### Pattern 3: Git Hook Chain with Backup

**What:** Back up existing pre-commit hook, chain-call it before running forge gate-check

**When to use:** Installing forge hook in repo that may already have hooks

**Example:**

```python
# Source: git-scm.com/docs/githooks + pre-commit.com patterns
def install_hook(hooks_dir: Path, forge_path: str):
    hook_path = hooks_dir / "pre-commit"
    backup_path = hooks_dir / "pre-commit.forge-backup"
    
    # Backup existing hook if present
    if hook_path.exists():
        shutil.move(hook_path, backup_path)
        hook_content = f"""#!/bin/sh
# Forge gate-check with chained existing hook
{backup_path} "$@" || exit 1
exec {forge_path} gate-check
"""
    else:
        hook_content = f"""#!/bin/sh
# Forge gate-check
exec {forge_path} gate-check
"""
    
    hook_path.write_text(hook_content)
    hook_path.chmod(0o755)  # Make executable
```

### Pattern 4: FAIL-OPEN Guard (Critical Security Pattern)

**What:** Isolate gate-check's own errors from test exit codes to prevent fail-open on misconfiguration

**When to use:** Any commit gate that must never silently pass on config errors

**Example:**

```python
# Source: SPEC v3.2 round-3 finding (FAIL-OPEN guard)
def gate_check() -> int:
    try:
        # CRITICAL: config/parse errors raise, never return 2-3
        config = load_gate_config()  # raises on missing/parse fail
        validate_command_safety(config.test.command)  # raises on unsafe
        baseline = load_baseline()  # OK if missing (bootstrap case)
    except (FileNotFoundError, ValueError, SecurityError) as e:
        # BLOCK path for gate-check own errors
        print(f"gate-check error: {e}", file=sys.stderr)
        return 1  # BLOCK (hook exit 1)
    
    # Run test command
    result = subprocess.run(...)
    
    # Translate test exit codes (separate from gate-check errors)
    if result.returncode == 0:
        return 0  # allow
    elif result.returncode in (1, 4, 5):
        return 1  # BLOCK
    elif result.returncode in (2, 3):
        print(f"warning: test exited {result.returncode}", file=sys.stderr)
        return 0  # allow with warning
    else:
        return 1  # BLOCK on unknown exit codes
```

### Anti-Patterns to Avoid

- **Shell string concatenation with user input:** Never build shell command strings by concatenating user-controlled values. Always use list arguments with subprocess.run.
- **Hardcoded .git/hooks path:** Use `git rev-parse --git-path hooks` to handle worktrees correctly. In worktrees, .git is a FILE, not a directory.
- **Silent hook overwrite:** Always backup existing hooks before installing. Silently clobbering user's hook destroys work.
- **Mixing gate-check errors with test exit codes:** EXIT_CLI_ERROR=2 would be mistranslated to "allow+warn" if not isolated. Use dedicated error handling for config/parse failures.
- **Relative forge path in hook:** Hooks run in minimal shell where `forge` may be off PATH. Embed absolute path via shutil.which or sys.executable.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| CLI subparser routing | Custom command dispatch with if/elif chains | argparse subparsers | argparse handles --help, error messages, nested flags automatically; custom dispatch misses edge cases (--help after flags, unknown subcommands) |
| Shell command parsing | String splitting, quote handling | subprocess.run with list args | Shell quoting is complex (nested quotes, escapes, wildcards); subprocess with list args bypasses shell entirely, preventing injection |
| Glob pattern matching | Hand-rolled wildcard logic | fnmatch or pathlib.match | Glob has edge cases (character classes, **, escapes); fnmatch is stdlib, battle-tested |
| Git hooks path resolution | Hardcoded .git/hooks | git rev-parse --git-path hooks | Worktrees, submodules, core.hooksPath all break hardcoded paths; git rev-parse handles them correctly |
| Test result parsing | Custom pytest output parser | pytest --tb=no -q + exit code | pytest output format changes; exit codes are stable API; parsing stdout is fragile |

**Key insight:** Security-sensitive paths (subprocess, hooks, git) have subtle edge cases that stdlib and git built-ins handle correctly. Custom implementations miss worktree edge cases, shell injection vectors, and platform differences.

## Common Pitfalls

### Pitfall 1: argparse Subparser Backward Compat Break

**What goes wrong:** Adding subparsers makes subcommand required by default in Python 3.7+, breaking `forge` (no args) invocations that worked before.

**Why it happens:** argparse.add_subparsers() defaults to required=True in Python 3.7+. Existing users run `forge` not `forge review`.

**How to avoid:** Set dest on add_subparsers, check if dest is None in main(), default to 'review' subcommand.

**Warning signs:** Users report "error: the following arguments are required: subcommand" after upgrade.

### Pitfall 2: Shell=True Injection via test.command

**What goes wrong:** If gate.yaml test.command is a string and passed to subprocess with shell=True, an attacker can inject shell metacharacters via test.command in a malicious gate.yaml.

**Why it happens:** Developer assumes "YAML is safe because yaml.safe_load prevents code execution" but forgets that subprocess.run(cmd, shell=True) executes the string as shell code.

**How to avoid:** 
1. test.command MUST be a list in gate.yaml schema, never a string
2. Always call subprocess.run with list argument, never shell=True
3. Validate test.command[0] is in known_runners allowlist
4. Reject any element containing shell metacharacters |;&$><`

**Warning signs:** Command execution errors with unexpected shell behavior, tests running commands not in gate.yaml.

### Pitfall 3: Worktree .git/hooks Hardcoded Path

**What goes wrong:** Hook installer writes to cwd/.git/hooks/pre-commit, but in a worktree .git is a FILE not a directory. Hook is never installed or git errors.

**Why it happens:** Developer tests in main worktree where .git is a directory. Worktrees are a git 2.5+ feature that changes .git to a file pointing to .git/worktrees/<name>/.

**How to avoid:** Always use `git rev-parse --git-path hooks` to resolve the correct hooks directory. This handles main repos, worktrees, submodules, and core.hooksPath.

**Warning signs:** `forge install-hooks` succeeds but pre-commit doesn't run in worktree; git errors about .git not being a directory.

### Pitfall 4: FAIL-OPEN on Config Error

**What goes wrong:** gate.yaml missing or parse error returns EXIT_CLI_ERROR=2, which the hook mistranslates as "test exit 2 -> allow+warn", so broken config passes commits.

**Why it happens:** Exit code 2 has two meanings: gate-check's own CLI errors (EXIT_CLI_ERROR) vs pytest's interrupt/internal error. Conflating them causes fail-open.

**How to avoid:** Separate error handling paths:
- Config/parse errors in gate-check: raise exceptions caught at top level -> return 1 (BLOCK)
- Test command exit 2-3: translate to 0+warn (expected pytest behavior)
- Never let gate-check return 2; use 1 for all BLOCK cases

**Warning signs:** Deleting gate.yaml allows commits to pass; typo in gate.yaml doesn't block commits.

### Pitfall 5: Baseline Bootstrap Deadlock

**What goes wrong:** First commit after adding gate-check has no baseline yet. If gate-check blocks on "no baseline", developers cannot commit to create the baseline. Chicken-and-egg.

**Why it happens:** Treating "no baseline" as an error condition blocks the initial commit that would record the baseline.

**How to avoid:** 
- No baseline file: allow commit + warn "no baseline; run forge gate-check --record-baseline"
- Baseline exists but test absent: treat as NEW (allow if passes, block if fails, add to baseline)
- Only block when baseline exists AND test moves from passing to failing

**Warning signs:** Cannot make first commit after installing hook; "baseline missing" error prevents all commits.

### Pitfall 6: Hook PATH is Minimal

**What goes wrong:** Hook writes `exec forge gate-check` but git runs hooks in minimal shell where virtual env is not activated. Command fails with "forge: command not found" (exit 127).

**Why it happens:** Pre-commit hooks run in git's minimal environment, not the user's interactive shell with activated venv and custom PATH.

**How to avoid:** 
- Resolve forge absolute path at install time: `shutil.which('forge')` or `sys.executable + ' -m forge'`
- Embed absolute path in hook: `exec /usr/bin/python3 -m forge gate-check` or `exec /home/user/.venv/bin/forge gate-check`
- Never rely on PATH in hooks

**Warning signs:** Hook works in interactive shell but fails in git commit; error "command not found" in git output.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Marker-based commit gate (PreToolUse hook checks "# post-review-c3" in command string) | Test-based commit gate (real pre-commit hook runs tests, blocks on NEW failures) | v2.1 Phase 1 (2026-05) | Closes terminal/IDE bypass; gates on actual test results not self-claim |
| Single forge command with flags | Subcommands (forge review / gate-check / install-hooks) | v2.1 Phase 1 | Better UX, separates concerns, backward compat via default subcommand |
| subprocess with shell=True for flexibility | subprocess.run with list args, shell=False always | v2.0 (enforced in runner.py) | Prevents shell injection, safer by default |
| Hardcoded .git/hooks path | git rev-parse --git-path hooks | v2.1 Phase 1 | Handles worktrees, submodules, core.hooksPath correctly |

**Deprecated/outdated:**

- **shell=True in subprocess:** Never needed; env vars can be merged via env= parameter, no shell prefix required. Using shell=True opens injection vector.
- **Hardcoded .git/hooks:** Breaks in worktrees (git 2.5+, 2015). Use git rev-parse --git-path hooks.
- **Bare `forge` in hooks:** Hooks run off-PATH; venv not activated. Embed absolute path at install time.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | pytest exit codes follow standard convention (0=pass, 1=fail, 2=interrupted, 3=internal, 4=usage, 5=no tests) [ASSUMED] | Exit code translation | Wrong translation blocks/allows incorrectly; verify pytest docs |
| A2 | shutil.which('forge') resolves to correct executable when forge installed via pip [ASSUMED] | Hook installer | Hook fails with "command not found"; verify in venv vs system install |
| A3 | git rev-parse --git-path hooks works in git 2.5+ (worktree support) [ASSUMED] | Hook installer | Fails in older git; check minimum git version requirement |
| A4 | fnmatch glob patterns match pytest's convention for file filtering [ASSUMED] | source_patterns filtering | Pattern mismatch causes tests to skip/run incorrectly; verify fnmatch semantics |

## Open Questions

1. **yamllint in pyproject.toml dev deps or inline pip install?**
   - What we know: Phase 0 used inline `pip install yamllint` in task
   - What's unclear: Should Phase 1 add it to pyproject.toml [tool.project.optional-dependencies] dev section?
   - Recommendation: Add to dev deps for consistency (already have pytest there). One-line change.

2. **Should gate-check support --record-baseline flag or is that a separate future feature?**
   - What we know: SPEC mentions "run forge gate-check --record-baseline" in bootstrap warn message
   - What's unclear: Is --record-baseline in Phase 1 scope or deferred?
   - Recommendation: Defer to separate task. Phase 1 gate-check reads baseline only. Recording is manual (user runs full suite, writes JSON).

3. **Does CI detection need ALL platform vars or just one?**
   - What we know: SPEC v3.2 says FORGE_MODE=ci OR CI OR GITHUB_ACTIONS OR GITLAB_CI OR JENKINS_URL OR BUILD_URL
   - What's unclear: Is it "any one set" or "all must be set"?
   - Recommendation: ANY one set (OR logic). CI platforms don't all set the same vars. Code: `if any([env.get("CI"), env.get("GITHUB_ACTIONS"), ...])`.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.3 (verified 2026-05-25) |
| Config file | none -- PYTHONPATH=src via gate.yaml test.env [VERIFIED: .forge/gate.yaml line 13] |
| Quick run command | `PYTHONPATH=src python3 -m pytest tests/test_gate_check.py tests/test_install_hooks.py tests/test_cli_subparsers.py -x` |
| Full suite command | `PYTHONPATH=src python3 -m pytest tests/ -q` |

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| EC-2 | Parse gate.yaml, run tests, translate exit codes | unit | `pytest tests/test_gate_check.py::test_parse_gate_yaml -x` | Wave 0 |
| EC-2 | FAIL-OPEN guard: config error -> BLOCK | unit | `pytest tests/test_gate_check.py::test_fail_open_guard -x` | Wave 0 |
| EC-3 | install-hooks writes pre-commit with absolute path | unit | `pytest tests/test_install_hooks.py::test_absolute_forge_path -x` | Wave 0 |
| EC-3 | install-hooks chains existing hook | unit | `pytest tests/test_install_hooks.py::test_chain_existing_hook -x` | Wave 0 |
| EC-4 | CI detection via FORGE_MODE=ci | unit | `pytest tests/test_mode_resolver.py::test_forge_mode_ci -x` | (extend existing) |
| EC-4 | CI detection via platform vars | unit | `pytest tests/test_mode_resolver.py::test_github_actions_ci -x` | Wave 0 |
| EC-5 | Baseline delta: NEW failure -> BLOCK | unit | `pytest tests/test_gate_check.py::test_baseline_new_failure -x` | Wave 0 |
| EC-5 | Baseline missing -> allow+warn | unit | `pytest tests/test_gate_check.py::test_baseline_bootstrap -x` | Wave 0 |
| EC-6 | Subparser backward compat: bare forge -> review | unit | `pytest tests/test_cli_subparsers.py::test_no_subcommand_defaults_review -x` | Wave 0 |
| EC-7 | Bug-inject: break exit translation -> test FAILS | smoke | Manual: edit gate_check.py exit translation, run suite, verify FAIL | manual-only (meta-test) |
| EC-8 | Real hook: red tree -> BLOCKS commit | smoke | Manual: `forge install-hooks`, edit file, git add, git commit (expect block) | manual-only (git integration) |
| EC-10 | R4 docs filled | manual | Human review of CLAUDE.md:287-288 | manual-only (prose quality) |

### Sampling Rate

- **Per task commit:** `PYTHONPATH=src python3 -m pytest tests/test_gate_check.py tests/test_install_hooks.py tests/test_cli_subparsers.py -x`
- **Per wave merge:** `PYTHONPATH=src python3 -m pytest tests/ -q`
- **Phase gate:** Full suite green + manual EC-7 (bug-inject) + manual EC-8 (real hook) before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/test_gate_check.py` -- covers EC-2 (parse, translate, FAIL-OPEN), EC-5 (baseline delta, bootstrap)
- [ ] `tests/test_install_hooks.py` -- covers EC-3 (absolute path, chain existing hook, core.hooksPath abort)
- [ ] `tests/test_cli_subparsers.py` -- covers EC-6 (backward compat default subcommand)
- [ ] `tests/test_mode_resolver.py` -- extend existing file for EC-4 (platform vars: GITHUB_ACTIONS, GITLAB_CI, etc.)

No framework install needed -- pytest already present in pyproject.toml dev deps.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|------------------|
| V2 Authentication | no | N/A (local dev tool, no auth) |
| V3 Session Management | no | N/A (stateless CLI) |
| V4 Access Control | no | N/A (inherits git/filesystem perms) |
| V5 Input Validation | yes | Command allowlist (known_runners), metacharacter rejection, YAML schema validation |
| V6 Cryptography | no | N/A (no crypto operations) |

### Known Threat Patterns for Python subprocess + git hooks

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Shell injection via test.command | Tampering | List arguments + shell=False + command allowlist + metacharacter rejection [VERIFIED: runner.py pattern] |
| YAML code execution | Tampering | yaml.safe_load only (never yaml.load) [VERIFIED: registry.py line 60] |
| Hook overwrite (destroy user's hook) | Denial of Service | Backup existing hook to .forge-backup before install [PLANNED: install_hooks.py] |
| TOCTOU on hook install | Tampering | Not applicable (single-user local tool, no concurrent hook installers) |
| Exit code confusion (FAIL-OPEN) | Security Feature Bypass | Isolate gate-check errors from test exit codes, dedicated BLOCK path [SPEC v3.2 round-3 finding] |

## Sources

### Primary (HIGH confidence)

- [forge codebase verified HEAD c0b2fd0] - cli.py, runner.py, mode_resolver.py, registry.py, exit_codes.py, baseline.py patterns
- [.forge/gate.yaml Phase 0 output] - Schema and config structure
- [.forge/test_baseline.json Phase 0 output] - Baseline schema (521 tests, known_failures array, test_results dict)
- [SPEC v3.2] - Exit code translation table, FAIL-OPEN guard, all R1 design decisions
- [Python 3.12 stdlib docs] - argparse, subprocess, shutil, fnmatch, pathlib
- [pytest 9.0.3 docs] - Exit codes, -q/-x flags [verified npm view pytest version 2026-05-25]

### Secondary (MEDIUM confidence)

- [Python argparse docs](https://docs.python.org/3/library/argparse.html) - Subparsers API
- [Semgrep Python command injection](https://semgrep.dev/docs/cheat-sheets/python-command-injection) - subprocess security patterns
- [Git hooks documentation](https://git-scm.com/docs/githooks) - Hook behavior, pre-commit semantics
- [Git worktree guide 2026](https://devtoolbox.dedyn.io/blog/git-worktrees-complete-guide) - git rev-parse --git-path hooks pattern
- [pre-commit framework](https://pre-commit.com/) - Hook chaining patterns

### Tertiary (LOW confidence)

None -- all core research verified against forge codebase or official docs.

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH - All dependencies already in forge or stdlib, versions verified
- Architecture: HIGH - Follows existing forge patterns (runner.py subprocess, registry.py YAML, mode_resolver.py pure functions)
- Pitfalls: HIGH - SPEC v3.2 round-3 findings documented (FAIL-OPEN, worktree paths, absolute forge path)
- CI detection: MEDIUM - Platform env vars list from SPEC, not verified against all CI providers
- R4 docs content: LOW - Sequencing discipline (LIVE vs PLANNED) is design guidance, actual prose quality is subjective

**Research date:** 2026-05-25
**Valid until:** 2026-06-25 (30 days -- stable domain: Python stdlib, git, pytest)
