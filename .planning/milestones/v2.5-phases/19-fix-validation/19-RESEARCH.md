# Phase 19: Fix Validation - Research

**Researched:** 2026-06-11
**Phase goal:** Bug-fix diffs prove their tests are not hollow by demonstrating RED on revert and GREEN on restore

## Prior Art

### Reverse Mutation Testing (RMT)

Traditional mutation testing mutates CODE and checks if tests catch it. RMT
inverts this: mutate the TESTS and check if they still pass. A test that passes
after its assertion is flipped was never meaningfully asserting anything.

- llanni/reverse-mutation-testing (Java PoC): scans *Test.java files, creates
  _Mutated.java copies with flipped assertions, runs them, reports tests that
  still pass as suspicious.
- The LinkedIn article "Don't trust a test you've never seen fail" captures the
  core insight: a test that has never been seen to fail is unverified.

**Relevance to forge:** Phase 19 is not RMT (we do not mutate tests). We mutate
the CODE by reverting the fix, and check that the test goes RED. This is closer
to standard mutation testing but with a specific mutant: the actual pre-fix
state of the code. RMT is complementary (D-03 STING overfit guard is closer to
RMT territory).

### AssertFlip (arXiv 2507.17542)

Pass-invert method: generate a passing test on buggy code, then flip assertions
so it fails on buggy and passes on fixed. The key insight is that generating a
PASSING test is easier for LLMs than generating a FAILING test from scratch.

**Relevance:** Not directly applicable (we have the dev's real test, not an
LLM-generated one), but the pass/fail duality is exactly FIXVAL's thesis: the
test must fail on the buggy version (revert) and pass on the fixed version.

### Rotten Green Tests (INRIA hal-02002346v2)

A rotten test passes and contains assertions, but at least one assertion is not
executed. Detected via static analysis + dynamic monitoring of assertion call
sites. Categories: missed skip (guard prevents execution), context-dependent
assertion, fully rotten.

**Relevance:** FIXVAL catches a different problem (test passes on buggy code,
not just unexecuted assertions), but the rotten-test taxonomy informs the overfit
guard (D-03). A test that passes both before and after the fix might have an
unexecuted assertion path (rotten), not just a tautological assertion (hollow).

### Poracle (TOSEM 2023)

Semi-automatic patch classification using "preservation conditions": when input
satisfies a condition, behavior should be preserved between buggy and patched
versions. Uses differential fuzzing to find violating inputs.

**Relevance:** Poracle targets automated program repair (APR) overfitting.
FIXVAL's overfit guard (D-03) is a simpler version of this: one
behavior-preserving transform, advisory only. Poracle's full differential
fuzzing is a potential future enhancement beyond v1.

### quelltest (PyPI)

Auto-generates killing tests for survived mutants from mutmut/Stryker. Uses
rule-based generators for 9 operator types, verifies each test against the live
mutant in isolation, injects via libcst.

**Relevance:** quelltest's verify step (run test against live mutant in
isolation) is exactly what FIXVAL does: run the test against the reverted code.
Its architecture (adapters -> core/analyzer -> core/verifier -> core/writer)
validates the component split.

## Technical Analysis

### Revert Mutant Generation (D-02 implementation)

The existing `mutation.py:run_mutation` uses mutmut to generate synthetic
mutants. FIXVAL needs a different mutant: the actual pre-fix state of the code.

**Mechanism: derive revert patch from diff_text via unidiff**

1. Parse the diff to classify files as test vs non-test (D-01 structural trigger
   already needs this classification from `diff.py`/`delta.py`).
2. For the revert mutant: parse diff_text with `unidiff.PatchSet`, filter to
   non-test files only, serialize back via `str(filtered_patchset)`, write to
   temp file. Apply `git apply -R <tempfile>`. Using the same diff_text as
   classification guarantees source agreement (avoids staged-vs-unstaged
   mismatch if `git diff --cached` were used instead).
3. Run the new/modified tests. At least one must FAIL (RED).
4. Restore: `git apply <tempfile>` (forward re-apply of the same patch).
   Never use `git checkout --` (destroys unstaged changes).

**Why not `git revert`:** `git revert` operates on commits. FIXVAL operates on
uncommitted staged changes (pre-commit gate). The diff is in the working tree /
index, not yet committed. `git apply -R` on the non-test portion is the right
mechanism.

**Integration with run_mutation:**

`run_mutation` signature: `run_mutation(diff_files, baseline_cmd, timeout, cwd)`.
FIXVAL cannot directly reuse this because:
- `run_mutation` delegates mutant generation to mutmut (external tool).
- FIXVAL generates exactly ONE mutant (the revert) programmatically.
- The baseline/survivor flow IS reusable: `_run_baseline_guard` (flaky check)
  and the `Survivor` dataclass.

**Proposed approach:** Create `run_fixval(diff, test_cmd, cwd)` that:
1. Calls `_run_baseline_guard` to confirm tests pass (GREEN baseline).
2. Applies the revert (inverse patch on non-test files).
3. Runs the test command scoped to new/modified tests.
4. If any test FAILs -> FIXVAL PASS (test is not hollow).
5. If all tests PASS -> FIXVAL FAIL (hollow test detected).
6. Restores the original state.

This reuses the baseline guard but not the mutmut-specific flow.

### Test Identification (D-07 implementation)

**Which tests to run on the reverted code?**

From the diff, extract files matching test patterns:
- `tests/test_*.py`, `*_test.py`, `test_*.py` (Python)
- `*.test.ts`, `*.spec.ts` (TypeScript)
- `*_test.go` (Go)

Within those files, identify NEW or MODIFIED test functions:
- Parse the diff hunks for added lines containing `def test_` / `it(` / `func Test`.
- Or simpler: run ALL tests in the modified test files against the reverted code.
  If ANY fail, the diff has at least one non-hollow test.

The simpler approach (run all tests in modified test files) is safer:
- Avoids fragile test-function-name parsing.
- A modified test file likely has tests related to the fix.
- Cost: one extra test run of a subset of the suite (not the full suite).

**pytest scoping:** `pytest <test_file> -x` runs only tests in the changed file.
Combined with `-k <test_name>` if finer scoping is needed.

### STING Overfit Guard (D-03 implementation)

STING (arXiv 2604.01518) proposes behavior-preserving transforms to detect
overfitting tests. A test that breaks on a behavior-preserving transform is
overfitting to implementation details rather than testing behavior.

**v1 minimal transform catalog (advisory only):**

1. **Variable rename:** Rename a local variable in the fixed code. If the test
   breaks, it is asserting on variable names (overfitting).
2. **Whitespace/comment transform:** Add or remove comments/whitespace. If the
   test breaks, it is doing textual matching (overfitting).
3. **Equivalent refactor:** Extract a helper function that does the same thing.
   If the test breaks, it is coupled to the implementation structure.

For v1, a single transform is sufficient (D-03 says "at least one"). The
variable-rename transform is the cheapest and most reliable: it never changes
behavior, and if the test breaks, it is definitively overfitting.

**Implementation:** LLM-based transform in the forge pipeline (the forge LLM
generates the renamed version, applies it, runs the test, reports advisory if
the test breaks). No external tool needed.

### Pipeline Integration (D-06)

FIXVAL runs in `_finalize_local_terminal` of `machine.py` (post-convergence,
called from `_run_local` after `_fixpoint_reached` returns True).

Current post-convergence flow in machine.py:
```
_run_local -> _fixpoint_reached -> _finalize_local_terminal -> set PASS
```

FIXVAL addition (inserted before the PASS verdict):
```
_run_local -> _fixpoint_reached -> _finalize_local_terminal:
  classify -> if candidate: run_fixval (sync) -> BLOCK or proceed to PASS
```

FIXVAL is synchronous (one revert mutant, one test run) unlike R2 mutation
(async, many mutants). It runs AFTER convergence, BEFORE the verdict (D-06).
FIXVAL only runs in LOCAL mode; CI mode (_run_ci) does not call
_finalize_local_terminal.

### Eval Integration (D-08)

`eval/runner.py` already has FIXVAL in `DETERMINISTIC_TAGS` (runs=1). The
`AxisHook` seam (`register_axis_hook`) is the integration point.

The FIXVAL hook:
- `pre_review`: inject the revert mutant.
- `post_review`: check if the corpus diff's test went RED on revert.
- Scoring: BUG-P12-01 expected_verdict=HOLD; if FIXVAL does not block, that is
  a false-green (the bug's test did not detect the revert).

### Block Message (from D-04 discretion)

When FIXVAL blocks:
```
FIXVAL: Test(s) did not fail when the fix was reverted.

  Reverted files:
    src/code_forge/foo.py (hunks 1-3)

  Tests that should have failed but passed:
    tests/test_foo.py::test_bar
    tests/test_foo.py::test_baz

  This means the test passes on both the fixed and unfixed code -- it does
  not actually verify the fix.

  To waive (nondeterministic bug), at pre-commit time use the env var:
    FIXVAL_WAIVER="<reason this bug cannot be deterministically tested>" git commit ...
  and also add the trailer for the permanent git-log record:
    Fixval-Waiver: <reason this bug cannot be deterministically tested>
```

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Revert breaks unrelated code (import cycles, shared fixtures) | Run only the diff's own test files, not the full suite |
| Test uses shared state from a prior test (ordering dependency) | `pytest -x <file>` runs the file in isolation |
| Multi-file fix with circular dependencies | Single revert of all non-test hunks together (D-02) avoids partial states |
| LLM-based STING transform hallucinates a behavior change | Advisory only (D-03); false-positive = noise, not a block |
| Flaky test passes on revert by chance | Baseline guard runs 3x; if baseline is flaky, FIXVAL skips |

## Validation Architecture

### SC mapping

| SC | What it proves | How to test |
|----|---------------|-------------|
| SC1 | FIXVAL blocks BUG-P12-01 (revert = test stays green = hollow) | eval corpus run with FIXVAL hook |
| SC2 | Non-fix diff (no test file) records SKIPPED | Unit test: diff with only .py, no test |
| SC3 | Fixval-Waiver trailer bypasses FIXVAL with advisory | Unit test: commit message contains trailer |
| SC4 | STING overfit guard emits advisory, not block | Unit test: overfit transform + assertion |
| SC5 | Eval scorecard records FIXVAL axis | Integration test: runner with FIXVAL hook |

## Summary

FIXVAL is a single-mutant validation gate that reuses forge's existing mutation
infrastructure pattern (baseline guard, survivor model) but with a
programmatically-generated revert mutant instead of mutmut-synthesized mutants.
The implementation splits into:

1. **Structural trigger** (`diff.py` / `delta.py`): classify test vs non-test files.
2. **Revert mutant** (`fixval.py`): apply inverse patch on non-test files.
3. **Test runner** (`fixval.py`): run scoped tests, check for RED.
4. **Pipeline gate** (`machine.py`): co-locate with L2, block on hollow.
5. **Overfit advisory** (`fixval.py` + `advisory.py`): STING transform, advisory.
6. **Waiver** (`fixval.py`): parse commit trailer, record advisory.
7. **Eval hook** (`eval/runner.py`): score FIXVAL axis on corpus.

---

*Phase: 19-Fix Validation*
*Researched: 2026-06-11*
