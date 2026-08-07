# R1 -- L2 finally ran, but the LLM layer was replayed, not re-reviewed

Run 2026-07-31 14:07 EDT, same backend (gemini-omniroute), after adding
the missing test: section to the worktree gate.yaml.

What improved: infra_errors is now empty -- the L2 commit gate executed.

What did NOT happen: the LLM passes did not re-run.

    metric              R0 (13:49)      R1 (14:07)
    input tokens        41515           41515
    output tokens       12155           12155
    duration            10.4s           0.1s
    source_hash         795e6f74db9a    795e6f74db9a

Receipt bodies with the timestamp field stripped hash identically
across the two rounds:

    receipt-c1p1  508f7f910144 == 508f7f910144
    receipt-c1p2  045e7c1049d7 == 045e7c1049d7
    receipt-c1p3  97acc7e0793e == 97acc7e0793e

So the receipts carry a FRESH timestamp over a STALE body. Anything
counting rounds by receipt mtime would score this as a second
independent clean cycle. It is not one. CP3's "3 consecutive 0/0/0/0"
cannot be satisfied by re-running the same backend against an
unchanged source_hash.

## Second finding: forge reviews with the UNFIXED receipt.py

R1's own receipts are stamped 1 second apart:

    c1p1 18:07:04.778170
    c1p2 18:07:05.778170
    c1p3 18:07:06.778170

That +1s/pass ladder is the exact defect this delivery removes.
code-forge-mcp runs `#!/usr/sbin/python3` and bare python3 resolves
code_forge to /home/houminxi/code/forge/src (MAIN TREE), so the review
tool executes the unfixed writer while reviewing the fix for it.

Consequence: any two forge rounds finishing within 3 seconds of each
other produce a receipt set that run_verify rejects with "timestamps
not monotonic". The commit comment's claim that this "failed a
converged review" describes a real incident, not a hypothetical.

Bootstrap note: the fix cannot attest itself through the MCP path
until the tool runs the fixed code. See memory
feedback_attestation_bootstrap_deadlock (wall 3).

## CORRECTION appended after further measurement

The claim above that "L2 finally ran" is WRONG and is retracted.

Empty infra_errors proves only that the config check passed and the
gate process was SPAWNED. mutation-result.json shows:

    pid 2911614, started_at 1785521224.783609,
    status "running", survivors []

state.json mtime is 1785521224.783980 -- the PASS verdict was written
0.4 milliseconds after the mutation gate started. pid 2911614 is now
dead and the status field is permanently "running". survivors:[] is
the initial value, never a result.

So R1 is: LLM layer replayed from R0, mutation gate spawned and
abandoned, verdict PASS. No layer of R1 verified anything.
