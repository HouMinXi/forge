FORGE DYNAMIC-GATE BACKPORT -- SPEC v3
(v2 + round-2 verify absorbed + HOST ground-truth corrections)
Round-2 views: opus + ds + glm + mimo (kimi FAILED -- cross-Pacific, 0B output).
4 requirements, 100% to solve.

== WHAT CHANGED v2 -> v3 ==
Round-2 verify reached strong consensus on 6 execution-level findings (below).
No architecture was overturned -- v2->v3 is convergent. Host ground-truthed 2
FACTUAL CONFLICTS where sub-models read the WRONG repo (plan-forge, not forge):
  - R4 sections: forge/CLAUDE.md:287-288 HAS "What Forge Covers" + "What Forge
    Is Missing" headers, both EMPTY (next line is "## AI Code Review"). ds/glm
    correct (headers exist+empty); mimo's "section does not exist -> CREATE"
    REJECTED (mimo read plan-forge/CLAUDE.md, which has neither). BUT v2's own
    "v1 wrongly said 'fill the empty section'" is ITSELF wrong: the section IS
    empty, so v1's "fill" was accurate. v3 drops that overcorrection.
  - forge tests: forge HAS 521 tests (PYTHONPATH=src python3 -m pytest). ds's
    "forge has ZERO tests, tests/ does not exist" REJECTED (read wrong repo);
    glm's "543 tests" is plan-forge's count. Ground-truth: bare pytest = "40
    collected, 44 errors" (import failures, NOT "No tests collected") ->
    PYTHONPATH=src = "521 collected". This CONFIRMS test.env is mandatory.

== MOTIVATION (plan-forge Phase 2, real-world) ==
plan-forge Phase 2 shipped 3 bugs a 9-pass static review + marker-based commit
gate did NOT catch: a RED commit under a "# post-review-c3" marker (gate trusted
the marker, never ran tests); toothless tests that hugged the implementation; an
integration bug isolated unit tests missed. What caught all three was DYNAMIC
ground-truth: run the suite + mutation (bug-inject) to prove tests have teeth +
end-to-end run of the real path. forge is the same kind of tool with the same
blind spots.

== GROUND-TRUTH: forge today (confirmed by reading forge repo, HEAD d65d2f7) ==
- commit gate (hooks/check_git_commit_review.sh) is a Claude Code PreToolUse
  hook: triggers ONLY on the CC Bash tool running `git commit`. Terminal / IDE /
  VS Code commits BYPASS it entirely. It checks AI-attribution + the
  "# post-review-c3" marker (re.search on the command string), runs NO tests
  (subprocess is only klist + git config). The marker is a self-claim it trusts.
- tool registry exists (.forge/tools.yaml 1898B, src/forge/registry.py) but has
  NO test-command config. NO .forge/gate.yaml. findings.json present.
- state machine: L0 (static parsers) -> autofix -> L1 (LLM candidates ->
  Falsifier.falsify() on a single finding). Cycle counter counts review PASSES.
  StateFinding.source is Literal["L0","L1"] -- no MUTANT, no l2_runner.
- forge reviews Python/shell/Go/Rust/C. forge's OWN tests REQUIRE PYTHONPATH=src:
  bare `python3 -m pytest` = 40 collected / 44 import errors; PYTHONPATH=src =
  521 tests. pytest is a dev optional-dep. (no pythonpath cfg in pyproject.toml)
- forge/CLAUDE.md:287-288 = empty "What Forge Covers That Nobody Else Does" +
  "What Forge Is Missing" headers; R4 fills them. CLAUDE.md states the right
  principle (tool grounding > prompt-only; confidence paradox) but the gate
  implementation is the prompt-only self-claim it criticizes.

== REQUIREMENTS (all four, 100%) ==

R1 -- VERIFICATION COMMIT GATE (a REAL git hook, not the PreToolUse hook)
  [v3 KEY RESTRUCTURE -- opus V2, the deepest finding:] a git PRE-COMMIT hook
  CANNOT see the commit message (it runs BEFORE the message is finalized; the
  message is only available to a commit-msg hook). The "# post-review-c3" marker
  lives in the message. THEREFORE the pre-commit hook MUST NOT gate on the
  marker -- that is architecturally impossible. It gates on DIFF CONTENT, which
  is exactly the point of moving off a self-claim marker.
  Two orthogonal hooks, no overlap (dissolves ds's "duplication" BLOCKER-1):
    - PreToolUse hook (KEPT, Claude-Code-context only): reads the Bash
      `git commit` command STRING -> AI-attribution + marker detection. Marker
      is visible here because it is in the -m argument. CC-only concern.
    - REAL .git/hooks/pre-commit (NEW, the R1 core): reads `git diff --cached
      --name-only`. If it contains any source file (.py/.sh/.go/.rs/.c) -> run
      tests -> block on NEW failures vs baseline. Does NOT read the marker
      (cannot). This closes the mixed-content bypass AND the marker-self-claim.
  Design, all decided:
  - Hook body = `forge gate-check` [v3 NEW CLI -- ds BLOCKER-3 + opus V4]: a
    subcommand that OWNS yaml-parse + baseline + test-run + exit-code mapping.
    The hook is literally `#!/bin/sh\nexec forge gate-check`. No yaml/safety/
    exit-code logic reimplemented in shell (which would be a brittle non-portable
    200-line script).
  - `.forge/gate.yaml` (or extend tools.yaml): `test.command` (list, e.g.
    ["python3","-m","pytest","tests/","-q"], ["cargo","test"], ["go","test",
    "./..."]), `test.timeout_seconds` (default 120), `test.cwd`, and
    [v3 NEW -- 4/4 consensus: opus HIGH, glm HIGH, ds, mimo] `test.env` (dict,
    e.g. {PYTHONPATH: "src"}) MERGED into the subprocess via env={**os.environ,
    **test.env}, NOT as a shell prefix -- this keeps the no-metacharacter rule
    intact AND lets forge dogfood (bare pytest = 44 import errors). UNSET
    test.command -> FAIL CLOSED ("configure test.command"), never silent pass.
  - Command safety: test.command[0] must be a known runner (pytest / python -m
    pytest / cargo test / go test / make test) and no element may contain shell
    metacharacters (|;&$><). Else block + error. (env vars go in test.env, not
    the command, so PYTHONPATH no longer needs a metachar-bearing string.)
  - Repo root via `git rev-parse --show-toplevel`; run there.
  - [v3.1 CORRECTED -- kimi #2, git protocol] a pre-commit hook exit is BINARY:
    git aborts the commit on ANY non-zero. So "WARN" cannot be a non-zero HOOK
    exit; the TEST command's codes are TRANSLATED by the hook. [v3.2 REFINED --
    round-3 glm/kimi/ds]:
      test 0       -> hook 0 (allow)
      test 1       -> hook 1 (BLOCK, real failure)
      test 2, 3    -> hook 0 + stderr warning (pytest interrupt / internal error
                      -- genuine infra noise, not code quality)
      test 4       -> hook 1 (BLOCK; usage error = a misconfigured command, not
                      a pass)
      test 5       -> hook 1 (BLOCK; "no tests collected" with source in the
                      diff = the gate is toothless here = treat as failure)
      timeout, >5  -> hook 1 (BLOCK)
    CRITICAL [v3.2 -- round-3 kimi/ds, FAIL-OPEN guard]: gate-check's OWN errors
    (gate.yaml missing / parse failure / unsafe command) MUST take a dedicated
    hook-1 BLOCK path, NEVER the test 2-3 warn path. forge already defines
    EXIT_CLI_ERROR=2 (exit_codes.py); if gate-check returned 2 for a config
    error it would be mistranslated to ALLOW and the gate would fail OPEN on
    misconfiguration. Isolate gate-check's config/parse errors from the test
    command's exit codes. WARN is always hook-exit-0-with-warning.
  - [v3 CORRECTED -- 4/4: opus V4, ds HIGH-2, glm, mimo] TEST-RESULT baseline is
    its OWN domain; do NOT "reuse the state machine's baseline (02-03)" -- that
    baseline is FINDING-level (which review findings pre-exist), unrelated to
    test pass/fail. Phase 0 records pass/fail per test-id (store in
    .forge/test_baseline.json or test.baseline_sha); the gate blocks only on
    NEW failures vs that baseline. Without this, forge is unusable on any repo
    with a known-red/flaky test. [v3.1 -- kimi #3 lifecycle] persist it as a
    .forge/test_baseline.json artifact with a defined schema, written after a
    completed review and read by the pre-commit hook to compute the new-failure
    delta. An in-memory-only baseline is invisible to the hook (separate
    process). [v3.2 -- round-3 ds, bootstrap] no baseline yet (first run) ->
    ALLOW + warn ("no baseline; run forge gate-check --record-baseline"), never
    block. A test in the diff but absent from the baseline that FAILS -> BLOCK
    (a new failing test is a new failure); absent-but-PASSES -> fold into the
    baseline.
  - [v3 NEW -- 4/4: opus V3, ds BLOCKER-2, glm HIGH, mimo] `forge install-hooks`
    CLI: writes .git/hooks/pre-commit. It MUST detect (a) an existing
    pre-commit hook, (b) core.hooksPath set elsewhere, (c) a
    .pre-commit-config.yaml (pre-commit framework) -> APPEND / chain / warn,
    NEVER silently overwrite. [v3.1 -- kimi #1] back up an existing hook to
    .git/hooks/pre-commit.forge-backup and chain-call it (#!/bin/sh;
    .git/hooks/pre-commit.forge-backup "$@" || exit 1; exec forge gate-check);
    if core.hooksPath is set, ABORT with manual-integration instructions rather
    than write to the wrong path. [v3.2 -- round-3 ds] resolve the hooks dir via
    `git rev-parse --git-path hooks`, NOT a hardcoded .git/hooks -- in a linked
    worktree .git is a FILE and --git-path returns the real shared hooks dir;
    install once per repo. [v3.2 -- round-3 glm/mimo] git hooks run in a minimal
    shell where `forge` may be off PATH (venv not activated) -> exit 127; so
    install-hooks must embed the ABSOLUTE forge path at install time
    (shutil.which('forge') or sys.executable + ' -m forge'), not a bare `forge`
    (the `exec forge gate-check` shown above is the shape; the installer
    substitutes the absolute path).
  - Dev escape: FORGE_SKIP_TESTS=1 -> WARN + allow (local velocity); CI mode
    ALWAYS runs regardless. The var is inherited from the invoking process
    (terminal commit = shell env; CC commit = Claude env) -- documented, since
    the two hooks live in different processes. (mimo #5)
  - [v3.1 NEW -- kimi #7; v3.2 +FORGE_MODE round-3 glm] CI mode = FORGE_MODE=ci
    (forge's own convention, mode_resolver.py, matched case-insensitively) OR
    any of CI / GITHUB_ACTIONS / GITLAB_CI / JENKINS_URL / BUILD_URL set and
    non-empty. Omitting FORGE_MODE would let a custom CI that sets only it be
    treated as LOCAL and honor FORGE_SKIP_TESTS -- the exact bypass CI mode
    prevents. In CI mode FORGE_SKIP_TESTS is ignored (tests always run).

R2 -- MUTATION GATE: a REVIEW-PIPELINE step, NOT a commit gate
  Mutation is O(mutants x suite-time) -- minutes to hours; it CANNOT block
  `git commit`. It runs in the review pipeline / state machine. (5-way round-1)
  Design:
  - State-machine integration: add `source="MUTANT"` to StateFinding (state.py)
    + an `l2_runner` (default no-op) wired after the L1 phase. Do NOT overload
    Falsifier (it runs on a single finding; mutation runs over the diff).
  - [v3 CLARIFIED -- opus V5 + glm async/sync contradiction:] split by mode:
      * LOCAL mode: a FAST diff-scoped mutation runs SYNCHRONOUSLY inside the
        cycle. A surviving mutant = a confirmed P1 ("tests toothless here") that
        RESETS the current cycle (maps to existing rounds_with_findings reset);
        3 consecutive survivor-cycles -> hard stop.
      * CI mode: full/async mutation is a SEPARATE gate that FAILS THE BUILD.
        It does NOT reach back into the cycle counter (the local cycle is over
        by the time async results return). Pick per mode -- no contradiction.
  - Language routing: Python-only MVP via mutmut (forge itself is Python). Table
    for later: {rust: cargo-mutants, go: go-mutesting, c: mull}; shell has no
    mature tool. Unsupported language -> emit a MUTATION_SKIPPED finding
    (visible, not a silent pass). Do NOT hardcode mutmut -- swappable.
  - Diff-scoping: file-level first (mutmut --paths-to-mutate on changed files
    via `git diff --name-only`); function-level (AST-located changed nodes,
    excluding comments/imports/docstrings) as an optimization with a threshold.
  - Flaky guard: before mutating, run the baseline suite 3x; any flake -> abort
    with "tests flaky, mutation unreliable" (mutation assumes a stable green
    baseline).

R3 -- INTEGRATION / E2E COVERAGE: checklist + opt-in config, NOT auto data-flow
  "spans >=2 components on a data path" is not reliably auto-detectable (no call
  graph; shell/C have none). Two explicit layers:
  - Heuristic (no config): if `git diff --name-only` touches >=2 source
    directories AND adds/modifies a function signature/return type -> emit a
    checklist finding "cross-component change; is there an e2e test for the
    joined path?" Non-blocking review dimension.
  - Explicit (opt-in): `.forge/components.yaml` (components -> paths, data_paths
    -> component pairs). When a diff hits two components on a shared data path,
    require an e2e test file (tests/e2e/* or test_*integration*); absent -> P2
    finding. Honest: layer 1 is best-effort, layer 2 is enforceable only on
    opt-in.

R4 -- GATE PHILOSOPHY (docs; FILL the empty sections; ship EARLY)
  [v3 CORRECTED:] forge/CLAUDE.md:287-288 has the two HEADERS but they are
  EMPTY. R4 FILLS them with content. (Both v1 "fill the empty section" and v2
  "update it" describe this correctly; v2's claim that v1 erred is dropped --
  the section IS empty.) Make explicit: static passes (parsers + cycle) are ONE
  layer; they must be backed by dynamic gates (run suite + mutation + e2e).
  Passes-count is not a quality guarantee -- forge applying its OWN
  hallucination data (verification grounding > prompt-only self-claim). Under
  "What Forge Covers That Nobody Else Does" add: mutation-gated review + a
  real-test commit gate. R4 is pure docs, zero dependency on R2/R3 -> ships in
  Phase 1 alongside R1.

R5 -- TEST LAYERING: threshold-triggered real-dependency regression
  [user input + Phase-4 ttl_class incident; DEFERRED to forge-code phase:]
  forge's own coverage has the same mock blind spot plan-forge had: 639 mock
  tests + a 9-pass review missed a real cache.set bug that only a live-backend
  run caught (the Phase-4 ttl_class='standard' incident -- the no-evidence
  branch tagged cache entries with a TTL class the backend rejects, crashing
  every real-provider call). Real-dependency smoke/integration tests are
  expensive (real DB/cache/network, slow), so they must NOT run on every commit.
  Layer them:
  - FAST unit tests (mocked deps): every commit, via the R1 gate.
  - REAL-dependency smoke/integration: THRESHOLD-triggered, not per-commit.
    Triggers: the diff touches an integration seam (cache/DB/network/
    serialization adapter), spans >= N source dirs, or CI mode. Below the
    threshold -> skip (the cost-vs-blind-spot trade-off the user flagged).
  - Each real-dependency regression test MUST drive the real backend (not a full
    mock) and be bug-injected (revert the fix -> the test FAILS) to prove teeth.
  Implementation deferred to the forge-code phase per user direction; captured
  here so the plan records it. Not in Phases 0-3 below.

== SCOPE-CHALLENGE (justified by the real incident) ==
- Consumers: forge's own commit gate (R1); forge's review pipeline/state machine
  (R2); every project that runs forge review (plan-forge just got bitten).
- do-nothing cost: marker gate stays bypassable (proven), toothless tests keep
  passing, integration bugs keep slipping -- 3 real bugs in one phase.
- value order if staged: R1 > R2 > R4 > R3 (R4 is cheap, rides with R1).

== PHASES (worktree first; each = own commit; 3-cycle review each) ==
0. worktree off forge main + create forge's own `.forge/gate.yaml`
   (test.command=["python3","-m","pytest","tests/","-q"],
    test.env={PYTHONPATH:"src"}  <- mandatory: bare pytest = 44 import errors)
   as a `# config` commit, + establish the test baseline (521 tests). This lets
   R1 dogfood without a chicken-and-egg deadlock.
1. R1 (real .git/hooks/pre-commit gating on DIFF CONTENT + `forge gate-check` +
   `forge install-hooks` + test.env + test-result baseline) + R4 (docs,
   parallel, zero-dep). Test R1 by committing a deliberately-red tree -> must
   block; a non-Bash (terminal) commit -> still gated (the whole point).
2. R2 (mutation as a state-machine step, source="MUTANT" + l2_runner, Python
   mutmut diff-scoped, flaky-guard, LOCAL-sync / CI-async split). Dogfood:
   mutate forge's own changed code, confirm forge's tests kill the mutants.
3. R3 (heuristic checklist + opt-in components.yaml).

== ROUND-2 VERIFY VERDICT (v3.1: 5 views, kimi added) ==
5 views (opus/ds/glm/mimo/kimi) + host ground-truth. Strong consensus; ALL
findings execution-level; NO architecture overturned -> v3.1 is convergent and
ready to implement. The Phase-4 ttl_class incident (real-API smoke caught a bug
that 639 mock tests + a 9-pass review missed) is the live proof of the R1/R2/R5
thesis: static + mock passes have a systematic blind spot only dynamic gates
close.

ABSORBED (8 findings, cross-model support):
  1. test.env for PYTHONPATH (4/4 + ground-truth: bare pytest = 44 errors)
  2. `forge install-hooks` cross-project + hooksPath/pre-commit-framework (4/4)
  3. test-result baseline is its own domain, not 02-03 finding-baseline (4/4)
  4. `forge gate-check` CLI entrypoint, hook calls it (opus + ds)
  5. R2 LOCAL-sync vs CI-async cycle-counter split (opus + glm)
  6. pre-commit gates on DIFF CONTENT, marker is PreToolUse-only -- the two
     hooks are orthogonal (opus V2 deepest + dissolves ds BLOCKER-1)
  7. [kimi, NEW] pre-commit exit-code protocol: git aborts on ANY non-zero, so
     'WARN' must be hook-exit-0; the test command's exit codes are TRANSLATED
     by the hook (test 2-5 -> hook 0 + stderr warning)
  8. [kimi, NEW] CI mode detection: CI / GITHUB_ACTIONS / GITLAB_CI /
     JENKINS_URL / BUILD_URL env vars; FORGE_SKIP_TESTS ignored in CI

REJECTED (host ground-truth, sub-models read wrong repo):
  - mimo "R4 -> CREATE section": sections EXIST (empty) at forge/CLAUDE.md:287.
  - ds HIGH-3 "forge zero tests": forge has 521 tests.
  - ds BLOCKER-1 severity: downgraded to resolved -- the hooks are orthogonal
    (command-string marker vs diff-content tests), not duplicated.

v2 SELF-ERRORS CORRECTED in v3:
  - "v1 wrongly said fill the empty section" -- dropped (section IS empty).
  - "bare pytest -> No tests collected" -- actually 44 import errors.
  - test count 543 -> 521 (543 was plan-forge's).

OUTSTANDING (user decision):
  - kimi 5th view: DONE -- caught 2 findings the 4-view panel missed (#7, #8),
    confirming cross-model complementarity. 5 views now complete.
  - re-review vs implement: v3.1 is convergent; recommend implement (start
    Phase 0). A full re-review of a convergent revision risks the small-change
    non-convergence spin (see memory).

== ROUND-3 RE-REVIEW (v3.2: aicc ds/glm/mimo/kimi + host) ==
All 5 views READY, 0 blockers. The re-review earned its keep -- it caught 7 real
execution-level findings the prior rounds missed, all in R1 Phase-1 detail
(absorbed above into R1):
  - FAIL-OPEN guard [HIGH]: gate-check's own config/parse errors must BLOCK, not
    reuse the test 2-3 warn path (forge's EXIT_CLI_ERROR=2 would mistranslate to
    ALLOW -> gate fails open on misconfiguration).
  - exit 4 (usage error) and exit 5 (no tests collected) -> BLOCK, not warn.
  - install-hooks: resolve the hooks dir via `git rev-parse --git-path hooks`;
    embed the ABSOLUTE forge path (git hooks run off-PATH -> exit 127).
  - CI detection: add FORGE_MODE=ci (forge's own convention, mode_resolver.py).
  - baseline bootstrap: first-run no-baseline -> allow+warn; a new failing test
    absent from the baseline -> BLOCK.
Phase 0 (gate.yaml + baseline) is unaffected by all 7 -> READY to start now; the
fixes land in the Phase-1 implementation.
