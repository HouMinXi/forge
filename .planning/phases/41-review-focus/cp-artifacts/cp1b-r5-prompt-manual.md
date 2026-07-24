# forge Phase 41 -- CP1b ROUND-5 plan review (manual relay, no repo access)

You are reviewing an IMPLEMENTATION PLAN (the document that follows). It
describes changes to a Python codebase you do NOT have live access to. Review
the PLAN as a document: reason from its own claims, its cited `file:line`
references, and your engineering judgment about Python / asyncio / tempfile /
trust-hash patterns. Where a finding depends on actual code you cannot see, say
so explicitly ("unverified -- depends on <file> real behavior") rather than
asserting it as fact. A well-reasoned "this looks inconsistent, confirm X" beats
a confident guess. A clean 0/0/0/0 is a valid result if the plan holds.

## What this feature is

Phase 41 adds a `review_focus` prompt mechanism at parity with `--contract`:
gate.yaml `review_focus:` (with its OWN trust hash, independent of backend
trust), a `--focus FILE` CLI flag, and a `focus` MCP param, all merging into a
"## Review Focus" prompt section on 3 builders / 4 review paths. It also renames
"## Contract Reference" -> "## Design Intent" and adds a committer date to
git-blame.

## Round-4 disposition (READ before reviewing -- do NOT re-report these as open)

Rounds 1-3 confirmed the core H1 fix CORRECT and are FULLY RESOLVED. Round-4
found 11 findings -- several were defects the plan author introduced in the
round-3 union fix, several pre-existing staleness. ALL 11 are now FIXED in the
plan below. Do NOT re-raise them unless you judge the FIX itself wrong:

1. REPLAN(a) captured the tmpfile name AFTER write/close (leak on write failure)
   -> FIXED: name captured immediately after NamedTemporaryFile(), before
   write/close. (You raised this last round as HIGH -- it is now fixed.)
2. `_dispatch_cli` docstring was contract-only -> FIXED (dual lifecycle).
3. 3b(d) sampling block omitted the `raw_focus = focus_spec` save line -> FIXED
   (added inside the block, before the merge).
4. tests never asserted tmpfile deletion on the INLINE result path -> FIXED (a
   6th test drives the inline path and asserts the file is gone).
5. the `_evict_stale` test used the wrong knob (`max_lifetime_s`, which only
   caps a different timeout) -> FIXED (inject a stale terminal job entry / patch
   the TTL constant, then call `_evict_stale` directly).
6. 3a-1 said "8192 bytes" but the mirror function counts characters via `len()`
   -> FIXED: reworded to "8192 CHARACTERS". (You raised this last round -- fixed.)
7. 3a-3 told the implementer to load a digest that already exists -> FIXED
   (marked already-present, do not re-add).
8. the 3b-1 wiring table cited a stale line number for the sampling call site
   -> FIXED: updated to the post-refactor call site. (You raised this last round
   as MEDIUM -- fixed.)
9. a message change silently broke two verbatim test assertions -> FIXED
   (instructs updating both).
10. a task header contradicted the RECONCILE section -> FIXED (reworded).
11. a test docstring still described the old format -> FIXED (instructs update).

The plan author already EXECUTED the two code-block fixes (#1 name-timing, #3
raw_focus-save) as a standalone script -- both pass. Do not re-litigate their
mechanics unless you find a NEW failure mode.

Do NOT resurrect: round-2 claimed H1 "fully resolved" via a backwards reading of
`_load_gate_backends` (it returns `([], {})` when untrusted, NOT `(cfgs, gd)`);
that false-green is exactly why H1 was fixed.

## Review this hardest (the 11 fixes CHANGED plan text -- attack the changes)

A. REPLAN(a) dual-tmpfile lifecycle: both tmpfiles created inside one
   try/except-unlink-both, name captured before the write that could fail, then
   an existing dispatch try follows. Reason through every exit path (creation
   failure, dispatch raise, inline return, timeout job-transfer, start_job
   raise): any double-unlink, leak, or unlink of a path a running job still
   owns? Does the new docstring over-promise vs the pseudocode?

B. The rewritten `_evict_stale` test (#5): the plan now injects a stale terminal
   job entry instead of using `max_lifetime_s`. From the plan's own description,
   would that test FAIL if the code under test forgot to add the new tuple key?
   Or is there still a path where it passes for the wrong reason?

C. 3b(d) sampling block (#3): is `raw_focus` saved BEFORE the merge reassigns
   `focus_spec`, and does the plan establish that the block's variables exist in
   the sampling dispatch function? Mark "unverified" where it depends on code
   you cannot see.

D. Cross-reference integrity: the edits changed line-number refs, test targets,
   and two headers. Reading the plan end to end, is any cross-reference now
   internally contradictory or pointing an implementer to the wrong place?

## Output format

First line: `SUMMARY: B=<n> H=<n> M=<n> L=<n>` (Blocker/High/Medium/Low).
Then per finding: severity, plan location (+ any cited file:line), description,
required fix. Mark anything you could not verify without the code as "unverified".

--- PLAN FOLLOWS ---
