# Offline review follow-up (schema 1)

This is the saved-output baseline protocol for issue #574. It does not launch
Forge review, a provider, or a candidate program. The checked-in inputs,
observations and human labels are synthetic controls, not historical model
outputs or evidence of improved review quality.

## Replay

From a checkout, on a filesystem supporting hard links:

```sh
fixture=tests/fixtures/review_followup/synthetic
out=$(mktemp -d)
PYTHONPATH=src python -m code_forge.eval.review_followup validate "$fixture"
PYTHONPATH=src python -m code_forge.eval.review_followup replay "$fixture" --output "$out/report.json"
PYTHONPATH=src python -m pytest -q docs/eval/review-followup
```

The offline suite is outside default CI collection. Run it explicitly; a green
repository-wide workflow is not evidence that this suite ran.

Exit 0 means the requested operation completed, not that a reviewer found
nothing. Exit 2 means input was rejected; a report can include a valid
`provider_failed` or `invalid_response` observation without returning 2.
Publication failure returns 1. Use a new output filename for each invocation;
a pre-existing result is deliberately not overwritten. Report publication
links a fully written temporary file and does not claim crash durability.
Once the link succeeds, inability to remove the scratch name does not turn
the published report into a failed run; a scratch file may remain.

## Frozen input and scoring boundary

All JSON schemas are closed; examples in the fixture tree are the v1 field
reference. Integer schema value 1 is required. Case IDs are unique in the
manifest, and a lineage cannot straddle calibration and holdout. Base and
candidate are either `git:<40 lowercase hex>` or labeled synthetic revisions.
Case-local artifact IDs identify base or candidate bytes in ordered groups.
A first-review input has no prior findings; revision follow-up can see old
identities but never rulings. Review inputs may open only their own
`inputs/<case>/` subtree. Saved observations and scoring artifacts each have
separate manifests and case-owned subtrees.

Only a trusted, static fixture tree supplied by the operator is supported.
Manifest and artifact paths must stay under that root and named symlink
components are rejected. This is an offline loader boundary, not a sandbox or
protection against concurrent replacement. Per-file size limit: 1 MiB;
cumulative read limit: 32 MiB per batch. JSON rejects invalid UTF-8,
duplicate keys and nonfinite numbers. Input-envelope references are resolved
before the artifact phase. For each batch, schema and references are checked
before files, including checking every path's syntax before any file lookup.
All files are read before any digest mismatch is reported;
manifest order decides ties. SHA-256 hashes the exact read bytes, including
whitespace and line endings. Consumers receive those verified bytes.

`validate` needs neither saved observations nor answers. Replay checks and
seals saved observations before opening scoring data. Each observation
references case, candidate revision and the digest recomputed from the raw
review input. The human adjudication additionally binds case, candidate,
run ID and the digest recomputed from the raw observation. Updating the
ordinary artifact manifest and retaining a run ID cannot make an old ruling
apply to new observation bytes. Usage is a nonnegative total token count or
`null`; synthetic values are controls, not measured provider usage.

First-review scoring counts each confirmed known root once per case, even if
multiple reports match it. False blockers and pending judgments are counted
per finding, separately from root hits. Unsupported/unknown observations
remain visible. An explicitly empty answer key can be scored; an unlabelled
one cannot. Null usage is not zero. Follow-up uses saved human conclusions,
not automated closure; missing human labels and insufficient evidence remain
open. Each report case includes the relative paths and SHA-256 of the exact
answer and adjudication bytes consumed by scoring.

## Read-only sample

| Slice | Hand-checked expectation |
| --- | --- |
| First review | 4 attempted, 2 completed, 2 failed; 1 root hit, 1 duplicate, 1 false blocker and 1 pending finding. |
| Revision follow-up | One verified fixed, one no longer applicable, one insufficient evidence and one without a ruling. Both latter findings stay open. |
| Cost | First-review controls include explicit zero and unknown values; a fully unknown subtotal is `null`. |

This does not qualify production behavior, real A/B, live GitHub/mail adapters,
runtime supersession, receipts, or merge gates.
