# R3 grouping receipt review (advisory only)

This manual offline verifier compares one grouping receipt ZIP with a
separate JSON expectation. It checks archive bytes, the expected workflow,
subject, run and attempt, and the fixed test node, source and test hashes,
call-phase trace summary and expected baseline / variant / restored exits
`0 / 1 / 0`. It accepts only the single bounded `r3-summary.json` ZIP member
and extracts nothing.

Choose the expected workflow revision, subject revision and run identity
from the review record. Obtain the archive digest independently from the
trusted artifact record. Never let a producer-supplied or archive-derived
expectation establish trust: someone who controls both files can make
them agree.

For example, the historical first attempt of
[run 44872854301](https://github.com/HouMinXi/forge/actions/runs/44872854301)
published [artifact 14694353070](https://github.com/HouMinXi/forge/actions/runs/44872854301/artifacts/14694353070).
To check retained original bytes, save a separate expectation with these
reviewed values:

```json
{
  "schema": 1,
  "profile": "forge-r3-grouping-v1",
  "identity": {
    "workflow_sha": "739f5fa945872b4f14b6a8c2c017f731c74c7090",
    "subject_sha": "12fbfac533f19c8d167500aa372543e4d2696089",
    "run_id": "44872854301",
    "run_attempt": "1"
  },
  "archive_sha256": "d3b4197736ba4313433c6ee10ec0616c66dfd9c17516ddc0b6364b86fed7762e"
}
```

From the repository root, with Python 3.12 or later:

```sh
python -B docs/eval/r3-grouping-receipt/verify_receipt.py reviewed-expectation.json receipt.zip
python -B -m unittest discover -s docs/eval/r3-grouping-receipt
```

The test suite builds two synthetic identities in memory; they are not
additional hosted runs. The historical ZIP is not checked in. Success means
the retained summary matches the supplied expectations for this grouping
example. It does not authenticate a producer, replay the original execution,
qualify other samples or revisions, or satisfy any Forge admission gate. This
tool is not part of required CI.
