# Phase 30: Switch-On + Dogfood - Context

**Gathered:** 2026-06-26
**Status:** Ready for planning

<domain>
## Phase Boundary

forge goes from "built" to "on": the CN backend is trusted and working,
install-hooks deploys gate-check + full LLM review to all daily repos, forge
dogfoods itself end-to-end, and the generated hooks are documented.

ADOPT-01 (resolve-outlet), ADOPT-02 (real CN review), ADOPT-04 (fail-closed)
are already verified this session. This phase closes ADOPT-03 (install-hooks)
and ADOPT-05 (dogfood).

</domain>

<decisions>
## Implementation Decisions

### Hook Target Repos
- **D-30-01:** install-hooks targets ALL daily projects under ~/code/, not
  just forge itself. Implementation order: (1) survey ~/code/ to catalog each
  repo's test runner, language, existing pre-commit hooks, and gate-check
  compatibility; (2) update forge's configuration documentation with per-repo
  install guidance; (3) roll out install-hooks repo by repo. Repos without
  pytest or a compatible test runner may need gate.yaml test_command overrides.

### Dogfood Verification Strategy
- **D-30-02:** Dogfood uses two-layer verification: (a) manual bug-inject
  proof first (inject `assert False` into a test, attempt commit, observe
  BLOCK, revert, observe PASS -- real terminal output as evidence); (b) then
  write a repeatable regression test (test_dogfood.py) that automates the
  inject/commit/check cycle to prevent regression.
- **D-30-03:** Dogfood runs in a dedicated worktree (not v26-adoption) to
  avoid polluting the development branch. Create with
  `git worktree add .worktrees/dogfood -b dogfood-test`.

### Hook Scope and Experience
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

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### forge core (hook generation + gate)
- `src/code_forge/install_hooks.py` -- generate_hook_content() (line 315),
  run_install_hooks() (line 515), _build_presubmit_block() (line 234)
- `src/code_forge/gate_check.py` -- run_gate_check() (line 814),
  load_test_baseline() (line 704), compute_baseline_delta() (line 736)
- `src/code_forge/trust.py` -- is_trusted() (line 99), record_trust() (line 110)
- `src/code_forge/outlet_resolver.py` -- resolve_outlet() (line 133)

### forge config (gitignored, local-only)
- `.code-forge/gate.yaml` -- backend config (deepseek default, mimo-pro alt)
- `.code-forge/test_baseline.json` -- R1 test baseline (grandfathered failures)
- `.code-forge/tools.yaml` -- detected language toolchain

### existing planning-leak guard (to merge)
- `.git/hooks/pre-commit` -- current planning-leak guard script
  (blocks staging of .planning/ and CLAUDE.md; recreate recipe in
  forge CLAUDE.md "Planning File Persistence" section)

### session handoff (verified state)
- `.planning/v2.6-SESSION-HANDOFF.md` -- items 1/2/4 verified, worktree
  setup, gate.yaml symlink resolution, HOW TO RUN section
- `.planning/v2.6-ADOPTION-ROADMAP.md` -- full Phase 1 scope/items/verify

### CN backend error context (Phase 31 feed-forward)
- CN provider error survey (2026-06-26 research agent): all 5 providers use
  429; DeepSeek+Kimi return Retry-After; Zhipu has 21 sub-codes with Chinese
  messages; MiniMax uses proprietary codes (1002/1039/1041). This informs
  Phase 31 but Phase 30 uses deepseek (reliable 3-pass, no 429 issues).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `generate_hook_content()` (install_hooks.py:315): already generates a
  complete pre-commit hook with gate-check + presubmit linters + non-code
  carve-out. Phase 30 extends this, not replaces.
- `_build_d12_precommit_block()` (install_hooks.py:202): built-in non-ASCII +
  AI-vocab check, already wired.
- `resolve_forge_path()` (install_hooks.py:113): finds the `code-forge`
  binary on PATH.
- `ensure_claude_worktree_hook()` (install_hooks.py:446): worktree-aware hook
  installation logic.

### Established Patterns
- Hook generation is template-based (string assembly in Python, written to
  .git/hooks/pre-commit with chmod 755).
- gate-check uses exit codes: 0=pass, 1=fail, 2=error, 6=timeout circuit
  breaker, 7=unreliable canary.
- Non-code carve-out: commits ending with `# docs`, `# config`, `# chore`,
  `# wip` skip presubmit linters.
- Trust is per-repo: gate.yaml hash stored in ~/.config/code-forge/trusted.json (XDG pattern);
  worktree symlinks resolve to main realpath.

### Integration Points
- `.git/hooks/pre-commit` is the single hook entry point. forge's install
  replaces whatever is there. The planning-leak guard must be merged IN,
  not run alongside.
- Worktrees share the main .git directory, so hooks installed in the main
  tree apply to all worktrees. But .code-forge/ (gitignored) must be
  symlinked per-worktree.
- `code-forge review` in the hook needs DEEPSEEK_API_KEY in the shell
  environment. The hook must fail gracefully if the key is absent (D-30-06
  silent skip for non-git, but for git without key = warn, not block).

</code_context>

<specifics>
## Specific Ideas

- Survey ~/code/ directory to catalog all repos: language, test runner,
  existing hooks, gate-check readiness. Output: a table in the updated docs.
- The planning-leak guard uses a simple `git diff --cached --name-only |
  grep` pattern. Merging it into generate_hook_content means adding a check
  block at the TOP of the generated hook (before gate-check runs).
- For repos without pytest (e.g. kernel C repos using make/kselftests),
  gate.yaml `test_command:` override should be documented but not
  required -- gate-check already handles "no test command" gracefully.

</specifics>

<deferred>
## Deferred Ideas

- Auto-detect test runner from repo structure (beyond tools.yaml language
  detection) -- belongs in a future enhancement phase
- Per-repo gate.yaml templates for common project types (Python/Go/Rust/C) --
  documentation improvement, not Phase 30 scope
- Resolve gate.yaml from `git rev-parse --git-common-dir` so worktrees
  auto-find main .code-forge without symlink -- logic change, deferred to
  Phase 31+ (noted in v2.6-ADOPTION-ROADMAP.md)

</deferred>

---

*Phase: 30-Switch-On + Dogfood*
*Context gathered: 2026-06-26*
