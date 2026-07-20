# Phase 30: Switch-On + Dogfood - Research

**Researched:** 2026-06-26
**Domain:** pre-commit hook wiring, install-hooks deployment, dogfood verification
**Confidence:** HIGH

## Summary

Phase 30 closes the gap between "forge is built" and "forge is on." Three of
five ADOPT requirements are already verified (ADOPT-01 resolve-outlet, ADOPT-02
real CN review, ADOPT-04 fail-closed). The remaining work is ADOPT-03
(install-hooks in target repos) and ADOPT-05 (dogfood: forge gates its own
commits).

The phase is primarily config/wiring -- no new engine code. The two code
modifications are both in `install_hooks.py`: (1) merge the planning-leak guard
into `generate_hook_content()` so the generated hook blocks `.planning/` and
`CLAUDE.md` staging, and (2) add a `code-forge review` invocation to the
generated hook so every commit goes through the full LLM review pipeline (D-30-04).
A critical design constraint: `code-forge review` enforces a worktree check
(cli.py:1271-1296) that refuses to run in the main tree. The hook must set
`FORGE_SKIP_WORKTREE_CHECK=1` when invoking review, since the pre-commit hook
runs wherever `git commit` runs (including worktrees, which are fine, but also
potentially the main tree for non-forge repos). Alternatively, the worktree
check can be documented as the user's responsibility -- they commit from
worktrees per convention.

**Primary recommendation:** Implement in 3 plans: (1) extend
`generate_hook_content()` with planning-leak guard + review call, (2) survey
and deploy `install-hooks` to daily repos, (3) dogfood bug-inject proof +
regression test in a dedicated worktree.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-30-01:** install-hooks targets ALL daily projects under ~/code/, not
  just forge itself. Implementation order: (1) survey ~/code/ to catalog each
  repo's test runner, language, existing pre-commit hooks, and gate-check
  compatibility; (2) update forge's configuration documentation with per-repo
  install guidance; (3) roll out install-hooks repo by repo. Repos without
  pytest or a compatible test runner may need gate.yaml test_command overrides.
- **D-30-02:** Dogfood uses two-layer verification: (a) manual bug-inject
  proof first (inject `assert False` into a test, attempt commit, observe
  BLOCK, revert, observe PASS -- real terminal output as evidence); (b) then
  write a repeatable regression test (test_dogfood.py) that automates the
  inject/commit/check cycle to prevent regression.
- **D-30-03:** Dogfood runs in a dedicated worktree (not v26-adoption) to
  avoid polluting the development branch. Create with
  `git worktree add .worktrees/dogfood -b dogfood-test`.
- **D-30-04:** Pre-commit hook runs BOTH gate-check (deterministic test
  baseline delta + presubmit linters) AND full LLM review (3-pass via CN
  backend). Every commit goes through the complete pipeline. This means each
  commit takes 60-120s and requires the API key in the environment.
- **D-30-05:** The existing planning-leak pre-commit guard (.planning/ and
  CLAUDE.md staging block) is merged INTO forge's generate_hook_content
  output. Only one pre-commit hook file is maintained. This requires modifying
  install_hooks.py to include the planning-leak check logic.
- **D-30-06:** The generated hook must detect whether the current directory is
  under .git jurisdiction (including subdirectories). Non-git directories
  silently skip -- no error, no output. This prevents hook failures when
  committing in non-git contexts.

### Claude's Discretion
- Hook execution order within generate_hook_content
- Test structure for test_dogfood.py
- Whether to use FORGE_SKIP_WORKTREE_CHECK=1 or document worktree-only commits
- Per-repo gate.yaml template content

### Deferred Ideas (OUT OF SCOPE)
- Auto-detect test runner from repo structure (beyond tools.yaml language
  detection) -- belongs in a future enhancement phase
- Per-repo gate.yaml templates for common project types (Python/Go/Rust/C) --
  documentation improvement, not Phase 30 scope
- Resolve gate.yaml from `git rev-parse --git-common-dir` so worktrees
  auto-find main .code-forge without symlink -- logic change, deferred to
  Phase 31+ (noted in v2.6-ADOPTION-ROADMAP.md)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ADOPT-01 | resolve-outlet names a real backend | VERIFIED this session -- no research needed |
| ADOPT-02 | One real review returns CN-API findings | VERIFIED this session -- no research needed |
| ADOPT-03 | Pre-commit hook in >=1 target repo blocks a commit introducing a new test failure | Repo survey completed (Section: Repo Survey); generate_hook_content() and run_install_hooks() documented; gate-check exit codes mapped |
| ADOPT-04 | With no backend configured, code-forge review fails closed | VERIFIED this session -- no research needed |
| ADOPT-05 | Forge dogfoods itself: injected new-failure blocked by forge's own gate | Dogfood test patterns documented; worktree-based verification strategy; bug-inject + regression test design |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Planning-leak guard | Git hook (shell) | -- | Must run at git commit time, before any code enters history |
| Gate-check (R1 test gate) | Git hook -> subprocess | API / Backend | Hook invokes `code-forge gate-check` which runs pytest |
| LLM review (3-pass) | Git hook -> subprocess | CN API backend | Hook invokes `code-forge review` which calls DeepSeek API |
| Hook generation | Python (install_hooks.py) | -- | Template-based string assembly, written to .git/hooks/ |
| Hook deployment | CLI (code-forge install-hooks) | -- | Resolves hooks dir, backs up existing, writes + chmod |
| Dogfood verification | Test (pytest) | Git worktree | Automated inject/commit/check cycle in isolated worktree |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| code-forge | current (editable install) | Hook generation, gate-check, review | The project itself [VERIFIED: source code] |
| pytest | current | Test runner for dogfood regression test | Already used throughout forge test suite [VERIFIED: pyproject.toml] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| subprocess | stdlib | Run git commands from hooks | Hook invokes code-forge via subprocess [VERIFIED: install_hooks.py] |

No new external packages are installed in this phase. All work uses existing
forge infrastructure and stdlib.

## Architecture Patterns

### System Architecture Diagram

```
git commit (user)
    |
    v
.git/hooks/pre-commit (generated by forge)
    |
    +-- [1] planning-leak guard (NEW: D-30-05)
    |       grep staged paths for .planning/ and CLAUDE.md
    |       BLOCK if found
    |
    +-- [2] non-code carve-out (EXISTING)
    |       skip all checks for docs/config/chore commits
    |
    +-- [3] attestation check (EXISTING)
    |       code-forge verify --quiet
    |
    +-- [4] D-12 non-ASCII + AI-vocab (EXISTING)
    |       grep staged diff for typographic chars / AI words
    |
    +-- [5] presubmit linters (EXISTING)
    |       user-configured linters from gate.yaml
    |
    +-- [6] chain call (EXISTING, if backup hook exists)
    |       run the backed-up pre-commit hook
    |
    +-- [7] LLM review (NEW: D-30-04)
    |       code-forge review --baseline HEAD --head INDEX
    |       3-pass CN backend review (60-120s)
    |       BLOCK if verdict != PASS
    |
    +-- [8] gate-check (EXISTING)
            code-forge gate-check
            R1 test baseline delta
            BLOCK if NEW test failure
```

### Recommended Project Structure
```
src/code_forge/
    install_hooks.py    # MODIFIED: add planning-leak + review blocks
    gate_check.py       # UNCHANGED: R1 test gate
    cli.py              # UNCHANGED: review command entry point
    exit_codes.py       # UNCHANGED: EXIT_PASS/FAIL/etc.
tests/
    test_install_hooks.py  # EXTENDED: tests for new hook blocks
    test_dogfood.py        # NEW: dogfood regression test
```

### Pattern 1: Hook Content Generation (Template Assembly)

**What:** `generate_hook_content()` at install_hooks.py:315 assembles a shell
script from blocks. Each block is a string fragment with proper shell syntax.

**When to use:** Adding new checks to the generated pre-commit hook.

**Example (current structure from source):**
```python
# Source: install_hooks.py:315-397
def generate_hook_content(
    forge_invocation: str,
    chain_path: Path | None,
    presubmit_entries: list[dict] | None = None,
    non_ascii_mode: str = "ai-smell",
) -> str:
    # Hook execution order:
    #   1. carveout block  -- non-code commits exit 0 here
    #   2. attestation     -- code-forge verify --quiet
    #   3. built-in staged-diff check -- non-ASCII + AI-vocab
    #   4. presubmit runner -- user-configured linters
    #   5. chain call      -- existing hook (if chaining)
    #   6. exec gate-check -- R1 test gate
    ...
```

The planning-leak guard (D-30-05) goes BEFORE the carveout (position 0 in the
execution order) because `.planning/` staging must be blocked even for non-code
commits. The review call (D-30-04) goes after presubmit linters but before
gate-check.

### Pattern 2: Planning-Leak Guard (Shell Fragment)

**What:** The existing `.git/hooks/pre-commit` at forge's repo blocks staging
of `.planning/` and `CLAUDE.md`.

**Current implementation (from .git/hooks/pre-commit):**
```sh
leak=$(git diff --cached --name-only | grep -E '^\.planning/|(^|/)CLAUDE\.md$')
if [ -n "$leak" ]; then
    echo "pre-commit BLOCKED: staged paths must never enter history:" >&2
    printf '%s\n' "$leak" | sed 's/^/  /' >&2
    exit 1
fi
```

This must be translated into a Python string builder (like `_build_d12_precommit_block`)
and emitted as the FIRST block in `generate_hook_content()`, before the carveout.

### Pattern 3: Review from Hook (D-30-04)

**What:** Invoke `code-forge review` from within the generated pre-commit hook
to run the full 3-pass LLM review on every commit.

**Critical constraint:** `code-forge review` enforces a worktree check
(cli.py:1271-1296) that refuses to run in the main tree. The review invocation
must set `FORGE_SKIP_WORKTREE_CHECK=1` to bypass this, since:
- The hook runs wherever `git commit` is invoked
- For forge's own repo, commits always happen in worktrees (convention)
- For other repos (ashare-lab, surflare-watchdog), commits may happen in the main tree

**Review flags for pre-commit context:**
- `--baseline HEAD --head INDEX` reviews the staged diff (what git commit will record)
- `--max-total-rounds 2` caps the review to avoid excessive API calls
- The review needs the API key exported in the environment

**Design (recommended):**
```sh
# LLM review (full 3-pass via CN backend)
if command -v code-forge >/dev/null 2>&1; then
    FORGE_SKIP_WORKTREE_CHECK=1 code-forge review \
        --baseline HEAD --head INDEX \
        --max-total-rounds 2 \
        --quiet || {
        _RC=$?
        if [ "$_RC" -eq 2 ]; then
            # CLI error (no backend, missing config) -- warn, don't block
            echo "code-forge: review skipped (no backend configured)" >&2
        else
            echo "code-forge: review FAILED (exit $_RC)" >&2
            exit 1
        fi
    }
fi
```

**Exit code semantics for the hook:**
- Exit 0 (PASS): review clean, continue to gate-check
- Exit 1 (FAIL): review found issues, BLOCK the commit
- Exit 2 (CLI_ERROR): no backend / config error -- warn but allow (graceful degradation)
- Exit 5 (DELEGATED): inline outlet, not a real review -- warn
- Exit 6 (TIMEOUT): backend timeout -- BLOCK (fail-closed)

### Pattern 4: Test Isolation for Dogfood Test

**What:** Forge tests use `GIT_CEILING_DIRECTORIES` to prevent test git
operations from reaching the real repo. The conftest.py:20-30 sets this
at session scope.

**For test_dogfood.py:** The test must create a scratch git repo in tmp_path,
install hooks there, stage a file with a deliberate test failure, attempt
`git commit`, and verify the hook blocks it. This requires:
1. A real `git init` in tmp_path (isolated by GIT_CEILING_DIRECTORIES)
2. A `.code-forge/gate.yaml` with a test command
3. A `.code-forge/test_baseline.json` with a known-good baseline
4. A staged file that introduces a NEW test failure
5. A `git commit` attempt that triggers the hook

### Anti-Patterns to Avoid
- **Replacing the entire pre-commit hook instead of extending:** The planning-leak
  guard must be MERGED into `generate_hook_content()`, not maintained as a
  separate hook. Two hooks on the same path = one overwrites the other.
- **Hardcoding forge-specific paths in the hook:** The planning-leak guard
  references `.planning/` and `CLAUDE.md` which are forge-repo-specific. For
  non-forge repos, this block should be optional or configurable (via a
  parameter to `generate_hook_content()`).
- **Running review with default baseline (HEAD..WORKING):** In a pre-commit
  context, the staged diff is HEAD..INDEX, not HEAD..WORKING. Using the wrong
  baseline reviews unstaged changes, not what will be committed.
- **Blocking on missing API key:** The review step must degrade gracefully
  when no backend is configured (exit 2 = warn, not block). gate-check
  already handles no-gate.yaml gracefully (FileNotFoundError -> EXIT_FAIL).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Hook generation | Manual .git/hooks/pre-commit editing | `generate_hook_content()` | Idempotent, backup-aware, handles worktrees |
| Test baseline delta | Custom test output parser | `compute_baseline_delta()` | Already parses pytest -q output, knows about grandfathered failures |
| Forge path resolution | Hardcoded `/usr/bin/code-forge` | `resolve_forge_path()` | Handles shutil.which, sys.executable fallback, liveness check |
| Hook deployment | cp + chmod | `run_install_hooks()` | Backup, chain, idempotency, core.hooksPath abort |

**Key insight:** The entire hook infrastructure already exists and is well-tested
(56 tests in test_install_hooks.py). Phase 30 extends it, does not rebuild it.

## Repo Survey (D-30-01)

### Daily-Use Repos (by commit activity since 2026-06-01)

| Repo | Commits | Language | Test Runner | Existing Hook | Gate-Check Ready |
|------|---------|----------|-------------|---------------|-----------------|
| forge | 326 | Python | pytest | YES (planning-leak) | YES (gate.yaml + baseline exist) |
| surflare-watchdog | 305 | Shell | bash smoke tests | no | NO (needs gate.yaml + test_command override) |
| ashare-lab | 153 | Python | pytest | no | Partial (needs gate.yaml + baseline) |
| mhou_workspace | 117 | Mixed (scripts) | none | no | NO (no tests, config repo) |
| code-review-graph | 2 | Python | pytest | no | Partial (needs gate.yaml + baseline) |

### Other Repos (low or zero recent activity)

| Repo | Language | Test Runner | Notes |
|------|----------|-------------|-------|
| sashiko | Rust | cargo test | 0 recent commits |
| hermes-agent | Python/Node | pytest | 0 recent commits, upstream project |
| linux-net-next | C | kselftests/make | kernel repo, 0 user commits |
| pynfs | Python | custom | upstream project, 0 user commits |

### Gate-Check Compatibility Assessment

**Fully compatible (pytest + immediate install):**
- `forge` -- already has gate.yaml, test_baseline.json, and the planning-leak hook
- `ashare-lab` -- has pyproject.toml + tests/ ; needs `code-forge init` + baseline
- `code-review-graph` -- has pyproject.toml + tests/ ; needs `code-forge init` + baseline

**Needs test_command override:**
- `surflare-watchdog` -- shell-only, test runner is `bash tests/run_smoke_tests.sh`
  gate.yaml would need `test: { command: [bash, tests/run_smoke_tests.sh] }`

**Not suitable for gate-check (no tests):**
- `mhou_workspace` -- config/dotfiles repo, no test suite
- Various upstream/kernel repos -- use their own CI, not forge

### Recommendation for D-30-01

Start with forge (already configured), then ashare-lab (high activity, pytest),
then surflare-watchdog (high activity, needs test_command override). Skip
mhou_workspace and low-activity repos. The ADOPT-03 requirement says ">=1 target
repo" -- forge alone satisfies it; the others are stretch goals.

## Common Pitfalls

### Pitfall 1: Review Worktree Check Blocks Hook

**What goes wrong:** `code-forge review` refuses to run in the main tree
(cli.py:1291-1296 raises CliError). If the hook calls review without
`FORGE_SKIP_WORKTREE_CHECK=1`, every commit attempt in a non-worktree
context fails with "must run inside a linked git worktree."

**Why it happens:** The worktree check was added to enforce the convention
that reviews only happen in worktrees. But a pre-commit hook runs wherever
`git commit` runs, which includes the main tree in non-forge repos.

**How to avoid:** Set `FORGE_SKIP_WORKTREE_CHECK=1` in the environment when
invoking review from the hook. Document that the worktree convention is a
forge-repo policy, not a universal constraint.

**Warning signs:** Hook exits with exit code 2 and message "must run inside
a linked git worktree."

### Pitfall 2: Planning-Leak Guard in Non-Forge Repos

**What goes wrong:** The planning-leak guard blocks `.planning/` and
`CLAUDE.md` staging. In repos that legitimately track `.planning/` or
`CLAUDE.md`, this would block valid commits.

**Why it happens:** The guard is forge-specific (forge's `.planning/` is
gitignored but can be accidentally staged via `git add -f`).

**How to avoid:** Make the planning-leak guard configurable -- add a
`planning_leak_guard: true` parameter to `generate_hook_content()` that
defaults to False. Only forge's own install enables it (or any repo that
opts in via gate.yaml).

**Warning signs:** Non-forge repos that track CLAUDE.md files get blocked.

### Pitfall 3: Review API Key Missing at Commit Time

**What goes wrong:** The review step in the hook requires `DEEPSEEK_API_KEY`
(or the appropriate backend key) in the environment. If the user opens a new
terminal without exporting the key, every commit blocks.

**Why it happens:** API keys are not persisted in shell config (security).
They must be exported per-session via `pass show`.

**How to avoid:** The hook must handle exit code 2 (CLI_ERROR, which includes
"no backend configured") as a warning, not a block. This allows commits to
proceed when the key is missing, with a warning message. The gate-check
(deterministic tests) still runs regardless.

**Warning signs:** Every commit fails with "error: No review backend configured."

### Pitfall 4: Hook Overwrites Planning-Leak Guard

**What goes wrong:** `run_install_hooks()` detects the existing forge-repo
pre-commit hook as "code-forge-generated" (install_hooks.py:611-614) and
overwrites it. The planning-leak guard is lost because it is NOT in the
generated hook content yet.

**Why it happens:** The idempotent reinstall path (install_hooks.py:618-620)
skips backup and overwrites. The current `.git/hooks/pre-commit` in forge
is a hand-written planning-leak guard, not a forge-generated hook. But if
someone runs `install-hooks` now, the detection logic checks for
"code-forge gate-check" or "installed by code-forge install-hooks" in the
first 3 lines -- the planning-leak guard has neither, so it would be
BACKED UP and CHAINED. Safe, but not ideal: the guard runs twice (once in
the chain, once in the generated hook after D-30-05).

**How to avoid:** Merge the planning-leak guard into `generate_hook_content()`
(D-30-05) BEFORE running `install-hooks` on forge. This way the generated
hook already contains the guard, and the hand-written one is safely backed up.

### Pitfall 5: Dogfood Test Creates Real Commits

**What goes wrong:** The dogfood regression test (test_dogfood.py) must
attempt `git commit` to trigger the hook. If test isolation fails, the
commit lands on a real branch.

**Why it happens:** GIT_CEILING_DIRECTORIES prevents reaching the real repo,
but the test must `git init` its own scratch repo. If the test accidentally
runs in the forge repo (not in tmp_path), commits pollute the real repo.

**How to avoid:** Use `tmp_path` for all git operations. Set
`GIT_CEILING_DIRECTORIES` to `tmp_path.parent`. The conftest.py session-level
fixture already sets this. Additionally, the test should use
`git commit --allow-empty` or a minimal staged file, not real forge code.

## Code Examples

### Example 1: Planning-Leak Guard Block Builder

```python
# Pattern: follows _build_d12_precommit_block() style
def _build_planning_leak_guard() -> str:
    """Build the planning-leak guard block.

    Blocks staging of .planning/ and CLAUDE.md paths.
    Placed BEFORE the non-code carveout (these paths must never
    enter history regardless of commit type).
    """
    return (
        "# planning-leak guard: block .planning/ and CLAUDE.md staging\n"
        "leak=$(git diff --cached --name-only | "
        "grep -E '^\\.planning/|(^|/)CLAUDE\\.md$')\n"
        "if [ -n \"$leak\" ]; then\n"
        '    echo "code-forge: BLOCKED: staged paths must never '
        'enter history:" >&2\n'
        "    printf '%s\\n' \"$leak\" | sed 's/^/  /' >&2\n"
        "    exit 1\n"
        "fi\n"
        "\n"
    )
```

### Example 2: Review Invocation Block Builder

```python
# Pattern: follows _build_presubmit_block() style
def _build_review_block(forge_exe_path: str) -> str:
    """Build the LLM review block for the pre-commit hook.

    Calls code-forge review on staged changes (HEAD..INDEX).
    Gracefully degrades when no backend is configured (exit 2 = warn).
    """
    # Extract base path (forge_invocation is "path gate-check",
    # we need just "path")
    base_path = forge_exe_path.rsplit(" gate-check", 1)[0]
    return (
        "# LLM review: full 3-pass via CN backend\n"
        "FORGE_SKIP_WORKTREE_CHECK=1 %s review "
        "--baseline HEAD --head INDEX "
        "--max-total-rounds 2 --quiet || {\n"
        "    _RC=$?\n"
        '    if [ "$_RC" -eq 2 ]; then\n'
        '        echo "code-forge: review skipped '
        '(no backend configured)" >&2\n'
        "    else\n"
        '        echo "code-forge: review FAILED '
        '(exit $_RC)" >&2\n'
        "        exit 1\n"
        "    fi\n"
        "}\n"
        "\n"
    ) % base_path
```

### Example 3: Dogfood Test Skeleton

```python
# Pattern: follows TestInstallHookIntegration style
class TestDogfood:
    """ADOPT-05: forge gates its own commits via the real pipeline."""

    def test_injected_failure_blocks_commit(self, tmp_path, monkeypatch):
        """A staged file with a new test failure is blocked by the hook."""
        monkeypatch.setenv(
            "GIT_CEILING_DIRECTORIES",
            str(tmp_path.parent),
            prepend=os.pathsep,
        )
        # 1. git init in tmp_path
        # 2. create minimal pyproject.toml + tests/test_foo.py (passing)
        # 3. create .code-forge/gate.yaml with test command
        # 4. create .code-forge/test_baseline.json (empty baseline)
        # 5. install hooks
        # 6. git add + commit (should pass -- all tests pass)
        # 7. modify test_foo.py to add a FAILING test
        # 8. git add + git commit (should BLOCK -- new failure)
        # 9. verify exit code is non-zero
        # 10. revert the failing test
        # 11. git add + git commit (should PASS)
        ...
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (current, in pyproject.toml) |
| Config file | pyproject.toml [tool.pytest.ini_options] |
| Quick run command | `pytest tests/test_install_hooks.py tests/test_dogfood.py -q` |
| Full suite command | `pytest -q` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ADOPT-03 | install-hooks creates hook that blocks new test failure | integration | `pytest tests/test_install_hooks.py -q -x` | Yes (extend) |
| ADOPT-05 | dogfood: injected failure blocked by forge's own gate | integration | `pytest tests/test_dogfood.py -q -x` | No (Wave 0) |
| D-30-04 | hook runs review + gate-check | unit | `pytest tests/test_install_hooks.py::TestReviewBlock -q -x` | No (Wave 0) |
| D-30-05 | planning-leak guard in generated hook | unit | `pytest tests/test_install_hooks.py::TestPlanningLeakGuard -q -x` | No (Wave 0) |
| D-30-06 | non-git dirs silently skip | unit | `pytest tests/test_install_hooks.py::TestNonGitSkip -q -x` | No (Wave 0) |

### Sampling Rate
- **Per task commit:** `pytest tests/test_install_hooks.py tests/test_dogfood.py -q -x`
- **Per wave merge:** `pytest -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_dogfood.py` -- covers ADOPT-05 (new file)
- [ ] Tests for `_build_planning_leak_guard()` in test_install_hooks.py -- covers D-30-05
- [ ] Tests for `_build_review_block()` in test_install_hooks.py -- covers D-30-04

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | -- |
| V3 Session Management | no | -- |
| V4 Access Control | no | -- |
| V5 Input Validation | yes | Shell injection prevention via shlex.quote (existing pattern) |
| V6 Cryptography | no | -- |

### Known Threat Patterns for Hook Generation

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Shell injection via gate.yaml fields | Tampering | shlex.quote() on all user-supplied values (existing F3 constraint, install_hooks.py:268) |
| API key leakage in hook output | Information Disclosure | Keys never embedded in hook content; read from env at runtime |
| Planning file staging bypass | Information Disclosure | Planning-leak guard runs BEFORE carveout; cannot be bypassed by `# docs` marker |

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hand-written .git/hooks/pre-commit | generate_hook_content() template | Phase 18.1 / 19.1 | Idempotent, testable, configurable |
| Manual `code-forge trust` + review | install-hooks auto-fires on commit | Phase 30 (this phase) | Zero-friction enforcement |
| Planning-leak guard as separate script | Merged into generated hook (D-30-05) | Phase 30 (this phase) | Single hook file, no drift |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `FORGE_SKIP_WORKTREE_CHECK=1` is the correct env var to bypass the worktree check in review | Code Examples | Hook would fail with exit 2 in main tree; verify against cli.py:1271 |
| A2 | `--baseline HEAD --head INDEX` correctly reviews staged changes | Architecture Patterns | Review would see wrong diff; verify against baseline.py INDEX handling |
| A3 | Exit code 2 from review means CLI error (no backend) and should be non-blocking | Common Pitfalls | Hook might block commits when it should warn; verify against exit_codes.py |

All three assumptions are supported by direct source code reading (cited lines).
A1: cli.py:1271 `env.get("FORGE_SKIP_WORKTREE_CHECK") == "1"` [VERIFIED: source].
A2: baseline.py:16 `INDEX` is a valid ref, cli.py:257 documents INDEX as head ref [VERIFIED: source].
A3: exit_codes.py:15 `EXIT_CLI_ERROR = 2` [VERIFIED: source].

## Open Questions

1. **Planning-leak guard scope for non-forge repos**
   - What we know: The guard blocks `.planning/` and `CLAUDE.md` staging.
     This is forge-specific (both are gitignored in forge).
   - What's unclear: Should non-forge repos get this guard? Some repos
     may legitimately track CLAUDE.md.
   - Recommendation: Make the guard opt-in via a parameter to
     `generate_hook_content()`. Default to False. Forge's own install
     enables it. Other repos can opt in via gate.yaml.

2. **Review timeout in the hook**
   - What we know: DeepSeek 3-pass review takes 60-120s per the handoff doc.
     The hook has no timeout of its own (the review subprocess has internal
     timeouts via the circuit breaker at Phase 25.1).
   - What's unclear: Should the hook impose its own timeout on the review
     subprocess? A hung API call could block commits indefinitely.
   - Recommendation: Rely on the existing circuit breaker (EXIT_TIMEOUT=6).
     The hook treats exit 6 as a block. No additional timeout needed since
     the circuit breaker already handles this.

3. **Which repos to deploy hooks to first**
   - What we know: forge (326 commits), surflare-watchdog (305), ashare-lab (153),
     mhou_workspace (117) are the most active.
   - What's unclear: Which repos benefit most vs. which will have the most friction
     (API key requirement, 60-120s commit delay).
   - Recommendation: forge first (already configured, dogfood requirement),
     then ashare-lab (pytest, natural fit). surflare-watchdog needs test_command
     override. mhou_workspace has no tests -- skip.

## Sources

### Primary (HIGH confidence)
- install_hooks.py source -- generate_hook_content(), run_install_hooks(), all block builders
- gate_check.py source -- run_gate_check(), load_test_baseline(), compute_baseline_delta()
- cli.py source -- _run() worktree check, _build_baseline_specs(), INDEX handling
- .git/hooks/pre-commit -- current planning-leak guard (verbatim)
- exit_codes.py -- EXIT_PASS/FAIL/CLI_ERROR constants
- tests/test_install_hooks.py -- 56 tests, all patterns
- 30-CONTEXT.md -- locked decisions D-30-01 through D-30-06

### Secondary (MEDIUM confidence)
- v2.6-SESSION-HANDOFF.md -- verified state (items 1/2/4 done)
- v2.6-ADOPTION-ROADMAP.md -- phase scope and acceptance criteria
- ~/code/ directory survey -- repo languages, test runners, hook state

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - no new packages, all existing forge infrastructure
- Architecture: HIGH - extending well-understood generate_hook_content() pattern
- Pitfalls: HIGH - each pitfall traced to specific source code lines

**Research date:** 2026-06-26
**Valid until:** 2026-07-26 (stable -- no external dependency churn)
