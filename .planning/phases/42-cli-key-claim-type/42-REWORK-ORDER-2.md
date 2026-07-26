# Phase 42 increment order (round 2)

**Issued:** 2026-07-25 by the PM session after verifying the round-1 delivery
(`cp-artifacts/p42-rework-delivery.md`) against real files.
**Baseline:** planning-local snapshot `cf1b4455bbae`.
**Scope:** two items. Nothing else.

## Round 1 is accepted -- do not redo it

Verified by the PM, not taken from the report:

- The behavioural test is real. Force-run with `--runxfail` it fails at
  `AssertionError: assert 'review' == 'lint'`, which means the fixture chain
  (`_prep_local_state` -> `_build_machine` -> `_write_ledger_rows` ->
  `iter_rows`) genuinely executes and catches the live hardcoded value. This
  is the strongest artifact in the delivery.
- `10 passed, 1 xfailed` reproduces.
- Items A, B and C are done: the false "ONLY test" claim is gone from both
  line 401 and line 428, Step 4 carries three distinct injections, non-ASCII
  is 0 in both plans.
- `xfail(strict=True)` is a good choice: if the marker is not removed after
  wiring, the test XPASSes and strict turns that into a suite failure. Self-
  correcting. Keep it.

Do not restructure the plan, do not re-verify anchors, do not re-run the
panel on anything outside the two items below.

## Item D (HIGH) -- the behavioural test is defeatable by its mirror mutation

The new test feeds `source="L0"` only (`grep` for `source="L1"` in the added
block returns 0). Given the plan's own mapping at line 210,
`"L0": ClaimType(type="lint", version_sensitive=False)`, the consequence is
deterministic:

| wiring | test feeds L0, asserts "lint" |
|--------|-------------------------------|
| `derive_claim_type(f.source)` (correct) | PASS |
| `derive_claim_type("L1")` (named in the last order) | "review" -> FAIL, caught |
| `derive_claim_type("L0")` (mirror) | "lint" -> PASS, **survives** |

The test catches exactly the mutation the previous order named and not its
mirror image. A single-source behavioural test is always defeatable by
hardcoding that same source, so the coverage has to be two-sided.

Second, independent gap in the same test: `version_sensitive` is asserted
only in its `False` form. The `True` branch (L1, MUTANT) has no real-path
coverage at all. One L1 case closes both gaps.

Required:
1. Extend the behavioural coverage so an L1 finding is exercised through the
   real `_write_ledger_rows` path and asserted to land as
   `axis_claim="review"` with `version_sensitive is True`.
2. Add a fourth injection to Task 2 Step 4: hardcode
   `derive_claim_type("L0")`. The L0 assertions stay green; the L1
   assertions must FAIL. State that explicitly, because the whole point is
   that the L0 half cannot detect this one.

Ground truth already measured, use it rather than re-deriving:

- `_make_finding(fp, disp=Disposition.CONFIRMED, source="L0", file="a.py", ...)`
  at `tests/test_machine_ledger.py:35`. `source` is a parameter, so an L1
  finding needs no new helper.
- `_build_machine(tmp_path, resolved, l0_findings=None, l0_infra=None)` at :69,
  `_prep_local_state(tmp_path)` at :90, `_resolved_with_shas(...)` at :49.
- The existing behavioural test passes `disp=Disposition.FIXED`. Whether an
  L1 finding needs the same disposition to reach a terminal write is NOT
  something I verified -- check it, do not assume it carries over.
- The new test currently spans lines 277-307 (decorator at 277, def at 281).

Shape is yours. One test with both findings, two tests, or a parametrisation
all work. What matters is that both source values traverse the real path and
that each row's claim is asserted individually.

## Item E (process) -- the test change sits on main

`tests/test_machine_ledger.py` is modified and uncommitted in the main
worktree (`git worktree list` shows only `/home/houminxi/code/forge [main]`;
no linked worktree exists). The global rule is that files are never edited in
a main worktree.

To be clear about fault: the previous order named `42-02-PLAN.md` as its
target and asked only for an edited plan, while Item A said "add real-path
behavioural coverage". That wording was ambiguous and the ambiguity is mine.
Writing a real test was the better reading and produced the delivery's
strongest evidence. Only the placement needs fixing.

Required: move the change onto a branch in a linked worktree. Save the patch
before reverting anything -- the diff is 35 insertions and is the only copy:

    git diff tests/test_machine_ledger.py > /tmp/p42-behavioural-test.patch
    # verify the patch is non-empty and applies cleanly before going further
    git worktree add .worktrees/p42 -b phase-42-cli-key-claim-type
    ln -sf "$(git rev-parse --show-toplevel)/CLAUDE.md" .worktrees/p42/CLAUDE.md
    # apply the patch inside the worktree, then restore main

Do not run `git checkout -- tests/test_machine_ledger.py` until the patch
file exists and you have confirmed it applies. Losing the test costs more
than the placement violation does.

Item D's test work happens in that worktree. The plan edit stays in
`.planning/`, which is outside git and unaffected.

## Output contract

1. Edited `42-02-PLAN.md` (Step 4 fourth injection, behaviour list updated).
2. The extended test, committed on `phase-42-cli-key-claim-type` in the
   worktree. Report the branch and SHA.
3. Injection proof for the new mutation, run for real: hardcode
   `derive_claim_type("L0")`, show the L1 assertion going RED and the L0
   assertion staying green, revert, show both back. Paste the actual pytest
   output. Note that with the wiring not yet built you may need to state
   plainly that this proof is deferred to the GREEN phase rather than
   fabricate a run -- see below.
4. Delta note. Every line number in it must be re-checked against the file
   after your final edit; round 1's note was off by 4 lines and claimed a
   trailing-newline fix that did not happen (the file still ends without a
   newline). Line numbers shift when you edit above them.
5. **Confirmation-round results go to
   `.planning/phases/42-cli-key-claim-type/cp-artifacts/`, not `/tmp`.**
   Round 1 left 23 files in `/tmp` that the PM had to archive by hand, one
   round after the same thing happened. Check each result file's byte size
   before reading its verdict: a 0-byte or ~140-byte file is a dispatch
   failure, never a clean verdict. This already happened twice in this phase.

Disclose every finding from every model in every round, including ones you
fixed and ones two models found independently. Round 1's report compressed
two rounds into one and credited a shared finding to a single model.

## Honest failure is pre-authorised

If the L1 case cannot reach a terminal ledger write without machinery far
heavier than the L0 case needs, say so, show what you hit, and propose the
alternative. If you conclude the mirror mutation is not actually reachable --
for instance if something upstream constrains `f.source` in a way I missed --
say so with file:line evidence and make no edit. A disproof with evidence
beats a compliant change.

If the injection proof in item 3 cannot run because the wiring does not exist
yet, write exactly that. Do not stage a fake run. An honest "deferred to
GREEN, here is why" is a passing answer; an invented pytest transcript is
not, and it is checked against the diff.
