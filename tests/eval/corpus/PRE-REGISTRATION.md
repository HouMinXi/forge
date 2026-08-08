# X-series pre-registration

Frozen 2026-08-01, BEFORE any result exists. Nothing below may be edited
after the first baseline run; a changed floor after seeing numbers is
goalpost-moving, and the record has to show which is which.

## The question

Does telling the reviewer to look for "a check whose outcome is fixed"
make it find that class of bug more often than it does now?

Not "is the pattern real" -- four instances turned up in one day, in three
different codebases, so it is. The question is whether a prompt can move
the detection rate, because prompts are the weakest enforcement layer and
this project has already watched a written rule fail to prevent its own
recurrence.

## What is being measured

Baseline arm: forge as it ships today, no prompt change.
Treatment arm: the same corpus with `review_focus` set to the text in
"Treatment text" below, in the harness gate.yaml.

Corpus: X1/X2/X3 (buggy) each paired with X1c/X2c/X3c (control).
Backend: one backend, the same in both arms, named in the results file.
Runs: 3 per entry (the default for non-deterministic tags), 18 reviews per
arm.

## Scoring, and why it is defined this way

`caught` = the review exited non-zero. That is the only signal that
reflects the three L1 passes; `expected_advisory` reads
advisory-findings.json, which the L1 passes never write, so it cannot be
used here.

Because "exited non-zero" is coarse -- forge can exit non-zero for reasons
unrelated to the planted bug -- a buggy entry counts ONLY when its control
does not. Per pair, per arm:

    DETECTED   buggy flagged >= 2/3 runs AND control flagged 0/3
    MISSED     buggy flagged <= 1/3 runs AND control flagged 0/3
    INVALID    control flagged >= 1/3 runs

An INVALID pair is thrown out of the numerator and the denominator and
reported as invalid. It does not count as a detection for either arm. If
two or more of the three pairs come back INVALID the whole run is void and
the instrument gets fixed before anything is concluded, because a
reviewer that flags correct code is not measuring the bug.

## Exit criteria, frozen

The treatment is adopted as a forge default only if BOTH hold:

  1. treatment DETECTED count > baseline DETECTED count, over >= 2 of 3
     valid pairs, and
  2. treatment introduces no new INVALID pair that the baseline did not
     have.

Equal counts mean "no evidence it helps" and the treatment is NOT adopted.
The prompt costs tokens on every pass of every review forever; the burden
of proof is on the change, not on the status quo.

If adopted, it goes to `review_focus` in gate.yaml first, per-project.
Promotion into REVIEW_JSON_CONTRACT (which every review pays for) needs a
second, separate decision with its own evidence.

## Treatment text (frozen)

    For every branch, guard, assertion and early-return in this diff, name
    the input that makes it take the other path. If you cannot name one,
    report it as a finding: a check whose outcome is fixed is not a check.

Deliberately a forced traversal rather than a warning. "Watch out for
checks that always pass" is a sentence a model agrees with and then does
nothing about; "name the input that flips each one" is a list it has to
walk. That distinction is itself untested, which is what this measures.

## Fixture validation, done before freezing

Every fixture was executed on both sides. A fixture nobody runs may
contain no bug at all -- the first draft of X2 was exactly that, and it is
recorded here rather than quietly fixed.

X1, guard whose condition is always false (JS, modelled on
bottleneck/lib/Job.js:162, verbatim shape). `doExpire` advances the job
state before asserting it; the paren sits inside the call, so the
comparison is passed as the job id.

    correct:  freed=1  threw "This job timed out after 15 ms."
    buggy:    freed=0  threw "Invalid job status RUNNING, expected EXECUTING."

freed=0 is the whole bug: the release never runs, so the slot leaks. This
is the real defect behind a day of proxy failures, and the error string
matches the one the real library emits.

X2, pipeline exit status masked by a trailing filter (shell). First draft
was NOT a bug: the base script began `set -uo pipefail`, and pipefail
makes the pipeline return grep's status, so the check behaved correctly.
Measured, caught, base changed to `set -u`, both diffs regenerated.

    with pipefail:     clean file -> PASS, dirty file -> FAIL   (correct)
    without pipefail:  clean file -> FAIL                       (the bug)

The real incidents were ad-hoc commands with no pipefail, so the corrected
fixture is the faithful one. Worth remembering that the rescue exists:
forge's own guard hook exempts pipefail for this reason.

X3, handler that is called from three sites and does nothing (Python,
modelled on TimeoutCircuitBreaker.record_other_error).

    buggy:    20 consecutive failures -> breaker count 0, never trips
    control:  trips at the third failure, as configured

## Not built, and why

X4 would have been "a signal computed, persisted, and never read" --
forge's own `pass_status`, written into every receipt by
`derive_pass_outcomes` and consumed by nothing. It is left out because the
bug is an ABSENCE somewhere else in the tree, and a reviewer holding only
a diff that adds the field cannot know whether a consumer exists. Making
it detectable means putting the would-be consumer in the same diff -- a
verification routine with N checks, none of which reads the new field --
which is buildable and faithful, just not built yet. Adding it later does
not disturb the frozen criteria above: it is a fourth pair, scored the
same way.

## Known limits of this experiment

Three pairs is small. A one-pair difference between arms is inside the
noise of three LLM runs, which is why the exit criterion asks for a
majority of valid pairs rather than any improvement at all.

The per-review timeout in the eval runner (1800s, or whatever
FORGE_EVAL_REVIEW_TIMEOUT_S is set to) may still be shorter than a full
review on a slow backend; a run that trips it is recorded as an infra
skip, not as a miss. If most runs come back skipped, the backend is too
slow for this harness and the arm has to be re-run elsewhere rather than
read as a result. The earlier 300s could not fit even one round on a
reasoning backend, which is what prompted the change; no baseline had
been run at that point, so nothing measured is affected.

Detection of a planted bug in a small diff is not the same as detection in
a real 20KB change. This measures the easier case. A treatment that fails
here almost certainly fails in the harder one; a treatment that succeeds
here has not yet been shown to survive it.
