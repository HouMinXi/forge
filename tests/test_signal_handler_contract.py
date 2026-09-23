"""Pin which signals the cleanup handlers attach to.

Import installs the handlers. The saved previous handler lives on the
module, so the test reads that instead of guessing a closure cell.
"""

import os
import signal
import subprocess
import sys
import textwrap

import code_forge.llm_invoke as invoke


_CHILD = textwrap.dedent("""
    import signal
    import code_forge.llm_invoke as invoke
    order = []
    invoke._kill_tree = lambda proc: order.append(("kill", proc))
    def previous(signum, frame):
        order.append(("previous", signum, frame))
    signal.signal(signal.SIGINT, previous)
    signal.signal(signal.SIGTERM, previous)
    invoke._handlers_installed = False
    invoke._install_signal_handlers()
    invoke._active_proc = "active"
    signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, "frame")
    assert order == [
        ("kill", "active"), ("previous", signal.SIGINT, None),
        ("kill", "active"), ("previous", signal.SIGTERM, "frame"),
    ]
    invoke._handlers_installed = False
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    invoke._original_sigint = signal.SIG_IGN
    invoke._install_signal_handlers()
    signal.getsignal(signal.SIGINT)(signal.SIGINT, "frame")
""")


def test_handler_kills_then_forwards_in_a_fresh_process():
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-c", _CHILD],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_cleanup_handlers_attach_to_sigint_and_sigterm():
    sigint = signal.getsignal(signal.SIGINT)
    sigterm = signal.getsignal(signal.SIGTERM)
    assert callable(sigint)
    assert callable(sigterm)
    assert sigint is not signal.SIG_DFL
    assert sigterm is not signal.SIG_DFL
    assert signal.getsignal(signal.SIGHUP) is signal.SIG_DFL
    saved_int = invoke._original_sigint
    saved_term = invoke._original_sigterm
    assert saved_int is not None
    assert saved_term is not None
    invoke._install_signal_handlers()
    assert invoke._original_sigint is saved_int
    assert invoke._original_sigterm is saved_term
    assert signal.getsignal(signal.SIGINT) is sigint
    assert signal.getsignal(signal.SIGTERM) is sigterm
