# Phase 19: Fix Validation - Context

**Gathered:** 2026-06-11
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 19 delivers REVIEW-FIXVAL-01: a fix-validation gate that proves a diff's
new tests are not hollow. It reverts the diff's non-test change(s) and asserts the
new test goes RED, then restores and asserts GREEN. FIXVAL CAN BLOCK -- it gates
only the diff's own hollow test (advisory=false). It reuses the R2 mutation
harness (a single reversion mutant) plus an overfit guard (a behavior-preserving
transform, STING-style). A written waiver path exists for nondeterministic bugs
(explicit opt-out, never silent-skip). The eval scorecard records the FIXVAL axis
(false-green rate on the BUG-P12-01 fixture).

In scope: revert-RED / restore-GREEN gate; single reversion mutant via the R2
engine; overfit guard (at least one behavior-preserving transform); commit-trailer
waiver; eval FIXVAL-axis scoring.

Out of scope (other axes / future work): mandating a test for every code change
(that is R3 / coverage); auto-detecting nondeterministic or flaky tests;
runtime-contract checks (Phase 20, RUNTIME axis); promoting FIXVAL beyond the
diff's own test; per-hunk reversion.

</domain>

<decisions>
## Implementation Decisions

### Trigger (when FIXVAL runs)
- **D-01:** Structural signal only. A diff is a FIXVAL candidate if and only if it
  changes BOTH non-test source and a test file. The commit message / `fix:` prefix
  is NOT used (gameable, and misses real fixes that do not use it). When the
  pairing is absent, record SKIPPED with a reason -- never silent. Rationale: this
  matches forge's never-silent-skip thesis and is the hardest signal to game.
- The mechanism generalizes to any code+test diff, not only "bug fixes" -- the
  structural trigger intentionally covers them all. SC1's bug-fix fixture is the
  named eval case, not a narrowing of the trigger.

### Reversion granularity (what proves RED)
- **D-02:** Single synthesized reversion mutant. All non-test hunks are reverted
  together as ONE mutant; the new test going RED passes the gate. This matches
  REQUIREMENTS "single reversion mutant" literally, costs one extra test run, and
  yields the fewest false-blocks on multi-hunk fixes. Per-hunk reversion (stronger,
  N runs) is a documented FUTURE enhancement, not v1.

### Overfit guard teeth (STING)
- **D-03:** Overfit is ADVISORY, never blocking. If the new test fails a
  behavior-preserving transform (overfit / brittle), forge emits an advisory -- it
  does NOT block. Only "the revert did not turn the new test RED" (hollow) blocks.
  Rationale: SC4 scopes the block to hollow tests; behavior-preserving transforms
  can false-positive; blocking on overfit would false-block legitimate stylized
  assertions and collides with the founding "advisory never blocks" principle.

### Waiver (nondeterministic bugs)
- **D-04 (amended 2026-06-11, HIGH-2):** Dual-channel waiver. Channel 1 (primary
  at pre-commit): `FIXVAL_WAIVER=<reason>` environment variable -- the in-flight
  commit message is not yet readable when the pre-commit gate runs, so the env
  var is the channel that actually works there. Channel 2: `Fixval-Waiver:
  <reason>` commit trailer -- works at commit-msg stage, post-commit, and CI, and
  is the permanent git-log record. Env takes precedence when both are present.
  Tradeoff: an env-only waiver leaves no git-history trace; the advisory records
  the reason AND the channel used, and the block message tells users to also add
  the trailer for the permanent record. Scoped to the commit either way (not a
  persistent repo property). Explicitly NOT a gate.yaml entry -- gate.yaml is the
  untrusted-config attack surface hardened in Phase 17 (SEC-01); waivers must not
  mix into it. forge emits an advisory recording the waiver and its reason (never
  silent-skip). (Original D-04 was trailer-only; amended after verification found
  the trailer is unreadable at pre-commit time.)
- **D-05:** Waiver abuse guard (v1): pure advisory record, no count limit. Every
  waiver surfaces in the verdict (visible, attributable). Visibility is the
  constraint; a threshold or alert is YAGNI for v1.

### Pipeline position
- **D-06:** FIXVAL runs only on otherwise-GREEN diffs -- after the 3-cycle static
  review converges clean, co-located with the R2 / L2 mutation phase, before the
  verdict. A diff already failing static review is not worth the revert cost; it
  re-enters FIXVAL after the author fixes it. FIXVAL is the last honesty gate
  before the verdict. (Respects the v2.3 diff-size tiering relief: smaller diffs
  run fewer cycles.)

### Test identification
- **D-07:** Run the diff's added-or-modified tests; at least one must go RED on
  revert to pass. If the diff has no meaningful new or strengthened test, record
  SKIPPED -- FIXVAL has nothing to validate. FIXVAL does NOT mandate a test (that
  is R3 / coverage's job); "no test" is a skip, not a block.

### Eval SKIPPED scoring
- **D-08:** Legitimate SKIPPED (no test+code pairing, or waived) is its own bucket
  -- not counted as caught or missed. The false-green rate is computed only over
  applicable entries, so the "honest green" metric keeps an honest denominator.
  Aligns with the existing eval skip-taxonomy (infra failures score SKIPPED).

### Claude's Discretion (deferred to research / planner)
- STING transform catalog: start with the cheapest reliable behavior-preserving
  transform (the result is advisory regardless); select specifics from the STING
  paper.
- Mutation-engine reuse nuance: "reuse R2" means reuse the run / baseline /
  survivor harness in `mutation.py`, but the mutant GENERATOR is new (revert the
  actual non-test hunk, not a mutmut-synthesized mutant). Research must confirm how
  `run_mutation` accommodates an externally-supplied revert mutant.
- "New test" run mechanics: how to execute only the diff's added/changed tests in
  isolation (test-id selection) is a planner detail.
- Block-message content: name the test that failed to go RED, show the reverted
  hunk, and print the exact `Fixval-Waiver:` trailer as the escape hatch when the
  bug is genuinely nondeterministic. Minimal-but-actionable; planner refines.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements and roadmap
- `.planning/REQUIREMENTS.md` -- REVIEW-FIXVAL-01 (P0, CAN BLOCK): revert-RED /
  restore-GREEN, R2 single reversion mutant, STING overfit guard, written waiver,
  success criteria 1-4.
- `.planning/ROADMAP.md` -- Phase 19 Goal + SC 1-5 (FIXVAL blocks only the diff's
  own hollow test; eval records FIXVAL false-green on BUG-P12-01).
- `.planning/PROJECT.md` -- founding principles: advisory axes NEVER block; only
  FIXVAL and TRUST may block; eval corpus = real bugs only.

### Code to reuse or extend (full relative paths)
- `src/code_forge/mutation.py` -- R2 engine: `run_mutation(diff_files, ...)`,
  `parse_mutmut_results`, `_run_baseline_guard`, `Survivor`. FIXVAL reuses the
  run / baseline / survivor harness with a new revert-mutant generator.
- `src/code_forge/machine.py` -- pipeline: `_run_l2_phase` (mutation, where FIXVAL
  co-locates), the `_run_local` survivor -> FAIL path, and advisory-axis wiring.
  FIXVAL is a blocking axis, so it gates like the mutation FAIL path, not the
  advisory path.
- `src/code_forge/advisory.py` -- `AxisRunner` Protocol (`is_advisory()` returns
  False for FIXVAL), `AdvisoryFinding` (used for the overfit advisory and the
  waiver record).
- `src/code_forge/eval/runner.py` -- `DETERMINISTIC_TAGS` already includes
  "FIXVAL"; the `AxisHook` seam (`pre_review` / `post_review`,
  `register_axis_hook`) is the SC5 integration point; `_default_runs` returns 1
  for FIXVAL.
- `tests/eval/corpus/corpus.yaml` -- `BUG-P12-01` (axis_tags: [FIXVAL]) and
  `ttl_class` (axis_tags: [RUNTIME, FIXVAL]); both expected_verdict HOLD.
- `src/code_forge/diff.py`, `src/code_forge/delta.py` -- changed-file
  classification feeding the structural trigger (D-01).
- `src/code_forge/verdict.py` -- the Verdict enum the FIXVAL block uses.

### External research (papers named in REQUIREMENTS)
- STING (arXiv 2604.01518) -- behavior-preserving transforms / overfit detection.
- Cleverest (arXiv 2501.11086) -- overfit test evidence.
  (No local copies; the researcher fetches these as needed.)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `mutation.run_mutation` + baseline / survivor machinery: FIXVAL's revert mutant
  rides the same run + baseline + survivor flow; only the mutant generator differs.
- `advisory.AxisRunner` / `AdvisoryFinding`: FIXVAL implements AxisRunner with
  `is_advisory()` = False; the overfit signal and the waiver record are
  AdvisoryFindings.
- `eval/runner.AxisHook` seam + `DETERMINISTIC_TAGS` already carrying "FIXVAL" +
  corpus entries: SC5 scoring is a wiring task, the seam already exists.

### Established Patterns
- Blocking gate = the mutation / L2 model: survivors -> FAIL after the convergence
  round (`machine.py` `_run_local`). FIXVAL "hollow test survives revert" mirrors
  "mutant survives".
- Advisory axes run once after convergence and never block (`advisory.py` header).
  FIXVAL's overfit and waiver outputs use this advisory channel; the RED / GREEN
  verdict uses the blocking channel.
- never-silent-skip: SKIPPED is always recorded with a reason (eval skip-taxonomy
  precedent).

### Integration Points
- `machine.py` L2 / verdict path: add the FIXVAL gate co-located with mutation,
  gated to otherwise-GREEN diffs (D-06).
- `eval/runner.py` AxisHook: register a FIXVAL hook to score the axis (D-08).
- `diff.py` / `delta.py` classification: the structural trigger (D-01).

</code_context>

<specifics>
## Specific Ideas

- The waiver trailer key is literally `Fixval-Waiver:` (one waiver per commit,
  reason required).
- The named v1 eval fixture is `BUG-P12-01` (3 tests green on buggy HEAD);
  `ttl_class` carries a secondary FIXVAL tag.

</specifics>

<deferred>
## Deferred Ideas

- Per-hunk reversion (each non-test hunk reverted independently; stronger coverage
  proof) -- a future enhancement beyond v1's single-mutant decision (D-02).
- Auto-detection of nondeterministic / flaky tests (re-run N times) -- out of
  scope; v1 relies solely on the explicit waiver (D-04). A new capability if ever
  wanted.
- Waiver threshold or alerting -- deferred; v1 is advisory-only (D-05).
- Requiring a test for every code change -- belongs to R3 / coverage, not FIXVAL.

None of these were in the phase scope; captured so they are not lost.

</deferred>

---

*Phase: 19-Fix Validation*
*Context gathered: 2026-06-11*
