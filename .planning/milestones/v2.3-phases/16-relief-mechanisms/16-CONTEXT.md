# Phase 16: Relief Mechanisms - Context

**Gathered:** 2026-06-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Diff-size tiering for the forge review cycle count. Small diffs get fewer
mandatory clean cycles; large diffs get more. The goal is to reduce
corner-cutting pressure on trivial changes while increasing scrutiny for
large changes. This is RELIEF (ergonomic adjustment), not DEFENSE (the
3-cycle default remains the security baseline).

Additionally, fix the F3 forge gate defect: JSON parse errors during
review must be fail-closed (dirty round), not silently dismissed.

</domain>

<decisions>
## Implementation Decisions

### D-01: Tier boundaries and cycle counts

Three tiers, two boundaries (50, 200):

| Diff size (insertions + deletions) | Clean round threshold |
|------------------------------------|----------------------|
| <50 lines                          | 2 cycles (6 passes)  |
| 50-199 lines                       | 3 cycles (9 passes, default) |
| >=200 lines                        | 4 cycles (12 passes) |

### D-02: Diff size metric

Total changed lines = insertions + deletions (semantic: same count as
`git diff --stat`). Implementation uses `unidiff.PatchSet` on the
resolved `diff_text` (`line.is_added` or `line.is_removed`), which is
equivalent and avoids a subprocess call. Caveat: binary files, pure
renames, and mode-only changes can make unidiff's count diverge from
`git diff --stat` -- acceptable because tiering is RELIEF, not DEFENSE
(a mis-tier is low-stakes).

### D-03: Env var override priority

Explicit `FORGE_CLEAN_ROUND_THRESHOLD=N` overrides tiering completely.
User-set value always wins. Tiering only applies when no env var is set.

### D-04: --whole-file mode

`--whole-file` forces default tier (3 cycles) regardless of file size.
No tiering applied -- the diff is artificial (entire file as diff).

### D-05: Outlet uniformity

All three outlets (A/B/C) use the same tiering logic. Diff size is
computed once, threshold passed to the state machine.

### D-06: F3 fail-closed (folded from ROADMAP candidate)

JSON parse / invoke / spawn errors during any review pass must be
fail-closed. `validate_reviewer_json` already raises ValueError
(fail-closed at parse layer) and the outlet callers (factories.py,
outlet_c.py) already convert it into a CONFIRMED infra finding. The
defect (F3) is downstream: `machine.py:_run_l1_phase` sends every L1
candidate -- including these infra findings -- to `falsifier.falsify()`,
which the LLM DISMISSES, overwriting the CONFIRMED so the round counts
clean -> false-green. Fix (see 16-RESEARCH Finding #4): (1) tag
error-path findings `source="INFRA"` in factories.py + outlet_c.py;
(2) skip the falsifier for `source=="INFRA"` in `_run_l1_phase` so the
CONFIRMED disposition sticks and blocks fixpoint.
`validate_reviewer_json` / `reviewer_json.py` need NO change. SHRK-03
adjacent -- large diffs are precisely where parse errors most likely
occur (DeepSeek 3560-line false-green incident).

### D-07: Documentation locations

Document tiering in all three places:
1. SKILL.md -- cycle counter section, explain tier table and rationale
2. CLI `--help` -- note that diff size affects cycle count
3. gate.yaml -- comments explaining tier thresholds

Wording must emphasize "relief, not defense": tiering reduces friction
for small safe changes, it does NOT weaken the review for large risky
changes.

### Claude's Discretion

- Where exactly in the code flow to compute diff size (before
  machine.run() or inside StateMachine.__init__)
- Whether to add a `--tier` CLI flag for manual override (vs env var only)
- Implementation of F3 fix -- root cause and fix sites verified (see D-06), only sequencing is discretionary

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Cycle counter logic
- `src/code_forge/machine.py` lines 449-461 -- FORGE_CLEAN_ROUND_THRESHOLD read + cycle counting
- `src/code_forge/state.py` lines 97, 202-203, 260 -- consecutive_clean_rounds field

### Diff computation
- `src/code_forge/diff.py` lines 21-64 -- extract_changed_lines (reference, not the chosen metric)

### F3 fail-closed target (verified root cause)
- `src/code_forge/machine.py` -- `_run_l1_phase` (~line 522): skip `falsifier.falsify()` for `source=="INFRA"` findings (PRIMARY fix site)
- `src/code_forge/factories.py` -- `build_l1_provider`: tag invoke-fail (~295-303) and schema-fail (~311-319) findings `source="INFRA"`
- `src/code_forge/outlet_c.py` -- `_l1_provider`: tag spawn-fail (~56-64) and schema-fail (~73-81) findings `source="INFRA"`
- `src/code_forge/reviewer_json.py` -- `validate_reviewer_json`: NO CHANGE (already fail-closed; listed to mark it is NOT the fix site)

### Documentation targets
- `src/code_forge/skills/code-forge/SKILL.md` -- cycle counter section (~line 1348)
- `src/code_forge/cli.py` -- CLI argument parser

### Prior context
- `.planning/phases/15-reviewer-independence/VERIFICATION.md` -- F3 finding details
- Memory: `feedback_forge_false_green_large_diff` -- DeepSeek false-green incident

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `extract_changed_lines(diff_text)` in diff.py: returns {file: set[line_no]}, useful as fallback metric reference
- `FORGE_CLEAN_ROUND_THRESHOLD` env var parsing in machine.py:449-451: already handles int conversion with fallback
- `validate_reviewer_json` in reviewer_json.py: already fail-closed (raises ValueError), but callers may swallow

### Established Patterns
- Threshold read via `os.environ.get()` with int cast and floor clamp to 1 (machine.py:449-453)
- State machine uses `self._state.consecutive_clean_rounds` counter (state.py:97)
- Outlet dispatch happens in cli.py `_run()`, diff_text is available at that scope

### Integration Points
- machine.py:449 -- where threshold is currently read (inject tiering here or before)
- cli.py _run() -- where diff_text is available and outlet dispatch happens
- factories.py build_l1_provider -- where L1 pass results are processed (F3 fix target)
- outlet_c.py run_outlet_c -- C-leg pass results (F3 fix target)

</code_context>

<specifics>
## Specific Ideas

No specific requirements -- standard implementation following the tier table in D-01.

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope.

</deferred>

---

*Phase: 16-relief-mechanisms*
*Context gathered: 2026-06-08*
