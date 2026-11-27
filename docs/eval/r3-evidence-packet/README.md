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
- Whether the node's call phase ran and what it produced. Expected failures
  keep their own outcome (`xpassed` / `xfailed`). In pytest 9.2.0 a strict
  XPASS is reported as `failed`, without `wasxfail`, and its string `longrepr`
  starts with `[XPASS(strict)]`. The producer checks that type and prefix.
  An inactive xfail marker or a mismatch with `xfail(raises=...)` remains an
  ordinary failure; the xfail keyword alone does not identify an XPASS.

## What it does not answer

- It is unsigned. Editing the packet is only detectable against a digest the
  caller recorded at execution time; the caller gets that digest on stdout and
  must keep it.
- It says nothing about lines other than the one named, other assertions, or
  other modules.
- It is not a gate, a verdict or an attestation, and it is not wired into CI.

## Producing

This example assumes the tools and the tested source are in the same
checkout. To replay the fixed historical sample, use the two-checkout
commands in [RESULTS.md](RESULTS.md) instead.

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
a resolvable commit, an output path inside the checkout, and a `--source` that
does not resolve to the same file as `--file`. The digest has to describe the
file the tracer watches, so one path serves both.

It also stamps HEAD, the source, the test and the observer before the run and
re-reads all four before writing. If any of them moved, it writes no packet and
says which one changed: a packet that describes opening state cannot also
describe a tree that changed underneath it. A variant prepared before the run
is stable by construction, and the packet records its digest.

The target node must be collected once. The producer and verifier refuse a
repeated target or multiple call reports for that node, because a single
aggregate hit count cannot be attributed to one of those calls.

## Verifying

```sh
python docs/eval/r3-evidence-packet/verify.py /tmp/stage1.json \
  --expect-run-id <id> --expect-commit <commit> \
  --expect-source-sha256 <source> --expect-test-sha256 <test> \
  --expect-observer-sha256 <observer> \
  --expect-target-file src/code_forge/llm_invoke.py --expect-line 2506 \
  --expect-node <node> --expect-call passed --expect-exit 0 --expect-hits 2 \
  --expect-packet-sha256 <digest>
```

`--expect-call`, `--expect-hits` and `--expect-packet-sha256` are required; the
verifier exits without one of them and prints no success line. A failing stage
is still evidence: pass `--expect-exit 1`, `--expect-call failed` and the hits
you expect, and the packet verifies while keeping its failure -- a failing run
alone is not evidence about this node, because another node may have failed or
this one may have failed in teardown.

The verifier refuses a different run identity, a different commit or digest, a
node that was never collected, a node collected without a call phase, a call
outcome that disagrees with the claim, a hit count that disagrees with the
target claim, a source digest that does not describe the traced target, and a
packet whose recorded digest no longer matches its
body.

## Tests

```sh
PYTEST_ADDOPTS=--rootdir=. python -m pytest -q docs/eval/r3-evidence-packet
```

The command-local rootdir option also reaches the fixture subprocesses. It
prevents an ancestor checkout's pytest configuration from changing their
node paths when temporary fixtures live under that checkout.

The manual suite checks matching packets and refusals, including a test node
in a subdirectory, strict and non-strict XPASS, inactive xfail conditions, and
`raises` mismatches. These tests are outside the default CI selection; run the
command explicitly. See [RESULTS.md](RESULTS.md) for the pinned tool and sample
checkouts and the recorded three-stage replay.

The tracer itself is not duplicated here: the producer loads
`docs/eval/r3-target-line/target_hits.py` and records its digest in the packet.

### Read-only inspection of one pinned change

`inspect_change.py` is a standalone offline command. It runs no tests or
review itself. It compares an exact local ancestor and a clean candidate
checkout, confirms the named test file exists there, and accepts only a
target line added or replaced in that pair's Git diff. Context lines never
count, even if Git emits them. Git must support run-time `--no-lazy-fetch`;
a missing local object is refused rather than fetched. It cross-checks the
file execution record and the node-level packet against a caller-owned run ID
and digests. A verified zero-hit case reports `not_hit`; a positive claim
against that same packet is refused. The verdict is local to the command.

Supply the expected packet digest from the producer output and hash the
separate execution record immediately after the trusted run. Generate the
two artifacts in the same pytest process with the root pinned to the
candidate Git root. These unsigned records are not attestations; do not
treat expectations copied from an imported packet as independent evidence.
A file record alone cannot identify the test node or prove a line hit.

To replay the real #552 sample, use a clean detached checkout at
`3fae8be7a6080ad645d09ec3e142081a678e9c8b` with its first parent locally
available. From the tool checkout, with the Forge test dependencies and
pytest 9.2.0 installed, run:

```sh
PYTHON=/path/to/python sh docs/eval/r3-evidence-packet/replay_552.sh /path/to/sample
```

The existing contract-change and rename-only tests use an offline backend
fixture. Both reach call and pass. Only the contract-change case hits the
selected line (one hit versus zero); claiming one for the rename-only case
fails. The replay also refuses unchanged line 1884 with inherited context
settings. The script leaves its logs and records outside the clean checkout.
The separately archived Layer 2 presence behavior is not inferred from
either record. This command does not alter `e2e-check`, P2, its exit status,
or any review receipt.

A single packet never establishes assertion strength; the command
deliberately reports `assertion: unproven`. The offline verifier suite
separately tests an unchanged toy assertion across a correct result, a
controlled wrong result and restoration. This exercises the verification
workflow, not production regression sensitivity of the #552 assertion.
