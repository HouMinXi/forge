# R3 evidence packet: one run, one identity, one set of hits

The original three-stage run was made on 2026-11-24. It used the initial
packet tools from `44a6b2a6a39ffe1d8641ae4170b8e728288a0a2e` against the
sample at `5a41cf44339a09fd233dea91af9d88993f88d308`. Those are different
checkouts: the sample commit does not contain the packet tools. The historical
results below do not establish that later tool versions were exercised.

The replay instructions pin both checkouts explicitly. All runs described
here are local, offline and manual; they cover the single sample below.

## Agreed scope

Accepted: `src/code_forge/llm_invoke.py:2506`, the `json.loads` inside
`loads_reply_json`, as reached by
`tests/test_excerpt_json_encoding.py::test_correct_json_encoding_preserves_literal_source`,
and the single assertion `assert assessment.status is ExcerptStatus.VALID` at
`tests/test_excerpt_json_encoding.py:46`.

Outside this scope: the rest of the decode path, other assertions in the same
module, every other module, and any CI gate. Nothing here says the decode path
is covered, and nothing here is wired into a verdict.

## Historical inputs (2026-11-24)

| Item | Value |
|---|---|
| Sample commit | `5a41cf44339a09fd233dea91af9d88993f88d308` |
| Tool commit for the original three stages | `44a6b2a6a39ffe1d8641ae4170b8e728288a0a2e` |
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

## Rerun with two pinned checkouts

Start in a local Forge clone that contains both commits. Use a Python
interpreter with Forge's test dependencies and pytest 9.2.0 already installed;
these commands do not install dependencies. The recorded interpreter was
CPython 3.14.10. Keep the same interpreter for the manual tests, producer and
verifier. Stop if a commit, dependency or expected exit is unavailable.

```sh
set -eu
REPO="$(git rev-parse --show-toplevel)"
PYTHON="$(python -c 'import sys; print(sys.executable)')"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/forge-r3-replay.XXXXXXXX")"
TOOLS="$WORK/tools"
SAMPLE="$WORK/sample"
TOOL_COMMIT=e3afad298d2895b88c1a601552bfc5a5904b71c5
SAMPLE_COMMIT=5a41cf44339a09fd233dea91af9d88993f88d308
git -C "$REPO" cat-file -e "$TOOL_COMMIT^{commit}"
git -C "$REPO" cat-file -e "$SAMPLE_COMMIT^{commit}"
git clone --shared --no-checkout --no-hardlinks "$REPO" "$TOOLS"
git clone --shared --no-checkout --no-hardlinks "$REPO" "$SAMPLE"
git -C "$TOOLS" checkout --detach "$TOOL_COMMIT"
git -C "$SAMPLE" checkout --detach "$SAMPLE_COMMIT"
export PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
unset PYTEST_ADDOPTS PYTEST_PLUGINS PYTHONHOME
"$PYTHON" --version
"$PYTHON" -m pytest --version
(
  cd "$TOOLS"
  PYTEST_ADDOPTS=--rootdir=. PYTHONPATH="$TOOLS/src" \
    "$PYTHON" -B -m pytest -q -p no:cacheprovider \
    docs/eval/r3-evidence-packet/test_packet.py \
    --junitxml="$WORK/manual.xml"
) >"$WORK/manual.stdout" 2>"$WORK/manual.stderr"
```

The manual suite spawns pytest in small fixture repositories. The command-local
`PYTEST_ADDOPTS=--rootdir=.` is inherited by those children, so a fixture
created under another Forge checkout does not acquire that ancestor's root
and a different node identity. This controls the documented test invocation;
it does not change the fixture implementation. The formal-sample commands
below do not use that override.

The sample owns the source, test and commit recorded in the packet. The tools
checkout owns `packet.py`, `verify.py` and the observer. Do not run a relative
`packet.py` from the sample checkout: that file is absent at its pinned
commit. `PYTHONPATH` must point at the sample's `src` for all three stages.

```sh
PRODUCER="$TOOLS/docs/eval/r3-evidence-packet/packet.py"
VERIFIER="$TOOLS/docs/eval/r3-evidence-packet/verify.py"
OBSERVER="$TOOLS/docs/eval/r3-target-line/target_hits.py"
SOURCE=src/code_forge/llm_invoke.py
TEST=tests/test_excerpt_json_encoding.py
NODE="$TEST::test_correct_json_encoding_preserves_literal_source"
SOURCE_SHA="$(sha256sum "$SAMPLE/$SOURCE" | cut -d ' ' -f 1)"
OBSERVER_SHA="$(sha256sum "$OBSERVER" | cut -d ' ' -f 1)"
cp "$SAMPLE/$TEST" "$WORK/test-original.py"
sha256sum "$PRODUCER" "$VERIFIER" "$OBSERVER" "$SAMPLE/$SOURCE" \
  "$SAMPLE/$TEST" >"$WORK/inputs-before.sha256"
PYTHONPATH="$SAMPLE/src" "$PYTHON" -B -c \
  'import code_forge.llm_invoke as m; print(m.__file__)'

run_stage() {
  label="$1"; call="$2"; expected_exit="$3"
  test_sha="$(sha256sum "$SAMPLE/$TEST" | cut -d ' ' -f 1)"
  run_id="$("$PYTHON" -c 'import secrets; print(secrets.token_hex(16))')"
  if (
    cd "$SAMPLE"
    PYTHONPATH="$SAMPLE/src" "$PYTHON" -B "$PRODUCER" \
      --root "$SAMPLE" --source "$SOURCE" --node "$NODE" \
      --file "$SOURCE" --line 2506 --run-id "$run_id" \
      --output "$WORK/$label.json" -- "$NODE" -q
  ) >"$WORK/$label.stdout" 2>"$WORK/$label.stderr"; then
    actual_exit=0
  else
    actual_exit=$?
  fi
  printf '%s: pytest exit %s (expected %s)\n' "$label" "$actual_exit" "$expected_exit"
  test "$actual_exit" -eq "$expected_exit" || return 1
  packet_sha="$("$PYTHON" -c \
    'import pathlib,re,sys; rows=re.findall(r"^forge-r3-packet run_id=([0-9a-f]{32}) sha256=([0-9a-f]{64})$",pathlib.Path(sys.argv[1]).read_text(),re.M); assert len(rows)==1 and rows[0][0]==sys.argv[2]; print(rows[0][1])' \
    "$WORK/$label.stdout" "$run_id")" || return 1
  "$PYTHON" -B "$VERIFIER" "$WORK/$label.json" \
    --expect-run-id "$run_id" --expect-commit "$SAMPLE_COMMIT" \
    --expect-source-sha256 "$SOURCE_SHA" --expect-test-sha256 "$test_sha" \
    --expect-observer-sha256 "$OBSERVER_SHA" \
    --expect-target-file "$SOURCE" --expect-line 2506 --expect-node "$NODE" \
    --expect-call "$call" --expect-exit "$expected_exit" --expect-hits 2 \
    --expect-packet-sha256 "$packet_sha" \
    >"$WORK/verify-$label.stdout" 2>"$WORK/verify-$label.stderr"
}
run_stage baseline passed 0
```

Move only the synthetic excerpt payload from 78 to 79 inside `_assessment`.
Leave the reference map at 78 and the original assertion at line 46 unchanged.
The next command runs the failing stage, restores the exact original bytes,
and runs the restored control. Each stage uses a new process and run identity.

```sh
"$PYTHON" - "$SAMPLE/$TEST" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
data = path.read_bytes()
old = b'"start_line": 78, "end_line": 78, "content": content'
new = b'"start_line": 79, "end_line": 79, "content": content'
assert data.count(old) == 1
path.write_bytes(data.replace(old, new))
PY
run_stage controlled failed 1
cp "$WORK/test-original.py" "$SAMPLE/$TEST"
run_stage restore passed 0
cmp "$WORK/test-original.py" "$SAMPLE/$TEST"
git -C "$SAMPLE" diff --exit-code
sha256sum -c "$WORK/inputs-before.sha256"
printf 'Evidence directory: %s\n' "$WORK"
```

Require three verifier exits of 0, each printing
`verified one bound pytest execution` in its saved stdout. The controlled
stage must show the original assertion failing at line 46, not a collection
or setup error. Its packet must retain call `failed` and pytest exit 1.
A verifier accepting that record confirms the failure evidence; it does not
turn the test into a pass. Preserve the logs and original test copy even if a
stage fails unexpectedly. Do not reuse the output directory for another run.

## Historical stages (2026-11-24)

| Stage | Exit | Hits | Test digest (first 16) | Run identity | Verify |
|---|---:|---:|---|---|---|
| Baseline | 0 | 2 | `f2a4dbe2a152f806` | `970a529138cfaebae81e83edb020032d` | verified |
| Controlled change | 1 | 2 | `25b67f77b757ae0b` | `2fdbb3c72a1a6245c8e4ecd8e5802d37` | verified |
| Restore | 0 | 2 | `f2a4dbe2a152f806` | `84dd594b3d8cf04c67399c570165a397` | verified |

The controlled change still reached the target line (2 hits) while the
original assertion failed at `tests/test_excerpt_json_encoding.py:46`.

## Historical refusal observations

These rows came from the original run and subsequent checks before the
`e3afad298` replay. They are not additional results from that replay. In
particular, strict XPASS support was added in `2e386745` and its recognition
was narrowed in `edc58936` and `e3afad298`; the initial `44a6b2a6` tools did
not establish that row.

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
| Strict XPASS claimed as an ordinary failure | `node call outcome is ['xpassed'], expected 'failed'` |
| Source edited by the test before it finished | `packet refused: changed during the run, no packet written: source` |

## Replay with the corrected tools (2026-11-25)

The manual suite and the three stages below were rerun with tools pinned to
`e3afad298d2895b88c1a601552bfc5a5904b71c5`. The sample remained at
`5a41cf44339a09fd233dea91af9d88993f88d308`. The two-checkout commands above
were also extracted from this document and executed as written.

| Input | SHA-256 |
|---|---|
| `packet.py` | `8667f372d2cad0462d826bc20ddc7d246dad64584cf0fabc4b7512a406f8baa6` |
| `verify.py` | `e5813f77163d00d758f2cf22a93cd757a95304482d16abb8001c209bad26fd14` |
| `target_hits.py` | `a9aac0ef16e69410391b56f849eeb3afd846604ea8f1411e06d0cc3af30853eb` |
| Sample source | `997b3c14b69b55388a2e528363724f174f55bb279bcae12d44fbaa7ad5121765` |
| Baseline and restored test | `f2a4dbe2a152f8068cb106b9a7a4a169c26ed5a43c8748bcdc180bfedeedaae9` |
| Controlled-change test | `25b67f77b757ae0bb9a7389a743b27ee6a7bd64e3489002e78b9faf71f630d56` |

The documented manual-test command ran 22 tests: 22 passed, no failures,
errors or skips, in 4.90 seconds (JUnit elapsed time 4.906 seconds).
The interpreter was CPython 3.14.10 with pytest 9.2.0. Disabling plugin
autoload produced one `PytestConfigWarning` about
`asyncio_default_fixture_loop_scope`; it was retained, not suppressed.

These are the stages from the executed documentation commands:

| Stage | pytest exit | Call | Hits | Verifier exit | Run identity |
|---|---:|---|---:|---:|---|
| Baseline | 0 | `passed` | 2 | 0 | `5da8ff1e2fb97140a6f4f65eb34737ac` |
| Controlled change | 1 | `failed` | 2 | 0 | `1fc4fe180bed8bb8f0b8ad60e4d731b7` |
| Restore | 0 | `passed` | 2 | 0 | `26218e7c1efacbd9762cea0e1f10a057` |

| Stage | Packet SHA-256 announced on stdout |
|---|---|
| Baseline | `264913490c363bfe82d686d94c2e0c066cb8dbfe0b6283532cccab5a965e7454` |
| Controlled change | `1aaa01424eee55551cdc241eab5ec1c79feea51407f17b79bada8f2a938c9bd3` |
| Restore | `79e8837efd5b225cc60ea709c0609bf49d499a03769bbe8bcd3481279ed84373` |

The controlled stage failed at
`tests/test_excerpt_json_encoding.py:46`, on
`assert assessment.status is ExcerptStatus.VALID`, with
`ExcerptStatus.INVALID`. The source, test and tool digests were checked
before and after the replay. The test was restored byte for byte.
This is a local execution record, not independent acceptance of R3.

## Independent execution (2026-11-25)

A fresh execution context, which had not implemented these tools, used its
own tool and sample checkouts at the same two commits. It executed the
existing 22-case manual suite and the same formal sample; it did not add
samples or reinterpret the expected outcomes.

The first manual-suite invocation used a temporary fixture root below
another Forge checkout. It produced `19 failed, 3 passed, 1 warning in 4.62s`.
The child pytest process found the ancestor's `pyproject.toml` and collected
node paths relative to that ancestor. The producer refused the mismatch;
the test assertions then failed because no packet was written. That failed
invocation is retained and is not counted as passing evidence.

The retry kept the tool, test bytes and interpreter fixed, changing only the
manual-suite environment to `PYTEST_ADDOPTS=--rootdir=.` and writing to a new
output directory. It produced `22 passed, 1 warning in 5.45s`, with no
failures, errors or skips. Both attempts executed the same 22 test identities.
The documentation command above now includes that local rootdir setting.
The fixture's dependence on parent configuration remains a disclosed
invocation limitation, not a repaired implementation.

The independent formal sample ran without the rootdir override. Each packet
was verified against input digests computed before generation and the digest
announced by its own producer process:

| Stage | pytest exit / call / hits | Run identity |
|---|---|---|
| Baseline | 0 / `passed` / 2 | `1fbaf23b52e9ffaecc37ee0b4099a360` |
| Controlled change | 1 / `failed` / 2 | `d4b3366c7f136f91d5b3144b4df39327` |
| Restore | 0 / `passed` / 2 | `e95a2e62fd51d45656ec46b993fa9d4f` |

| Stage | Packet SHA-256 announced on stdout |
|---|---|
| Baseline | `2d08d4e7ee32e888fb7bfae3cbd1a6a045f701255eda39f6ced9941587e077aa` |
| Controlled change | `46804d0588568085261d13c8f977b2e1ae665e837ff36dc6f4223d110fc87612` |
| Restore | `eeaa4469d4e8d5c8ae57c29dba96fa55e746f797004d1da17c8f380b6235be4f` |

All three verifier commands exited 0 and printed
`verified one bound pytest execution`. The controlled stage failed on the
unchanged assertion at line 46. Baseline and restore packets matched after
excluding exactly `identity.run_id` and `packet_sha256`; source, test and
observer digests and phase/hit fields remained in the comparison. Both
private checkouts ended clean, with the original sample bytes restored.

The independent report retained a findings verdict for the stale README
case count and the nested-fixture limitation. The README count is corrected
in this documentation change; that does not rewrite the report's verdict.
This evidence is independent execution of one predeclared sample, not a
clean product-review round or acceptance of issue #33 R3.

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
