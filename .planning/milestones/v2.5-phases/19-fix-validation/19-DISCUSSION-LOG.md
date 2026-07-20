# Phase 19: Fix Validation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-06-11
**Phase:** 19-Fix Validation
**Areas discussed:** Trigger, Reversion granularity, Overfit handling, Waiver
channel, Pipeline position, Test identification, Eval SKIPPED scoring, Waiver
abuse guard

---

## Trigger (when FIXVAL runs)

| Option | Description | Selected |
|--------|-------------|----------|
| Structural signal | Run iff diff changes BOTH non-test source and a test; else SKIPPED+reason; message-independent | [x] |
| Intent signal | Only when commit marks `fix:`/type; feature diffs skip | |
| You decide | Defer to Claude (= structural) | |

**User's choice:** Structural signal (recommended).
**Notes:** Hardest signal to game; aligns with never-silent-skip. The mechanism
generalizes to any code+test diff, not only bug fixes; SC1's bug-fix fixture is
the named eval case, not a narrowing of the trigger.

---

## Reversion granularity (what proves RED)

| Option | Description | Selected |
|--------|-------------|----------|
| Single synthesized mutant | All non-test hunks reverted as one mutant; new test goes RED = pass; matches "single reversion mutant"; 1 extra run | [x] |
| Per-hunk (any) | Each non-test hunk reverted independently; any makes the new test RED = pass; stronger, N runs | |
| Per-hunk (all) | Every hunk must be caught; strongest; over-blocks multi-hunk fixes; conflicts with "single mutant" | |
| You decide | Defer to Claude (= single mutant; per-hunk recorded as future enhancement) | |

**User's choice:** Single synthesized mutant (recommended).
**Notes:** Per-hunk reversion documented as a future enhancement, not v1.

---

## Overfit handling (STING transform failure)

| Option | Description | Selected |
|--------|-------------|----------|
| Advisory, no block | Overfit is a test-quality signal, not hollow; transforms can false-positive; only "revert did not turn RED" blocks | [x] |
| Block (= hollow) | Overfit also blocks; strongest; transform false-positives become false-blocks; tension with "advisory never blocks" | |
| You decide | Defer to Claude (= advisory) | |

**User's choice:** Advisory, no block (recommended).
**Notes:** SC4 scopes the block to hollow tests; overfit is a different failure
mode handled on the advisory channel.

---

## Waiver channel (nondeterministic bugs)

| Option | Description | Selected |
|--------|-------------|----------|
| Commit trailer | `Fixval-Waiver: <reason>`; scoped to commit; git-log visible; does not rot; avoids gate.yaml | [x] |
| gate.yaml entry | path/test scoped; persistent/reusable; mixes into the untrusted-config surface; rots | |
| Test magic comment | `# fixval: nondeterministic <reason>`; travels with test; persistent; copy-paste propagation risk | |
| You decide | Defer to Claude (= commit trailer) | |

**User's choice:** Commit trailer (recommended).
**Notes:** Avoids the gate.yaml attack surface hardened in Phase 17 (SEC-01).
forge emits an advisory recording the waiver and reason (never silent-skip).

---

## Pipeline position

| Option | Description | Selected |
|--------|-------------|----------|
| Otherwise-GREEN only | After 3-cycle static convergence, co-located with R2/L2, before verdict; never spends revert cost on a failing diff | [x] |
| With R2/L2 regardless | Runs with mutation regardless of static verdict; more info on failing diffs but wastes cost | |
| Fail-fast front | Runs before static cycles; earliest hollow signal but highest total cost on the common non-fix case | |
| You decide | Defer to Claude (= otherwise-GREEN) | |

**User's choice:** Otherwise-GREEN only (recommended; accepted via "continue").
**Notes:** FIXVAL is the last honesty gate before the verdict; respects v2.3
diff-size tiering relief.

---

## Test identification (which tests judged RED; no-test behavior)

| Option | Description | Selected |
|--------|-------------|----------|
| Diff's added/modified tests | Run the diff's added-or-modified tests; any RED on revert = pass; no meaningful new test -> SKIPPED (FIXVAL does not mandate tests) | [x] |
| Strict new functions only | Only brand-new test functions count; misses strengthened-existing-test fixes | |
| Full-suite pass/fail diff | Run whole suite before/after revert, diff the failing set; most robust, most expensive | |
| You decide | Defer to Claude (= diff's added/modified tests) | |

**User's choice:** Diff's added/modified tests (recommended; accepted via "continue").
**Notes:** "No test" is a skip, not a block; mandating a test belongs to R3/coverage.

---

## Eval SKIPPED scoring

| Option | Description | Selected |
|--------|-------------|----------|
| Own bucket | SKIPPED (no pairing / waived) not counted as caught or missed; false-green rate over applicable entries only | [x] |
| Count as miss | Conservative; counts skips against forge; punishes legitimate non-applicability | |
| You decide | Defer to Claude (= own bucket) | |

**User's choice:** Own bucket (recommended; accepted via "continue").
**Notes:** Honest denominator for the "honest green" metric; aligns with existing
skip-taxonomy.

---

## Waiver abuse guard

| Option | Description | Selected |
|--------|-------------|----------|
| Advisory-only, no limit | Every waiver emits a visible, attributable advisory; no count cap; visibility is the constraint | [x] |
| Threshold alert | Warn if >N waivers in a window; more mechanism; YAGNI for v1 | |
| You decide | Defer to Claude (= advisory-only) | |

**User's choice:** Advisory-only, no limit (recommended; accepted via "continue").
**Notes:** Consistent with "advisory never blocks".

---

## Claude's Discretion

Deferred to research / planner (recorded in CONTEXT.md):
- STING transform catalog -- start with the cheapest reliable behavior-preserving
  transform; specifics from the STING paper.
- Mutation-engine reuse nuance -- reuse the run/baseline/survivor harness; the
  revert-mutant GENERATOR is new (not mutmut-synthesized).
- "New test" run mechanics -- test-id selection to run only the diff's tests.
- Block-message content -- name the failed test, show the reverted hunk, print the
  `Fixval-Waiver:` escape hatch.

## Deferred Ideas

- Per-hunk reversion (future enhancement beyond v1's single mutant).
- Auto-detection of nondeterministic / flaky tests (out of scope; waiver is the
  sole opt-out).
- Waiver threshold / alerting (v1 is advisory-only).
- Requiring a test for every code change (belongs to R3 / coverage).
