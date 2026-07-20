# Phase 16: Relief Mechanisms - Research

**Researched:** 2026-06-08
**Domain:** Forge state machine cycle-counting / diff analysis / security defect fix
**Confidence:** HIGH (all findings verified by code trace against actual source)

## Summary

Phase 16 has two workstreams: (1) diff-size tiering that adjusts the
`FORGE_CLEAN_ROUND_THRESHOLD` based on how many lines changed, and (2) the
F3 fail-closed security fix where infrastructure error findings (invoke-fail,
schema-fail, spawn-fail) are incorrectly routed to the LLM falsifier, which
dismisses them, causing false-green results.

Both workstreams are well-scoped. The threshold injection point is a single
`os.environ.get()` call at machine.py:448-454, read inside the per-round
loop. The diff size counting can reuse the existing `unidiff.PatchSet`
infrastructure in diff.py. The F3 fix requires changing `source="L1"` to
`source="INFRA"` at 4 sites (2 in factories.py, 2 in outlet_c.py) and
adding a source-filter guard in machine.py's `_run_l1_phase` loop.

**Primary recommendation:** Implement tiering as two pure functions in
diff.py (`count_diff_lines`, `tier_threshold`), compute the threshold once
in cli.py `_run()` before outlet dispatch, and thread it to StateMachine
via a new constructor parameter. The F3 fix is a small targeted change at
5 sites with no architectural risk.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Three tiers: <50 lines -> 2 cycles; 50-199 -> 3 cycles (default); >=200 -> 4 cycles
- **D-02:** Metric = insertions + deletions via `unidiff.PatchSet` on `diff_text`
- **D-03:** Explicit `FORGE_CLEAN_ROUND_THRESHOLD=N` overrides tiering completely
- **D-04:** `--whole-file` forces default tier (3 cycles) regardless of file size
- **D-05:** All three outlets (A/B/C) use the same tiering logic
- **D-06:** F3 fail-closed: tag error-path findings `source="INFRA"`, skip falsifier for INFRA
- **D-07:** Document tiering in SKILL.md, CLI --help, and gate.yaml

### Claude's Discretion

- Where to compute diff size (before machine.run() or inside StateMachine.__init__)
- Whether to add a `--tier` CLI flag for manual override (vs env var only)
- F3 fix sequencing (root cause and fix sites verified, only sequencing is discretionary)

### Deferred Ideas (OUT OF SCOPE)

None.

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SHRK-03 | Diff-size tiering reduces corner-cutting pressure | Tiering logic, threshold injection, all 3 outlets, documentation (Findings 1-3, 5) |
| F3 (folded) | Fail-closed for error-path findings | F3 root cause verified, fix sites confirmed (Finding 4) |

</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Diff size counting | diff.py (utility) | -- | Pure function on diff_text, no side effects |
| Tier threshold computation | diff.py (utility) | -- | Pure function: line_count + flags -> int |
| Threshold threading to machine | cli.py (orchestrator) | outlet_c.py | cli.py computes once, threads to both outlet paths |
| Cycle-counting with threshold | machine.py (state machine) | -- | _run_local loop owns the convergence check |
| F3 source tagging | factories.py + outlet_c.py | -- | Error-path findings created at these two sites |
| F3 falsifier skip | machine.py (_run_l1_phase) | -- | Loop guard on source field |
| Documentation | SKILL.md + cli.py + init_template.py | -- | Three distinct insertion points |

## Gaps Found in Prior Research

### Gap 1: `--whole-file` does NOT produce `git_diff=None` (CORRECTION)

The prior research stated:

> "NOTE: --whole-file mode: resolved.git_diff is None, so count_diff_lines("") returns 0"

**This is WRONG.** Verified by code trace:

1. `--whole-file` resolves to `(EmptyBaseline(), GitRefBaseline("WORKING"), paths)` at cli.py:1428
2. `EmptyBaseline` + `WORKING` head flows to `_resolve_empty()` at baseline.py:175
3. Inside a git repo, `_resolve_empty` diffs the empty tree SHA against WORKING tree
4. This produces a REAL `git_diff` string containing the entire file as additions
5. `count_diff_lines()` on this diff returns the total line count of the file

**Impact:** Without the `whole_file` guard in `tier_threshold()`, a 500-line file
reviewed with `--whole-file` would hit tier 3 (4 cycles) instead of the default
3 cycles that D-04 requires. The `whole_file` flag is necessary for correctness,
but the reason is "the diff is artificially large" (the whole file appears as
additions), not "the diff is None."

**Detection method:** `_resolve_whole_file_specs` returns `EmptyBaseline()` at
cli.py:1429. `_build_baseline_specs` calls `_resolve_whole_file_specs` at
cli.py:1439 and returns `(EmptyBaseline(), GitRefBaseline("WORKING"))`.
`resolve_baseline` routes `EmptyBaseline` to `_resolve_empty` at baseline.py:163,
which calls `working_tree_diff(empty_tree_sha, paths, cwd)` at baseline.py:179.
This returns a diff string, NOT None.

### Gap 2: Prior research omitted `reviewer_json.py:105` source="L1" site

There are 5 total `source="L1"` assignments in the codebase, not 4:

| File | Line | Context | Needs change? |
|------|------|---------|---------------|
| factories.py | 298 | invoke-fail, `Disposition.CONFIRMED` | YES -> `source="INFRA"` |
| factories.py | 314 | schema-fail, `Disposition.CONFIRMED` | YES -> `source="INFRA"` |
| outlet_c.py | 59 | spawn-fail, `Disposition.CONFIRMED` | YES -> `source="INFRA"` |
| outlet_c.py | 76 | schema-fail, `Disposition.CONFIRMED` | YES -> `source="INFRA"` |
| reviewer_json.py | 105 | normal L1 finding, `Disposition.UNCERTAIN` | **NO** (legitimate finding) |

The `reviewer_json.py:105` site is `_json_to_state_findings()` which creates
normal code-review findings with `Disposition.UNCERTAIN`. These SHOULD go
through the falsifier. **No change needed.** This was implicitly correct in
the prior research but not explicitly called out, which could confuse a planner
scanning for all `source="L1"` sites.

### Gap 3: Backward compatibility risk with small-diff tests

Prior research stated "All 1160 existing tests pass unchanged" and "backward-compatible."
This is only true if the StateMachine constructor defaults `clean_round_threshold=3`.
Two existing tests use small diffs (<50 lines) and assert `consecutive_clean_rounds >= 3`:

- `test_consecutive_clean.py:92` (`test_all_clean_run_passes_verify`) -- diff is 2 lines
- `test_outlet_c.py:122` (`test_needs_3_clean_rounds`) -- diff is 2 lines

If tiering were computed inside StateMachine based on the diff, these would tier
to 2 cycles and the `>= 3` assertions would fail. The fix is to keep tiering
computation in cli.py (external to StateMachine) so tests that construct
StateMachine directly get the default threshold (3).

## Standard Stack

### Core (already in project, no new dependencies)

| Library | Version | Purpose | Status |
|---------|---------|---------|--------|
| unidiff | >=0.7.5,<0.8.0 | Diff parsing, line counting | Already in pyproject.toml [VERIFIED: pyproject.toml line 20] |

No new packages needed. `count_diff_lines` and `tier_threshold` are pure
functions using the existing `unidiff.PatchSet` API.

### API Verification

`line.is_added` and `line.is_removed` confirmed working on unidiff 0.7.x:
- Added lines: `is_added=True, is_removed=False` [VERIFIED: runtime test]
- Removed lines: `is_added=False, is_removed=True` [VERIFIED: runtime test]
- Context lines: `is_added=False, is_removed=False` [VERIFIED: runtime test]
- Binary files: `is_binary_file=True`, zero hunks, zero lines [VERIFIED: runtime test]
- Rename-only: `is_rename=True`, zero hunks, zero changed lines [VERIFIED: runtime test]
- Mode-only: zero hunks, zero changed lines [VERIFIED: runtime test]

All edge cases return 0 changed lines, which maps to tier 1 (2 cycles) --
acceptable per D-02 ("a mis-tier is low-stakes").

## Package Legitimacy Audit

No new packages. All functionality uses existing `unidiff` dependency.

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| unidiff | PyPI | 12+ yrs | existing dep | github.com/matiasb/python-unidiff | N/A | Already approved (pyproject.toml) |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
CLI args (--whole-file, FORGE_CLEAN_ROUND_THRESHOLD env)
    |
    v
cli.py _run()
    |
    +-- resolved = resolve_baseline(...)  # produces git_diff
    |
    +-- diff_text = resolved.git_diff
    |
    +-- count_diff_lines(diff_text) -----> line_count (int)
    |
    +-- tier_threshold(line_count,         # D-01 tiers
    |       whole_file=bool,               # D-04 override
    |       env_override=os.environ)       # D-03 env wins
    |       ----> clean_threshold (int)
    |
    +-- if outlet == "subagent":
    |       run_outlet_c(..., clean_round_threshold=clean_threshold)
    |           |
    |           +-- StateMachine(clean_round_threshold=clean_threshold)
    |
    +-- else (outlet == "subprocess"):
            _run_hold_loop(...)
                |
                +-- StateMachine(clean_round_threshold=clean_threshold)
                        |
                        +-- _run_local() loop:
                                consecutive_clean_rounds >= threshold -> PASS
```

### F3 Data Flow (Fix)

```
factories.py / outlet_c.py
    |
    +-- invoke-fail/schema-fail/spawn-fail
    |       source="INFRA"  <-- FIX (was "L1")
    |       disposition=CONFIRMED
    |
    v
machine.py _run_l1_phase()
    |
    for f in l1_candidates:
        if f.source == "INFRA":   <-- FIX (new guard)
            l1_findings.append(f)  # keep CONFIRMED, skip falsifier
            continue
        f.disposition = falsifier.falsify(f)  # only for real findings
    |
    v
_fixpoint_reached()
    |
    +-- condition (c): "zero CONFIRMED remain" -> FALSE (INFRA finding blocks)
    +-- round counts DIRTY -> consecutive_clean_rounds = 0
    +-- no false-green
```

### Recommended Project Structure (no new files)

```
src/code_forge/
    diff.py             # ADD: count_diff_lines(), tier_threshold()
    machine.py          # MODIFY: accept clean_round_threshold param, skip INFRA falsifier
    cli.py              # MODIFY: compute threshold, thread to outlets
    outlet_c.py         # MODIFY: accept clean_round_threshold param
    factories.py        # MODIFY: source="L1" -> source="INFRA" on error paths
    init_template.py    # MODIFY: add tiering comment to GATE_YAML_TEMPLATE
    skills/code-forge/
        SKILL.md        # MODIFY: add adaptive cycle count section
tests/
    test_diff.py        # ADD: count_diff_lines + tier_threshold tests
    test_consecutive_clean.py  # ADD: threshold-param tests
    test_outlet_c.py    # ADD: threshold threading test
    test_machine_local.py      # ADD: INFRA finding blocks fixpoint test
```

### Pattern: Pure Function Tiering

**What:** Two pure functions in diff.py that convert diff_text to a threshold integer.

**Why:** Keeps the tiering logic testable in isolation, outside the StateMachine.
The StateMachine receives the threshold as a constructor parameter and does not
need to know about diffs, tiers, or `--whole-file`.

**Code pattern:**

```python
# In diff.py -- VERIFIED: unidiff API confirmed by runtime test
def count_diff_lines(diff_text: str) -> int:
    """Count insertions + deletions across all hunks."""
    if not diff_text or not diff_text.strip():
        return 0
    try:
        patchset = unidiff.PatchSet(diff_text)
    except unidiff.errors.UnidiffParseError:
        return 0  # safe fallback -> tier 2 (3 cycles)
    total = 0
    for pf in patchset:
        for hunk in pf:
            for line in hunk:
                if line.is_added or line.is_removed:
                    total += 1
    return total


def tier_threshold(
    line_count: int,
    whole_file: bool = False,
    env_override: int | None = None,
) -> int:
    """D-01/D-03/D-04 priority chain."""
    if env_override is not None:
        return max(1, env_override)  # D-03: user wins
    if whole_file:
        return 3  # D-04: forces default
    if line_count < 50:
        return 2  # D-01: small diff
    if line_count >= 200:
        return 4  # D-01: large diff
    return 3  # D-01: mid-tier default
```

### Anti-Patterns to Avoid

- **Reading env var per-round inside the loop:** The current code reads
  `FORGE_CLEAN_ROUND_THRESHOLD` at machine.py:448-454 inside the per-round
  `for round_index in range(self.max_total_rounds)` loop. This means
  changing the env var mid-run changes the threshold mid-review. The fix
  should compute the threshold ONCE before the loop, either via a constructor
  parameter or at `_run_local()` entry. [VERIFIED: machine.py:448 is inside
  the `for round_index` loop at line 398]

- **Putting tier logic inside StateMachine:** The StateMachine should not
  know about diff sizes or `--whole-file`. It should receive a plain `int`
  threshold. Tier computation belongs in diff.py (pure functions) with
  orchestration in cli.py.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Diff line counting | Manual regex on diff text | `unidiff.PatchSet` iteration | Handles binary, rename, mode-only, multi-hunk correctly |
| Env var parsing with fallback | Custom parsing logic | Existing pattern at machine.py:448-454 | Already handles int cast, ValueError, floor clamp |

## Common Pitfalls

### Pitfall 1: `--whole-file` Produces a Real Diff, Not None

**What goes wrong:** Assuming `--whole-file` means `git_diff=None` and
defaulting to 0 changed lines (tier 1, 2 cycles).

**Why it happens:** The name suggests "no baseline comparison" but the
implementation diffs from the empty tree to WORKING, producing a large
diff containing the entire file as additions.

**How to avoid:** The `tier_threshold()` function MUST check the `whole_file`
flag BEFORE counting lines. The flag check has HIGHER priority than the
line count (D-04).

**Warning signs:** A 500-line file reviewed with `--whole-file` getting 4
cycles instead of 3.

### Pitfall 2: Breaking the Env Var Override Backward Compat

**What goes wrong:** The tiering logic runs even when
`FORGE_CLEAN_ROUND_THRESHOLD` is explicitly set.

**Why it happens:** Wrong priority chain -- tiering computed first, env var
checked second.

**How to avoid:** D-03 is explicit: env var ALWAYS wins. The priority chain
in `tier_threshold()` must check env override FIRST, before any tier logic.

**Warning signs:** `test_threshold_1_recovers_single_fixpoint` in
test_consecutive_clean.py:76-90 breaks.

### Pitfall 3: Modifying reviewer_json.py for F3

**What goes wrong:** Changing `source="L1"` at reviewer_json.py:105 to
`source="INFRA"`, which would make ALL L1 findings skip the falsifier.

**Why it happens:** Grep for `source="L1"` finds 5 sites; the one in
reviewer_json.py looks similar to the error-path sites.

**How to avoid:** reviewer_json.py:105 creates NORMAL code-review findings
with `Disposition.UNCERTAIN`. These SHOULD go through the falsifier. Only
the error-path findings (invoke-fail, schema-fail, spawn-fail) with
`Disposition.CONFIRMED` need the source change. The distinguishing feature
is the disposition, not the source string.

**Warning signs:** L1 review findings no longer get falsified; all findings
stay UNCERTAIN; HOLD state triggered on every review.

### Pitfall 4: Threshold Read Inside Per-Round Loop

**What goes wrong:** Leaving the env var read inside the per-round loop at
machine.py:448-454 while also adding a constructor parameter creates two
competing threshold sources.

**Why it happens:** The constructor parameter is added but the old env var
read is not removed.

**How to avoid:** When adding `clean_round_threshold` to StateMachine, the
env var read at line 448-454 MUST be replaced. The constructor parameter
becomes the single source of truth. The env var is read ONCE in
`tier_threshold()` (called from cli.py), not in machine.py.

**Warning signs:** Two different threshold values in the same run.

### Pitfall 5: Backward Compat with Small-Diff Tests

**What goes wrong:** Existing tests that assert `consecutive_clean_rounds >= 3`
fail because the test diff is <50 lines and tiers to 2 cycles.

**Why it happens:** Tiering is computed inside StateMachine based on the diff
passed via resolved_review, and tests that construct StateMachine directly
with a small diff get the wrong threshold.

**How to avoid:** Tiering computation MUST stay in cli.py. StateMachine
receives `clean_round_threshold` as a plain int with a default of 3. Tests
that don't pass a threshold get the old behavior (3 cycles).

**Warning signs:** `test_needs_3_clean_rounds_not_1` and `test_needs_3_clean_rounds`
fail with "assert 2 >= 3".

## Code Examples

### Finding 1: Threshold Injection Path (VERIFIED)

**Current code (machine.py:447-461):**

```python
# Inside _run_local(), inside the for round_index loop:
            try:
                _threshold = int(os.environ.get(
                    "FORGE_CLEAN_ROUND_THRESHOLD", "3"
                ))
            except (ValueError, TypeError):
                _threshold = 3
            if _threshold < 1:
                _threshold = 1

            if self._fixpoint_reached():
                self._state.consecutive_clean_rounds += 1
            else:
                self._state.consecutive_clean_rounds = 0

            if self._state.consecutive_clean_rounds >= _threshold:
                self._finalize_local_terminal()
                return self._state.verdict
```

**Key observations:**
- `_threshold` is computed INSIDE the `for round_index in range(self.max_total_rounds)` loop (line 398)
- It reads the env var every round (lines 448-450)
- The env var default is "3" (string)
- Floor clamp to 1 at line 453-454
- The threshold comparison is at line 461: `consecutive_clean_rounds >= _threshold`

**Fix approach:** Add `clean_round_threshold: int = 3` to `StateMachine.__init__`
(after `max_fix_attempts` at line 147). Replace lines 447-454 with
`_threshold = self.clean_round_threshold`. The env var parsing moves to
`tier_threshold()` in diff.py.

[VERIFIED: machine.py lines 398, 447-461]

### Finding 2: Diff Size Counting (VERIFIED)

**unidiff is already imported in diff.py:**

```python
# diff.py line 16
import unidiff
```

**`line.is_added` / `line.is_removed` confirmed working** (runtime test on
unidiff 0.7.x installed via pyproject.toml):

- Added line: `is_added=True, is_removed=False`
- Removed line: `is_added=False, is_removed=True`
- Context line: `is_added=False, is_removed=False`

**Edge cases (all return 0 changed lines):**
- Binary files: `is_binary_file=True`, zero hunks
- Rename-only: `is_rename=True`, zero hunks
- Mode-only changes: zero hunks

**Empty/None diff:** `count_diff_lines("")` returns 0 (guard at top).
`count_diff_lines(None)` must be handled -- the `if not diff_text` guard
covers `None` because `not None` is `True`.

**Parse error:** `UnidiffParseError` caught, returns 0 -> falls to mid-tier
(3 cycles). Safe default per D-02 ("a mis-tier is low-stakes").

[VERIFIED: diff.py line 16, runtime test of unidiff API]

### Finding 3: CLI Threading (VERIFIED)

**Where diff_text is available in cli.py:**

After `resolve_baseline()` at line 945-956, `resolved.git_diff` is in scope.
Both outlet branches have access:

**Outlet C (subagent) branch -- cli.py:972-995:**

```python
    if outlet == "subagent":
        from .outlet_c import run_outlet_c
        _post_image, _conv_digest = _assemble_post_image(
            cwd, resolved.git_diff or ""
        )
        _subagent_spawn = _make_subagent_spawn(backend, _conv_digest, _post_image)
        verdict = run_outlet_c(
            resolved_review=resolved,
            source_hash=source_hash,
            cwd=cwd,
            spawn_fn=_subagent_spawn,
            # INSERT: clean_round_threshold=_clean_threshold
        )
```

**Outlet A (subprocess) branch -- cli.py:1054-1072:**

```python
        with ForgeLock(lock_path):
            verdict = _run_hold_loop(
                mode=mode,
                ...
                max_rounds=max_rounds,
                max_fix_attempts=max_fix,
                # INSERT: clean_round_threshold=_clean_threshold
                ...
            )
```

**Threshold computation point -- insert between line 967 and line 972:**

```python
    # Compute diff-size tier threshold (D-01/D-03/D-04)
    from .diff import count_diff_lines, tier_threshold
    _line_count = count_diff_lines(resolved.git_diff or "")
    _whole_file = bool(getattr(args, 'whole_file', None))
    _env_threshold = None
    try:
        _env_raw = os.environ.get("FORGE_CLEAN_ROUND_THRESHOLD")
        if _env_raw is not None:
            _env_threshold = int(_env_raw)
    except (ValueError, TypeError):
        pass
    _clean_threshold = tier_threshold(_line_count, _whole_file, _env_threshold)
```

**Threading chain:**
- cli.py `_run()` -> `_clean_threshold` computed once
- Outlet C: `run_outlet_c(..., clean_round_threshold=_clean_threshold)`
- Outlet A: `_run_hold_loop(..., clean_round_threshold=_clean_threshold)` ->
  `StateMachine(..., clean_round_threshold=_clean_threshold)`

[VERIFIED: cli.py lines 945, 972-984, 1054-1072]

### Finding 4: Outlet C Threading (VERIFIED)

**Current `run_outlet_c` signature (outlet_c.py:34-41):**

```python
def run_outlet_c(
    resolved_review: ResolvedReview,
    source_hash: str,
    cwd: Path,
    spawn_fn: ReviewerSpawnFn,
    falsifier: "Falsifier | None" = None,
    max_total_rounds: int = 20,
) -> Verdict:
```

**Does NOT accept any threshold param currently.** Add
`clean_round_threshold: int = 3` to the signature.

**StateMachine construction (outlet_c.py:84-100):**

```python
    sm = StateMachine(
        mode=Mode.LOCAL,
        falsifier=falsifier,
        ...
        max_total_rounds=max_total_rounds,
        # INSERT: clean_round_threshold=clean_round_threshold
    )
```

[VERIFIED: outlet_c.py lines 34-41, 84-100]

### Finding 5: F3 Fix Sites (VERIFIED -- root cause confirmed by prior code trace)

**factories.py error-path findings:**

Line 295-303 (invoke-fail):
```python
                all_candidates.append(StateFinding(
                    id="l1-%s-invoke-fail" % pass_name,
                    fingerprint="invoke-fail-%s" % pass_name,
                    source="L1",           # <-- FIX: change to "INFRA"
                    disposition=Disposition.CONFIRMED,
                    file="<llm-invoke>",
                    line_range=[0, 0],
                    description="L1 invoke failed: %s" % exc,
                ))
```

Line 311-319 (schema-fail):
```python
                all_candidates.append(StateFinding(
                    id="l1-%s-schema-fail" % pass_name,
                    fingerprint="schema-fail-%s" % pass_name,
                    source="L1",           # <-- FIX: change to "INFRA"
                    disposition=Disposition.CONFIRMED,
                    file="<schema-validation>",
                    line_range=[0, 0],
                    description="schema validation failed: %s" % exc,
                ))
```

**outlet_c.py error-path findings:**

Line 56-64 (spawn-fail):
```python
                findings.append(StateFinding(
                    id="l1-%s-spawn-fail" % pass_name,
                    fingerprint="spawn-fail-%s" % pass_name,
                    source="L1",           # <-- FIX: change to "INFRA"
                    disposition=Disposition.CONFIRMED,
                    file="<spawn>",
                    line_range=[0, 0],
                    description="spawn failed: %s" % e,
                ))
```

Line 73-81 (schema-fail):
```python
                findings.append(StateFinding(
                    id="l1-%s-schema-fail" % pass_name,
                    fingerprint="schema-fail-%s" % pass_name,
                    source="L1",           # <-- FIX: change to "INFRA"
                    disposition=Disposition.CONFIRMED,
                    file="<schema-validation>",
                    line_range=[0, 0],
                    description="schema validation failed: %s" % e,
                ))
```

**machine.py falsifier skip (line 522-524):**

```python
        for f in l1_candidates:
            try:
                f.disposition = self.falsifier.falsify(f)
```

**FIX:** Insert guard before the falsify call:

```python
        for f in l1_candidates:
            if f.source == "INFRA":
                l1_findings.append(f)
                continue
            try:
                f.disposition = self.falsifier.falsify(f)
```

**reviewer_json.py:105 -- NO CHANGE (confirmed):**

```python
        findings.append(StateFinding(
            ...
            source="L1",              # KEEP: normal finding, Disposition.UNCERTAIN
            disposition=Disposition.UNCERTAIN,
            ...
        ))
```

This is `_json_to_state_findings()` which creates NORMAL code-review findings.
These SHOULD go through the falsifier. The distinguishing feature between
error-path findings (change to INFRA) and normal findings (keep L1) is the
**disposition**: error-path findings are `CONFIRMED`, normal findings are
`UNCERTAIN`.

[VERIFIED: factories.py:298,314; outlet_c.py:59,76; machine.py:522-524; reviewer_json.py:105]

### Finding 6: Existing Tests (VERIFIED)

**Tests directly affected by threshold logic:**

| Test file | Test name | Line | Current behavior | Impact of change |
|-----------|-----------|------|-----------------|------------------|
| test_consecutive_clean.py | `test_needs_3_clean_rounds_not_1` | 28 | Asserts `consecutive_clean_rounds >= 3`, `round >= 2` | SAFE: default threshold stays 3 |
| test_consecutive_clean.py | `test_threshold_1_recovers_single_fixpoint` | 76 | Sets env var to 1, asserts `round == 0` | SAFE: env override preserved (D-03) |
| test_consecutive_clean.py | `test_all_clean_run_passes_verify` | 92 | Deletes env var, diff is 2 lines, asserts `sm.run() == PASS` | SAFE: StateMachine default threshold=3, no tiering inside machine |
| test_outlet_c.py | `test_needs_3_clean_rounds` | 122 | diff is 2 lines, asserts `consecutive_clean_rounds >= 3` | SAFE: StateMachine default threshold=3 |

**Key insight:** All existing tests construct StateMachine (or run_outlet_c)
directly without passing a `clean_round_threshold`. With the default of 3,
all existing assertions hold. Tiering is computed only in cli.py, which
these tests do not exercise.

**New tests needed:**

| Test file | Test name | What it tests |
|-----------|-----------|---------------|
| test_diff.py | `test_count_diff_lines_empty` | Empty/None diff returns 0 |
| test_diff.py | `test_count_diff_lines_added_only` | Only additions counted |
| test_diff.py | `test_count_diff_lines_removed_only` | Only deletions counted |
| test_diff.py | `test_count_diff_lines_mixed` | Additions + deletions summed |
| test_diff.py | `test_count_diff_lines_parse_error` | Malformed diff returns 0 |
| test_diff.py | `test_count_diff_lines_binary` | Binary file returns 0 |
| test_diff.py | `test_count_diff_lines_rename_only` | Rename-only returns 0 |
| test_diff.py | `test_tier_threshold_env_override` | D-03: env var always wins |
| test_diff.py | `test_tier_threshold_whole_file` | D-04: whole_file forces 3 |
| test_diff.py | `test_tier_threshold_small` | <50 lines -> 2 |
| test_diff.py | `test_tier_threshold_medium` | 50-199 lines -> 3 |
| test_diff.py | `test_tier_threshold_large` | >=200 lines -> 4 |
| test_diff.py | `test_tier_threshold_zero` | 0 lines -> 2 (smallest tier) |
| test_consecutive_clean.py | `test_threshold_param_2` | SM(clean_round_threshold=2) exits after 2 |
| test_consecutive_clean.py | `test_threshold_param_4` | SM(clean_round_threshold=4) requires 4 |
| test_outlet_c.py | `test_threshold_threading` | run_outlet_c(clean_round_threshold=2) threads |
| test_machine_local.py | `test_infra_finding_blocks_fixpoint` | F3 regression test |

[VERIFIED: test_consecutive_clean.py, test_outlet_c.py, test_machine_local.py, test_diff.py all exist]

### Finding 7: Documentation Insertion Points (VERIFIED)

**SKILL.md:**

The cycle counter / state machine section starts at line 248. The adaptive
cycle count section should go between line 308 (after `cycle_counter == 3`
block at "proceed to Step 3.5 or Step 4") and line 313 ("Genuine Execution"
section). There is no existing "Adaptive Mechanism" section.

[VERIFIED: SKILL.md lines 248-311]

**CLI `--help` (cli.py):**

The `review_parser` epilog is at lines 171-177. Add a note about diff-size
tiering after the exit codes block, or add it to the `review_parser.description`
at line 169.

[VERIFIED: cli.py lines 167-177]

**init_template.py `GATE_YAML_TEMPLATE`:**

The template is at lines 5-66. The tiering comment should go before the
`backends:` section (line 15), after the outlet comment block (line 13).

[VERIFIED: init_template.py lines 5-66]

### Finding 8: Edge Cases (VERIFIED)

**Q: What happens when diff_text is None (CI mode, `--baseline` mode)?**

A: `count_diff_lines(None)` returns 0 because `not None` is `True`, hitting
the early return. This maps to tier 1 (2 cycles). But CI mode uses `_run_ci()`
(machine.py:188-389) which does NOT use `consecutive_clean_rounds` or
`_threshold` at all -- it runs exactly 1 round and returns PASS/FAIL based
on confirmed findings. The threshold is irrelevant in CI mode.
[VERIFIED: machine.py:188-389 has no threshold logic]

**Q: What happens when diff_text is empty string?**

A: `count_diff_lines("")` returns 0 (line guard `if not diff_text`). Maps
to tier 1 (2 cycles). This occurs when there are no changes to review.
The state machine would PASS in 2 clean rounds. Acceptable.

**Q: Does the threshold affect CI mode?**

A: NO. CI mode uses `_run_ci()` which runs a single round and exits based
on confirmed findings count. The `consecutive_clean_rounds` field and
`_threshold` are LOCAL-mode-only constructs. No change needed for CI mode.
[VERIFIED: machine.py:_run_ci at lines 188-389, _run_local at lines 391-487]

**Q: Are there other places FORGE_CLEAN_ROUND_THRESHOLD is read?**

A: Only ONE place: machine.py:448-449. Grep confirms no other reads in src/.
Tests reference it at test_consecutive_clean.py:77,90,107 (setting/unsetting
the env var for test isolation).
[VERIFIED: grep across entire project]

**Q: Does the autofixer loop interact with the threshold?**

A: No. The autofixer loop is in `_apply_autofix_loop_to()` at machine.py:680-734.
It operates on individual findings (CONFIRMED -> FIXED/UNCERTAIN). It does NOT
read or modify `consecutive_clean_rounds` or `_threshold`. The threshold is
checked AFTER `_execute_round()` returns, which is after both autofix and
falsification have completed.
[VERIFIED: machine.py:680-734 has no threshold reference]

**Q: Non-git review mode?**

A: In non-git mode, `resolved.git_diff` is `None` (baseline.py:195).
`count_diff_lines(None)` returns 0 -> tier 1 (2 cycles). This is acceptable:
non-git reviews are typically single-file, small-scope reviews where 2 cycles
is appropriate. If the user wants more, they can set `FORGE_CLEAN_ROUND_THRESHOLD=3`.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Flat 3-cycle threshold | Diff-size tiered (2/3/4 cycles) | Phase 16 | Reduces friction for small changes |
| Env var read per-round in loop | Constructor parameter, computed once | Phase 16 | Single source of truth, testable |
| INFRA errors sent to falsifier | INFRA findings skip falsifier | Phase 16 | Closes F3 false-green defect |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Non-git mode producing git_diff=None maps correctly to tier 1 (2 cycles) | Edge Cases | Low -- user can override with env var |

**All other claims verified via code trace or runtime tests.**

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.3 |
| Config file | pyproject.toml |
| Quick run command | `python3 -m pytest tests/test_diff.py tests/test_consecutive_clean.py tests/test_outlet_c.py tests/test_machine_local.py -x -q` |
| Full suite command | `python3 -m pytest -x -q` |

### Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SHRK-03 | Diff-size tiers produce correct threshold | unit | `pytest tests/test_diff.py -x -q` | Exists (needs new tests) |
| SHRK-03 | StateMachine respects threshold param | unit | `pytest tests/test_consecutive_clean.py -x -q` | Exists (needs new tests) |
| SHRK-03 | Outlet C threads threshold | unit | `pytest tests/test_outlet_c.py -x -q` | Exists (needs new test) |
| F3 | INFRA finding blocks fixpoint | integration | `pytest tests/test_machine_local.py -x -q` | Exists (needs new test) |

### Sampling Rate

- **Per task commit:** `python3 -m pytest tests/test_diff.py tests/test_consecutive_clean.py tests/test_outlet_c.py tests/test_machine_local.py -x -q`
- **Per wave merge:** `python3 -m pytest -x -q` (full suite, 1160 tests)
- **Phase gate:** Full suite green before verification

### Wave 0 Gaps

- [ ] `tests/test_diff.py` -- needs `count_diff_lines` + `tier_threshold` test functions (7+6 = 13 new tests)
- [ ] `tests/test_consecutive_clean.py` -- needs `test_threshold_param_2` and `test_threshold_param_4`
- [ ] `tests/test_outlet_c.py` -- needs `test_threshold_threading`
- [ ] `tests/test_machine_local.py` -- needs `test_infra_finding_blocks_fixpoint`

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | -- |
| V3 Session Management | no | -- |
| V4 Access Control | no | -- |
| V5 Input Validation | yes | `tier_threshold()` clamps env var to >= 1; `count_diff_lines()` handles None/empty/malformed |
| V6 Cryptography | no | -- |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| F3: INFRA finding dismissed by falsifier -> false-green | Tampering | `source="INFRA"` tag + falsifier skip guard (this phase) |
| Env var FORGE_CLEAN_ROUND_THRESHOLD=0 -> infinite loop | Denial of Service | Floor clamp to 1 (existing pattern, preserved) |
| Malicious diff_text causing PatchSet crash | Denial of Service | `UnidiffParseError` caught, returns 0 (safe default) |

## Recommended Plan Structure

**Wave 1 (core logic + F3 fix, parallelizable):**

- **16-01:** Add `count_diff_lines()` + `tier_threshold()` to diff.py. Add
  `clean_round_threshold` param to StateMachine. Thread threshold from cli.py
  through both outlet paths (Outlet A via `_run_hold_loop`, Outlet C via
  `run_outlet_c`). Replace env var read in machine.py loop. Unit tests for
  tier functions and threshold-param convergence.

- **16-02:** F3 fail-closed fix: change `source="L1"` to `source="INFRA"` in
  factories.py (lines 298, 314) and outlet_c.py (lines 59, 76). Add falsifier
  skip guard in machine.py `_run_l1_phase` (line 522). Regression test:
  INFRA finding blocks fixpoint (dirty round).

**Wave 2 (documentation, depends on Wave 1):**

- **16-03:** SKILL.md adaptive cycle count section. CLI `--help` epilog update.
  gate.yaml template tiering comment. Wording emphasizes "relief, not defense."

## Files to Touch

| File | Change | Wave |
|------|--------|------|
| `src/code_forge/diff.py` | Add `count_diff_lines()`, `tier_threshold()` | 1 |
| `src/code_forge/machine.py` | Add `clean_round_threshold` param; replace env var loop read; add INFRA falsifier skip | 1 |
| `src/code_forge/cli.py` | Compute `_clean_threshold` once; thread to both outlets and `_run_hold_loop` | 1 |
| `src/code_forge/outlet_c.py` | Add `clean_round_threshold` param; pass to StateMachine | 1 |
| `src/code_forge/factories.py` | Change `source="L1"` to `source="INFRA"` on lines 298, 314 | 1 |
| `src/code_forge/skills/code-forge/SKILL.md` | Adaptive cycle count section after state machine block | 2 |
| `src/code_forge/cli.py` (epilog) | Diff-size tiering note in review --help | 2 |
| `src/code_forge/init_template.py` | Tiering comment in GATE_YAML_TEMPLATE | 2 |
| `tests/test_diff.py` | 13 new tests for `count_diff_lines` + `tier_threshold` | 1 |
| `tests/test_consecutive_clean.py` | 2 new tests for threshold-param convergence | 1 |
| `tests/test_outlet_c.py` | 1 new test for threshold threading | 1 |
| `tests/test_machine_local.py` | 1 new test for INFRA finding blocks fixpoint | 1 |

## Open Questions

1. **`--tier` CLI flag:** Recommendation is NOT to add it. D-03 env var
   override is sufficient. Adding a CLI flag creates a third competing
   override source (tier function, env var, CLI flag). Listed as Claude's
   Discretion in CONTEXT.md. Planner's call.

2. **Non-git mode tier 1:** When `git_diff=None`, `count_diff_lines` returns
   0 -> tier 1 (2 cycles). This seems reasonable for non-git single-file
   reviews but is technically an assumption (A1). If the user expects 3 cycles
   for non-git, they can set `FORGE_CLEAN_ROUND_THRESHOLD=3`.

## Sources

### Primary (HIGH confidence)

- machine.py lines 391-487 (`_run_local`), 447-461 (threshold), 506-532 (`_run_l1_phase`) -- all code-traced
- factories.py lines 288-320 (invoke-fail, schema-fail) -- code-traced
- outlet_c.py lines 34-100 (run_outlet_c, _l1_provider) -- code-traced
- cli.py lines 940-1000 (outlet dispatch, resolved.git_diff scope) -- code-traced
- diff.py lines 1-67 (existing extract_changed_lines, unidiff import) -- code-traced
- baseline.py lines 163-198 (_resolve_empty, EmptyBaseline produces real diff) -- code-traced
- reviewer_json.py lines 83-111 (_json_to_state_findings, source="L1" for normal findings) -- code-traced
- test_consecutive_clean.py (5 tests, all threshold-related) -- read and verified
- test_outlet_c.py (12 tests, cycle counting + independence) -- read and verified
- unidiff 0.7.x API (line.is_added, line.is_removed) -- runtime verified

### Secondary (MEDIUM confidence)

- SKILL.md lines 248-356 (state machine, cycle counter section) -- read for doc insertion point
- init_template.py lines 5-66 (GATE_YAML_TEMPLATE) -- read for comment insertion point

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new deps, unidiff API verified by runtime test
- Architecture: HIGH -- all injection points verified by code trace
- F3 fix: HIGH -- root cause verified by prior code trace, fix sites confirmed
- Pitfalls: HIGH -- backward compat issue with small-diff tests identified and resolved
- Documentation: HIGH -- all three insertion points verified

**Research date:** 2026-06-08
**Valid until:** 2026-07-08 (stable codebase, no external dependencies)

---

## RESEARCH COMPLETE
