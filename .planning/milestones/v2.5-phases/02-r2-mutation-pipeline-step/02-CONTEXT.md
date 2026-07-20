# Phase 2: R2 (mutation pipeline step) - Context

**Gathered:** 2026-05-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver mutation testing as a review-pipeline step in the forge state machine.
A surviving mutant proves tests are toothless at that code point. This is NOT
a commit gate -- mutation is O(mutants x suite-time) and cannot block
`git commit`. It runs inside the state machine's review cycle (LOCAL sync)
or as a separate build gate (CI async).

Additionally, fix the resolve_forge_path liveness issue carried from Phase 1.

</domain>

<decisions>
## Implementation Decisions

### l2_runner Integration
- **D-01:** l2_runner interface design is Claude's discretion. Must follow the
  SPEC requirements: wired after L1 phase, does NOT overload Falsifier, returns
  StateFinding(s) with source="MUTANT". The existing DI pattern (l0_runner,
  l1_provider as injectable callables) is the reference. Testability via
  stub injection is required.

### mutmut Invocation
- **D-02:** Direct subprocess.run of `mutmut run --paths-to-mutate <files>` +
  `mutmut results` to extract survivors. Parse stdout for surviving mutants.
  Python-only per SPEC. Keep the subprocess call in one place (single module)
  so future language runners can replace the implementation without changing
  the l2_runner interface (satisfies SPEC "Do NOT hardcode mutmut -- swappable").
- **D-05:** mutmut is a soft dependency. If `shutil.which("mutmut")` returns
  None, emit MUTATION_SKIPPED finding (disposition=DISMISSED, visible in
  report but does not block convergence). Same behavior for unsupported
  language. Do NOT add mutmut to pyproject.toml hard deps.

### CI Async Design
- **D-03:** CI mode uses async subprocess + state file per SPEC design:
  - _run_ci() launches mutation via subprocess.Popen after the L0+L1 round
  - Results written to `.forge/mutation-result.json` with schema:
    `{pid, started_at, status: "running"|"done"|"error", survivors: [...]}`
  - On next `forge review` in CI: if mutation-result.json exists with
    status="done" and survivors is non-empty -> EXIT_FAIL with message
    listing survivors. If status="running" -> ignore (still in progress).
    If status="error" -> EXIT_FAIL with error detail.
  - `consecutive_survivor_rounds` is LOCAL-only; CI does NOT use it
  - Async result does NOT reach back into the cycle counter
  - Stale result detection: if mutation-result.json pid is not running
    (kill -0) and status is still "running" -> treat as error -> emit
    MUTATION_SKIPPED (not EXIT_FAIL; missing result is a false-negative,
    not a build failure). This is "degrade" behavior, not hard failure.

### LOCAL Sync Design (SPEC-locked, clarified by cross-model review)
- A FAST diff-scoped mutation runs SYNCHRONOUSLY inside the cycle
- A surviving mutant = StateFinding(source="MUTANT", disposition=CONFIRMED).
  MUTANT findings MUST skip _apply_autofix_loop_to() -- a coverage gap is
  not a code bug and autofix is semantically wrong for it. Filter by
  source="MUTANT" before the autofix loop.
- A CONFIRMED MUTANT finding naturally prevents _fixpoint_reached() from
  returning True (condition: zero CONFIRMED findings). No explicit "cycle
  reset" variable is needed -- the existing convergence logic handles it.
  NOTE: the SPEC's "maps to existing rounds_with_findings reset" references
  a v1.1 concept that does NOT exist in the current machine.py. Ignore it.
- A new consecutive_survivor_rounds counter tracks how many rounds in a row
  had at least one CONFIRMED MUTANT finding. The counter RESETS to 0 when a
  round produces zero CONFIRMED MUTANT findings (all mutants killed). If
  consecutive_survivor_rounds reaches 3 -> hard stop with Verdict.FAIL and
  EXIT_FAIL. This is a resource guard, not escalation: 3 consecutive rounds
  with survivors means the tests are demonstrably weak at that code point.
- MUTATION_SKIPPED findings use disposition=DISMISSED (informational, does
  not block convergence or trigger autofix)

### Diff-Scoping (SPEC-locked)
- File-level first: mutmut --paths-to-mutate on changed files via
  `git diff --name-only`
- Function-level (AST-located changed nodes) is an optimization for later

### Flaky Guard (SPEC-locked)
- Before mutating, run baseline suite 3x; any flake -> abort with
  "tests flaky, mutation unreliable"
- Mutation assumes a stable green baseline

### resolve_forge_path Liveness (Phase 1 followup)
- **D-04:** Include in Phase 2 scope. After resolving the forge path via
  shutil.which, run `<forge_path> --version` and verify exit 0 with output
  matching `forge `. If liveness check fails, fall back to sys.executable.
  See: `.planning/phases/01-r1-commit-gate-r4-docs/followup-resolve-forge-path-liveness.md`

### Diff-Scoping Git Ref
- **D-06:** Use `git diff HEAD --name-only` for diff-scoping in LOCAL review
  mode (shows working tree changes vs last commit -- what the user is
  reviewing). For CI mode, derive changed files from the CI environment's
  diff mechanism (e.g., `git diff origin/main...HEAD --name-only` for
  PRs). Do NOT use `--cached` for mutation scoping (mutation runs on the
  working tree, not only on staged changes).

### Claude's Discretion
- Internal module layout for mutation.py / l2_runner wiring
- mutmut output parsing implementation details
- Error message wording for MUTATION_SKIPPED and flaky guard
- Test file organization for mutation tests
- Whether to split mutation logic into mutation.py + mutation_runner.py or keep as one
- MUTANT finding fingerprint scheme (l2_runner must produce unique fingerprints
  per mutant; the scheme is implementation detail as long as it's stable)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Spec and Design
- `.planning/milestones/v2.1-dynamic-gate/SPEC.md` -- section "R2 -- MUTATION GATE":
  state-machine integration, mode split, language routing, diff-scoping, flaky guard
- `.planning/milestones/v2.1-dynamic-gate/ROADMAP.md` -- Phase 2 exit criteria (8 items)

### State Machine (must read before modifying)
- `src/forge/machine.py` -- StateMachine class, _execute_round(), l0_runner/l1_provider
  injection pattern, _run_local() cycle loop, _run_ci() linear mode
- `src/forge/state.py` -- StateFinding dataclass; source field is Literal["L0", "L1"]
  (line 48, must extend to include "MUTANT"); Disposition enum, load_state/save_state
- `src/forge/disposition.py` -- 5-state disposition model (CONFIRMED/UNCERTAIN/DISMISSED/
  FIXED/PENDING)
- `src/forge/exit_codes.py` -- EXIT_PASS/FAIL/CLI_ERROR constants
- `src/forge/falsify.py` -- Falsifier class (SPEC says do NOT overload; l2_runner is
  separate from falsification)
- `src/forge/runner.py` -- L0 runner implementation (run_tools); reference for how
  l0_runner produces findings from subprocess output
- `src/forge/factories.py` -- builds falsifier/autofixer/revert_fn; reference for
  the factory pattern (l2_runner factory follows the same shape)

### Phase 1 Code (reference for patterns)
- `src/forge/gate_check.py` -- subprocess.run pattern, exit code translation,
  command safety validation (reference for mutmut subprocess)
- `src/forge/install_hooks.py` -- resolve_forge_path (liveness fix target)

### Phase 1 Followup
- `.planning/phases/01-r1-commit-gate-r4-docs/followup-resolve-forge-path-liveness.md` --
  liveness check problem statement and suggested fix

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `machine.py` StateMachine: DI pattern for l0_runner/l1_provider -- l2_runner
  follows the same injectable callable approach
- `gate_check.py` subprocess.run: timeout handling, exit code translation --
  reference for mutmut subprocess invocation
- `state.py` StateFinding: source field is Literal["L0", "L1"] -- extend to
  include "MUTANT"
- `disposition.py`: surviving mutant maps to CONFIRMED disposition

### Established Patterns
- Module-per-concern: machine.py, gate_check.py, install_hooks.py are all
  single-responsibility -- mutation.py follows this
- Pure functions with injected deps: l0_runner takes (registry, files) and
  returns (findings, infra_errors) -- l2_runner follows same testability
- Tests mirror source: tests/test_machine.py, tests/test_gate_check.py

### Integration Points
- machine.py _execute_round(): add l2 phase after L1
- machine.py _run_local(): cycle reset on surviving mutant
- machine.py _run_ci(): async mutation launch + result check
- state.py StateFinding.source: extend Literal type
- cli.py: no CLI changes needed (mutation is internal pipeline step)
- install_hooks.py resolve_forge_path(): add liveness check

</code_context>

<specifics>
## Specific Ideas

- Phase 2 plan output goes to kimi/deepseek/mimo via aicc for cross-AI review
  before execution
- Bug-inject test (EC-6 equivalent): add a toothless test that passes regardless
  of impl -> mutation surfaces a survivor -> phase flags it; remove it -> clean
- Mutation dogfood (EC-7 equivalent): mutate forge's own code changed in this
  phase, confirm tests kill the mutants

</specifics>

<deferred>
## Deferred Ideas

- Function-level diff-scoping (AST-located changed nodes, excluding
  comments/imports/docstrings) -- optimization for after file-level MVP works
- Multi-language mutation runners (cargo-mutants, go-mutesting, mull) -- SPEC
  says Python-only MVP, table for later
- R5 test layering (threshold-triggered real-dependency regression) -- deferred
  to forge-code phase per ROADMAP

</deferred>

## Reference Class

Three comparable projects that integrated mutation testing as a pipeline step,
with plan-vs-actual implementation ratios:

- **mutmut + pytest diff-scoped CI** (Python, open source, 2018-present):
  standard pattern for diff-scoped Python mutation in CI. Teams reported
  initial integration taking ratio 1.8x planned (subprocess wiring harder
  than expected; flaky-test handling added ~40% unplanned work). Lesson:
  flaky guard and output parsing are the long-tail items.

- **Pitest + Maven build phase** (Java, used in Apache projects): mutation
  as a build phase (not a commit gate). Implementation ratio 2.3x planned
  (CI parallelism setup and HTML/XML result aggregation dominated). Lesson:
  mutation is a review-layer tool; making it a hard commit gate is infeasible
  at >50K LOC without diff-scoping.

- **Forge Phase 1** (internal reference): commit gate + install-hooks.
  Four plans, estimated 5 days, actual 7 days including reviews = ratio 1.4x.
  Main overrun: EC-9 review found 11 additional findings; the install_hooks
  backup-conflict bug added unplanned fix work. Lesson: review is the long
  tail, not implementation.

## Risks

### Known Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| mutmut is slow on large Python codebases | High | Medium | diff-scoped file-level run only; timeout guard |
| mutmut output format changes between versions | Medium | High | subprocess call in one place; document required mutmut version in CLAUDE.md; NOT a pyproject.toml hard dep (per D-05) |
| Flaky tests produce spurious survivors | Medium | High | 3x baseline flaky guard before mutation runs |
| CI async process orphaned when CI agent restarts | Low | Medium | PID liveness check in mutation-result.json reader |

### Gray Rhinos

| Risk | Denial Reason | Counter |
|------|--------------|---------|
| Mutation round adds unacceptable latency to LOCAL review | Accepted for MVP: diff-scoped fast run is O(mutants x test-time); mutants per changed file is small for typical commits; typically <30s on forge's own suite | Timeout guard at 60s; MUTATION_SKIPPED on timeout |
| Diff-scoping misses relevant files | Acceptable tradeoff: file-level scoping is the standard MVP; function-level is deferred | Documented as known limitation; users can widen scope |

### Black Swans

| Risk | Survival Plan |
|------|--------------|
| mutmut becomes unmaintained or incompatible with Python 3.14+ | subprocess interface is stable; swap to cosmic-ray or custom AST mutator; l2_runner interface is language-independent |
| mutation-result.json corrupted by concurrent CI agents | Corruption detected by JSON parse error; treat as MUTATION_SKIPPED error; recommend per-job state file path |

## Pre-mortem

Imagine this phase shipped and mutation testing failed to work in practice.
The most likely causes, with early warning signals and counters: <!-- plan-forge: hedge-ok -->

1. **mutmut output format changed between install and run.** Early warning:
   `mutmut results` returns unexpected lines; survivor count is always 0.
   Counter: unit-test the stdout parser against mutmut's own test fixtures;
   pin mutmut version in developer docs.

2. **Flaky guard 3x baseline is too slow.** Early warning: LOCAL review time
   triples before any mutation runs; users skip `forge review`. Counter:
   make the 3x baseline optional (gate.yaml `mutation.flaky_guard: false`);
   default on but escapable.

3. **CI async process is silently orphaned.** Early warning: mutation-result.json
   stays `status: running` indefinitely; CI never fails even with survivors.
   Counter: PID liveness check; auto-expire stale results after 2x expected
   runtime.

4. **MUTANT findings trigger autofix loop.** Early warning: autofix attempts
   to "fix" a coverage gap (no-op), hits max_fix_attempts, promotes to
   UNCERTAIN, triggers HOLD UI; review hangs. Counter: filter source="MUTANT"
   before _apply_autofix_loop_to() (locked in D-01 LOCAL Sync Design).

5. **Consecutive survivor counter not implemented correctly.** Early warning:
   review loops indefinitely instead of hard-stopping after 3 survivor rounds.
   Counter: explicit unit test for the counter logic with a mock l2_runner
   that always returns a survivor.

6. **resolve_forge_path liveness fix causes regression.** Early warning:
   `forge install-hooks` fails on machines where the liveness check hangs.
   Counter: timeout the --version subprocess call (1s); fall back to
   sys.executable on timeout or non-zero exit.

## Chaos Response

Stressor scenarios for the mutation pipeline step, classified by outcome:

| Stressor | Response | Classification |
|---------|----------|----------------|
| mutmut hangs or timeout | Kill subprocess; emit MUTATION_SKIPPED (DISMISSED); review continues; commit is not blocked | survive |
| mutation-result.json corrupt | JSON parse error treated as status=error; emit MUTATION_SKIPPED; no crash | survive |
| git diff fails in l2_runner | subprocess non-zero; emit MUTATION_SKIPPED "could not determine changed files"; review continues | survive | <!-- plan-forge: hedge-ok -->
| CI async process orphaned | PID liveness check detects dead process; next forge review treats as error; false-negative (missed survivors) acceptable vs blocking CI | degrade |
| resolve_forge_path --version hangs | 1s timeout; fall back to sys.executable; hook install succeeds with fallback path | survive |
| mutmut finds zero mutants (no mutable code in diff) | Normal case; emit info finding; do not treat as MUTATION_SKIPPED; review continues | survive |
| All staged files are test files (no source to mutate) | Diff-scoping naturally excludes test files; zero mutations generated; pass through | benefit |

## Scope Challenge

**Q1: Does this need to exist?**
Yes. A plan-forge development incident established the need: 639 mock tests
+ 9-pass static review missed a real integration bug; the real-API smoke
caught it (see memory feedback_real_api_smoke_catches_mock_blindspot.md).
Mutation testing proves tests HAVE TEETH at the code level, not just that
tests exist. Without R2, forge continues to trust mock-only coverage while
shipping an anti-hallucination thesis that its own test suite does not
mutation-validate.

**Q2: Three real consumers**
- Forge itself: mutates forge's own code changed in each phase; a survivor
  is a real coverage gap in forge's own tests.
- plan-forge: already uses forge review pipeline; R2 adds a mutation layer
  to plan-forge's own CI.
- Any project running `forge review`: gets mutation coverage on their changed
  files without additional configuration.

**Q3: Do-nothing cost**
Doing nothing + documenting the gap: forge continues to ship the thesis
"verification grounding beats prompt-only self-claim" while its own commit
gate relies on test coverage that has not been mutation-validated. The
specific cost: forge's own CLAUDE.md says "until R2 and R3 land, forge is
partially implementing its own thesis." This self-contradiction remains in
the LIVE documentation until R2 ships.

**Q4: Barbell vs middle ground**
The barbell options are: (A) full mutation suite on every review (hours,
unusable), (B) no mutation (status quo, already documented as a gap).
Diff-scoped LOCAL sync is NOT the middle ground -- it IS the minimal viable
implementation of the correct design. Function-level mutation and CI-async
are incremental enhancements, not compromises.

## External Voices

Primary sources on mutation testing in practice and dissenting perspectives:

- Petrovic & Ivankovic (2018). State of Mutation Testing at Google. ICSE 2018.
  Documents Google's deployment of mutation testing to ~6,000 engineers who
  author or review code, affecting over 13,000 code authors (roughly 30% of
  all diffs). Key findings: (1) diff-scoped mutation (only mutate lines touched
  by the change) is the enabling design decision for making mutation practical
  in CI pipelines; (2) results are integrated into the code review workflow
  (shown to reviewers during review), not as a separate async gate; (3) engineer
  acceptance requires framing surviving mutants as actionable suggestions, not
  failures. This is the primary real-world reference for the diff-scoped design
  in D-02. Note: D-03's CI async pattern differs from Google's synchronous
  code-review integration -- forge's CI async is closer to a build-check
  pattern than a review-integration pattern.

- Jia & Harman (2011). An Analysis and Survey of the Development of Mutation
  Testing. IEEE Transactions on Software Engineering, Vol. 37, No. 5. The
  most-cited comprehensive survey on mutation testing. Documents the long-standing
  adoption challenge: equivalent mutants (syntactically mutated but semantically
  unchanged code) produce false positives, making mutation-adequate tests hard
  to achieve in practice.

However, a legitimate objection exists: the Jia & Harman survey and subsequent
literature document that equivalent mutants are a fundamental unsolved problem.
A surviving mutant may indicate a test weakness OR an equivalent mutant -- <!-- plan-forge: hedge-ok -->
the planner cannot distinguish them automatically. Critics of mutation as a
review signal argue this reduces actionability: if engineers routinely see
"MUTANT survivor" findings that turn out to be equivalent mutants, they will
dismiss mutation findings entirely. This is a real adoption risk; the design
mitigates it by classifying survivors as CONFIRMED findings (review-worthy,
not blocking commits) rather than hard CI failures.

Historical lesson from Petrovic & Ivankovic (2018): early internal pilots of
mutation at Google without diff-scoping produced runs that took too long to
be actionable; the retrospective lesson documented in the paper is that
selective (diff-scoped) mutation was the enabling design decision. The same
lesson applies here: forge R2 inherits this constraint as a first-principles
design requirement, not an optimization.

## References

Cross-plan reference audit:

- **Phase 1** (referenced lines 15, 82, 125, 130, 192):
  `.planning/phases/01-r1-commit-gate-r4-docs/` -- Phase 1 delivered
  gate_check.py, install_hooks.py (resolve_forge_path liveness fix target),
  R4 gate-philosophy docs. Phase 2 builds on Phase 1's state machine and
  install_hooks.py without modifying Phase 1's gate_check.py.

- **2026-05-25** (date reference in header/footer):
  Date this CONTEXT.md was gathered. Not a cross-plan reference.

---

*Phase: 02-r2-mutation-pipeline-step*
*Context gathered: 2026-05-25*
