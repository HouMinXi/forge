# SPDX-License-Identifier: Apache-2.0
"""#230: child test runs start with SIGINT at its default even when forge
inherited SIGINT=SIG_IGN from a background shell."""

import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from code_forge import _gate_pytest, _mutation_process

PROBE = "import signal; print(signal.getsignal(signal.SIGINT))"
DEFAULT = "<built-in function default_int_handler>"
SRC = Path(_mutation_process.__file__).parent
MODULES = [_mutation_process, _gate_pytest]


@pytest.fixture
def ignored_sigint():
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


@pytest.fixture
def default_sigint():
    """Explicit precondition: independent of how pytest itself was launched
    (#225 not required) and restored so no swap leaks into later tests."""
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _child(**kwargs):
    return subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, **kwargs).stdout.strip()


def test_inherited_sig_ign_reaches_an_unprotected_child(ignored_sigint):
    assert _child() == "1"


def test_owned_mutation_command_child_gets_default_sigint(ignored_sigint):
    result = _mutation_process.run_owned_command([sys.executable, "-c", PROBE], timeout=30, text=True)
    assert result.stdout.strip() == DEFAULT


@pytest.mark.parametrize("module", MODULES)
def test_main_thread_swaps_ignore_for_caught_noop(ignored_sigint, module):
    assert _child(**module.child_sigint_options()) == DEFAULT
    handler = signal.getsignal(signal.SIGINT)
    assert handler is module._ignore_sigint  # parent still ignores in effect
    handler(signal.SIGINT, None)  # and the no-op raises nothing


@pytest.mark.parametrize("module", MODULES)
def test_non_main_thread_falls_back_to_preexec(ignored_sigint, module):
    out = {}
    thread = threading.Thread(target=lambda: out.update(opts=module.child_sigint_options()))
    thread.start()
    thread.join()
    assert out["opts"] == {"preexec_fn": module.reset_child_sigint}
    assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
    assert _child(**out["opts"]) == DEFAULT


@pytest.mark.parametrize("module", MODULES)
def test_no_change_when_sigint_is_not_ignored(default_sigint, module):
    assert module.child_sigint_options() == {}
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


@pytest.mark.parametrize(
    ("module", "count"),
    [("_mutation_process.py", 1), ("_gate_pytest.py", 1), ("exec_falsify.py", 1), ("fixval.py", 2)],
)
def test_child_test_spawn_sites_use_the_reset(module, count):
    # fixval: the reverted-candidate test run and the overfit run (#238).
    assert (SRC / module).read_text(encoding="utf-8").count("**child_sigint_options()") == count
