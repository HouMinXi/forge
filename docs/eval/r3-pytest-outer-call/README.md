# R3 pytest outer-call evidence (manual only)

A fixture can emit a passing `SubtestReport` marked `call` during **setup**,
then fail setup before the test body is entered. A `pytest_runtest_logreport`
filter on phase and outcome alone incorrectly treats that file as executed.
`SubtestReport` subclasses `TestReport`, so `isinstance` alone is insufficient.

Use the final `pytest_runtest_logreport` hook and require the concrete
`pytest.TestReport` type, an outer `call` phase and a passed or failed outcome.
Checking the type in `pytest_runtest_makereport` is too early: the subtest
helper builds an ordinary intermediate `TestReport` from a synthetic
`CallInfo(when="call")` before wrapping it as a `SubtestReport`.
The collector only credits an unambiguous selected file under pytest's
root. A subtest inside a genuine outer call does not prevent the outer
report from counting.

Use a fresh collector for one trusted, single-process pytest invocation.
This runs the selected tests, their `conftest.py` files, and any explicitly
loaded plugins as normal Python code. It is not a sandbox; run it only
when those inputs are trusted. The example disables ambient third-party
plugin autoload.

Stdout contains normal pytest output followed by a final JSON line
listing the observed files. That line is not a standalone receipt or a
completion certificate. With Python 3.12 and pytest 9.2.0, from the
repository root:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=docs/eval/r3-pytest-outer-call python -B -c '
import json, sys
import pytest
from collect_outer_calls import OuterCallEvidence
evidence = OuterCallEvidence()
status = pytest.main(sys.argv[1:], plugins=[evidence])
print(json.dumps({"observed_files": sorted(evidence.observed_files)}))
raise SystemExit(status)
' path/to/test.py
```

Exercise both the setup-failure case and genuine outer calls with:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -B -m pytest -q docs/eval/r3-pytest-outer-call
```

Absence means “unobserved,” not “did not execute,” especially after a skipped,
interrupted or partial run. This helper does not establish run freshness,
snapshot identity, target-path coverage, assertion strength or an
attestation. It is not wired into Forge, required CI, verdicts or gates.

## Record and verify one passing call

`run_receipt.py` is an offline follow-up to the collector above. Use a clean
trusted checkout, one exact node and one distinct tracked source path. Use
Git 2.45 or later, with the required commit, tree and blob objects already
available locally. Git lazy fetching is disabled; missing local objects
cause the operation to fail rather than contact a promisor remote.
Independently pin the commit and SHA-256 of the two Git blobs and choose a
fresh 32-character lowercase hexadecimal run ID before starting pytest. Do
not get expected values from the receipt that will be checked.

For a temporary fixture with `arithmetic.py` and
`test_arithmetic.py::test_total`, from the checkout, with pytest 9.2.0:

```sh
SOURCE=arithmetic.py
TEST=test_arithmetic.py
NODE=$TEST::test_total
COMMIT=$(git rev-parse HEAD)
SOURCE_SHA=$(git --no-replace-objects --no-lazy-fetch show "$COMMIT:$SOURCE" | sha256sum | cut -d' ' -f1)
TEST_SHA=$(git --no-replace-objects --no-lazy-fetch show "$COMMIT:$TEST" | sha256sum | cut -d' ' -f1)
RUN=$(python -c 'import secrets; print(secrets.token_hex(16))')
OUT=$(mktemp -d)/receipt.json
HELPER=/path/to/run_receipt.py

python -B "$HELPER" produce --root "$PWD" --source "$SOURCE" \
  --node "$NODE" --run-id "$RUN" --output "$OUT"
python -B "$HELPER" verify "$OUT" --root "$PWD" --source "$SOURCE" --node "$NODE" \
  --commit "$COMMIT" --source-sha256 "$SOURCE_SHA" --test-sha256 "$TEST_SHA" \
  --run-id "$RUN"
```

The producer requires exactly one collected node and ordinary passed setup,
call and teardown reports in order, plus pytest exit 0. Synthetic subtest
reports do not count. It checks the clean Git snapshot and exact blob bytes
before and after pytest, and does not overwrite an existing receipt. Failed,
skipped, interrupted or partial runs are deliberately outside this profile.
An unexpected pass (XPASS) is also excluded, even if pytest exits 0.
The verifier uses Python's standard library and local Git. Provide the
trusted reviewed worktree root rather than any path named by the receipt.
The verifier rejects a receipt inside that tree or supplied as a symlink,
then matches the complete receipt against the separately pinned expectation.

As above, use only trusted tests, `conftest.py` files and Python execution
environments. This helper disables ambient pytest plugin autoload and uses
an empty pytest configuration. Pytest does not auto-discover ancestor
`conftest.py` files above the checkout, but does load those within it.
This does not prevent trusted test code from
changing and restoring files between the two snapshot checks. The receipt
does not prove the named source was imported or executed, assertions were
effective, or a runner was authentic. A run ID identifies this invocation
only if the operator supplies and verifies it independently and never reuses
it. This profile is not wired into Forge, CI, reports or admission gates.
