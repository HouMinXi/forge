# R3 target path and assertion discrimination: one recorded sample

Run on 2026-11-24. Local, offline, manual. This is an acceptance record for
one sample, not a gate result and not an attestation.

## Fixed inputs

| Item | Value |
|---|---|
| Source commit | `c59e51d528bd8c52ef23bea9827f431abf1b55f6` (`main`) |
| Node | `tests/test_excerpt_json_encoding.py::test_correct_json_encoding_preserves_literal_source` |
| Target line | `src/code_forge/llm_invoke.py:2506`, the `json.loads` inside `loads_reply_json` |
| Interpreter | CPython 3.14.10 |
| pytest | 9.2.0 |
| Observer digest | `target_hits.py` `a9aac0ef16e69410391b56f849eeb3afd846604ea8f1411e06d0cc3af30853eb`, taken from the revision that adds this file |
| Test digest | `tests/test_excerpt_json_encoding.py` `f2a4dbe2a152f8068cb106b9a7a4a169c26ed5a43c8748bcdc180bfedeedaae9` |

Two further nodes from the same file serve as counter-cases:
`test_review_contract_explains_source_and_json_layers`, which passes without
decoding, and `test_halved_backslashes_are_rejected_with_private_encoding_diagnostic`,
which is used for the red-without-hit case.

## Three evidence classes, kept apart

- **Execution receipt** from `docs/eval/r3-pytest-outer-call/run_receipt.py`.
  Binds profile, pytest version, run identity, commit and both file digests,
  and the three phase outcomes. It refuses a dirty worktree, so it was
  produced in a clean checkout of the same commit.
- **Hit record** from `docs/eval/r3-target-line/target_hits.py`. Reports the
  per-node execution count for one line and nothing else.
- **Assertion result** as pytest's own output and exit status. A failing
  stage keeps its failure; no passing receipt is written for it.

## Rerun

The observer is not present at `c59e51d5`; that commit pins the sample only.
Take the observer from the revision that adds it, keep it outside the sample
checkout, and point `--root` at a checkout of the sample commit:

```sh
# observer, from the revision that introduced this file
git -C <forge clone> show <observer revision>:docs/eval/r3-target-line/target_hits.py \
  > /tmp/target_hits.py

# baseline, against a checkout of c59e51d5
PYTHONPATH=<sample checkout>/src python /tmp/target_hits.py \
  --root <sample checkout> \
  --file src/code_forge/llm_invoke.py --line 2506 \
  --output /tmp/hits-clean.json \
  tests/test_excerpt_json_encoding.py::test_correct_json_encoding_preserves_literal_source
```

Controlled change: inside `_assessment`, move the synthetic excerpt payload
from line 78 to line 79 (`start_line` and `end_line` only), leaving everything
else untouched, then run the same command. Revert that one line afterwards and
run again.

Receipt:

```sh
PYTHONPATH=src python docs/eval/r3-pytest-outer-call/run_receipt.py produce \
  --root . --source src/code_forge/llm_invoke.py \
  --node "<node>" --run-id "<32 hex>" --output /tmp/receipt.json
```

## Results

| Stage | Change | pytest exit | Target hits | Outcome |
|---|---|---:|---:|---|
| Baseline | none | 0 | 2 | passes and the line really ran |
| Controlled change | payload 78 -> 79 | 1 | 2 | decode still ran; `assert assessment.status is ExcerptStatus.VALID` failed at line 46 |
| Restore | revert that line | 0 | 2 | passes again |
| Green and irrelevant | run the guidance node | 0 | 0 | passes without reaching the target |
| Red and irrelevant | replace `damaged` with undamaged content | 1 | 0 | failed at line 52, before the decode |

The failure text of the controlled change is
`assert <ExcerptStatus.INVALID> is <ExcerptStatus.VALID>` at
`tests/test_excerpt_json_encoding.py:46`.

## Version and run identity are refused when wrong

| Check | Exit | Output |
|---|---:|---|
| Receipt with matching arguments | 0 | `verified one ordinary passing pytest call` |
| Receipt against another commit | 1 | `receipt refused: receipt does not match expected snapshot` |
| Receipt against another run identity | 1 | `receipt refused: receipt does not match expected run_id` |

## Evidence digests

| Record | SHA-256 |
|---|---|
| baseline hits | `3dc733fdffa383f254b024927eb9bae6679257bf6515773dc7a3933bea95425b` |
| restored hits | `3dc733fdffa383f254b024927eb9bae6679257bf6515773dc7a3933bea95425b` |
| controlled-change hits | `fb0143674c491b08e152fb69ccd560c9d4aa920ed489eedaed62959583d22386` |
| green-without-hit | `10a3d6b954d95d0de3eab39c45027c4209ab4dde14a2eebb4200d78faa171055` |
| red-without-hit | `cbb1b6eb7ba3345e63d0b33b7b697459aa59ef1dbcc17da20173d87f93fb4a4d` |

Baseline and restore records are byte-identical, which is expected: they carry
no clock and no absolute path.

## What this record does not establish

- It covers one line, not the decode path it belongs to, and not the
  repository.
- Assertion strength is shown for the one assertion the controlled change
  tripped. It does not generalise to the other assertions in that module.
- The controlled change moved one synthetic value. It says nothing about
  whether a different damage would be caught.
- No CI wiring, no gate, no cross-repository link. Multi-hop and cross-repo
  submission stay deferred under the existing decision.
