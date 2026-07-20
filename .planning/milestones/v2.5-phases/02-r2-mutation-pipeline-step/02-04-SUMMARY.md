---
phase: 02-r2-mutation-pipeline-step
plan: 04
subsystem: dogfood-verification
tags: [mutation, dogfood, host-verification]
dependency_graph:
  requires: [02-01, 02-02, 02-03]
  provides: [phase-2-complete]
  affects: []
tech_stack:
  added: []
  patterns: []
key_files:
  created: []
  modified: []
decisions:
  - mutmut not installed (soft dependency per D-05)
  - Dogfood deferred until mutmut installation
  - All exit criteria except EC-7 ready for host verification
metrics:
  duration_minutes: 0
  completed_at: "2026-05-26T01:35:00Z"
  tasks_completed: 1
  files_modified: 0
  commits: 0
---

# Phase 02 Plan 04: Mutation Dogfood and Host Verification Summary

mutmut not installed (soft dependency) - dogfood deferred; host verification checkpoint reached.

## What Was Built

**Objective:** Mutation dogfood on forge's own Phase 2 code and host verification checkpoint.

**One-liner:** Phase 2 delivers mutation testing as a review-pipeline step with LOCAL sync, CI async, and consecutive-survivor guard.

### Completed Tasks

| Task | Description | Status | Notes |
|------|-------------|--------|-------|
| 1 | Mutation dogfood on forge's Phase 2 code | SKIPPED | mutmut not installed (soft dep per D-05) |
| 2 | Host verification checkpoint | READY | All ECs except EC-7 ready for verification |

### Phase 2 Deliverables

Phase 2 shipped 7 core components across 3 plans:

**Plan 02-01 (Mutation Foundation):**
- Extended `StateFinding.source` to include `"MUTANT"` literal
- Created `mutation.py` with mutmut subprocess integration
- Implemented 3x flaky guard (baseline must pass 3 times before mutation)
- Added `build_l2_runner` factory with soft-dependency check

**Plan 02-02 (L2 Runner Integration):**
- Wired `l2_runner` into StateMachine as injectable callable (DI pattern)
- Added `_run_l2_phase()` method in state machine round orchestration
- Implemented MUTANT autofix filter (source="MUTANT" skips autofix loop)
- Added `consecutive_survivor_rounds` counter (LOCAL-only, resets on clean round, 3 -> FAIL)
- Implemented CI async mutation via daemon thread + mutation-result.json state file
- Fixed `resolve_forge_path` liveness check (--version with 1s timeout, fallback to sys.executable)

**Plan 02-03 (Tests):**
- 15 unit tests for mutation.py (parser + run_mutation scenarios)
- 11 integration tests for L2 wiring (machine_l2.py)
- 4 liveness tests for resolve_forge_path
- 2 factory tests for build_l2_runner
- 2 state schema tests for consecutive_survivor_rounds serialization
- Bug-inject teeth tests (EC-6): toothless l2_runner -> 3 rounds -> FAIL

Total test count: 635 (601 existing + 34 new)

## Task 1: Mutation Dogfood (SKIPPED)

**Status:** SKIPPED - mutmut not installed

mutmut is a soft dependency per D-05. Plan action step 1 requires `pip install "mutmut>=3.5.0"`, but installing external packages during execution is a package-legitimacy checkpoint (deviation rules: package manager installs are NOT auto-fixable).

**Rationale for skip:**
- mutmut is correctly implemented as a soft dependency (shutil.which check in build_l2_runner)
- When mutmut is missing, forge returns MUTATION_SKIPPED finding (disposition=DISMISSED)
- This behavior is already tested (test_factories.py test 16)
- The plan note acknowledges: "If mutmut is unavailable (cannot install), document the skip and note it as a follow-up"

**Follow-up required:**
After host installs mutmut (`pip install "mutmut>=3.5.0"`), run dogfood manually:
```bash
cd /home/houminxi/code/forge/.worktrees/phase-02
mutmut run --paths-to-mutate src/forge/mutation.py --tests-dir tests/
mutmut results
```

Expected outcome: zero survivors (all mutants killed by tests/test_mutation.py).

If survivors exist:
- Examine each with `mutmut show <ID>`
- Classify as: equivalent mutant (semantically identical) or coverage gap (add test to kill)

## Task 2: Host Verification Checkpoint (READY)

**Branch:** feat-02-r2-mutation
**Commit SHA:** bf13d04
**Test Count:** 635 tests (601 existing + 34 new for Phase 2)
**merge-base main feat-02-r2-mutation:** 6672bf4

### Diff Stat (main...feat-02-r2-mutation)

```
src/forge/factories.py      |  41 +++++
src/forge/install_hooks.py  |  37 +++-
src/forge/machine.py        | 275 ++++++++++++++++++++++++++++-
src/forge/mutation.py       | 272 +++++++++++++++++++++++++++++
src/forge/state.py          |  10 +-
tests/test_factories.py     |  42 +++++
tests/test_install_hooks.py |  79 +++++++++
tests/test_machine_l2.py    | 413 ++++++++++++++++++++++++++++++++++++++++++++
tests/test_mutation.py      | 222 ++++++++++++++++++++++++
tests/test_state_schema.py  |  37 ++++
10 files changed, 1419 insertions(+), 9 deletions(-)
```

**Files Created:**
- src/forge/mutation.py (272 lines)
- tests/test_mutation.py (222 lines)
- tests/test_machine_l2.py (413 lines)

**Files Modified:**
- src/forge/state.py (StateFinding.source extended to include MUTANT)
- src/forge/machine.py (l2_runner wiring, consecutive_survivor_rounds, CI async)
- src/forge/install_hooks.py (resolve_forge_path --version liveness check)
- src/forge/factories.py (build_l2_runner factory)
- tests/test_factories.py (build_l2_runner tests)
- tests/test_install_hooks.py (liveness tests)
- tests/test_state_schema.py (consecutive_survivor_rounds serialization tests)

### Exit Criteria Status

**EC-1 (Step 0 clean):** READY
```bash
cd /home/houminxi/code/forge/.worktrees/phase-02
ruff check src/forge/mutation.py src/forge/machine.py src/forge/state.py src/forge/factories.py src/forge/install_hooks.py
# Expected: All checks passed!

git diff HEAD --diff-filter=AM -U0 | grep '^+' | grep -P '[^\x00-\x7F]' && echo "FAIL" || echo "PASS: no non-ASCII"
# Expected: PASS: no non-ASCII
```

**EC-2 (source="MUTANT" + l2_runner wired):** READY
```bash
cd /home/houminxi/code/forge/.worktrees/phase-02
PYTHONPATH=src python3 -c "from forge.state import StateFinding; sf = StateFinding(id='t', fingerprint='t', source='MUTANT', disposition='CONFIRMED', file='x', line_range=[], description='t'); print('MUTANT source OK')"
# Expected: MUTANT source OK

PYTHONPATH=src pytest tests/test_machine_l2.py -q
# Expected: 11 passed
```

**EC-3 (LOCAL sync + CI async):** READY
```bash
cd /home/houminxi/code/forge/.worktrees/phase-02
PYTHONPATH=src pytest tests/test_machine_l2.py -k "survivor" -q
# Expected: tests for consecutive_survivor_rounds pass

PYTHONPATH=src pytest tests/test_machine_l2.py -k "ci" -q
# Expected: tests for CI async mutation pass
```

**EC-4 (MUTATION_SKIPPED + flaky guard):** READY
```bash
cd /home/houminxi/code/forge/.worktrees/phase-02
PYTHONPATH=src pytest tests/test_mutation.py -k "skip or flaky or unsupported" -q
# Expected: MUTATION_SKIPPED tests pass
```

**EC-5 (full suite green):** READY
```bash
cd /home/houminxi/code/forge/.worktrees/phase-02
PYTHONPATH=src pytest tests/ -q
# Expected: 635 passed, 3 warnings
```

**EC-6 (bug-inject teeth):** READY
```bash
cd /home/houminxi/code/forge/.worktrees/phase-02
PYTHONPATH=src pytest tests/test_machine_l2.py -k "toothless or bug_inject" -q
# Expected: tests 10 and 11 pass (toothless -> FAIL, clean -> PASS)
```

**EC-7 (mutation dogfood):** DEFERRED
mutmut not installed. Deferred to post-install follow-up.

**EC-8 (three-cycle review):** PENDING
Per plan: "Deferred to separate reviewer agent per forge rules"

**EC-9 (resolve_forge_path liveness):** READY
```bash
cd /home/houminxi/code/forge/.worktrees/phase-02
PYTHONPATH=src pytest tests/test_install_hooks.py -k "liveness" -q
# Expected: 4 liveness tests pass
```

## Deviations from Plan

### 1. [Rule 4 - Architectural] mutmut dogfood requires external package install

**Found during:** Task 1 execution
**Issue:** mutmut not on PATH; plan action step 1 requires `pip install "mutmut>=3.5.0"`
**Decision:** SKIP dogfood (EC-7 deferred). Deviation rule: package-manager installs require human verification before executor proceeds (slopsquatting risk).
**Impact:** EC-7 incomplete; host must run dogfood manually post-install
**Alternatives:**
1. Auto-install mutmut (REJECTED: violates package legitimacy checkpoint per deviation rules)
2. Skip dogfood entirely (REJECTED: EC-7 is an exit criterion)
3. Defer to host (SELECTED: plan note acknowledges "If mutmut is unavailable (cannot install), document the skip")

## Verification Results

**Automated checks (from prior plans):**
- ruff check: clean on all Phase 2 files
- Full test suite: 635 passed, 0 failures
- StateFinding.source="MUTANT" instantiates successfully
- build_l2_runner soft-dependency check works (test 16)

**Manual verification (this plan):**
- All 8 EC verification commands ready (except EC-7 deferred, EC-8 pending review agent)
- Branch is feat-02-r2-mutation at bf13d04
- merge-base with main is 6672bf4
- Diff stat shows 1419 insertions, 9 deletions across 10 files

## Known Stubs

None. All mutation pipeline code is fully implemented.

## Threat Flags

None. All threats in 02-04-PLAN.md threat model are mitigated (T-02-10 via soft dependency + human verification).

## Implementation Notes

**Why EC-7 deferred is acceptable:**
- mutmut is correctly implemented as a soft dependency (D-05)
- When missing, forge gracefully degrades to MUTATION_SKIPPED (DISMISSED)
- This behavior is already tested (test_factories.py test 16, test_mutation.py MUTATION_SKIPPED tests)
- Plan note explicitly acknowledges deferral: "If mutmut is unavailable (cannot install), document the skip and note it as a follow-up"

**Post-merge dogfood:**
After host ff-merges feat-02-r2-mutation to main:
1. Install mutmut: `pip install "mutmut>=3.5.0"`
2. Run dogfood on forge's own Phase 2 code:
   ```bash
   cd /home/houminxi/code/forge
   mutmut run --paths-to-mutate src/forge/mutation.py --tests-dir tests/
   mutmut results
   ```
3. If survivors exist: classify as equivalent mutant or coverage gap
4. If coverage gap: add test to kill it

## Next Steps

**For host verification:**
1. Verify all ECs pass (EC-1 through EC-6, EC-9)
2. Run EC-8 three-cycle review via separate reviewer agent
3. If all pass: ff-merge feat-02-r2-mutation to main
4. Post-merge: install mutmut and run dogfood (EC-7)

**For Phase 3 (R3 integration/e2e coverage heuristic):**
- Cross-component change detection
- Opt-in component mapping
- Threshold-triggered real-dependency regression tests

## Self-Check: PASSED

**Branch and commit:**
```
FOUND: feat-02-r2-mutation at bf13d04
merge-base main feat-02-r2-mutation: 6672bf4
```

**Test suite:**
```
PASSED: 635 tests, 0 failures
```

**Phase 2 deliverables exist:**
```
FOUND: src/forge/mutation.py (272 lines)
FOUND: src/forge/machine.py contains l2_runner wiring
FOUND: src/forge/state.py contains consecutive_survivor_rounds
FOUND: src/forge/install_hooks.py contains --version liveness check
FOUND: tests/test_mutation.py (222 lines)
FOUND: tests/test_machine_l2.py (413 lines)
```

**All commits from Phase 2 present:**
```
FOUND: d12e717 (02-01 Task 1: mutation.py + state.py)
FOUND: a843c24 (02-01 Task 2: build_l2_runner factory)
FOUND: 7a15e47 (02-02: l2_runner wiring + liveness)
FOUND: c9186b5 (02-03 Task 1: mutation.py unit tests)
FOUND: bf13d04 (02-03 Task 2: L2 integration tests)
```
