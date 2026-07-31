# Evidence loss disclosure (S2 violation), 2026-07-31

Branch feat/context-quotes was landed on main as e29f9f4, 67d6cd4,
1cc45b0 using worktree .worktrees/b2-integrate. git worktree remove
then deleted the worktree -- and with it r4 and r5 evidence, which
lived only inside that tree.

## What was lost (cannot be recovered)

- r4/packet.md and r4/{kimi,gemini,deepseek}.md -- the round-4
  convergence brief and the three reviewer outputs as received.
- r5/packet.md and r5/{kimi,gemini,deepseek}.md -- the round-5
  convergence brief and the three reviewer outputs.
- r2/heldout-run.txt -- the held_out adversary output on context_quotes
  (the script itself, r2/heldout_context_quotes.py, is preserved here).
- r4/r5 dry-run verification outputs tied to those packets.

## What survives (restored from .worktrees/fix-verify-gate, itself a
sibling worktree that was never removed)

- r1/packet.md + r1/{kimi,gemini,deepseek}.md
- r2/packet.md + r2/{kimi.retry2,gemini,deepseek}.md +
  r2/heldout_context_quotes.py
- r3/packet.md + r3/{kimi,gemini,deepseek}.md
- All adversary scripts and per-finding verification logs for rounds
  1-3 (c4_valid, verify_ds_claims, verify_gm_claim, *_branch/main.txt)
- change-c-shelved.patch + BRIEF.md

## Root cause (my own process failure, disclosed per S1)

The S2 rule says every review round's prompt, response, fix summary,
and exit scorecard goes into .planning so artifacts survive anything.
b2-integrate provided that space, and I never moved the accumulated
evidence out of the worktree before its scheduled removal. The copy
into fix-verify-gate happened only because the same directory existed
there by accident of a parallel branch; nothing in my process made
sure of it. fix-verify-gate being alive after this landing is luck,
not design.

The regressions were not code regressions: main holds every fix the
review rounds produced, both by construction of the split and by the
final md5 and full-suite runs recorded at the time. What is lost is
the review-pipeline evidence that the fixes are the right fixes,
which matters for any future audit of "how was this tree produced".

## Mitigation applied now

- r1-r3 + scripts + per-finding logs moved to /home/houminxi/code/forge/.planning/reviews/verify-gate-alignment/
- snapshot-planning.sh run, planning-local @ 8623af9cd114 covers
  everything present at this writeup time.

## Process change this forces

- Before any `git worktree remove`, any .planning subtree under that
  worktree must be copied to a planning-tree OUTSIDE the worktree
  being removed, and a snapshot-planning run must complete first.
- A session that knows it will not return to a worktree should write
  the convergence STATUS file to the destination before the worktree
  is ever considered finished; otherwise the STATUS file goes down
  with the tree.
