# Measured review quality

[中文版](EVALUATION.zh-CN.md)

This page reports what code-forge's review loop measured against a corpus of
real defects. Every number below was produced by `scripts/analyse_arms.py`
from the run ledgers, and the analyser prints the ledger line numbers behind
each aggregate. Nothing here is an estimate presented as a result.

Read the caveats before quoting anything.

## Corpus

150 entries built from `princeton-nlp/SWE-bench_Verified` (test split) by
`python3 -m code_forge.eval.build_corpus`:

- 75 defect entries: the upstream fix patch applied in reverse, so the diff
  under review introduces the bug the issue describes. The answer key is the
  fix's file and line range plus the first line of the issue text.
- 75 clean controls: the fix itself, applied forward. Each control asserts
  that no finding belongs to it, so a reviewer that flags every diff is
  charged for it.
- 11 repositories (astropy, django, matplotlib, seaborn, requests, xarray,
  pylint, pytest, scikit-learn, sphinx, sympy), at most 8 instances per
  repository. 357 of the 500 SWE-bench Verified instances met the size
  filters; the rest were rejected for being pure additions, spanning too
  many files or hunks, or having an unusable problem statement.
- Seed-reproducible. The generator rebuilds the committed tree byte for
  byte from seed `20260830`; `.eval-corpus/PROVENANCE.json` records the
  seed, the rejection counts and a sha256 per diff.

One structural limitation of the corpus is worth knowing before reading the
numbers. Base files are reconstructed from each patch's own context lines,
so the reviewer sees the hunk neighbourhood and nothing else. The review
task is therefore harder than a real review, where surrounding code is
available.

## Setup

- Backend: `mimo-v2.5-pro` on the depth and ablation arms. Switching
  models mid-sweep would confound those comparisons. Later depth-1 arms
  used `agnes-cn`, `qwen-27b-dflash`, the international Agnes effort
  ladder (`agnes-intl` / `-2` / `-3`), and `onmi-gemini3.6`; those
  comparisons are their own sections.
- One run per entry. Replicates at three depths would have cost over 100
  hours of review time; the consequence is stated under caveats.
- Entry-level scoring: a defect entry counts as caught when the verdict is
  HOLD; a clean control counts as passed when the verdict is PASS. Precision
  and recall are computed over those two counts.
- Finding-level scoring: each CONFIRMED finding in the run's final state is
  matched against the answer key by file, line range and token overlap.
  Hits, misses and false positives are summed across entries.

## Depth sweep

Three arms on the same 150 entries, differing only in
`FORGE_CLEAN_ROUND_THRESHOLD`: how many consecutive clean rounds the loop
needs before it declares PASS.

Entry-level, n=150 per arm (75 defect + 75 clean):

| Depth | Defects caught | Controls passed | Recall | Precision | F1 | Wall per entry |
|---|---|---|---|---|---|---|
| 1 | 52/75 | 34/75 | 0.693 | 0.559 | 0.619 | 429 s (SE 19) |
| 2 | 57/75 | 24/75 | 0.760 | 0.528 | 0.623 | 396 s (SE 21) |
| 3 | 60/75 | 33/75 | 0.800 | 0.588 | 0.678 | 308 s (SE 16) |

Finding-level, depths 2 and 3 only (149 of 150 scored in each; one entry
per arm was SKIPPED by the harness):

| Depth | Hits | Misses | False positives | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| 2 | 72 | 76 | 119 | 37.7% | 48.6% | 0.425 |
| 3 | 75 | 77 | 112 | 40.1% | 49.3% | 0.442 |

The depth-1 ledger carries verdicts only. Finding counts were added to the
ledger format after that arm had started, and it was not restarted; the
entry-level comparison does not need them.

What the sweep says, at one run per entry:

- Recall rises with depth: 52, 57, 60 of 75 defects. Depth 3 is not noisier
  than depth 1 on clean entries (33 vs 34 passed).
- Precision does not move with depth. It sits between 53% and 59% at every
  depth, entry-level, and between 38% and 40% at finding level. Depth buys
  recall; the false-positive rate is a separate problem.
- The wall-clock column falls as depth rises, which cannot be a depth
  effect since deeper arms run more rounds. The three arms ran back to back
  over 16 hours and backend latency drifted across the day. Do not read
  that column as a cost of depth.

## Falsification ablation

Every reviewer candidate passes through a falsification gate before it can
become a finding: the model is asked to disprove its own claim, and only a
claim that survives is CONFIRMED. The gate costs a model call per candidate.
This experiment measures what it buys.

Two arms at depth 3, both capped at `FORGE_MAX_TOTAL_ROUNDS=3`. The cap is
load-bearing: with the gate off nothing is ever rejected, so every round
resets the clean counter and the loop would run to its default bound of 20
rounds. Cap 3 matches the deepest depth-sweep arm so the two experiments
stay comparable.

Entry-level, n=150 per arm:

| Arm | Verdicts | Defects caught | Controls passed | Wall per entry |
|---|---|---|---|---|
| Gate on (`auto`) | HOLD 107, PASS 42, SKIPPED 1 | 62/75 | 30/75 | 458 s (SE 20) |
| Gate off (`stub`) | HOLD 150 | 75/75 | 0/75 | 74 s (SE 6) |

Finding-level, gate-on arm (149 of 150 scored):

| Arm | Hits | Misses | False positives | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| Gate on (`auto`) | 72 | 79 | 121 | 37.3% | 47.7% | 0.419 |

Finding-level numbers for the gate-off arm are not available. Under the
stub engine every candidate is marked CONFIRMED, the round resets each
cycle, the run stops at the cap, and the review does not produce scorable
findings; the ledger records 0 hits, 0 false positives and 152 misses,
which is that harness gap and not a measurement of the model. Only the
entry-level comparison between the two arms is valid.

The entry-level reading is unambiguous. With the gate off, every one of the
150 entries exits HOLD, including all 75 clean controls. The pipeline has no
discriminating power without the gate; the gate is the component that lets
a clean diff pass at all. It is also where most of the review time goes:
74 s per entry without it against 458 s with it.

The gate-on arm at depth 3 with a three-round cap and the depth-3 sweep arm
without a cap are close on every entry-level count (62 vs 60 defects caught,
30 vs 33 controls passed), which is the agreement one would expect from two
runs of the same configuration at one sample each.

## Backend swap

The depth sweep and the falsification ablation both used `mimo-v2.5-pro`.
A later arm kept depth 1, `engine=real`, one run per entry, and the same
150-entry corpus, and changed only the review backend to `agnes-cn`
(Agnes 3.0 Flash).

Entry-level:

| Backend | Defects caught | Controls passed | Recall | Precision | F1 | Wall per entry |
|---|---|---|---|---|---|---|
| `mimo-v2.5-pro` (depth 1, from the sweep above) | 52/75 | 34/75 | 0.693 | 0.559 | 0.619 | 429 s (SE 19) |
| `agnes-cn` (depth 1) | 57/75 | 18/75 | 0.760 | 0.500 | 0.603 | 45 s (SE 2) |

Finding-level, `agnes-cn` only (all 150 scored). The depth-1 mimo ledger
is verdict-only, so there is no finding-level comparison at this depth.

| Backend | Hits | Misses | False positives | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| `agnes-cn` | 59 | 93 | 196 | 23.1 | 38.8 | 0.290 |

What this says, one run per entry:

- Recall went up: 57 of 75 defects against 52. Precision went down: 18 of
  75 clean controls passed against 34, so 57 clean diffs were flagged HOLD
  against 41 on mimo.
- Entry-level F1 is close (0.603 against 0.619). Finding-level precision
  on `agnes-cn` is 23.1%, below the depth-2/3 mimo figures (37.7% / 40.1%).
  That is a different depth and a different ledger format; it is not a
  head-to-head at finding level.
- Wall-clock is not a model ranking. The caveat below still holds: latency
  moved more than configuration. The 45 s figure is what the ledger
  recorded.

## Local 27B with DFlash2 (depth 1)

A later arm kept depth 1, `engine=real`, one run per entry, and the same
150-entry corpus. The review backend was `qwen-27b-dflash`: Qwen3.8-27B
UD-IQ4_XS with a DFlash2 Q4_K_M draft, served by llama.cpp on a single
RTX 3080 20 GB. The knowledge-base reranker and query service on that
host were stopped so the card was exclusive. Forge reached the server
through an SSH tunnel to `127.0.0.1:8081`. Thinking was off
(`enable_thinking: false`). The serving binary was llama.cpp `64e9bce`
(DFlash2). The older production binary `eb25b72` cannot load this draft
(`wrong number of tensors; expected 81, got 58`).

Ledger: `docs/eval/qwen-dflash-d1.jsonl`
SHA-256: `0b72b0531a77da63c0e9bb4fc7daa50c5e55e35165ea36baa3047332b688925c`

Entry-level:

| Backend | Defects caught | Controls passed | Recall | Precision | F1 | Wall per entry |
|---|---|---|---|---|---|---|
| `mimo-v2.5-pro` (depth 1, from the sweep above) | 52/75 | 34/75 | 0.693 | 0.559 | 0.619 | 429 s (SE 19) |
| `agnes-cn` (depth 1) | 57/75 | 18/75 | 0.760 | 0.500 | 0.603 | 45 s (SE 2) |
| `qwen-27b-dflash` (depth 1) | 41/75 | 44/75 | 0.547 | 0.569 | 0.558 | 85 s (SE 6) |

Finding-level, all 150 scored (`agnes-cn` and `qwen-27b-dflash`). The
depth-1 mimo ledger is still verdict-only.

| Backend | Hits | Misses | False positives | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| `agnes-cn` | 59 | 93 | 196 | 23.1 | 38.8 | 0.290 |
| `qwen-27b-dflash` | 33 | 119 | 91 | 26.6 | 21.7 | 0.239 |

What this says, one run per entry:

- Recall dropped: 41 of 75 defects against 57 on `agnes-cn` and 52 on
  mimo. Precision rose on the clean side: 44 of 75 controls passed
  against 18 on `agnes-cn` and 34 on mimo.
- Entry-level F1 0.558 is below both cloud arms. Finding-level F1 0.239
  is below `agnes-cn` 0.290: fewer false positives (91 against 196) and
  fewer hits (33 against 59).
- Wall-clock 85 s per entry, total 3.5 h, SKIPPED 0. The 3080 was
  otherwise idle, so this wall column is less mixed with shared-endpoint
  latency than the cloud arms. It is still not a model ranking.
- This arm does not replace `agnes-cn` as the default review backend.
  Missed defects cost more than extra false positives on this corpus.

Weights and flags used on that host:

- Target: `Qwen3.8-27B-UD-IQ4_XS.gguf`
- Draft: `Qwen3.8-27B-DFlash2-Q4_K_M.gguf` (SHA-256
  `1a25c56858e1ebe93f2718ac1d49d1151f9323325c1bbfd6209370f4db131ebd`),
  `--spec-type draft-dflash`, `--spec-draft-n-max 8` (clamped to 7)
- Bind: `127.0.0.1:8081`. VRAM with the pair loaded: about 19394 of
  20480 MiB.

Reproduce this arm (tunnel to 8081 and the `qwen-27b-dflash` user
backend already in place):

```bash
FORGE_CLEAN_ROUND_THRESHOLD=1 FORGE_LOCAL_KEY=local code-forge eval \
    --corpus tests/eval/swebench/corpus.yaml --backend qwen-27b-dflash \
    --jobs 1 --runs 1 --arm-depth 1 \
    --resume-log docs/eval/qwen-dflash-d1.jsonl
python3 scripts/analyse_arms.py docs/eval/qwen-dflash-d1.jsonl
```

## International Agnes effort and Gemini (depth 1)

Four later arms kept depth 1, `engine=real`, one run per entry, and the
same 150-entry corpus. They differ only in the review backend. All four
ran through the public OmniRoute endpoint from the same eval host, one
job at a time.

The three Agnes arms are combo members on the international provider,
not `agnes-cn`:

- `agnes-intl`: `agnes/agnes-3.0-flash-none` (short probe: no reasoning
  tokens)
- `agnes-intl-2`: `agnes/agnes-3.0-flash-xhigh`
- `agnes-intl-3`: `agnes/agnes-3.0-flash-max`

The fourth arm is `onmi-gemini3.6`. Combo members are
`agy/gemini-3.8-flash-high`, routed as `antigravity/gemini-3.8-flash-high`.

These rows are not a ranking against the published `agnes-cn` line
above. That arm used a different site and account pool, and the ledger
does not record which effort pin the combo held at the time. Do not read
the 45 s `agnes-cn` figure as "the same model with thinking off".

Ledgers (SHA-256 of the jsonl file):

| Backend | Ledger | SHA-256 |
|---|---|---|
| `agnes-intl` (none) | `docs/eval/agnes-intl-none-d1.jsonl` | `aa21019548db1f2a5c19852ab14b9c3fba91474b38f4f467a95e086963228b8f` |
| `agnes-intl-2` (xhigh) | `docs/eval/agnes-intl-xhigh-d1.jsonl` | `ddb668b865cdddecd890cb5a529ea551310951c32fd41cb8dbb3a0fd0e541988` |
| `agnes-intl-3` (max) | `docs/eval/agnes-intl-max-d1.jsonl` | `1740ca68f9f4bed41245997c69e638cc111744a0d173fbfc9913de814626e071` |
| `onmi-gemini3.6` | `docs/eval/onmi-gemini36-d1.jsonl` | `850961d4d1741c5e91afa10109fa583a2e2e0b753d8e1615e4261eb87d752c3c` |

SKIPPED rows, all `infra: code-forge review timeout after 3600s`:

- xhigh, ledger lines 52 and 104: `psf__requests-1142-clean`,
  `scikit-learn__scikit-learn-10297-clean`
- max, lines 13, 32, 111: `astropy__astropy-7336-bug`,
  `django__django-16595-clean`, `scikit-learn__scikit-learn-14087-bug`
- gemini, line 51: `matplotlib__matplotlib-20676-bug`
- none: none

Entry-level, n=150 per arm. A SKIPPED defect counts as a miss and a
SKIPPED control as passed, same rule as the rest of this page.

| Backend | Defects caught | Controls passed | Recall | Precision | F1 | Wall per entry |
|---|---|---|---|---|---|---|
| `agnes-intl` (none) | 63/75 | 31/75 | 0.840 | 0.589 | 0.692 | 156 s (SE 10) |
| `agnes-intl-2` (xhigh) | 45/75 | 50/75 | 0.600 | 0.643 | 0.621 | 268 s (SE 34) |
| `agnes-intl-3` (max) | 45/75 | 51/75 | 0.600 | 0.652 | 0.625 | 284 s (SE 40) |
| `onmi-gemini3.6` | 74/75 | 6/75 | 0.987 | 0.517 | 0.679 | 229 s (SE 24) |

Finding-level from `scripts/analyse_arms.py`. SKIPPED rows have no
finding counts, so they drop out of the scored n:

| Backend | Hits | Misses | False positives | Precision | Recall | F1 | Scored |
|---|---|---|---|---|---|---|---|
| `agnes-intl` (none) | 64 | 88 | 126 | 33.7% | 42.1% | 0.374 | 150/150 |
| `agnes-intl-2` (xhigh) | 51 | 101 | 85 | 37.5% | 33.6% | 0.354 | 148/150 |
| `agnes-intl-3` (max) | 52 | 97 | 101 | 34.0% | 34.9% | 0.344 | 147/150 |
| `onmi-gemini3.6` | 85 | 66 | 319 | 21.0% | 56.3% | 0.306 | 149/150 |

What this says, one run per entry:

- On the international Agnes combos, none caught more defects (63 of 75)
  than xhigh or max (45 of 75) and passed fewer clean controls (31 of 75
  against 50 and 51). Entry-level F1 is 0.692 against 0.621 and 0.625.
  Finding-level F1 is close (0.374, 0.354, 0.344): none reports more hits
  and more false positives. Thinking did not make line ranges more
  accurate; it moved the hold/pass gate.
- xhigh and max agree. Exact McNemar on the 145 entries both scored:
  defects 7 vs 9 (p=0.80), clean 7 vs 8 (p=1.0). This corpus does not
  give a reason to prefer max over xhigh.
- none versus xhigh on those 145: 22 defects only none caught, 4 only
  xhigh (p=0.0005); 5 clean only none passed, 23 only xhigh (p=0.0009).
  The same shape against max (20 vs 4 defects, p=0.0015; 5 vs 24 clean,
  p=0.0005).
- `onmi-gemini3.6` caught 74 of 75 defects and passed 6 of 75 clean
  controls. Entry-level F1 0.679 sits next to none because recall is at
  the ceiling and precision is at the floor. Finding-level false
  positives are 319 against 85 to 126 on Agnes. It is not a backend that
  can let a clean diff through.
- Wall per entry from the analyser includes the 3610 s SKIPPED
  timeouts, which is why xhigh, max and gemini look slower than their
  completed rows. Means on rows that produced a verdict: 156 s (none),
  223 s (xhigh), 216 s (max), 206 s (gemini). Shared-endpoint latency
  still applies; the wall column is a cost, not a ranking.

On pairwise non-SKIPPED entries, gemini held every defect the matching
Agnes arm held, and uniquely held 12 (vs none), 30 (vs xhigh) and 28
(vs max). On clean entries the thinking Agnes arms uniquely passed 46
and 47 controls that gemini held.

None of these four arms replaces `agnes-cn` as the default review
backend recorded on this page. The CN pin was not re-run at none, xhigh
or max.

Reproduce (named backends already in the eval-host user config, public
OmniRoute):

```bash
FORGE_CLEAN_ROUND_THRESHOLD=1 code-forge eval \
    --corpus tests/eval/swebench/corpus.yaml --backend agnes-intl \
    --jobs 1 --runs 1 --arm-depth 1 \
    --resume-log docs/eval/agnes-intl-none-d1.jsonl
python3 scripts/analyse_arms.py \
    docs/eval/agnes-intl-none-d1.jsonl \
    docs/eval/agnes-intl-xhigh-d1.jsonl \
    docs/eval/agnes-intl-max-d1.jsonl \
    docs/eval/onmi-gemini36-d1.jsonl
```

## Caveats

These apply to every number on this page.

- One run per entry, so there is no within-entry spread and no standard
  error on any precision, recall or F1 figure. A difference between two
  arms is a point estimate with no error bar. A small gap is not evidence
  of a difference. What would settle it: three runs per entry, at roughly
  three times the cost.
- Best-of-N is off. Where the harness supports repeated runs it reports the
  mean, not the best run and not a majority vote. A majority vote of three
  would map a per-run hit rate of 0.3 to 0.216 and penalise exactly the
  hard defects the tool exists to catch.
- SKIPPED entries are counted, not dropped. When the harness could not
  produce a verdict for an entry, the ledger records it as not caught: a
  skipped defect counts as a miss, a skipped control counts as passed. One
  defect was SKIPPED in each of the depth-2 and gate-on arms and one
  control in the depth-3 arm, so the depth-3 precision is flattered by at
  most one entry. The analyser prints the ledger line of each SKIPPED row.
- Wall-clock per entry is confounded by API latency. Arms ran serially
  over many hours against a shared endpoint, and time of day moved the
  numbers more than the configuration did. Treat the wall column as a
  rough cost, not a measurement of depth or of the gate.
- Not comparable to published code-review F1 figures. Martian's online
  score marks a comment useful when the developer changed code in
  response; there is no known-defect set. Its offline set is 50 PRs with
  173 hand-written golden comments judged by three models. CodeRabbit and
  Qodo publish against their own injected-defect sets. This page scores
  against SWE-bench Verified defects with the upstream fix as answer key
  plus matched clean controls. Different ground truth, different numbers;
  they do not belong in one table.
- Several backends at depth 1, one backend everywhere else. Depth sweep
  and ablation ran on `mimo-v2.5-pro`. Later depth-1 arms ran on
  `agnes-cn`, `qwen-27b-dflash`, the international Agnes effort ladder,
  and `onmi-gemini3.6`. Whether the pipeline or the model sets the
  ceiling is still not separable for depths 2 and 3, or for the gate.
  The depth-1 swaps show the backend moving recall and precision; they
  do not answer the depth or gate questions. The international Agnes
  rows are not interchangeable with the published `agnes-cn` row.
- Corpus shape. Python library code with an upstream fix, reviewed without
  surrounding context. Results do not transfer unexamined to other
  languages or to defect classes SWE-bench does not contain.

## Reproducing

```bash
pip install -e '.[eval-corpus]'
python3 -m code_forge.eval.build_corpus --out .eval-corpus   # seed 20260830, cap 8
FORGE_CLEAN_ROUND_THRESHOLD=3 code-forge eval \
    --corpus .eval-corpus/corpus.yaml --backend <name> \
    --jobs 3 --runs 1 --arm-depth 3 --resume-log arm-d3.jsonl
/usr/bin/python3 scripts/analyse_arms.py arm-d3.jsonl
```

The analyser accepts any number of ledgers and reports deltas against the
first. It prints `n/a` rather than `0.0` for any statistic the data cannot
support.
