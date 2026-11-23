"""V07: real mutmut run inside the isolation sandbox.

One killed mutant (test detects it) and one surviving mutant (x*0 -> x*1
at x=0 is still 0, so the test cannot see it) prove the adapter maps both
sides from real native artifacts, not fixtures of its own making.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from code_forge.mutation_engines.adapters.base import (
    ExecutionContext,
    InputEntry,
    InputSnapshot,
)
from code_forge.mutation_engines.adapters.python_mutmut import (
    ADAPTER_ID,
    MutmutAdapter,
)
from code_forge.mutation_engines.schemas import (
    BaselineState,
    Budget,
    NormalizedStatus,
    RunState,
    TargetDeclaration,
)
from code_forge.mutation_engines.targets import TargetSelection

_build_outcomes = MutmutAdapter._build_outcomes
_collect_meta = MutmutAdapter._collect_meta

CGROUP_ROOT = "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service"
SANDBOX_PYTHON = "/usr/bin/python3"

import mutmut  # noqa: E402

MUTMUT_SITE = str(Path(mutmut.__file__).resolve().parent.parent)

# pytest kept Node._nodeid until 9.2.0, which replaced the internal string with
# a structured NodeId. mutmut 3.8.0 still reads item._nodeid in its harness
# plugin; 3.8.1 reads the public item.nodeid. The wall is the pairing, not a
# major version: a mutmut that reads the private attribute cannot drive a
# pytest that no longer has it.
_PYTEST_NODE_PRIVATE = (9, 2)

from packaging.version import InvalidVersion, Version  # noqa: E402


def _soabi(python: str) -> str:
    """Extension ABI such as ``cpython-314t-x86_64-linux-gnu``, or "".

    SOABI carries the flags that decide whether an extension module loads:
    ``t`` for a free-threaded build and ``d`` for a debug build, plus the
    platform. The import cache tag does not.
    """

    try:
        out = subprocess.run(
            [python, "-I", "-c", "import sysconfig;print(sysconfig.get_config_var('SOABI'))"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    tag = out.stdout.strip()
    return "" if tag == "None" else tag


def _mutmut_release(version: str) -> Version | None:
    """The parsed release, or None when no PEP 440 parser accepts it."""

    try:
        return Version(version.strip())
    except InvalidVersion:
        return None


def _reads_private_nodeid(mutmut_version: str) -> bool:
    """Whether this mutmut reads ``item._nodeid`` rather than ``item.nodeid``.

    3.8.1 switched the harness to the public attribute; anything earlier that
    parses still reads the private one. A string no parser accepts is not
    evidence that the harness was fixed.
    """

    parsed = _mutmut_release(mutmut_version)
    if parsed is None or parsed.is_prerelease or parsed.is_devrelease:
        return True
    return parsed.release < (3, 8, 1)


def _skip_reason(sandbox_abi: str, host_abi: str, pytest_version: str, mutmut_version: str) -> str:
    """Why this test cannot run here, or "" when it can.

    The sandbox may have no interpreter to probe; the two interpreters may
    disagree on the extension ABI, so one cannot load the other's compiled
    modules; and a mutmut that reads ``item._nodeid`` cannot drive a pytest
    whose nodes no longer carry it.
    """

    if not sandbox_abi:
        return f"cannot probe {SANDBOX_PYTHON}; the sandbox interpreter is unknown"
    if not host_abi:
        return "cannot read the test interpreter's SOABI; the sandbox comparison is unknown"
    if sandbox_abi != host_abi:
        return (
            f"sandbox interpreter {sandbox_abi} would load {host_abi} site-packages; "
            "extension modules are ABI-specific"
        )
    pytest_release = _mutmut_release(pytest_version)
    private_removed = pytest_release is not None and pytest_release.release >= _PYTEST_NODE_PRIVATE
    if private_removed and _reads_private_nodeid(mutmut_version):
        return (
            f"mutmut {mutmut_version.strip()} reads item._nodeid, removed in pytest "
            f"{pytest_version.strip()}; mutants are generated but never executed"
        )
    return ""


def _sandbox_skip_reason() -> str:
    """Evaluate the walls when the test runs, not while the suite is collected.

    Probing spawns the sandbox interpreter, which collection has no reason to
    pay for, and a transient failure while probing should not decide the fate
    of a whole session.
    """

    return _skip_reason(
        _soabi(SANDBOX_PYTHON),
        sysconfig.get_config_var("SOABI") or "",
        pytest.__version__,
        mutmut.__version__,
    )


CALC = """def add(a, b):
    return a + b


def double(x):
    return x * 0
"""

TEST_CALC = """from calc import add, double


def test_add():
    assert add(1, 2) == 3


def test_double():
    assert double(0) == 0
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot(root: Path) -> InputSnapshot:
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root))
            entries.append(
                InputEntry(
                    path=rel,
                    digest=_sha256(path.read_bytes()),
                    mode=path.stat().st_mode & 0o777,
                    symlink_target=None,
                )
            )
    return InputSnapshot(
        reviewed_source_id="review-v07",
        manifest_digest=_sha256(b"manifest"),
        selection_digest=_sha256(b"selection"),
        root=str(root),
        files=tuple(entries),
    )


def _target() -> TargetDeclaration:
    return TargetDeclaration(
        id="py-t",
        adapter=ADAPTER_ID,
        root=".",
        sources=("calc.py",),
        tests=("test_calc.py",),
        inputs=(),
        oracle="pytest",
        command=("python3", "-m", "pytest"),
        execution_profile="p",
        environment="e",
        budget=Budget(
            total_seconds=420,
            baseline_seconds=120,
            mutant_seconds=300,
            concurrency=1,
            memory_mb=1024,
            processes=128,
            workspace_mb=128,
            evidence_mb=32,
        ),
    )


def _context(state_root: str) -> ExecutionContext:
    return ExecutionContext(
        run_id="run-v07",
        config_digest="c",
        execution_policy_digest="e",
        toolchain_fingerprint="t",
        cgroup_root=CGROUP_ROOT,
        state_root=state_root,
        approved_python=SANDBOX_PYTHON,
        memory_mb=1024,
        pids=128,
        workspace_mb=128,
        process_headroom_mb=128,
        extra_python_paths=(MUTMUT_SITE,),
    )


def _selection() -> TargetSelection:
    return TargetSelection(
        target_id="py-t",
        granularity="full",
        files=("calc.py",),
        line_ranges={},
        reasons=("test",),
    )


@pytest.mark.parametrize(
    ("sandbox_abi", "host_abi", "pytest_version", "mutmut_version", "expected"),
    [
        (
            "cpython-314t-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "8.3.0",
            "3.8.1",
            "sandbox interpreter cpython-314t-x86_64-linux-gnu would load cpython-314-x86_64-linux-gnu site-packages; extension modules are ABI-specific",
        ),
        (
            "cpython-315-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0",
            "sandbox interpreter cpython-315-x86_64-linux-gnu would load cpython-314-x86_64-linux-gnu site-packages; extension modules are ABI-specific",
        ),
        (
            "",
            "cpython-314-x86_64-linux-gnu",
            "8.3.0",
            "3.8.1",
            f"cannot probe {SANDBOX_PYTHON}; the sandbox interpreter is unknown",
        ),
        (
            "",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.8.0",
            f"cannot probe {SANDBOX_PYTHON}; the sandbox interpreter is unknown",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "",
            "9.2.0",
            "3.9.0",
            "cannot read the test interpreter's SOABI; the sandbox comparison is unknown",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "",
            "9.2.0",
            "3.8.0",
            "cannot read the test interpreter's SOABI; the sandbox comparison is unknown",
        ),
        (
            "",
            "",
            "9.2.0",
            "3.9.0",
            f"cannot probe {SANDBOX_PYTHON}; the sandbox interpreter is unknown",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.8.0",
            "mutmut 3.8.0 reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.1.1",
            "3.8.0",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.8.1",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "10.0.0",
            "3.8.0",
            "mutmut 3.8.0 reads item._nodeid, removed in pytest 10.0.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0rc1",
            "mutmut 3.9.0rc1 reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "4.0.0rc1",
            "mutmut 4.0.0rc1 reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            " 3.9.0rc1",
            "mutmut 3.9.0rc1 reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-315-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.8.0",
            "sandbox interpreter cpython-315-x86_64-linux-gnu would load cpython-314-x86_64-linux-gnu site-packages; extension modules are ABI-specific",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0garbage",
            "mutmut 3.9.0garbage reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "4.0.0garbage.dev1",
            "mutmut 4.0.0garbage.dev1 reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "garbage",
            "mutmut garbage reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "8.3.0",
            "3.8.0",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "8.3.0",
            "3.9.0rc1",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0.dev0",
            "mutmut 3.9.0.dev0 reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0.post1",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0post1",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0.post.1",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0.post_1",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0rev-1",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0.POST1",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0.rev1",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0.r2",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0.post",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0-1",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "v3.9.0",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "1!3.9.0",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0+",
            "mutmut 3.9.0+ reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0+foo..bar",
            "mutmut 3.9.0+foo..bar reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0_1",
            "mutmut 3.9.0_1 reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0RC1",
            "mutmut 3.9.0RC1 reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0.post1.dev0",
            "mutmut 3.9.0.post1.dev0 reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0rc1.post1",
            "mutmut 3.9.0rc1.post1 reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0.post1.post2",
            "mutmut 3.9.0.post1.post2 reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.8.1.post1",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.8.0+local",
            "mutmut 3.8.0+local reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0+local",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0+foo.dev0",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0+foo-rc1",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "4.0.0",
            "",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "3.9.0.post１",
            "mutmut 3.9.0.post１ reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
        (
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            "３.９.０",
            "mutmut ３.９.０ reads item._nodeid, removed in pytest 9.2.0; mutants are generated but never executed",
        ),
    ],
    ids=[
        "abi-flags-differ",
        "abi-version-differ",
        "unprobeable-pytest8",
        "unprobeable-pytest9",
        "empty-host-abi",
        "empty-host-abi-beats-version-wall",
        "both-abi-empty",
        "old-mutmut-on-pytest-9-2",
        "old-mutmut-on-pytest-9-1",
        "fixed-mutmut-on-pytest-9-2",
        "old-mutmut-on-pytest-10",
        "prerelease-is-not-fixed",
        "future-prerelease-is-not-fixed",
        "leading-space-prerelease",
        "abi-and-version-walls",
        "malformed-suffix-not-fixed",
        "dev-segment-after-garbage",
        "unparseable-version",
        "pytest8",
        "pytest8-prerelease",
        "two-component-fixed",
        "released-fixed",
        "dev-release-not-fixed",
        "post-release-is-fixed",
        "post-no-separator-is-fixed",
        "post-separated-number-is-fixed",
        "post-underscore-number-is-fixed",
        "rev-hyphen-number-is-fixed",
        "uppercase-post-is-fixed",
        "rev-release-is-fixed",
        "r-release-is-fixed",
        "bare-post-is-fixed",
        "implicit-post-is-fixed",
        "v-prefix-is-fixed",
        "epoch-is-fixed",
        "empty-local-segment-not-fixed",
        "malformed-local-not-fixed",
        "underscore-number-not-fixed",
        "uppercase-prerelease-not-fixed",
        "post-then-dev-not-fixed",
        "rc-then-post-not-fixed",
        "repeated-post-suffix-not-fixed",
        "post-of-fixed-is-fixed",
        "local-below-threshold-not-fixed",
        "local-version-is-fixed",
        "local-dev-suffix-is-fixed",
        "local-rc-suffix-is-fixed",
        "future",
        "fullwidth-post-digit",
        "fullwidth-version",
    ],
)
def test_skip_reason_matrix(sandbox_abi, host_abi, pytest_version, mutmut_version, expected):
    """Each wall reports itself alone, and a runnable pair reports nothing."""

    assert _skip_reason(sandbox_abi, host_abi, pytest_version, mutmut_version) == expected


def test_soabi_probe_contract(monkeypatch):
    """The probe's command shape, decoding and failure mode are all pinned."""

    calls = []

    class Result:
        stdout = "  cpython-314-x86_64-linux-gnu\n"

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _soabi("/opt/other/python") == "cpython-314-x86_64-linux-gnu"
    assert len(calls) == 1
    assert calls[0][0] == [
        "/opt/other/python",
        "-I",
        "-c",
        "import sysconfig;print(sysconfig.get_config_var('SOABI'))",
    ]
    assert calls[0][1] == {
        "capture_output": True,
        "text": True,
        "timeout": 60,
        "check": True,
    }

    class Tagless:
        stdout = "None\n"

    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: Tagless())
    assert _soabi("/usr/bin/python3") == ""

    class Blank:
        stdout = "   \n"

    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: Blank())
    assert _soabi("/usr/bin/python3") == ""

    def boom(argv, **kwargs):
        raise OSError("no interpreter")

    monkeypatch.setattr(subprocess, "run", boom)
    assert _soabi("/usr/bin/python3") == ""

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=60)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert _soabi("/usr/bin/python3") == ""

    def failed(argv, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=argv)

    monkeypatch.setattr(subprocess, "run", failed)
    assert _soabi("/usr/bin/python3") == ""


def test_sandbox_skip_reason_forwards_inputs(monkeypatch):
    """The wall check reads the live interpreter, pytest and mutmut versions."""

    probed = []

    def fake_abi(python):
        probed.append(python)
        return "cpython-314-x86_64-linux-gnu"

    monkeypatch.setattr(sys.modules[__name__], "_soabi", fake_abi)
    monkeypatch.setattr(sysconfig, "get_config_var", lambda name: "cpython-315-x86_64-linux-gnu")
    monkeypatch.setattr(mutmut, "__version__", "3.8.1")
    monkeypatch.setattr(pytest, "__version__", "9.2.0")
    assert _sandbox_skip_reason() == (
        "sandbox interpreter cpython-314-x86_64-linux-gnu would load "
        "cpython-315-x86_64-linux-gnu site-packages; extension modules are ABI-specific"
    )
    assert probed == [SANDBOX_PYTHON]

    monkeypatch.setattr(sysconfig, "get_config_var", lambda name: "cpython-314-x86_64-linux-gnu")
    monkeypatch.setattr(mutmut, "__version__", "3.8.0")
    assert _sandbox_skip_reason() == (
        "mutmut 3.8.0 reads item._nodeid, removed in pytest 9.2.0; "
        "mutants are generated but never executed"
    )
    assert probed == [SANDBOX_PYTHON] * 2

    monkeypatch.setattr(pytest, "__version__", "9.1.1")
    assert _sandbox_skip_reason() == ""
    assert probed == [SANDBOX_PYTHON] * 3

    monkeypatch.setattr(pytest, "__version__", "9.2.0")

    def empty_abi(python):
        probed.append(python)
        return ""

    monkeypatch.setattr(sys.modules[__name__], "_soabi", empty_abi)
    assert _sandbox_skip_reason() == (
        f"cannot probe {SANDBOX_PYTHON}; the sandbox interpreter is unknown"
    )
    assert probed == [SANDBOX_PYTHON] * 4

    monkeypatch.setattr(sys.modules[__name__], "_soabi", fake_abi)
    monkeypatch.setattr(sysconfig, "get_config_var", lambda name: None)
    assert _sandbox_skip_reason() == (
        "cannot read the test interpreter's SOABI; the sandbox comparison is unknown"
    )
    assert probed == [SANDBOX_PYTHON] * 5

    monkeypatch.setattr(sysconfig, "get_config_var", lambda name: "cpython-314-x86_64-linux-gnu")
    monkeypatch.setattr(mutmut, "__version__", "3.8.1")
    assert _sandbox_skip_reason() == ""
    assert probed == [SANDBOX_PYTHON] * 6  # one probe per call, no memo


def test_version_wall_uses_the_pep440_parser():
    """A string no PEP 440 parser accepts never counts as a fixed mutmut.

    ``4.0.0garbage.dev1`` is the shape a digit-only regular expression lets
    through: the leading numbers are high and a ``dev`` segment follows, so
    the tail looks like a dev release of a future series. packaging rejects
    it, and so does the wall.
    """

    for text in [
        "garbage",
        "3.9.0garbage",
        "4.0.0garbage",
        "4.0.0garbage.dev1",
        "3.10.0.post1.post2",
        "3.9.0.post1.post2",
        "3.9.0_1",
        "3.9.0+",
        "3.9.0+foo..bar",
        "3.9.0.post\uff11",
        "\uff13.\uff19.\uff10",
    ]:
        assert _mutmut_release(text) is None, text
        assert _skip_reason(
            "cpython-314-x86_64-linux-gnu",
            "cpython-314-x86_64-linux-gnu",
            "9.2.0",
            text,
        ) == (
            f"mutmut {text} reads item._nodeid, removed in pytest 9.2.0; "
            "mutants are generated but never executed"
        ), text


@pytest.mark.skipif(not os.path.isdir(CGROUP_ROOT), reason="delegated cgroup root unavailable")
def test_real_mutmut_run_killed_and_survived(tmp_path):
    skip_reason = _sandbox_skip_reason()
    if skip_reason:
        pytest.skip(skip_reason)

    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "calc.py").write_text(CALC)
    (source_root / "test_calc.py").write_text(TEST_CALC)
    state_root = tmp_path / "state"
    state_root.mkdir()

    result = MutmutAdapter().run(
        _target(), _selection(), _snapshot(source_root), _context(str(state_root))
    )

    assert result.run_state is RunState.COMPLETE, result.infrastructure_errors
    assert result.reason_code == "complete"
    assert result.baseline.state is BaselineState.PASSED
    assert result.baseline.test_count == 2

    normalized = {o.mutant_id: o.normalized_status for o in result.outcomes}
    assert result.inventory.generated == len(normalized) >= 2
    killed = [k for k, v in normalized.items() if v is NormalizedStatus.KILLED]
    survived = [k for k, v in normalized.items() if v is NormalizedStatus.SURVIVED]
    assert killed, "expected at least one killed mutant, got %r" % (normalized,)
    assert survived, "expected at least one survived mutant, got %r" % (normalized,)

    # killed verdicts must carry plugin event evidence, not just the label
    for outcome in result.outcomes:
        if outcome.normalized_status is NormalizedStatus.KILLED:
            assert outcome.test_evidence, outcome.mutant_id

    # artifacts really published into the run directory
    results_dir = state_root / "runs" / "run-v07" / "results"
    assert (results_dir / "mutmut-meta.json").exists()

    # receipts: baseline plus mutmut invocation
    assert len(result.command_receipts) == 2
    baseline_receipt = result.command_receipts[0]
    assert baseline_receipt.exit_code == 0
    assert "pytest" in " ".join(baseline_receipt.argv)


def test_build_outcomes_native_killed_without_event_is_unknown(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "calc.py").write_text(CALC)
    events = tmp_path / "events"
    events.mkdir()
    run_dir = tmp_path / "run"
    meta = {"exit_code_by_key": {"x_calc.add__mutmut_1": 1}}
    outcomes, artifacts = _build_outcomes({Path("calc.py"): meta}, events, "run-x", run_dir, workspace)
    assert len(outcomes) == 1
    assert outcomes[0].normalized_status is NormalizedStatus.UNKNOWN
    assert outcomes[0].native_status == "killed"
    assert outcomes[0].test_evidence == ()


def test_collect_meta_missing_source_is_hold_signal(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "mutants").mkdir(parents=True)
    (workspace / "mutants" / "a.py.meta").write_text(
        '{"exit_code_by_key": {}, "durations_by_key": {}, "estimated_durations_by_key": {}}'
    )
    found, missing = _collect_meta(workspace, [Path("a.py"), Path("b.py")])
    assert list(found) == [Path("a.py")]
    assert missing == [Path("b.py")]


def test_collect_meta_empty_map_still_completes(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "mutants").mkdir(parents=True)
    (workspace / "mutants" / "a.py.meta").write_text(
        '{"exit_code_by_key": {}, "durations_by_key": {}, "estimated_durations_by_key": {}}'
    )
    found, missing = _collect_meta(workspace, [Path("a.py")])
    assert missing == []
    assert found[Path("a.py")]["exit_code_by_key"] == {}
