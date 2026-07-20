---
phase: 25
reviewers: [kimi, deepseek, agy-gemini]
reviewed_at: 2026-06-17T13:00:00Z
plans_reviewed: [25-01-PLAN.md, 25-02-PLAN.md, 25-03-PLAN.md, 25-04-PLAN.md, 25-05-PLAN.md]
---

# Cross-AI Plan Review -- Phase 25: Cross-Repo Merge Review

Reviewers: Kimi K2 (aicc), DeepSeek V4 Pro (aicc), Gemini (agy).
Running inside Claude Code; claude self-review skipped for independence.

---

## Kimi K2 Review

**Overall risk: MEDIUM-HIGH**

### Summary

The plan set is well-scoped and follows the locked decisions cleanly: a new `cross_repo.py`
orchestrator, schema validation in `gate_check.py`, conditional CLI dispatch, and per-repo
cwd isolation for thread safety. Wave ordering is sensible, file conflicts are minimal, and
the fail-closed posture is mostly consistent.

The main gaps are in implementation detail depth (especially 25-03), verification of claimed
safety properties (symlink guard, real concurrent StateMachine isolation, zero-drift), and
timing of the same-stack check relative to the locked D-09 decision.

### Strengths

- Clean separation: cross_repo.py owns orchestration, gate_check.py owns config validation,
  cli.py owns dispatch, machine.py untouched
- Fail-closed defaults: invalid refs, remote URLs, duplicate labels, diff failures all raise
- R-04 addressed at the right layer via per-repo ephemeral cwd without modifying machine.py
- Wave dependencies are correct: 25-01/02 parallel, 25-03 blocks both, 25-04/05 parallel Wave 3
- Test coverage map: schema corpus, unit, integration, and zero-drift tests all planned
- Receipt naming collision (R-05) handled in validate_siblings() at config-load time

### Concerns

**HIGH: Symlink guard `_symlink_guard_passes()` is claimed but not called in 25-01**

Threat model T-25-04 claims D-03 is mitigated by `_symlink_guard_passes()` called in
validate_siblings(). But Plan 01 only checks string prefixes (https://, git@). A local
`repo:` path can still escape the gate.yaml directory via symlink.

**HIGH: 25-03 is under-specified for autonomous execution**

Critical integration points are hand-waved: ResolvedReview construction, source_hash
computation, registry loading, StubAutoFixer import path, advisory runner construction,
receipt rename/copy logic. The `autonomous: true` flag should likely be `false`.

**HIGH: CLI dispatch in 25-04 bypasses existing ForgeLock**

The cross-repo dispatch returns early before `with ForgeLock(lock_path):`. If the lock
prevents concurrent reviews in the same repo, cross-repo mode should acquire it too.

**MEDIUM: D-09 same-stack validation is deferred to run time, not config load**

25-01 passes `primary_language=None` in load_gate_config() and defers the check to 25-03
Step 2. The locked decision says "validate at gate config load time."

**MEDIUM: Zero-drift test (D-11) is weaker than the claim**

`test_single_repo_zero_drift` only verifies run_cross_repo() is not called. Does not
verify byte-identical output, receipt filenames, or that _run_hold_loop is truly unchanged.

**MEDIUM: `test_thread_isolation` proves path uniqueness, not real concurrency safety**

Writes sentinel files sequentially to two different directories. Does not run two
StateMachine instances concurrently to assert mutation-result.json is not corrupted.

**MEDIUM: 25-03 verdict merge does not specify D-12 grouped output format**

D-12 requires `=== [label] ===` sections in output, but 25-03 only emits a single
advisory warning string. The grouping logic needs to be explicit.

**LOW: Thread result/error dicts mutated without explicit synchronization** (CPython GIL)

**LOW: Plan 25-05 leaves receipt integration as possible xfail**

### Suggestions

1. Add `_symlink_guard_passes()` to validate_siblings() in Plan 01 Task 2
2. Clarify D-09 timing in CONTEXT.md: same-stack check at review-start, not config-load
3. Strengthen test_single_repo_zero_drift: mock _run_hold_loop and assert identical call args
4. Make 25-03 autonomous: false OR add concrete signatures for all five executor unknowns
5. Decide and document ForgeLock behavior for cross-repo mode
6. Add a true concurrency stress test for R-04 (two StateMachines, concurrent, assert isolation)
7. Specify D-12 output grouping function in 25-03

---

## DeepSeek V4 Pro Review

**Overall risk: MEDIUM**

### Summary

The five plans decompose Phase 25 cleanly across three waves with correct dependency ordering
and disjoint file sets per wave. Locked decisions are faithfully traced to implementation steps
and TDD structure is consistently applied. Three cross-cutting gaps: the symlink guard is
declared but wired into zero implementation steps; D-12 (verdict grouped by repo) and D-13
(finding attribution) have no assigned plan; Plan 03 has too many executor unknowns.

### Strengths

- Correct wave decomposition; no circular or missing dependencies
- Locked decisions traceable: D-04, D-05, D-06, D-07/D-08, D-09, D-10/D-11, D-14 to D-20
- D-11 zero-drift structurally enforced: conditional early-return, old path never reorganized
- R-04 tmpdir isolation has a dedicated structural test
- Threat models per-plan with STRIDE classification and supply-chain check

### Concerns

**HIGH: Symlink guard (D-03) absent from all implementation steps**

D-03 states path validation reuses `_symlink_guard_passes()`. Plan 02 threat model claims
T-25-04 mitigated by validate_siblings() -- but Plan 01 never calls the guard.
Impact: a sibling path like `../outside-project/repo` would pass validation.
Fix: Add `_symlink_guard_passes(resolved_path, gate_yaml_dir)` to Plan 01 Task 2.

**HIGH: D-12 (verdict grouped by repo) and D-13 (finding attribution) have no assigned plan**

Plan 03 verdict merge returns a single joint Verdict enum (PASS or FAIL), not grouped output.
D-12's `=== [label] ===` sections and D-13's per-repo attribution are locked decisions with
no corresponding implementation plan. They would silently drop.
Fix: Add Plan 06 for post-processing, or explicitly defer with a note in CONTEXT.md.

**MEDIUM: Plan 03 delegates too much discovery to the executor**

StubAutoFixer path, ResolvedReview construction, advisory runner imports, verdict_to_exit
availability, receipt write path -- all deferred to "read before writing." Any one wrong
could block Wave 2, cascading to Wave 3.
Fix: Run pre-flight discovery in planning; quote exact signatures in Plan 03 interface block.

**MEDIUM: `test_thread_isolation` is structural, not concurrent**

Proves path uniqueness (sequential writes) but not timing-specific race conditions.
Fix: Either make the test threaded or rename it with a comment about design-based safety.

**LOW: ref format check could reject valid git refs with dots in branch names**

A branch named `a..b` would be split into `a` and `.b` -- confusing errors.
Accept for v1; document in docstring.

**LOW: Plan 05's receipt test has a documented xfail escape**

If xfail is triggered, D-19 (receipt naming) has no automated coverage.

### Suggestions

1. Wire `_symlink_guard_passes()` into Plan 01 Task 2 after resolving sibling path
   against gate_yaml_dir; update T-25-04 disposition to reference the actual plan
2. Add Plan 06 or defer D-12/D-13 explicitly in 25-CONTEXT.md
3. Run Plan 03 pre-flight discovery in planning -- add exact signatures to interface block
4. Strengthen D-11 test: mock _run_hold_loop and assert called with same args; or add
   lazy-import check for run_cross_repo not imported in single-repo path
5. Add label-defaulting edge case test: sibling with `repo: ../primary` (no explicit label)
   should reject as reserved label (different code path from explicit `label: primary`)

---

## Gemini (agy) Review

**Overall risk: MEDIUM (critical gaps require resolution)**

### Summary

The plans are well-structured with solid architecture separation. However, critical gaps in
completeness (path validation, finding attribution) and risks (stdout race conditions during
threading) would cause violations of locked decisions if executed as-is.

### Strengths

- Clean separation: gate_check.py validates, cross_repo.py orchestrates, cli.py dispatches
- Wave isolation is appropriate; Plan 02 is mostly pristine
- Fail-closed behavior on diff acquisition failure

### Concerns

**HIGH: D-03 -- `_symlink_guard_passes()` omitted from Plan 01 execution steps**

Plan 02's threat model relies on validate_siblings() calling `_symlink_guard_passes()` but
Plan 01's behavior block completely omits it.
Fix: Plan 01 Task 2 must call `_symlink_guard_passes(Path(entry["repo"]).resolve(), gate_yaml_dir)`;
add corpus test for out-of-bounds path.

**HIGH: D-12 -- stdout interleaving in concurrent threads**

Multiple StateMachine instances writing to stdout concurrently produce interleaved output,
violating D-12's `=== [label] ===` grouping.
Fix: Plan 03 must capture per-thread output (OutputBuffer class) and print sequentially
with `=== [label] ===` headers after all threads join().

**HIGH: D-13 -- no plan explains how `[label]` prefix is parsed for attribution**

If attribution relies on parsing `[label]` prefix from LLM response, Plan 03 must include
logic to intercept findings (process receipts) and attribute them to repos.

**MEDIUM: D-09 DRY Violation**

Plan 03 Step 2 manually reimplements detect_toolchain loop instead of calling
`validate_siblings(siblings, gate_yaml_dir, primary_language=primary_language)`.

**MEDIUM: Vagueness in `ResolvedReview` construction**

Plan 03 Step 5 mentions "Build ResolvedReview with joint_diff" without specifying
how source_files, baseline_spec_repr are populated for multi-repo context.

### Suggestions

1. Plan 01: Add `_symlink_guard_passes` to interfaces, implementation, and corpus test
2. Plan 03: Implement OutputBuffer class or redirect per-thread UI logger; flush sequentially
   with `=== [label] ===` headers; attribute findings based on `[label]` prefix
3. Plan 03 Step 2: Remove manual detect_toolchain -- call validate_siblings() directly
4. Plan 03 Step 5: Specify exact ResolvedReview construction for multi-repo context

---

## Consensus Summary

Three reviewers (Kimi K2, DeepSeek V4 Pro, Gemini) -- full cross-AI panel.

### Agreed Strengths (2+ reviewers)

- Architecture separation is clean: cross_repo.py / gate_check.py / cli.py roles well-defined
- Wave ordering is correct with no dependency cycles
- Fail-closed defaults throughout (refs, remote URLs, duplicate labels, diff acquisition)
- R-04 tmpdir isolation is the right design approach

### Agreed Concerns -- HIGHEST PRIORITY (all 3 reviewers)

**[CRITICAL] D-03 symlink guard: `_symlink_guard_passes()` declared but not called**
All 3: HIGH
- Threat model T-25-04 claims mitigation but Plan 01 never calls the guard
- Fix: Add `_symlink_guard_passes(resolved_path, gate_yaml_dir)` to Plan 01 Task 2

**[CRITICAL] D-12 grouped verdict output and D-13 finding attribution have no plan**
DeepSeek+Gemini: HIGH; Kimi: MEDIUM
- `=== [label] ===` grouping and per-repo attribution are locked decisions with no plan
- Fix: Add Plan 06 for output post-processing, or explicitly defer in CONTEXT.md

**[HIGH] Plan 03 executor unknowns create Wave 2 blocking risk**
All 3: HIGH/MEDIUM
- StubAutoFixer, ResolvedReview, advisory runners -- all deferred to "read before writing"
- Fix: run pre-flight discovery in planning; add exact signatures to Plan 03 interface block

**[MEDIUM] D-11 zero-drift test is weaker than the `byte-for-byte identical` claim**
Kimi+DeepSeek: MEDIUM
- Test only checks siblings key is absent from config; doesn't verify output identity
- Fix: mock _run_hold_loop and assert called with identical args before/after Phase 25

### Divergent Views

- **ForgeLock bypass** (Kimi only, HIGH): cross-repo dispatch returns before lock acquisition
- **D-09 DRY violation** (Gemini only, MEDIUM): Plan 03 reimplements detect_toolchain
- **autonomous: true concern** (Kimi only): Plan 03 should be false until unknowns filled in
- **Dot-in-branch-name edge case** (DeepSeek only, LOW): `a..b` branch ref parsing ambiguity

### Required Before Execution (priority order)

1. [CRITICAL] Add `_symlink_guard_passes()` call to Plan 01 Task 2
2. [CRITICAL] Resolve D-12/D-13: add Plan 06 or defer explicitly in CONTEXT.md
3. [HIGH] Add pre-flight source signatures to Plan 03 interface block (drop autonomous:true)
4. [MEDIUM] Fix D-09 DRY: Plan 03 Step 2 calls validate_siblings(primary_language=...) not re-detect
5. [MEDIUM] Check ForgeLock: document whether run_cross_repo() needs to acquire it
6. [LOW] Strengthen D-11 test: mock _run_hold_loop call-args assertion
