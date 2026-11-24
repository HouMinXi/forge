# R3 evidence packet (manual only)

One pytest execution, one packet. The packet carries what a reviewer needs to
tie the claim to that run: the commit, the source and test digests, the
observer digest, which nodes were collected, what each phase did, how often
the target line ran, and a run identity generated for this execution.

Separate records cannot do this. A passing receipt from one run says nothing
about the hits recorded in another, and a hit record that carries no identity
can be paired with any receipt that matches its text.

## What it answers

- For this execution, which commit, source file digest, test digest and
  observer digest were in force.
- Whether the named line executed, how often, and per node.
- Whether the node's call phase ran and what it produced.

## What it does not answer

- It is unsigned. Editing the packet is only detectable against a digest the
  caller recorded at execution time; the caller gets that digest on stdout and
  must keep it.
- It says nothing about lines other than the one named, other assertions, or
  other modules.
- It is not a gate, a verdict or an attestation, and it is not wired into CI.

## Producing

```sh
PYTHONPATH=src python docs/eval/r3-evidence-packet/packet.py \
  --root . \
  --source src/code_forge/llm_invoke.py \
  --node tests/test_excerpt_json_encoding.py::test_correct_json_encoding_preserves_literal_source \
  --file src/code_forge/llm_invoke.py --line 2506 \
  --run-id "$(python -c 'import secrets;print(secrets.token_hex(16))')" \
  --output /tmp/stage1.json \
  -- tests/test_excerpt_json_encoding.py::test_correct_json_encoding_preserves_literal_source
```

Pytest flags go after `--`. The last stdout line is
`forge-r3-packet run_id=<id> sha256=<digest>`; keep both.

The producer refuses a target outside the root, a missing file, a root without
a resolvable commit, and an output path inside the checkout.

## Verifying

```sh
python docs/eval/r3-evidence-packet/verify.py /tmp/stage1.json \
  --expect-run-id <id> --expect-commit <commit> \
  --expect-source-sha256 <source> --expect-test-sha256 <test> \
  --expect-observer-sha256 <observer> \
  --expect-target-file src/code_forge/llm_invoke.py --expect-line 2506 \
  --expect-node <node> --expect-exit 0 --expect-hits 2 \
  --expect-packet-sha256 <digest>
```

A failing stage is still evidence: pass `--expect-exit 1` and the hits you
expect, and the packet verifies while keeping its failure. The verifier refuses
a different run identity, a different commit or digest, a node that was never
collected, a node collected without a call phase, a hit count that disagrees
with the target claim, and a packet whose recorded digest no longer matches its
body.

## Tests

```sh
python -m pytest -q docs/eval/r3-evidence-packet
```

Seven cases cover the packet contents, a matching verification, and the
refusals above. Withdrawing each refusal in turn turns its covering case red:
run identity, commit, call phase, hit count and packet digest.

The tracer itself is not duplicated here: the producer loads
`docs/eval/r3-target-line/target_hits.py` and records its digest in the packet.
