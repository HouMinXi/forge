# Controlled A/B: does annotating the diff improve excerpt line numbers?

Run by the PM session 2026-07-31, because the delivery marked this obligation
"not measured". Method is the one the dispatch order specified.

## Design

Everything held constant except the annotation:

    diff        f4d845dfe724103d      identical in both arms
    path        outlet=subprocess -> cli.py:2628 -> build_l1_provider
                (verified this is one of the four annotated sites)
    backend     gemini-omniroute, 3 passes, --mode local --max-total-rounds 1
    control     PYTHONPATH=<main>/src   -- verified: annotate_diff_lines absent
    treatment   PYTHONPATH=<worktree>/src -- annotation live

Measurement: for every `code_excerpts` entry, compare the first line of
`content` against the real file at `start_line`.

## Result

    CONTROL  (raw diff)   21/57 = 36.8%
    ANNOTATED             63/75 = 84.0%
    DELTA                        +47.2 points

Miss offsets tell the mechanism apart:

    control    1, 8, -2, -77, 1, 1, -2, -31, -1, 1, -311, 3, 1, 8
    annotated  2, 2, -1, -2, 2, 2, -1, -2, 2, 2, -1, -2

Without the numbers the model's errors are unbounded -- it loses the position
entirely (-311, -77, -31). With them every residual error is within +/-2 and
repeats identically across the three passes, which is a systematic off-by-one,
not lost tracking.

## Token cost: the reported number measures the wrong thing

    control total    138570 tokens (102664 in + 35906 out)
    annotated total  139059 tokens (108829 in + 30230 out)
    delta            +489 tokens = +0.35%

The delivery briefing reported +12% and the PM's own re-measure said +17.3%.
Both measured the diff text in isolation. The prompt is dominated by the
`## Post-Image` block (whole file contents), so annotation overhead against the
real prompt is +0.35%. The two earlier numbers are not wrong arithmetic, they
answer a question nobody asked.

## Bracket contamination: measured, not assumed

The prompt never explains the `[  792]` tag, and its existing instruction
("no +/- prefixes, no @@ headers") predates the format. The risk was that the
model copies the tag into `content` and fails STEP C on every excerpt.

    excerpts carrying a bracket tag in content: 0 / 78

Not a problem with this backend. It is still undesigned -- nothing tells the
model the bracket is the number to report, and a weaker model could copy it.

## What this does NOT unblock

STEP C fails the whole attestation on any content mismatch. 12 of 75 excerpts
still land on the wrong line, so `code-forge verify` would still fail here.
Annotation moves the wall a long way; it does not remove it. The residual is
systematic (+/-2, repeating), so it is worth one more look before assuming the
remaining 16% is model noise.
