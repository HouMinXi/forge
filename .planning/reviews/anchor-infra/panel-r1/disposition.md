# Round 1 disposition (deepseek, stopped after round 1)

Stopped deliberately at round 1 rather than letting the 12-round cap run:
both substantive findings were legitimate and answerable, and eleven more
rounds against a diff about to change would have measured the wrong thing.
Round 1 receipts kept in receipts-round1-realpath-proof/.

## What round 1 produced

- c1p1 qodo: no findings. The pass never reached the model -- deepseek
  exceeded its 600s read deadline, so the receipt carries the infra finding
  and pass_status=timeout.
- c1p2 expert: 1 CONFIRMED, 1 UNCERTAIN.
- c1p3 adversarial: 2 UNCERTAIN.

Expert and adversarial reached the same two points independently, which is
the signal worth acting on.

## Finding 1: the write-time filter does not repair receipts on disk

ACCEPTED AS ACCURATE, SCOPE UNCHANGED. A write-time filter cannot reach
files already written; that is the shape of the fix, not an oversight. The
sets poisoned before this lands still have to be deleted and their reviews
re-run, which was already the plan for the mutation-gate panel.

Considered and rejected: filtering sentinels at read time in verify's
anchor check instead, which would rescue historical sets. Rejected because
it would not have rescued the set that prompted this -- those receipts also
fail check 5 on excerpt misalignment, which is verify working correctly --
so it buys robustness for a case that has not occurred while making verify
lenient about what a receipt may contain. The producer is where the
contract is broken.

Now stated explicitly in the commit message rather than left implicit.

## Finding 2: no test exercises a pass whose only finding is INFRA

ACCEPTED AND FIXED, and the reviewers understated it. Measured rather than
reasoned about, in two variants:

    infra-only pass in cycle 1  (outside the attested window)  -> passes
    infra-only pass in cycle 4  (INSIDE the last three)        -> FAILS
                                          coverage 52% < 60% cycle 4

So for a failure inside the attested window the fix only moves the refusal
from check 3 to check 6. That is correct behaviour, not a residual bug: a
cycle whose pass never ran genuinely read less of the diff, and letting it
through would attest a three-pass cycle that ran two. Both variants are now
pinned by tests so the line cannot move by accident.

The original test was also the wrong shape -- it paired the infra finding
with a normal code finding in the same pass. A real timed-out pass returns
the infra finding alone, as c1p1 above shows. Rewritten to match, and the
bug-injection redone against the new shape rather than inherited from the
old one.

## Ground truth the round did not disprove

Nothing in round 1 contradicted the fix's core claim. The run itself
became evidence for it: the deepseek timeout on c1p1 is exactly the
failure the fix exists for, it happened on the pinned (fixed) engine, and
the receipt shows the sentinel in findings and absent from anchors.
