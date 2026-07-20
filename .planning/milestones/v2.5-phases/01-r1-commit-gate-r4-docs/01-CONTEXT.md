# Phase 1: R1 (commit gate) + R4 (docs) - Context

**Gathered:** 2026-05-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver a real test commit gate (forge gate-check CLI + .git/hooks/pre-commit +
forge install-hooks) and fill the gate-philosophy docs (CLAUDE.md empty sections).
This closes forge's marker-trusting blind spot: the pre-commit hook gates on
DIFF CONTENT and test results, not a self-claim marker.

</domain>

<decisions>
## Implementation Decisions

### CLI Architecture
- **D-01:** argparse subparsers -- forge gains three subcommands: `forge review`
  (existing pipeline, behavior unchanged), `forge gate-check` (new: parse
  gate.yaml, run tests, translate exit codes, baseline delta), `forge
  install-hooks` (new: write .git/hooks/pre-commit). cli.py currently has no
  subparsers; this requires restructuring the argparse setup. Existing `forge`
  (no subcommand) behavior must remain as `forge review` for backward compat.

### Source-File Filter
- **D-02:** gate.yaml gains a `source_patterns` field. gate-check reads
  `git diff --cached --name-only` and checks against source_patterns to decide
  if tests should run. For forge itself: `source_patterns: ["*.py"]`. This is
  separate from tools.yaml file_patterns (which control which linters run on
  which files). Example gate.yaml update:

  test:
    command: ["python3", "-m", "pytest", "tests/", "-q"]
    env:
      PYTHONPATH: "src"
    timeout_seconds: 120
    cwd: "."
    source_patterns: ["*.py"]

### R4 Docs -- Sequencing Discipline (CRITICAL)
- **D-03:** R4 docs must distinguish LIVE vs PLANNED at the time of writing.
  Phase 1 delivers R4, but R2 (mutation) and R3 (e2e) do not exist yet.
  Writing them as "already present" would be the exact overclaim forge's
  anti-hallucination thesis warns against -- and these sections are WHERE
  forge states that thesis. Self-contradiction.

  "What Forge Covers That Nobody Else Does":
  - LIVE (Phase 1): static passes (parser + 3-cycle convergence) + step-4
    smoke test + real test commit gate (R1, not marker self-claim) +
    anti-hallucination gates (3 gates).
  - PLANNED (v2.1 in-progress): mutation-gated review (R2), e2e coverage
    heuristic (R3). Mark explicitly as planned, not present.

  "What Forge Is Missing":
  - From the Review Dimensions Matrix "No" column: cross-repo impact,
    feedback learning, long-term maintainability, performance benchmarks.
  - Honest: static passes are one layer. Passes-count is not a quality
    guarantee. Verification grounding (run suite + mutation + e2e) is the
    thesis; until R2/R3 land, forge is partially implementing its own thesis.

  As R2 and R3 land in Phases 2-3, update R4 to promote items from PLANNED
  to LIVE. This is a living document, not a one-shot write.

### Phase 1+ Merge Discipline
- **D-04:** Phase 0 committed to main before host verification (acceptable
  for config-only). Phase 1+ (logic code) MUST follow: sub-session implements
  in worktree -> sub-session reports -> host ground-truth verification ->
  pass -> THEN merge to main. gate-check logic with a bug landing on main is
  high cost. The worktree stays until host accepts.

### SPEC-Locked Decisions (not re-discussed)

All of the following are locked by SPEC v3.2 (3 rounds, 5-model review).
Downstream agents MUST read the SPEC for full detail:

- Exit-code translation: test 0->allow, 1/4/5/timeout->BLOCK, 2-3->warn
- FAIL-OPEN guard: gate-check own config/parse errors -> dedicated BLOCK
  path, isolated from test exit codes (EXIT_CLI_ERROR=2 must not be
  mistranslated to ALLOW)
- install-hooks: git rev-parse --git-path hooks, absolute forge path,
  backup + chain existing hook, ABORT on core.hooksPath
- CI detection: FORGE_MODE=ci + CI/GITHUB_ACTIONS/GITLAB_CI/JENKINS_URL/
  BUILD_URL
- Baseline: blocks only NEW failures vs .forge/test_baseline.json; no
  baseline -> allow+warn; absent-but-FAILS -> BLOCK; absent-but-PASSES ->
  fold into baseline
- Pre-commit gates on DIFF CONTENT (not marker); PreToolUse hook KEPT
  (orthogonal, CC-only)
- FORGE_SKIP_TESTS=1 -> warn+allow (local); CI mode always runs regardless
- Command safety: test.command[0] must be known runner, no shell metachar

### Claude's Discretion
- Internal module layout for gate_check.py / install_hooks.py
- Test file organization for new gate-check tests
- Specific error message wording
- Whether to add yamllint to pyproject.toml dev deps (currently pip installed
  inline in Phase 0)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Spec and Design
- `.planning/milestones/v2.1-dynamic-gate/SPEC.md` -- full v3.2 spec with
  all R1-R5 requirements, exit-code translation table, FAIL-OPEN guard,
  install-hooks design, CI detection, baseline semantics
- `.planning/milestones/v2.1-dynamic-gate/ROADMAP.md` -- Phase 1 exit
  criteria (10 items), Phase 0 context (what was delivered)

### Existing Code (must read before modifying)
- `src/forge/cli.py` -- current CLI entry point (no subparsers, needs
  restructure for D-01)
- `src/forge/exit_codes.py` -- EXIT_PASS/FAIL/CLI_ERROR/BUSY/ESCALATED
  constants (gate-check reuses these)
- `src/forge/mode_resolver.py` -- FORGE_MODE resolution logic (gate-check
  CI detection builds on this)
- `src/forge/registry.py` -- YAML loading pattern (yaml.safe_load, gate.yaml
  follows same pattern)
- `.forge/gate.yaml` -- Phase 0 output, gate-check config to parse
- `.forge/test_baseline.json` -- Phase 0 output, baseline schema for delta
- `.gitignore` -- current .forge/ exception rules

### CLAUDE.md (R4 target)
- `CLAUDE.md` lines 287-288 -- empty "What Forge Covers That Nobody Else
  Does" + "What Forge Is Missing" headers. R4 fills these per D-03.
- `CLAUDE.md` Review Dimensions Matrix -- source for "What Forge Is Missing"
  (the "No" column)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `exit_codes.py`: EXIT_PASS/FAIL/CLI_ERROR constants -- gate-check reuses
  the same exit code scheme
- `mode_resolver.py`: resolve_mode() already handles FORGE_MODE + TTY
  detection -- gate-check CI detection extends this
- `registry.py`: yaml.safe_load() pattern for .forge/ config files --
  gate.yaml loading follows same shape
- `baseline.py`: existing baseline infrastructure (finding-level) -- test
  baseline is a SEPARATE domain but can follow similar serialize/deserialize
  patterns

### Established Patterns
- Pure functions with injected deps (mode_resolver takes env as arg, not
  os.environ directly) -- gate-check should follow same testability pattern
- Module-per-concern: exit_codes.py, mode_resolver.py, lock.py are all
  single-responsibility -- gate_check.py and install_hooks.py follow this
- Tests mirror source: tests/test_mode_resolver.py for mode_resolver.py

### Integration Points
- cli.py argparse setup: restructure to subparsers (D-01)
- pyproject.toml console_scripts: forge entry point (unchanged, subparsers
  are internal routing)
- .git/hooks/pre-commit: install-hooks writes this file

</code_context>

<specifics>
## Specific Ideas

- R4 docs are a living document: Phase 1 writes LIVE/PLANNED distinction;
  Phases 2-3 promote PLANNED to LIVE as they ship
- The 7 round-3 findings from SPEC v3.2 (FAIL-OPEN guard, exit 4/5 BLOCK,
  hooks-dir resolve, absolute forge path, FORGE_MODE=ci, baseline bootstrap)
  are all Phase 1 implementation detail -- fold into PLAN.md

</specifics>

<deferred>
## Deferred Ideas

- R5 (test layering / threshold-triggered real-dependency regression) --
  deferred to forge-code phase per user direction
- Adding yamllint to pyproject.toml dev deps -- minor, can do in Phase 1
  or leave as pip install
- pythonpath = ["src"] in pyproject.toml [tool.pytest.ini_options] -- would
  make bare pytest work but undermines EC-5 justification for test.env

</deferred>

## Reference Class

Three comparable projects that implemented real commit gates, with plan-vs-actual ratios:

- Chromium CQ Submit (2018-present): the Commit Queue runs the full builder matrix
  before merging. OWNERS-based domain enforcement + tiered CI (Dry Run / Submit /
  Mega CQ). Integration ratio: estimated ~1.5x planned. The directory-to-builder
  mapping was straightforward; shared-header impact radius was the long tail.
  Lesson: tiering by risk level is the right design; a single run-everything gate
  is unusable for large repos.

- Rust bors merge queue (2015-present): pre-merge test execution via a merge queue
  bot. Exit-code translation is the critical design (bors maps test outcome to
  merge/reject). Ratio: estimated ~1.2x planned. Lesson: the test suite must be
  stable before a commit gate is meaningful; a flaky suite means the gate is
  randomly blocking, not blocking on real failures.

- Forge Phase 0 (internal reference): gate.yaml + baseline bootstrap. One plan,
  config-only. Actual: straightforward, no ratio overrun. Lesson: separating config
  bootstrap (Phase 0) from gate logic (Phase 1) avoids chicken-and-egg deadlock;
  Phase 1 can dogfood Phase 0's baseline from day one.

## Risks

### Known Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| CLI subparser restructure breaks existing "forge review" behavior | Medium | High | backward compat: bare "forge" with no subcommand routes to "forge review" (D-01) |
| Exit-code translation bug: test-1 mapped to hook-0 (ALLOW instead of BLOCK) | Medium | High | bug-inject test required (EC-7 equivalent): break translation, confirm test FAILS, restore, confirm PASS |
| install-hooks absolute path stale after forge reinstall | Low | Medium | liveness check: --version subprocess at install time |
| baseline becomes stale after test additions; blocks clean trees | Medium | Medium | baseline regeneration documented; schema includes regeneration command |

### Gray Rhinos

| Risk | Denial Reason | Counter |
|------|--------------|---------|
| Real-dependency smoke (EC-8) takes more time than expected | "It is just one commit test" | terminal commit + non-Bash commit paths both required; two paths, not one |
| FORGE_SKIP_TESTS bypass in CI is discovered post-merge | "Dev need escape hatches" | FORGE_SKIP_TESTS is local-only by SPEC; CI mode always runs regardless |

### Black Swans

| Risk | Survival Plan |
|------|--------------|
| FAIL-OPEN guard misconfigured: gate.yaml parse error allows instead of blocks | Bug-inject test specifically for FAIL-OPEN guard path; any regression surfaces immediately |
| core.hooksPath is set in target repo; install-hooks must abort | SPEC-locked: abort with explicit error; test with a repo that has core.hooksPath set |

## Pre-mortem

Imagine Phase 1 ships and the commit gate fails to work in practice. The most
likely causes:

1. Gate-check fails to block on terminal (non-Bash) commits. Early warning: a
   deliberate red-tree commit from a terminal passes the gate silently. Counter:
   real-dependency smoke test drives the real hook in both terminal and Bash paths
   (EC-8, two paths required).

2. FAIL-OPEN guard is on the wrong code path. Early warning: a broken gate.yaml
   allows instead of blocking; gate is silently open. Counter: dedicated unit test
   for the FAIL-OPEN path; bug-inject confirms the guard is load-bearing.

3. install-hooks backup conflict with an existing pre-commit hook. Early warning:
   install-hooks silently overwrites without backup; original hook is lost. Counter:
   backup + chain logic; install-hooks aborts on core.hooksPath (SPEC-locked).

4. Exit-code translation is off by one. Early warning: test exit 2 (forge
   EXIT_CLI_ERROR) maps to BLOCK instead of warn; forge review errors block every
   commit. Counter: explicit translation table test covering exit 0/1/2/3/4/5.

5. R4 docs overclaim: "What Forge Covers" lists R2/R3 as LIVE when they are
   PLANNED. Early warning: users try to use mutation or e2e features that do not
   exist. Counter: D-03 sequencing discipline: LIVE vs PLANNED distinction is
   explicit; each phase updates R4 as it ships.

## Chaos Response

| Stressor | Response | Classification |
|---------|----------|----------------|
| gate.yaml missing in repo | gate-check BLOCKS with "gate.yaml not found"; never silently allows | survive |
| gate.yaml has YAML parse error | FAIL-OPEN guard: dedicated BLOCK path; EXIT_CLI_ERROR never maps to ALLOW | survive |
| Existing pre-commit hook present; install-hooks must chain | backup existing hook; chain it after gate-check; tested explicitly | survive |
| core.hooksPath set in repo | install-hooks ABORTS with explicit error; does not overwrite hooksPath target | survive |
| forge binary not on PATH at hook run time | install-hooks embeds absolute forge path; liveness check at install time verifies the path | survive |
| Test suite exits 2 (forge EXIT_CLI_ERROR during gate-check) | warn + allow (not BLOCK); gate-check own config errors use a SEPARATE BLOCK path | survive |

## Scope Challenge

Q1: Does this need to exist?
Yes. The PreToolUse hook (Claude Code only) is bypassable: terminal commits, Cursor,
Aider, and direct git usage all skip it. A real .git/hooks/pre-commit is the only
mechanism that gates at the git protocol level, not at the IDE integration level.
Forge's own CLAUDE.md documented this blind spot explicitly before R1 shipped.

Q2: Three real consumers
- Forge itself: after R1 ships, forge's own commits are gated by gate-check.
- plan-forge: uses forge review pipeline; will benefit from R1 gate on its own
  repo once gate.yaml is configured.
- Any project running forge install-hooks: gets the real commit gate without
  writing custom hooks.

Q3: Do-nothing cost
Doing nothing: the PreToolUse hook continues to be the only gate, covering one IDE
only. Terminal commits, CI commits, and other IDE commits bypass the gate. The
specific cost: CLAUDE.md honestly documents the bypass vector, which is visible to
every evaluator who reads forge's own design docs.

Q4: Barbell vs middle ground
Barbell: (A) PreToolUse hook only (single-IDE, bypassable, status quo), (B) full
CI/CD pipeline with per-branch policy enforcement and org-level hooks (enterprise
scope). R1's .git/hooks/pre-commit is NOT the middle ground -- it is the minimal
correct mechanism for closing the terminal-commit bypass at the git-protocol level,
without enterprise-scope policy infrastructure.

## External Voices

Primary sources on commit gate design and verification grounding:

- Petrovic & Ivankovic (2018). State of Mutation Testing at Google. ICSE 2018.
  Primary reference for verification grounding over self-claim. Key finding: tool
  grounding reduces hallucinations by 65-80%; prompt-only mitigations cap at 15%.
  Directly motivates R1's design: a real test run gates the commit, not a marker
  that claims the tests passed.

- Jia & Harman (2011). An Analysis and Survey of the Development of Mutation
  Testing. IEEE Transactions on Software Engineering, Vol. 37, No. 5. The most
  comprehensive survey on verification-based quality gates. Documents why static
  review alone is insufficient: equivalent mutants and false-positive rates
  accumulate; dynamic verification is the complement, not the replacement.

A legitimate objection exists: a real pre-commit hook adds latency to every commit.
If the test suite is slow, developers will use FORGE_SKIP_TESTS=1 or --no-verify.
Forge mitigates by gating only on diff content (source_patterns filter) and allowing
FORGE_SKIP_TESTS locally (with a warning), rejecting it in CI. The skip vector is
intentional and documented -- forge is a developer tool, not a security enforcement
mechanism.

Historical lesson from Chromium CQ tiering: a single run-everything gate is unusable
at scale. Forge's source_patterns filter and CI-mode enforcement follow the same
principle: the gate is meaningful only if developers run it voluntarily, which means
it must be fast on common cases.

---

*Phase: 01-r1-commit-gate-r4-docs*
*Context gathered: 2026-05-25*
