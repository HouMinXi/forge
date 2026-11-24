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
