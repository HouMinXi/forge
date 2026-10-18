# SPDX-License-Identifier: Apache-2.0
"""Child test runs start with SIGINT (#230) and SIGHUP (#249) at their
defaults even when forge inherited them ignored (background shell, nohup)."""

import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from code_forge import _gate_pytest, _mutation_process

PROBE = "import signal; print(signal.getsignal(signal.SIGINT), int(signal.getsignal(signal.SIGHUP)))"
DEFAULT = "<built-in function default_int_handler> 0"  # SIGINT default handler, SIGHUP SIG_DFL
SRC = Path(_mutation_process.__file__).parent
MODULES = [_mutation_process, _gate_pytest]
SIGNALS = (signal.SIGINT, signal.SIGHUP)


def _set(dispositions):
    return {signum: signal.signal(signum, handler) for signum, handler in dispositions.items()}


@pytest.fixture(params=["sigint", "sighup", "both"])
def ignored(request):
    """Forge inherited one or both signals as SIG_IGN; restored afterwards."""
    chosen = {"sigint": (signal.SIGINT,), "sighup": (signal.SIGHUP,), "both": SIGNALS}[request.param]
    previous = _set({signal.SIGINT: signal.default_int_handler, signal.SIGHUP: signal.SIG_DFL})
    _set({signum: signal.SIG_IGN for signum in chosen})
    try:
        yield chosen
    finally:
        _set(previous)


@pytest.fixture
def defaults():
    """Explicit precondition, independent of how pytest itself was launched."""
    previous = _set({signal.SIGINT: signal.default_int_handler, signal.SIGHUP: signal.SIG_DFL})
    try:
        yield
    finally:
        _set(previous)


def _child(**kwargs):
    return subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, **kwargs).stdout.strip()


def test_inherited_sig_ign_reaches_an_unprotected_child(ignored):
    sigint, sighup = _child().rsplit(" ", 1)
    assert (sigint == "1") is (signal.SIGINT in ignored)
    assert (sighup == "1") is (signal.SIGHUP in ignored)


def test_owned_mutation_command_child_gets_default_signals(ignored):
    result = _mutation_process.run_owned_command([sys.executable, "-c", PROBE], timeout=30, text=True)
    assert result.stdout.strip() == DEFAULT


@pytest.mark.parametrize("module", MODULES)
def test_main_thread_swaps_ignore_for_caught_noop(ignored, module):
    assert _child(**module.child_signal_options()) == DEFAULT
    for signum in ignored:
        handler = signal.getsignal(signum)
        assert handler is module._ignore_inherited  # parent still ignores in effect
        handler(signum, None)  # and the no-op raises nothing


@pytest.mark.parametrize("module", MODULES)
def test_non_main_thread_falls_back_to_preexec(ignored, module):
    out = {}
    thread = threading.Thread(target=lambda: out.update(opts=module.child_signal_options()))
    thread.start()
    thread.join()
    assert out["opts"] == {"preexec_fn": module.reset_child_signals}
    assert all(signal.getsignal(signum) == signal.SIG_IGN for signum in ignored)
    assert _child(**out["opts"]) == DEFAULT


@pytest.mark.parametrize("module", MODULES)
def test_no_change_when_nothing_is_ignored(defaults, module):
    assert module.child_signal_options() == {}
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    assert signal.getsignal(signal.SIGHUP) is signal.SIG_DFL


@pytest.mark.parametrize(
    ("module", "count"),
    [("_mutation_process.py", 1), ("_gate_pytest.py", 1), ("exec_falsify.py", 1), ("fixval.py", 2)],
)
def test_child_test_spawn_sites_use_the_reset(module, count):
    # fixval: the reverted-candidate test run and the overfit run (#238).
    assert (SRC / module).read_text(encoding="utf-8").count("**child_signal_options()") == count
