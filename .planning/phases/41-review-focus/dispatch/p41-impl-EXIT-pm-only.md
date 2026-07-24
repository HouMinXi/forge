# Phase 41 impl -- EXIT verifier (PM-ONLY -- do NOT send to the executor)

FROZEN 2026-07-24, before any delivery exists (pre-registration: the bar is set
before the result, so it cannot be moved to fit what came back). Run against the
executor's branch on return. The executor's own pasted output is a CLAIM; only the
PM's independent re-run is evidence.

## A. Mechanical verifier (PM runs every line; executor output is never trusted as-is)
1. Diff scope -- `git diff --stat master..<branch>` touches ONLY:
   cli.py, factories.py, git.py, legacy.py, trust.py (if the focus trust-hash lands
   there), mcp_server.py, mcp_jobs.py, the gate schema + template, tests/*.
   It must NOT re-touch the sampling contract_spec lines from 2edb9d4 or the
   _dispatch_cli tmpfile lines from 5c8e001 -- confirm with
   `git range-diff` or a targeted `git diff master..<branch> -- <those hunks>`.
   It must NOT touch any kimi file.
2. Header rename -- `grep -rn "Contract Reference" src/ tests/` == 0;
   `grep -rn "Design Intent" src/` >= 3 (the 3 builder sites).
3. Full suite, PM's OWN run (not the executor's pasted text):
   `cd <worktree> && python3 -B -m pytest tests/ -q` -> zero failures, zero errors.
4. Non-ASCII gate -- `git diff master..<branch> | grep '^+' | grep -P '[^\x00-\x7F]'` == empty.
5. Commit hygiene -- `grep -rnE '#.*(F[0-9]+:|[Dd]-[0-9])' src/ tests/` == empty;
   read every commit message: no P0-P3, no task labels, no review vocabulary,
   Signed-off-by present and correct (houminxi@gmail.com, never an AI co-author).
6. Must-Haves -- tick each of the 8 in the plan header against the actual diff.
7. Coverage -- each new/changed logic line executed by a test (diff-cover, or
   `pytest --cov` cross-referenced with the diff). "Suite passes" proves liveness,
   not coverage (forge principle 5).

## B. Held-out adversary (NOT in the work order -- the executor must not know to pre-satisfy it)
Run at least one, blind, after A is green:
- A) Re-do the Task 1 per-site bug-injection MYSELF: revert factories.py:576 to
  "Contract Reference", run ONLY the test the executor claims covers that site. If
  it still PASSES, the assertion is silently testing the wrong thing (forge's exact
  639-mock false-green failure mode). Repeat for whichever site the executor's
  report is thinnest on. The work order tells the executor to bug-inject; it does
  NOT tell it that the PM independently re-injects the site the report under-covers.
- B) Trust-hash tamper (security edge): build a gate.yaml with `review_focus:`,
  trust it, then hand-edit the focus content AFTER trust (post-trust-edit). Confirm
  the focus is DROPPED with a warning AND backend loading still succeeds
  (Must-Have: "untrusted or post-trust-edited focus is dropped ... must not break
  backend loading"). The order lists this Must-Have but never says the PM tests the
  post-trust-EDIT path specifically -- that is the un-gameable check.
- C) Degenerate focus: `--focus` an empty file, and a missing file -> no crash,
  graceful skip (the plan's empty/None-input discipline).

## C. Gate
Phase 41 impl advances to CP3 (separate-model forge review) ONLY after A is fully
green AND at least one B check passes. Any A failure or B false-green -> return to
the executor with the specific evidence (per the non-convergence protocol: state
what failed, what to fix, and what was already verified clean -- never a bare
re-dispatch). The executor's "DONE" is never the gate; this file is.

## D. Dispatch reliability note (window-specific, 2026-07-24)
mimo-pro rides an aicc CN gateway. This window, the CN panel (ds/lc/kimi) failed
wholesale on infrastructure (drift / drops / engine-overload). mimo-pro was NOT
tested this window and need not be as clogged, but if its dispatch also fails,
fall back to: (a) a copyable work-order the user forwards through a trusted
channel, or (b) a local gsd-executor subagent. Do NOT silently retry a clogged
gateway -- diagnose the failure mode first (the ds/kimi lesson).
