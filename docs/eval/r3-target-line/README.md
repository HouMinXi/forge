# R3 target-line observation (manual only)

A passing test proves its assertions held for the output it saw. It does not
prove the code under review ran, and it does not prove the assertion would
have caught the defect the test is about.

This helper records one of those four things: whether a named line inside a
named file executed while a selection ran, counted per node and across
threads. Pair it with an execution receipt and with a controlled change to a
synthetic output, and the three questions get separate answers instead of one
green tick.

## What it answers

- Did `src/pkg/module.py:123` execute during `test_x.py::test_y`, and how many
  times.
- Which node hit it, when several nodes ran in one process.

## What it does not answer

- It does not prove the line was *not* executed when it reports zero. Another
  process, another selection, an interrupted run, a line reached off the
  traced path or a thread started before the test all produce the same empty
  record. Absence means unobserved.
- It does not bind a commit, a run identity or a file digest. Use an execution
  receipt for that.
- It does not establish that the test's assertions have discriminating power.
  That needs the controlled change described in `RESULTS.md`.
- It is not an attestation, and nothing in Forge reads it.

## Usage

From the repository root, with the interpreter and pytest the record names:

```sh
PYTHONPATH=src python docs/eval/r3-target-line/target_hits.py \
  --root . \
  --file src/code_forge/llm_invoke.py \
  --line 2506 \
  --output /tmp/hits.json \
  tests/test_excerpt_json_encoding.py::test_correct_json_encoding_preserves_literal_source
```

The exit status is pytest's. The record is written only when the run finishes,
outside the checkout, and looks like this:

```json
{
  "schema": "forge-r3-target-hits-v1",
  "target": {"path": "src/code_forge/llm_invoke.py", "line": 2506},
  "collected": ["tests/test_excerpt_json_encoding.py::test_correct_json_encoding_preserves_literal_source"],
  "hits": {"tests/test_excerpt_json_encoding.py::test_correct_json_encoding_preserves_literal_source": 2},
  "pytest_exit": 0
}
```

The record carries no clock and no absolute paths, so two runs of the same
selection over the same source produce a byte-identical file.

## Refusals

The helper exits 2 without running pytest when the target is not a regular
file under the given root, when the line is past the end of the file, when the
line number is not positive, or when the record path is inside the checkout.

## Threads

A thread keeps the trace function it started with, so that function carries
the node that started it. Events arriving after that call has finished are
dropped rather than credited to whichever node runs next: a thread released by
a later test's setup records zero for both nodes.

## Tests

```sh
python -m pytest -q docs/eval/r3-target-line
```

Ten cases cover a hit, a passing node with no hit, a hit from another thread, a
late hit from a thread started by an earlier node, per-node separation, a
leading `--` before the selection, and the four refusals. Withdrawing any one
of the checks the helper depends on turns its covering case red: thread
registration, the line comparison, the active-node check, the per-node count
key, the outside-checkout guard and the missing-file guard.

Run this by hand. It is not wired into CI, verdicts or gates, and it traces
frames in one file only so the cost stays low on a single selection.
