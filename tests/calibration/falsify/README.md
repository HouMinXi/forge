# Falsify calibration set

Twenty real L1 findings with a hand-written expected verdict each, used to
measure how often the falsifier agrees with a human on the findings it
actually sees in production.

## Where the findings come from

`scripts/dump_calibration_candidates.py` re-ran the twenty entries in
`entries.txt` (ten `-bug`, ten `-clean`, one pair per repository where the
corpus allowed) at depth 1 with `replay_entry(keep_state_dir=...)`, which
copies each entry's `state.json` out before the eval runner removes its
temp directory. Every L1 finding from those files went into
`candidates.json` (62 findings from 20 entries on 2026-09-05).

`scripts/freeze_calibration_labels.py` picks one finding per entry by a
description fragment and writes `expected.json`. The fragment, the
expected verdict, the reason and the evidence line were written by hand
after reading each entry's diff and the SWE-bench issue behind it.

## Label rules

- `-bug` entry: the chosen finding overlaps an answer-key hunk in
  `.eval-corpus/corpus.yaml`. Expected `CONFIRMED`. Evidence names the
  hunk.
- `-clean` entry: the chosen finding is one the model marked `CONFIRMED`
  at dump time where such a finding existed, otherwise `UNCERTAIN`.
  Expected `DISMISSED`. Evidence names the line or upstream fact that
  refutes it.

Seven of the ten clean-side picks are the model confirming that the
upstream fix is a defect (for example: "removing `.lower()` makes term
registration case-sensitive", on the diff whose whole point was to stop
lowercasing). That pattern is why the set exists.

## Labels are frozen

`expected.json` carries `frozen_at`, the commit it was written at. A
label change after a real calibration run is its own commit, with the
reason in the message and a line added here. Do not edit a label to make
a run pass.

## Running

    PYTHONPATH=src /usr/bin/python3 scripts/run_falsify_calibration.py \
        tests/calibration/falsify --engine real

Exit 0 when agreement is at or above `--min-agree` (default 0.9), 1 when
below, 2 when the backend could not answer (`LLMInvokeError`, including
`FalsifyProtocolError`). Each miss prints `MISS <id> expected=<x> got=<y>
why=<why>`.

The `--engine stub` and `--engine binary` arms exist for the state
machine's own tests; only `real` measures anything.

## Runs

| date | commit | engine | agree | log |
|---|---|---|---|---|
| 2026-09-05 | 10bb893 | real (mimo-v2.5-pro) | 8/20 (40%) | `.planning/eval/phase-59/falsify-calibration-r1-merged.log` |

r1 notes: bug side 6/10 (c01 c08 UNCERTAIN, c06 c09 DISMISSED); clean side
2/10, seven of the eight misses are the falsifier CONFIRMING a finding that
calls the upstream fix a defect. c14's finding text says "Good change" and
was still CONFIRMED. The run was interrupted at 14/20 by a gateway restart
and resumed with `--only c15,...,c20`; both logs are merged above.
