# R3 evidence packet: one run, one identity, one set of hits

Run on 2026-11-24. Local, offline, manual. This records one accepted scope and
the refusals that keep the evidence inside it.

## Agreed scope

Accepted: `src/code_forge/llm_invoke.py:2506`, the `json.loads` inside
`loads_reply_json`, as reached by
`tests/test_excerpt_json_encoding.py::test_correct_json_encoding_preserves_literal_source`,
and the single assertion `assert assessment.status is ExcerptStatus.VALID` at
`tests/test_excerpt_json_encoding.py:46`.

Outside this scope: the rest of the decode path, other assertions in the same
module, every other module, and any CI gate. Nothing here says the decode path
is covered, and nothing here is wired into a verdict.

## Fixed inputs

| Item | Value |
|---|---|
| Commit | `5a41cf44339a09fd233dea91af9d88993f88d308` (`main`) |
| Node | `tests/test_excerpt_json_encoding.py::test_correct_json_encoding_preserves_literal_source` |
| Target | `src/code_forge/llm_invoke.py:2506` |
| Interpreter | CPython 3.14.10 |
| pytest | 9.2.0 |
| Observer | `docs/eval/r3-target-line/target_hits.py`, `a9aac0ef16e69410391b56f849eeb3afd846604ea8f1411e06d0cc3af30853eb` |
| Packet producer | `docs/eval/r3-evidence-packet/packet.py`, `ef99318caef1b0e613612f7a8df22538e57c15a17024ad92d1847764a1d942fe` |
| Verifier | `docs/eval/r3-evidence-packet/verify.py`, `2e42219a1bb0b5de1c24d2de3a8cc6c3985245ed1f5d8cc425f3412eb0b261c8` |
| Sample test | `tests/test_excerpt_json_encoding.py`, `f2a4dbe2a152f8068cb106b9a7a4a169c26ed5a43c8748bcdc180bfedeedaae9` |

## Why the identity is inside the record

Each stage is one pytest execution. That single process writes the commit, the
source and test digests, the observer digest, the collected nodes, the phase
outcomes, the per-node target hits and a run identity generated for that
execution. A passing receipt produced by another run cannot be attached to
these hits: the run identity and the packet digest are announced on stdout at
execution time and recorded separately by the caller.

## Rerun

```sh
PYTHONPATH=src python docs/eval/r3-evidence-packet/packet.py \
  --root . --source src/code_forge/llm_invoke.py \
  --node tests/test_excerpt_json_encoding.py::test_correct_json_encoding_preserves_literal_source \
  --file src/code_forge/llm_invoke.py --line 2506 \
  --run-id <32 hex> --output /tmp/stage1.json \
  -- tests/test_excerpt_json_encoding.py::test_correct_json_encoding_preserves_literal_source
```

The last line of stdout is `forge-r3-packet run_id=<id> sha256=<digest>`. Keep
both; the verifier needs them.

```sh
python docs/eval/r3-evidence-packet/verify.py /tmp/stage1.json \
  --expect-run-id <id> --expect-commit <commit> \
  --expect-source-sha256 <source> --expect-test-sha256 <test> \
  --expect-observer-sha256 <observer> \
  --expect-target-file src/code_forge/llm_invoke.py --expect-line 2506 \
  --expect-node <node> --expect-call passed --expect-exit 0 --expect-hits 2 \
  --expect-packet-sha256 <digest>
```

The controlled change moves the synthetic excerpt payload from line 78 to 79
inside `_assessment`, leaving everything else alone. For that stage the
expected test digest is the digest of the changed file, the expected call
outcome is `failed`, and the expected exit is 1: a failing stage keeps its
failure and produces no passing record.

## Accepted stages

| Stage | Exit | Hits | Test digest (first 16) | Run identity | Verify |
|---|---:|---:|---|---|---|
| Baseline | 0 | 2 | `f2a4dbe2a152f806` | `970a529138cfaebae81e83edb020032d` | verified |
| Controlled change | 1 | 2 | `25b67f77b757ae0b` | `2fdbb3c72a1a6245c8e4ecd8e5802d37` | verified |
| Restore | 0 | 2 | `f2a4dbe2a152f806` | `84dd594b3d8cf04c67399c570165a397` | verified |

The controlled change still reached the target line (2 hits) while the
original assertion failed at `tests/test_excerpt_json_encoding.py:46`.

## Refused

| Case | Verifier output |
|---|---|
| Packet checked against another run's identity | `run identity is '970a5291...', expected '2fdbb3c7...'` |
| Legitimate receipt from another execution paired with this packet | `run identity is '970a5291...', expected '235053d9...'` |
| Another commit | `commit is '5a41cf44...', expected '1111...'` |
| Hits edited after the run | `packet digest is e0870992e4b0, caller recorded f8916308d23e` |
| Green run that never reached the target | `target hits are 0, expected 1` |
| Collected but never executed (`--collect-only`) | `node has no call phase: collected but not executed` |
| `--source` pointing at a second file | `packet refused: source and target must resolve to the same file` |
| Target call passed while another node failed | `node call outcome is ['passed'], expected 'failed'` |
| Digest or hit count omitted from the check | exits without printing `verified one bound pytest execution` |
| XPASS claimed as an ordinary pass | `node call outcome is ['xpassed'], expected 'passed'` |
| Source edited by the test before it finished | `packet refused: changed during the run, no packet written: source` |

## What this does not establish

- One line and one assertion. The rest of the decode path is unverified.
- The packet is unsigned. Editing it is detected only against a digest the
  caller recorded at execution time; without that record, nothing detects an
  edit.
- No CI wiring, no verdict, no cross-repository link. Multi-hop and cross-repo
  submission stay deferred.
- Two further stages of the same sample (green-without-hit and
  red-before-the-decode) remain in `docs/eval/r3-target-line/RESULTS.md`; they
  are not re-recorded here as packets.
