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
    return subprocess.run(
        [sys.executable, "-c", PROBE], capture_output=True, text=True, **kwargs
    ).stdout.strip()


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
    [
        ("_mutation_process.py", 1),
        ("_gate_pytest.py", 1),
        ("exec_falsify.py", 1),
        ("fixval.py", 2),
        ("baseline_guard.py", 1),
        ("gate_check.py", 1),
    ],
)
def test_child_test_spawn_sites_use_the_reset(module, count):
    # fixval: the reverted-candidate test run and the overfit run (#238).
    # baseline_guard: the default (non-owned) runner fixval's guard uses;
    # gate_check: the non-pytest test command (#273).
    assert (SRC / module).read_text(encoding="utf-8").count("**child_sigint_options()") == count


@pytest.fixture
def ignored_sighup():
    previous = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGHUP, previous)


HUP_PROBE = "import signal; print(int(signal.getsignal(signal.SIGHUP)))"


@pytest.mark.parametrize("thread", ["main", "worker"])
@pytest.mark.parametrize("module", MODULES)
def test_child_keeps_ignored_sighup(ignored_sigint, ignored_sighup, module, thread):
    # #249 option (a): only SIGINT is reset. Under nohup the child must keep
    # SIGHUP ignored, or a group hangup kills it mid-run (and fixval would
    # read the signal exit as a failing test).
    if thread == "main":
        options = module.child_sigint_options()
    else:
        out = {}
        worker = threading.Thread(target=lambda: out.update(opts=module.child_sigint_options()))
        worker.start()
        worker.join()
        options = out["opts"]
    child = subprocess.run([sys.executable, "-c", HUP_PROBE], capture_output=True, text=True, **options)
    assert child.stdout.strip() == "1"  # SIG_IGN
    assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
    assert _child(**options) == DEFAULT  # SIGINT is still reset


def test_default_baseline_guard_child_gets_default_sigint(ignored_sigint, tmp_path):
    # #273: fixval's guard runs with run_command=None (plain subprocess.run).
    from code_forge import baseline_guard

    marker = tmp_path / "seen"
    probe = "import signal; open(%r, 'a').write(str(signal.getsignal(signal.SIGINT)) + chr(10))"
    probe %= str(marker)
    status, _, infra = baseline_guard._run_baseline_guard(
        [sys.executable, "-c", probe], {}, str(tmp_path), allow_strip_retry=False, timeout=30
    )
    assert status == "passed", infra
    assert marker.read_text().splitlines() == [DEFAULT] * 3


def test_gate_check_non_pytest_command_child_gets_default_sigint(ignored_sigint, tmp_path):
    # #273: gate_check's non-pytest test command spawns with the reset too.
    import os
    from io import StringIO

    import yaml

    from code_forge.gate_check import run_gate_check

    repo = tmp_path / "repo"
    (repo / "src" / "demo").mkdir(parents=True)
    (repo / "src" / "demo" / "core.py").write_text("X = 1\n")
    marker = tmp_path / "seen"
    (repo / "probe.py").write_text(
        "import signal\nopen(%r, 'w').write(str(signal.getsignal(signal.SIGINT)))\n" % str(marker)
    )
    (repo / ".code-forge").mkdir()
    (repo / ".code-forge" / "gate.yaml").write_text(
        yaml.safe_dump(
            {
                "test": {
                    "command": ["python3", "probe.py"],
                    "source_patterns": ["src/**/*.py"],
                    "timeout_seconds": 60,
                }
            }
        )
    )

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q")
    git("-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
    git("add", "-A")
    path = os.pathsep.join([os.path.dirname(sys.executable), os.environ.get("PATH", "")])
    env = dict(os.environ, FORGE_ALLOW_MAIN="1", PATH=path)
    err = StringIO()
    run_gate_check(args=None, env=env, cwd=repo, stdout=StringIO(), stderr=err)
    assert marker.exists(), err.getvalue()
    assert marker.read_text() == DEFAULT
