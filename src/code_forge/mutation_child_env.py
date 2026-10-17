"""Keep mutmut's control variable inside the pytest process.

mutmut sets ``MUTANT_UNDER_TEST`` so its trampoline knows which mutant is
being measured. The trampoline keeps a process-local copy of that value, so
the pytest process keeps working after the variable is removed. A test that
starts the mutated code as a subprocess inherits the whole environment, and
mutmut's trampoline then looks for its configuration in the subprocess's own
working directory. A test that deliberately runs from outside the mirror has
no configuration there, so stats collection dies before the code under test
runs and the whole mutation round is reported as an infrastructure error.

Removing the variable at pytest startup stops it reaching those children.
The plugin lives in the mutants/ directory, which pytest sees through a
pythonpath entry in mutants/pytest.ini, and is loaded with ``-p``.
"""

import os

_CONTROL = "MUTANT_UNDER_TEST"


def pytest_configure(config) -> None:
    os.environ.pop(_CONTROL, None)
