"""V07: real mutmut run inside the isolation sandbox.

One killed mutant (test detects it) and one surviving mutant (x*0 -> x*1
at x=0 is still 0, so the test cannot see it) prove the adapter maps both
sides from real native artifacts, not fixtures of its own making.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
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

# mutmut 3.8.x reads ``item._nodeid`` in its harness plugin; pytest 9 dropped
# that attribute, so the stats sweep dies before any mutant runs. A release at
# or above this version is expected to carry the fix, pre-releases excepted.
MUTMUT_PYTEST9_FIXED = (3, 9)

_MUTMUT_NUMBER = re.compile(r"(?i)^\s*v?(?:[0-9]+!)?([0-9]+)(?:\.([0-9]+))?(?:\.[0-9]+)*")
_MUTMUT_LOCAL = re.compile(r"(?i)^[a-z0-9]+(?:[.\-_][a-z0-9]+)*$")
_MUTMUT_POSTRELEASE = re.compile(r"(?i)^[.\-_]?(?:post|rev|r)[.\-_]?[0-9]*$")
_MUTMUT_IMPLICIT_POST = re.compile(r"^[.\-][0-9]+$")
_MUTMUT_PRERELEASE = re.compile(r"(?i)(?:^|[.\-_])(?:a|b|c|rc|alpha|beta|pre|preview)[0-9]*(?=$|[.\-_])")
_MUTMUT_DEV = re.compile(r"(?i)[.\-_]?dev[0-9]*$")


def _cache_tag(python: str) -> str:
    """Interpreter ABI tag such as ``cpython-315``, or "" when unprobeable.

    ``sys.implementation.cache_tag`` folds the version, the flags (``d`` for a
    debug build, ``t`` for a free-threaded one) and the implementation into the
    token extension filenames are stamped with, so comparing tags answers the
    question the sandbox actually raises: can this interpreter load these
    compiled modules at all.
    """

    try:
        out = subprocess.run(
            [python, "-I", "-c", "import sys;print(sys.implementation.cache_tag)"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    tag = out.stdout.strip()
    # A build without a cache tag prints the word None; that is absence, not a tag.
    return "" if tag == "None" else tag


def _mutmut_numbers(version: str) -> tuple[tuple[int, int], bool, bool]:
    """Major/minor of ``version`` plus whether its tail rules out the fix.

    A version that ends in its numbers, or in a post-release suffix, names
    shipped code. Every other tail rules the fix out: pre-releases and
    dev-releases precede it, post-releases carrying a further dev segment do
    too, and a string no parser accepts is not evidence of the fix.
    """

    text = version.strip()
    public, separator, local = text.partition("+")
    if separator and not _MUTMUT_LOCAL.match(local):
        return (0, 0), False, False
    match = _MUTMUT_NUMBER.match(public)
    if match is None:
        return (0, 0), False, False
    pair = (int(match.group(1)), int(match.group(2) or 0))
    tail = public[match.end() :]
    if not tail:
        return pair, False, True
    if _MUTMUT_DEV.search(tail):
        return pair, True, True
    if _MUTMUT_POSTRELEASE.match(tail) or _MUTMUT_IMPLICIT_POST.match(tail):
        return pair, False, True
    # A pre-release marker is a version a parser accepts; anything else after
    # the numbers is a string no parser accepts.
    return pair, True, bool(_MUTMUT_PRERELEASE.search(tail))


def _skip_reason(sandbox_tag: str, host_tag: str, pytest_major: int, mutmut_version: str) -> str:
    """Why this test cannot run here, or "" when it can.

    Three walls hit different hosts: the sandbox may have no interpreter to
    launch at all; it mounts the test interpreter's site-packages, whose
    compiled modules only load under a matching ABI tag; and mutmut below 3.9
    cannot drive pytest 9 onwards. Pre-releases are held to the unfixed side
    because the fix may land after them.
    """

    if not sandbox_tag:
        return f"cannot probe {SANDBOX_PYTHON}; the sandbox interpreter is unknown"
    if not host_tag:
        return "cannot read the test interpreter's cache tag; the sandbox comparison is unknown"
    if sandbox_tag != host_tag:
        return (
            f"sandbox interpreter {sandbox_tag} would load {host_tag} site-packages; "
            "extension modules are ABI-specific"
        )
    pair, rules_out_fix, recognized = _mutmut_numbers(mutmut_version)
    # A later release series carries the fix, but only when the version is one
    # a parser accepts. An unparseable string is not evidence of the fix, no
    # matter how large its leading numbers are.
    unfixed = (
        not recognized or pair < MUTMUT_PYTEST9_FIXED or (pair == MUTMUT_PYTEST9_FIXED and rules_out_fix)
    )
    if pytest_major >= 9 and unfixed:
        return (
            f"mutmut {mutmut_version.strip()} calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed"
        )
    return ""


def _sandbox_skip_reason() -> str:
    """Evaluate the walls when the test runs, not while the suite is collected.

    Probing spawns the sandbox interpreter, which collection has no reason to
    pay for, and a transient failure while probing should not decide the fate
    of a whole session.
    """

    return _skip_reason(
        _cache_tag(SANDBOX_PYTHON),
        sys.implementation.cache_tag or "",
        int(pytest.__version__.split(".")[0]),
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
    ("sandbox_tag", "host_tag", "pytest_major", "mutmut_version", "expected"),
    [
        (
            "cpython-314t",
            "cpython-314",
            8,
            "3.8.1",
            "sandbox interpreter cpython-314t would load cpython-314 site-packages; "
            "extension modules are ABI-specific",
        ),
        (
            "cpython-315",
            "cpython-314",
            9,
            "3.9.0",
            "sandbox interpreter cpython-315 would load cpython-314 site-packages; "
            "extension modules are ABI-specific",
        ),
        (
            "",
            "cpython-314",
            8,
            "3.8.1",
            f"cannot probe {SANDBOX_PYTHON}; the sandbox interpreter is unknown",
        ),
        (
            "",
            "cpython-314",
            9,
            "3.8.1",
            f"cannot probe {SANDBOX_PYTHON}; the sandbox interpreter is unknown",
        ),
        (
            "cpython-314",
            "",
            9,
            "3.9.0",
            "cannot read the test interpreter's cache tag; the sandbox comparison is unknown",
        ),
        (
            "cpython-314",
            "",
            9,
            "3.8.1",
            "cannot read the test interpreter's cache tag; the sandbox comparison is unknown",
        ),
        (
            "",
            "",
            9,
            "3.9.0",
            f"cannot probe {SANDBOX_PYTHON}; the sandbox interpreter is unknown",
        ),
        (
            "cpython-314",
            "cpython-314",
            9,
            "3.8.1",
            "mutmut 3.8.1 calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        (
            "cpython-314",
            "cpython-314",
            10,
            "3.8.1",
            "mutmut 3.8.1 calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        (
            "cpython-314",
            "cpython-314",
            9,
            "3.9.0rc1",
            "mutmut 3.9.0rc1 calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        ("cpython-314", "cpython-314", 9, "4.0.0rc1", ""),
        (
            "cpython-314",
            "cpython-314",
            9,
            " 3.9.0rc1",
            "mutmut 3.9.0rc1 calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        (
            "cpython-315",
            "cpython-314",
            9,
            "3.8.1",
            "sandbox interpreter cpython-315 would load cpython-314 site-packages; "
            "extension modules are ABI-specific",
        ),
        (
            "cpython-314",
            "cpython-314",
            9,
            "3.9.0garbage",
            "mutmut 3.9.0garbage calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        (
            "cpython-314",
            "cpython-314",
            9,
            "garbage",
            "mutmut garbage calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        ("cpython-314", "cpython-314", 8, "3.8.1", ""),
        ("cpython-314", "cpython-314", 8, "3.9.0rc1", ""),
        ("cpython-314", "cpython-314", 9, "3.9", ""),
        ("cpython-314", "cpython-314", 9, "3.9.0", ""),
        (
            "cpython-314",
            "cpython-314",
            9,
            "3.9.0.dev0",
            "mutmut 3.9.0.dev0 calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        ("cpython-314", "cpython-314", 9, "3.9.0.post1", ""),
        ("cpython-314", "cpython-314", 9, "3.9.0post1", ""),
        ("cpython-314", "cpython-314", 9, "3.9.0.post.1", ""),
        ("cpython-314", "cpython-314", 9, "3.9.0.post_1", ""),
        ("cpython-314", "cpython-314", 9, "3.9.0rev-1", ""),
        ("cpython-314", "cpython-314", 9, "3.9.0.POST1", ""),
        ("cpython-314", "cpython-314", 9, "3.9.0.rev1", ""),
        ("cpython-314", "cpython-314", 9, "3.9.0.r2", ""),
        ("cpython-314", "cpython-314", 9, "3.9.0.post", ""),
        ("cpython-314", "cpython-314", 9, "3.9.0-1", ""),
        ("cpython-314", "cpython-314", 9, "v3.9.0", ""),
        ("cpython-314", "cpython-314", 9, "1!3.9.0", ""),
        (
            "cpython-314",
            "cpython-314",
            9,
            "3.9.0+",
            "mutmut 3.9.0+ calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        (
            "cpython-314",
            "cpython-314",
            9,
            "3.9.0+foo..bar",
            "mutmut 3.9.0+foo..bar calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        (
            "cpython-314",
            "cpython-314",
            9,
            "3.9.0_1",
            "mutmut 3.9.0_1 calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        (
            "cpython-314",
            "cpython-314",
            9,
            "3.9.0RC1",
            "mutmut 3.9.0RC1 calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        (
            "cpython-314",
            "cpython-314",
            9,
            "3.9.0.post1.dev0",
            "mutmut 3.9.0.post1.dev0 calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        (
            "cpython-314",
            "cpython-314",
            9,
            "3.9.0rc1.post1",
            "mutmut 3.9.0rc1.post1 calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        (
            "cpython-314",
            "cpython-314",
            9,
            "3.9.0.post1.post2",
            "mutmut 3.9.0.post1.post2 calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        (
            "cpython-314",
            "cpython-314",
            9,
            "3.8.1.post1",
            "mutmut 3.8.1.post1 calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        (
            "cpython-314",
            "cpython-314",
            9,
            "3.8.1+local",
            "mutmut 3.8.1+local calls item._nodeid, removed in pytest 9; "
            "mutants are generated but never executed",
        ),
        ("cpython-314", "cpython-314", 9, "3.9.0+local", ""),
        ("cpython-314", "cpython-314", 9, "3.9.0+foo.dev0", ""),
        ("cpython-314", "cpython-314", 9, "3.9.0+foo-rc1", ""),
        ("cpython-314", "cpython-314", 10, "4.0.0", ""),
    ],
    ids=[
        "abi-flags-differ",
        "abi-version-differ",
        "unprobeable-pytest8",
        "unprobeable-pytest9",
        "empty-host-tag",
        "empty-host-tag-beats-version-wall",
        "both-tags-empty",
        "pytest9-wall",
        "pytest10-still-wall",
        "prerelease-is-not-fixed",
        "future-prerelease-is-fixed",
        "leading-space-prerelease",
        "abi-and-version-walls",
        "malformed-suffix-not-fixed",
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
        "post-below-threshold-not-fixed",
        "local-below-threshold-not-fixed",
        "local-version-is-fixed",
        "local-dev-suffix-is-fixed",
        "local-rc-suffix-is-fixed",
        "future",
    ],
)
def test_skip_reason_matrix(sandbox_tag, host_tag, pytest_major, mutmut_version, expected):
    """Each wall reports itself alone, and a runnable pair reports nothing.

    Isolated combinations keep one wall from silently gating another: the ABI
    cases hold the mutmut version runnable where the wall under test is the
    ABI, and the version cases hold both tags equal.
    """

    assert _skip_reason(sandbox_tag, host_tag, pytest_major, mutmut_version) == expected


def test_cache_tag_probe_contract(monkeypatch):
    """The probe's command shape, decoding and failure mode are all pinned."""

    calls = []

    class Result:
        stdout = "  cpython-314\n"

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _cache_tag("/opt/other/python") == "cpython-314"
    assert len(calls) == 1
    assert calls[0][0] == [
        "/opt/other/python",
        "-I",
        "-c",
        "import sys;print(sys.implementation.cache_tag)",
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
    assert _cache_tag("/usr/bin/python3") == ""

    class Blank:
        stdout = "   \n"

    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: Blank())
    assert _cache_tag("/usr/bin/python3") == ""

    def boom(argv, **kwargs):
        raise OSError("no interpreter")

    monkeypatch.setattr(subprocess, "run", boom)
    assert _cache_tag("/usr/bin/python3") == ""

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=60)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert _cache_tag("/usr/bin/python3") == ""

    def failed(argv, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd=argv)

    monkeypatch.setattr(subprocess, "run", failed)
    assert _cache_tag("/usr/bin/python3") == ""


def test_sandbox_skip_reason_forwards_inputs(monkeypatch):
    """The wall check reads the live interpreter, pytest and mutmut versions."""

    probed = []

    def fake_tag(python):
        probed.append(python)
        return "cpython-314"

    monkeypatch.setattr(sys.modules[__name__], "_cache_tag", fake_tag)
    monkeypatch.setattr(sys.implementation, "cache_tag", "cpython-315", raising=False)
    monkeypatch.setattr(mutmut, "__version__", "3.8.1")
    monkeypatch.setattr(pytest, "__version__", "9.2.0")
    assert _sandbox_skip_reason() == (
        "sandbox interpreter cpython-314 would load cpython-315 site-packages; "
        "extension modules are ABI-specific"
    )
    assert probed == [SANDBOX_PYTHON]

    monkeypatch.setattr(sys.implementation, "cache_tag", "cpython-314", raising=False)
    assert _sandbox_skip_reason() == (
        "mutmut 3.8.1 calls item._nodeid, removed in pytest 9; mutants are generated but never executed"
    )
    assert probed == [SANDBOX_PYTHON] * 2

    monkeypatch.setattr(pytest, "__version__", "8.3.0")
    assert _sandbox_skip_reason() == ""
    assert probed == [SANDBOX_PYTHON] * 3

    monkeypatch.setattr(pytest, "__version__", "9.2.0")

    def empty_tag(python):
        probed.append(python)
        return ""

    monkeypatch.setattr(sys.modules[__name__], "_cache_tag", empty_tag)
    assert _sandbox_skip_reason() == (
        f"cannot probe {SANDBOX_PYTHON}; the sandbox interpreter is unknown"
    )
    assert probed == [SANDBOX_PYTHON] * 4

    monkeypatch.setattr(sys.modules[__name__], "_cache_tag", fake_tag)
    monkeypatch.setattr(sys.implementation, "cache_tag", None, raising=False)
    assert _sandbox_skip_reason() == (
        "cannot read the test interpreter's cache tag; the sandbox comparison is unknown"
    )
    assert probed == [SANDBOX_PYTHON] * 5

    monkeypatch.setattr(sys.implementation, "cache_tag", "cpython-314", raising=False)
    monkeypatch.setattr(mutmut, "__version__", "3.9.1")
    assert _sandbox_skip_reason() == ""
    assert probed == [SANDBOX_PYTHON] * 6  # one probe per call, no memo


PEP440_VERSIONS = [
    "3.9.0",
    "3.9",
    "3.9.0rc1",
    "3.9.0.dev0",
    "3.9.0.post1",
    "3.9.0.post",
    "3.9.0-1",
    "3.9.0.rev1",
    "3.9.0.r2",
    "v3.9.0",
    "1!3.9.0",
    "4.0.0rc1",
    "3.9.0+foo.dev0",
    "3.9.0+local",
    "3.9.0.post1.dev0",
    "3.9.0rc1.post1",
    "3.9.0.dev1+local",
    "3.9.0.post1+local",
    "3.9.0post1",
    "3.9.0RC1",
    "3.9.0.POST1",
    "3.9.0_POST1",
    "3.9.0.post.1",
    "3.9.0.post_1",
    "3.9.0rev-1",
    "3.9.0a1.post1",
    "3.9.0a1",
    "3.9.0b1",
    "3.9.0c1",
    "3.9.0alpha1",
    "3.9.0beta1",
    "3.9.0pre1",
    "3.9.0preview1",
]


def test_version_classifier_matches_pep440_reference():
    """The hand-written classifier agrees with a real PEP 440 parser.

    ``packaging`` arrives with pytest, so this cross-check runs wherever the
    suite does; the matrix above stays authoritative for the forms the guard
    documents, and this test is what catches a version spelling nobody thought
    to list.
    """

    reference = pytest.importorskip("packaging.version")

    for text in PEP440_VERSIONS:
        parsed = reference.Version(text)
        pair, rules_out_fix, recognized = _mutmut_numbers(text)
        release = parsed.release
        assert pair == (release[0], release[1] if len(release) > 1 else 0), text
        # The helper reports what the tail says; how a later series is treated
        # is policy, pinned by the matrix instead.
        assert rules_out_fix is parsed.is_prerelease, text
        assert recognized is True, text


def test_unparseable_versions_still_report_the_wall():
    """Strings no PEP 440 parser accepts never pass for a runnable mutmut."""

    reference = pytest.importorskip("packaging.version")

    for text in [
        "garbage",
        "3.9.0garbage",
        "4.0.0garbage",
        "3.10.0.post1.post2",
        "3.9.0.post1.post2",
        "3.9.0_1",
        "3.9.0+",
        "3.9.0+foo..bar",
        # Python's \\d matches non-ASCII digits; packaging does not.
        "3.9.0.post\uff11",
        "\uff13.\uff19.\uff10",
    ]:
        with pytest.raises(reference.InvalidVersion):
            reference.Version(text)
        assert _skip_reason("cpython-314", "cpython-314", 9, text) == (
            f"mutmut {text} calls item._nodeid, removed in pytest 9; "
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
