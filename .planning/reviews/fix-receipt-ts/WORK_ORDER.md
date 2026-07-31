# Work order: land the receipt timestamp fix (wall 3)

You are a sub-session executing a pinned task inside the forge repo.
Everything below is measured ground truth as of 2026-07-31, main @
1fb3eea. Re-verify anything you depend on with a command; if the ground
contradicts this order, STOP and report the contradiction instead of
guessing.

## Ground truth (re-check these first)

- Worktree: /home/houminxi/code/forge/.worktrees/fix-receipt-ts
  Branch defects/receipt-timestamp @ 695f739, carrying an UNCOMMITTED
  4-file diff:
    src/code_forge/receipt.py    (replaces per-pass +pass_idx second
                                  timestamp offset with one now per round)
    src/code_forge/verify.py     (a (cycle,pass) sort)
    tests/test_receipt.py        (new timestamp tests, 71 lines)
    tests/test_verify.py         (sort tests, 33 lines)
- Main @ 1fb3eea ALREADY CONTAINS the verify-side work: commit e29f9f4
  shipped _load_receipts sorting by (r["cycle"], r["pass"]) plus the
  last-three-consecutive-cycles counting, and its tests. Confirm:
    git -C /home/houminxi/code/forge log --oneline -5
    grep -n "r\[.cycle.\], r\[.pass.\]" /home/houminxi/code/forge/src/code_forge/verify.py
- So the verify.py and test_verify.py hunks in the working diff are
  SUPERSEDED. Do not carry them. Your deliverable is the receipt side
  ONLY: receipt.py change + test_receipt.py tests, re-anchored on main.
- The rationale for the fix: write_receipts stamps each pass receipt
  with now + timedelta(seconds=pass_idx). Rounds complete within the
  3-second window, so a slow next round looks OLDER than a fast prior
  round to any consumer ordering by timestamp. Verify does not consume
  timestamps today (it sorts by cycle/pass), but the offset is dead
  weight that leaks a wrong ordering signal to any future consumer.
  This is the open wall 3 in memory feedback_attestation_bootstrap_deadlock.

## Scope fence (what you may and may not touch)

- MAY: rebase the branch onto 1fb3eea; reduce the working diff to the
  receipt side only (src/code_forge/receipt.py + tests/test_receipt.py);
  adjust test_receipt.py additions so they pass against main's current
  layout. Deliver the change UNCOMMITTED as a diff on the branch.
- MAY NOT: commit, merge, push, install/reinstall hooks, park any
  hook, touch verify.py, test_verify.py, lock.py, cli.py, delete
  branches or worktrees, run code-forge review (the main session owns
  the external review panel), edit .planning/.
- Commit message: subsystem/case subject, WHY-body (rounds complete
  inside the offset window; the offset inverts cross-round chronology
  for any consumer). No review vocabulary, no task IDs, no severity
  labels. Signed-off-by: Minxi Hou <houminxi@gmail.com>.

## Required work (each step has a checkable done-condition)

1. Ground-read receipt.py:70-105 on main and the working diff; state in
   your report the exact line changes you are carrying.
2. Rebase defects/receipt-timestamp onto 1fb3eea; drop verify.py and
   test_verify.py changes (git restore those two paths); keep receipt.py
   and test_receipt.py as the deliverable diff. Done-condition:
   `git -C .worktrees/fix-receipt-ts status -sb` shows ONLY those two
   paths modified.
3. Tests: PYTHONPATH=src python3 -m pytest tests/test_receipt.py
   tests/test_verify.py -q passes in the worktree. Record the counts.
4. Bug-injection on the fix line: restore
   `ts = now + datetime.timedelta(seconds=pass_idx)` semantics (undo the
   fix in place), rerun tests/test_receipt.py -- at least one of the new
   tests MUST go red; revert with the backup copy you made first, md5
   must match. Record test names that went red.
5. Do NOT commit. Leave the change uncommitted as a clean diff on the
   rebased branch. The main session owns committing after the external
   review panel converges; a self-issued commit is outside the contract.
6. Report (evidence-before-conclusion): branch state, diffstat, pytest
   output lines, injection red test names, restore md5, any ground
   contradiction found. UKNOWN/BLOCKED is an acceptable outcome;
   guessing is not.

## Exit verifier (frozen by the main session -- do not optimize to it)

a. Branch tip is a rebase of defects/receipt-timestamp onto 1fb3eea
   with ZERO new commits; the working diff touches only receipt.py and
   tests/test_receipt.py.
b. grep -n "timedelta(seconds=pass_idx)" on receipt.py returns nothing.
c. Re-running steps 3-4 by the main session reproduces your numbers.
d. Held-out check (not for you to run): main session writes a receipt
   set spanning a round boundary of <3 seconds and confirms ordering
   interpretation no longer depends on the offset.
