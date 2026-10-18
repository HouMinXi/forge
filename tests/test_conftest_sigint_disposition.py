# SPDX-License-Identifier: Apache-2.0
"""Tests that raise SIGINT need Python's default handler under the chain."""

import os
import signal
import subprocess
import sys
from pathlib import Path

from code_forge import llm_invoke

_REPO = Path(__file__).resolve().parents[1]
_PROBE = "tests/test_conftest_sigint_disposition.py::test_llm_invoke_chains_to_default_int_handler"


def _ignore_sigint():
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _child(*argv):
    # A shell that backgrounds a command without job control (`cmd &`) hands it
    # SIGINT=SIG_IGN; exec keeps an ignored disposition, so this child starts
    # exactly like such a run, however this suite itself was launched.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_"))}
    return subprocess.run(
        [sys.executable, *argv], cwd=_REPO, env=env, preexec_fn=_ignore_sigint,
        capture_output=True, text=True, timeout=120,
    )


def test_llm_invoke_chains_to_default_int_handler():
    # Probe run by the test below in a SIGINT-ignored child; in a normal run
    # it also holds because the interpreter installs default_int_handler.
    assert llm_invoke._original_sigint is signal.default_int_handler


def test_conftest_restores_default_int_handler_when_launched_with_sigint_ignored():
    started = _child("-c", "import signal; print(signal.getsignal(signal.SIGINT) is signal.SIG_IGN)")
    assert started.stdout.strip() == "True", started.stderr  # the child really starts ignored

    probe = _child("-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", "-p", "no:randomly", _PROBE)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert "1 passed" in probe.stdout, probe.stdout
