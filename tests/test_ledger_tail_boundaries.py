# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Issue 93 append framing and ownership boundaries.

Prepared by source review against 0fa09cb33391cfcb3d885d59b02b5c261bc72e0d.
Authoring/AST inspection is not runtime qualification. The consumer regression
must first be run RED on that exact baseline, then on the candidate; retain the
original failure. Repeat with only the separator decision omitted and restored,
and independently with the OS-lock site omitted. A fixture existing here is not
evidence that any of those controls, or any platform, has passed.

The integration-marked tests require separately admitted process/thread runs.
Run them on real Linux and Windows local filesystems, including Windows reader
denial, and any additionally claimed POSIX/filesystem combination. Mocked Windows
branches do not qualify Windows. Process-owned flock emulation and weak network
filesystems remain unqualified; an unrelated close can defeat the former.
Run unchanged ledger, machine, mutation-consumer, and CLI regressions afterwards.
Real write-only ACL denial (especially under elevated users), Windows sharing
and replacement behavior, filesystem lock unavailability, resource exhaustion,
and cancellation at every individual syscall still require platform admission
or fault-injection expansion. No fsync, hostile-writer, ABA-race, exactly-once,
or hard filesystem-latency guarantee is asserted by these tests.
A single asynchronous delivery before final guard cleanup or between raw open
return and descriptor assignment can retain resources. These tests do not claim
unconditional cleanup at every opcode.
"""

from __future__ import annotations

import errno
import inspect
import json
import multiprocessing
import os
import queue
import stat
import subprocess
import sys
import threading
import traceback
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_forge import ledger
from code_forge.ledger import LedgerRow, TerminalState


def _guard_is_locked():
    """Observe guard state without requiring RLock.locked(), added in 3.14."""
    guard = ledger._LEDGER_APPEND_LOCK
    if hasattr(guard, "locked"):
        return guard.locked()
    if guard._is_owned():
        return True
    acquired = guard.acquire(blocking=False)
    if acquired:
        guard.release()
    return not acquired


def _row(root, fingerprint="fp-boundary", **changes):
    values = dict(
        fingerprint=fingerprint,
        repo_root=str(root.resolve()),
        base_sha="a" * 40,
        head_sha="b" * 40,
        file="src/example.py",
        line=7,
        axis_claim="boundary review",
        pass_provenance="L0",
        terminal_state=TerminalState.FIXED,
        evidence_class="fix_applied",
        ts="2026-10-15T00:00:00Z",
    )
    values.update(changes)
    return LedgerRow(**values)


def _record(row):
    """Independent oracle for the existing serializer, including evidence cap."""
    data = asdict(row)
    data["terminal_state"] = row.terminal_state.value
    evidence = row.evidence_class
    marker = "... [truncated]"
    if len(evidence) > 500:
        evidence = evidence[: 500 - len(marker)] + marker
    data["evidence_class"] = evidence
    return (json.dumps(data, separators=(",", ":")) + "\n").encode("utf-8")


def _path(root):
    return root / ".code-forge" / "ledger.jsonl"


def _seed(root, content):
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _subprocess_env():
    env = os.environ.copy()
    source = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _cli(root, *args):
    # Same isolation convention as tests/conftest.py, without importing tests.
    (root / "src").mkdir(exist_ok=True)
    (root / "setup.cfg").write_text(
        "# managed-by-code-forge-mutation\n[mutmut]\nsource_paths=src\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, "-m", "code_forge", "ledger", *args],
        cwd=root,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def _ordinary_finding(fingerprint):
    from code_forge.disposition import Disposition
    from code_forge.state import StateFinding

    return StateFinding(
        id=fingerprint,
        fingerprint=fingerprint,
        source="L0",
        disposition=Disposition.CONFIRMED,
        file="src/example.py",
        line_range=[7, 7],
        description="ordinary repeated finding",
    )


def _ordinary_machine(root, finding):
    from code_forge.autofix import StubAutoFixer
    from code_forge.baseline import ResolvedReview
    from code_forge.falsify import StubFalsifier
    from code_forge.machine import StateMachine
    from code_forge.state import Mode

    machine = StateMachine(
        mode=Mode.CI,
        falsifier=StubFalsifier(),
        autofixer=StubAutoFixer(),
        revert_fn=lambda finding: None,
        resolved_review=ResolvedReview(
            source_files=[Path("src/example.py")],
            baseline_content=None,
            git_diff=None,
            mode_hint="git",
            base_sha="a" * 40,
            head_sha="b" * 40,
        ),
        source_hash="boundary-source",
        baseline_spec_repr="empty",
        cwd=root,
        registry={},
        l0_runner=lambda registry, files: ([], []),
    )
    machine._state.findings.append(finding)
    return machine


def _native_survivor_and_ruling(root):
    from code_forge.disposition import Disposition
    from code_forge.state import StateFinding

    finding = StateFinding(
        id="mutant-example.x_check__mutmut_1",
        fingerprint="mutant:stable-boundary",
        source="MUTANT",
        disposition=Disposition.CONFIRMED,
        file="src/example.py",
        line_range=[7, 7],
        description="mutant survived: example.x_check__mutmut_1",
    )
    diff = "--- a/src/example.py\n+++ b/src/example.py\n@@ -1 +1 @@\n-old\n+new\n"
    key = ledger.finding_suppression_key(
        base_sha="a" * 40,
        head_sha="b" * 40,
        git_diff=diff,
        file=finding.file,
        line=7,
        source=finding.source,
        description=finding.description,
    )
    ruling = _row(
        root,
        finding.fingerprint,
        pass_provenance="MUTANT",
        suppression_key=key,
        axis_claim=finding.description,
    )
    return finding, ruling, diff


@pytest.mark.parametrize(
    "tail_kind",
    [
        "absent",
        "empty",
        "lf",
        "crlf",
        "complete_json",
        "malformed_only",
        "valid_then_malformed",
        "spaces",
        "final_cr",
        "invalid_utf8",
        "complete_line_corruption",
    ],
)
def test_exact_original_bytes_and_one_conditional_separator(tmp_path, tail_kind):
    old = _record(_row(tmp_path, "fp-old"))
    prefixes = {
        "absent": b"",
        "empty": b"",
        "lf": old,
        "crlf": old[:-1] + b"\r\n",
        "complete_json": old[:-1],
        "malformed_only": b'{"unfinished":',
        "valid_then_malformed": old + b'{"unfinished":',
        "spaces": b" \t ",
        "final_cr": old[:-1] + b"\r",
        "invalid_utf8": old + b"\xff\xfe",
        "complete_line_corruption": b"broken\n" + old,
    }
    before = prefixes[tail_kind]
    if tail_kind != "absent":
        _seed(tmp_path, before)
    new = _row(tmp_path, "fp-new", terminal_state=TerminalState.ESCAPED)
    ledger.append_row(tmp_path, new)
    separator = b"\n" if before and not before.endswith(b"\n") else b""
    assert _path(tmp_path).read_bytes() == before + separator + _record(new)


@pytest.mark.parametrize("evidence_size", [499, 500, 501, 10000])
def test_large_non_ascii_record_retains_serializer_and_evidence_contract(tmp_path, evidence_size):
    row = _row(
        tmp_path,
        axis_claim="\u8fb9\u754c\U0001f30d\n" * 5000,
        evidence_class="\u8bc1" * evidence_size,
    )
    ledger.append_row(tmp_path, row)
    assert _path(tmp_path).read_bytes() == _record(row)
    reopened = list(ledger.iter_rows(tmp_path, strict=True))
    assert len(reopened) == 1
    assert reopened[0].axis_claim == row.axis_claim
    assert len(reopened[0].evidence_class) <= 500


@pytest.mark.parametrize("old_ending", [b"", b"\n", b"\r\n", b"\r"])
def test_valid_non_lf_record_remains_strict_readable(tmp_path, old_ending):
    first = _row(tmp_path, "old")
    second = _row(tmp_path, "new")
    before = _record(first)[:-1] + old_ending
    _seed(tmp_path, before)
    ledger.append_row(tmp_path, second)
    assert list(ledger.iter_rows(tmp_path, strict=True)) == [first, second]
    assert _path(tmp_path).read_bytes().startswith(before)


@pytest.mark.integration
@pytest.mark.parametrize("corrupt_tail", [False, True], ids=["valid-no-lf", "torn-tail"])
def test_real_cli_escaped_reopens_and_stops_actual_ordinary_suppression(tmp_path, corrupt_tail):
    """RED oracle: success text alone does not prove the new ruling was readable."""
    from code_forge.disposition import Disposition

    row = _row(tmp_path)
    before = _record(row) + b'{"unfinished":' if corrupt_tail else _record(row)[:-1]
    path = _seed(tmp_path, before)
    control = _ordinary_finding(row.fingerprint)
    control_machine = _ordinary_machine(tmp_path, control)
    control_machine._suppress_known_findings()
    assert control.disposition == Disposition.DISMISSED
    assert any("ledger: suppressed" in note for note in control_machine._state.infra_errors)

    marked = _cli(
        tmp_path,
        "mark",
        row.fingerprint,
        "ESCAPED",
        "--evidence",
        "reopened",
        "--base-sha",
        row.base_sha,
        "--head-sha",
        row.head_sha,
    )
    assert marked.returncode == 0, (marked.stdout, marked.stderr)
    listed = _cli(tmp_path, "list", "--json", "--fingerprint", row.fingerprint)
    assert listed.returncode == 0, (listed.stdout, listed.stderr)
    assert [r["terminal_state"] for r in json.loads(listed.stdout)] == ["FIXED", "ESCAPED"]
    assert path.read_bytes().startswith(before + b"\n")
    reopened = list(ledger.iter_rows(tmp_path))
    assert reopened[-1].terminal_state == TerminalState.ESCAPED
    assert row.fingerprint not in ledger.known_terminal_fingerprints(tmp_path)
    finding = _ordinary_finding(row.fingerprint)
    machine = _ordinary_machine(tmp_path, finding)
    machine._suppress_known_findings()
    assert finding.disposition == Disposition.CONFIRMED
    assert not any("ledger: suppressed" in note for note in machine._state.infra_errors)
    if corrupt_tail:
        with pytest.raises(ValueError):
            list(ledger.iter_rows(tmp_path, strict=True))
    else:
        assert len(list(ledger.iter_rows(tmp_path, strict=True))) == 2


@pytest.mark.parametrize(
    "corruption",
    [
        b'{"unfinished":',
        b" \t",
        b"\xff",
        b"broken\n",
        b"{}\n",
        b"\n",
        b"\r\n",
        b"\r",
    ],
)
def test_actual_strict_mutation_consumer_refuses_all_authority_after_corruption(tmp_path, corruption):
    from code_forge.disposition import Disposition
    from code_forge.mutation_findings import apply_mutation_ledger_dispositions

    finding, ruling, diff = _native_survivor_and_ruling(tmp_path)
    before = _record(ruling) + corruption
    path = _seed(tmp_path, before)
    ledger.append_row(tmp_path, ruling)
    notes = apply_mutation_ledger_dispositions(
        [finding],
        source_root=tmp_path,
        base_sha=ruling.base_sha,
        head_sha=ruling.head_sha,
        git_diff=diff,
        eligible_measurement=True,
    )
    assert finding.disposition == Disposition.CONFIRMED
    assert len(notes) == 1 and "ledger dispositions unavailable" in notes[0]
    assert path.read_bytes().startswith(before)


def test_actual_strict_mutation_consumer_accepts_valid_non_lf_control(tmp_path):
    from code_forge.disposition import Disposition
    from code_forge.mutation_findings import apply_mutation_ledger_dispositions

    finding, ruling, diff = _native_survivor_and_ruling(tmp_path)
    path = _seed(tmp_path, _record(ruling)[:-1])
    ledger.append_row(tmp_path, _row(tmp_path, "unrelated"))
    notes = apply_mutation_ledger_dispositions(
        [finding],
        source_root=tmp_path,
        base_sha=ruling.base_sha,
        head_sha=ruling.head_sha,
        git_diff=diff,
        eligible_measurement=True,
    )
    assert finding.disposition == Disposition.DISMISSED
    assert any("dismissed by latest FIXED ruling" in note for note in notes)
    assert path.read_bytes().startswith(_record(ruling))


@pytest.mark.parametrize("failure", ["invalid-utf8", "read-denied"])
def test_actual_ordinary_suppression_failed_lookup_keeps_finding(tmp_path, monkeypatch, failure):
    from code_forge.disposition import Disposition

    row = _row(tmp_path)
    before = _record(row) + (b"\xff" if failure == "invalid-utf8" else b"")
    path = _seed(tmp_path, before)
    finding = _ordinary_finding(row.fingerprint)
    machine = _ordinary_machine(tmp_path, finding)
    original_open = Path.open

    def denied(self, mode="r", *args, **kwargs):
        if self == path and "r" in mode:
            raise PermissionError(errno.EACCES, "ledger read denied")
        return original_open(self, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        if failure == "read-denied":
            patch.setattr(Path, "open", denied)
        machine._suppress_known_findings()
    assert finding.disposition == Disposition.CONFIRMED
    assert any("ledger read for suppression failed" in note for note in machine._state.infra_errors)
    assert not any("ledger: suppressed" in note for note in machine._state.infra_errors)
    assert path.read_bytes() == before


def test_actual_strict_mutation_consumer_unreadable_authority_keeps_survivor(tmp_path, monkeypatch):
    from code_forge.disposition import Disposition
    from code_forge.mutation_findings import apply_mutation_ledger_dispositions

    finding, ruling, diff = _native_survivor_and_ruling(tmp_path)
    path = _seed(tmp_path, _record(ruling))
    original_open = Path.open

    def denied(self, mode="r", *args, **kwargs):
        if self == path and "r" in mode:
            raise PermissionError(errno.EACCES, "ledger read denied")
        return original_open(self, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", denied)
        notes = apply_mutation_ledger_dispositions(
            [finding],
            source_root=tmp_path,
            base_sha=ruling.base_sha,
            head_sha=ruling.head_sha,
            git_diff=diff,
            eligible_measurement=True,
        )
    assert finding.disposition == Disposition.CONFIRMED
    assert len(notes) == 1 and "ledger dispositions unavailable" in notes[0]
    assert path.read_bytes() == _record(ruling)


class _OSProxy:
    """Override ledger's OS branch without corrupting pathlib/pytest globally."""

    def __init__(self, **changes):
        self.__dict__.update(changes)

    def __getattr__(self, name):
        return getattr(os, name)


def _noop_lock(*_args):
    """Accept a platform lock callback without touching its facilities."""
    return None


def _noop_backend(monkeypatch, callback=None, platform="posix"):
    if callback is None:
        callback = _noop_lock
    backend = (platform, callback, 101, 102)
    monkeypatch.setattr(ledger, "_ledger_lock_backend", lambda: backend)
    return backend


def _binary_open(path, flags, mode=0o777):
    """Keep mocked POSIX lock branches from accidentally using Windows text I/O."""
    return os.open(path, flags | getattr(os, "O_BINARY", 0), mode)


def _capture_descriptor(monkeypatch, *, close_error=None):
    opened = []
    closed = []
    real_open, real_close = os.open, os.close

    def open_once(path, flags, mode=0o777):
        assert _guard_is_locked()
        # Record the production flags before normalizing test-only fake backends.
        fd = real_open(path, flags | getattr(os, "O_BINARY", 0), mode)
        opened.append((fd, flags, mode))
        return fd

    def close_once(fd):
        assert _guard_is_locked()
        closed.append(fd)
        real_close(fd)
        if close_error is not None:
            raise close_error

    monkeypatch.setattr(ledger, "os", _OSProxy(open=open_once, close=close_once))
    return opened, closed


def test_one_read_write_append_descriptor_one_tail_byte_one_write(tmp_path, monkeypatch):
    before = b'{"unfinished":'
    path = _seed(tmp_path, before)
    row = _row(tmp_path)
    opened, closed = _capture_descriptor(monkeypatch)
    reads, writes = [], []
    real_read, real_write = os.read, os.write

    def read(fd, size):
        reads.append((fd, size, os.lseek(fd, 0, os.SEEK_CUR)))
        return real_read(fd, size)

    def write(fd, data):
        assert _guard_is_locked()
        writes.append((fd, data))
        return real_write(fd, data)

    monkeypatch.setattr(ledger.os, "read", read, raising=False)
    monkeypatch.setattr(ledger.os, "write", write, raising=False)
    ledger.append_row(tmp_path, row)
    assert len(opened) == 1
    fd, flags, mode = opened[0]
    assert flags & os.O_RDWR and flags & os.O_CREAT and flags & os.O_APPEND
    assert not flags & os.O_TRUNC
    assert mode == 0o666
    if os.name == "nt":
        assert flags & os.O_BINARY
    assert reads == [(fd, 1, len(before) - 1)]
    assert writes == [(fd, b"\n" + _record(row))]
    assert closed == [fd]
    assert not _guard_is_locked()
    assert path.read_bytes() == before + b"\n" + _record(row)


@pytest.mark.parametrize("bad_value", [object(), "invalid-terminal-state"])
def test_serialization_finishes_before_backend_or_creation(tmp_path, monkeypatch, bad_value):
    def forbidden():
        raise AssertionError("backend selected before serialization finished")

    monkeypatch.setattr(ledger, "_ledger_lock_backend", forbidden)
    row = (
        _row(tmp_path, axis_claim=bad_value)
        if not isinstance(bad_value, str)
        else _row(
            tmp_path,
            terminal_state=bad_value,
        )
    )
    with pytest.raises((TypeError, AttributeError)):
        ledger.append_row(tmp_path, row)
    assert not _path(tmp_path).parent.exists()


@pytest.mark.parametrize(
    "platform,module_name,missing",
    [
        ("posix", "fcntl", "module"),
        ("posix", "fcntl", "flock"),
        ("posix", "fcntl", "LOCK_NB"),
        ("nt", "msvcrt", "module"),
        ("nt", "msvcrt", "locking"),
        ("nt", "msvcrt", "LK_NBLCK"),
        ("other", "unused", "module"),
    ],
)
def test_missing_platform_facility_fails_before_creation(
    tmp_path, monkeypatch, platform, module_name, missing
):
    monkeypatch.setattr(ledger, "os", _OSProxy(name=platform, O_BINARY=0x8000))
    if missing == "module":
        monkeypatch.setitem(sys.modules, module_name, None)
    else:
        fake = SimpleNamespace(
            flock=lambda *args: None,
            LOCK_EX=1,
            LOCK_NB=2,
            LOCK_UN=4,
            locking=lambda *args: None,
            LK_NBLCK=10,
            LK_UNLCK=11,
        )
        delattr(fake, missing)
        monkeypatch.setitem(sys.modules, module_name, fake)
    with pytest.raises(OSError, match="ledger: locking unavailable"):
        ledger.append_row(tmp_path, _row(tmp_path))
    assert not _path(tmp_path).parent.exists()


@pytest.mark.parametrize(
    "platform,module_name,bad_attribute",
    [
        ("posix", "fcntl", "flock"),
        ("posix", "fcntl", "LOCK_EX"),
        ("posix", "fcntl", "LOCK_NB"),
        ("posix", "fcntl", "LOCK_UN"),
        ("nt", "msvcrt", "locking"),
        ("nt", "msvcrt", "LK_NBLCK"),
        ("nt", "msvcrt", "LK_UNLCK"),
        ("nt", "msvcrt", "O_BINARY"),
    ],
)
def test_invalid_platform_facility_is_rejected_before_mkdir(
    tmp_path, monkeypatch, platform, module_name, bad_attribute
):
    fake = SimpleNamespace(
        flock=_noop_lock,
        LOCK_EX=1,
        LOCK_NB=2,
        LOCK_UN=4,
        locking=_noop_lock,
        LK_NBLCK=10,
        LK_UNLCK=11,
    )
    binary_mode = True if bad_attribute == "O_BINARY" else 0x8000
    if bad_attribute != "O_BINARY":
        setattr(fake, bad_attribute, None if bad_attribute in {"flock", "locking"} else True)
    mkdir_calls = []

    def forbidden_mkdir(path, *args, **kwargs):
        mkdir_calls.append((path, args, kwargs))
        raise AssertionError("invalid lock facility reached filesystem creation")

    monkeypatch.setattr(ledger, "os", _OSProxy(name=platform, O_BINARY=binary_mode))
    monkeypatch.setitem(sys.modules, module_name, fake)
    monkeypatch.setattr(Path, "mkdir", forbidden_mkdir)
    with pytest.raises(BaseException) as raised:
        ledger.append_row(tmp_path, _row(tmp_path))
    assert type(raised.value) is OSError
    assert raised.value.args == ("ledger: locking unavailable",)
    assert mkdir_calls == [] and not _path(tmp_path).parent.exists()
    assert not _guard_is_locked()


@pytest.mark.parametrize("platform,module_name", [("posix", "fcntl"), ("nt", "msvcrt")])
def test_backend_uses_only_the_nonblocking_platform_facility(monkeypatch, platform, module_name):
    callback = _noop_lock
    fake = SimpleNamespace(
        flock=callback,
        LOCK_EX=2,
        LOCK_NB=4,
        LOCK_UN=8,
        locking=callback,
        LK_NBLCK=20,
        LK_UNLCK=21,
    )
    monkeypatch.setattr(ledger, "os", _OSProxy(name=platform, O_BINARY=0x8000))
    monkeypatch.setitem(sys.modules, module_name, fake)
    monkeypatch.setitem(sys.modules, "msvcrt" if platform == "posix" else "fcntl", None)
    expected = (platform, callback, 6, 8) if platform == "posix" else (platform, callback, 20, 21)
    assert ledger._ledger_lock_backend() == expected


@pytest.mark.integration
def test_import_succeeds_without_either_platform_lock_module(tmp_path):
    script = """
import importlib.abc
import subprocess
import sys
class DenyLockModules(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"fcntl", "msvcrt"}:
            raise ImportError("qualification denied lock module")
for name in ("fcntl", "msvcrt"):
    sys.modules.pop(name, None)
sys.meta_path.insert(0, DenyLockModules())
import code_forge.ledger as ledger
assert "fcntl" not in sys.modules and "msvcrt" not in sys.modules
try:
    ledger._ledger_lock_backend()
except OSError:
    pass
else:
    raise AssertionError("missing backend was accepted")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)


@pytest.mark.parametrize("operation", ["open", "stat", "seek", "read"])
def test_prewrite_access_failures_are_not_lock_contention(tmp_path, monkeypatch, operation):
    before = b"unchanged tail"
    path = _seed(tmp_path, before)
    error = PermissionError(errno.EACCES, "injected access denial")
    calls = []

    def fail(*args):
        calls.append(operation)
        raise error

    name = {"open": "open", "stat": "stat", "seek": "lseek", "read": "read"}[operation]
    monkeypatch.setattr(ledger, "os", _OSProxy(**{name: fail}))
    with pytest.raises(PermissionError) as raised:
        ledger.append_row(tmp_path, _row(tmp_path))
    assert raised.value is error
    assert calls == [operation]
    assert path.read_bytes() == before
    assert not _guard_is_locked()


def test_premature_tail_eof_fails_before_write_and_closes(tmp_path, monkeypatch):
    path = _seed(tmp_path, b"tail")
    opened, closed = _capture_descriptor(monkeypatch)
    monkeypatch.setattr(ledger.os, "read", lambda fd, size: b"", raising=False)
    with pytest.raises(OSError):
        ledger.append_row(tmp_path, _row(tmp_path))
    assert path.read_bytes() == b"tail"
    assert closed == [opened[0][0]]
    assert not _guard_is_locked()


def test_empty_file_does_not_read_or_write_a_placeholder(tmp_path, monkeypatch):
    path = _seed(tmp_path, b"")
    writes = []
    real_write = os.write

    def forbidden_read(fd, size):
        raise AssertionError("empty ledger must not be read")

    def write(fd, data):
        writes.append(data)
        return real_write(fd, data)

    monkeypatch.setattr(ledger, "os", _OSProxy(read=forbidden_read, write=write))
    row = _row(tmp_path)
    ledger.append_row(tmp_path, row)
    assert writes == [_record(row)] and path.read_bytes() == _record(row)


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner-mode permission qualification")
def test_real_write_only_permission_is_refused(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("elevated user bypasses owner read permission; qualify unprivileged")
    path = _seed(tmp_path, b"tail")
    old_mode = stat.S_IMODE(path.stat().st_mode)
    path.chmod(0o200)
    try:
        with pytest.raises(PermissionError):
            ledger.append_row(tmp_path, _row(tmp_path))
    finally:
        path.chmod(old_mode)
    assert path.read_bytes() == b"tail"
    assert not _guard_is_locked()


@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit, MemoryError])
def test_cancellation_during_os_acquisition_closes_without_unlock(tmp_path, monkeypatch, failure_type):
    path = _seed(tmp_path, b"tail")
    error = failure_type("cancel acquisition")
    modes = []

    def lock(fd, mode):
        modes.append(mode)
        raise error

    _noop_backend(monkeypatch, lock)
    opened, closed = _capture_descriptor(monkeypatch)
    with pytest.raises(failure_type) as raised:
        ledger.append_row(tmp_path, _row(tmp_path))
    assert raised.value is error
    assert modes == [101] and closed == [opened[0][0]]
    assert path.read_bytes() == b"tail" and not _guard_is_locked()


def test_filesystem_lock_unavailable_may_leave_only_empty_created_file(tmp_path, monkeypatch):
    error = OSError(errno.ENOSYS, "filesystem locking unavailable")
    calls = []

    def lock(fd, mode):
        calls.append(mode)
        raise error

    _noop_backend(monkeypatch, lock)
    opened, closed = _capture_descriptor(monkeypatch)
    with pytest.raises(OSError) as raised:
        ledger.append_row(tmp_path, _row(tmp_path))
    assert raised.value is error
    assert calls == [101] and closed == [opened[0][0]]
    assert _path(tmp_path).read_bytes() == b""
    assert not _guard_is_locked()


@pytest.mark.parametrize(
    "kind", ["descriptor-directory", "named-directory", "zero-inode", "mismatch", "missing"]
)
def test_identity_unavailability_refuses_before_write(tmp_path, monkeypatch, kind):
    path = _seed(tmp_path, b"tail")
    real_fstat, real_stat = os.fstat, os.stat

    def changed(value, **updates):
        fields = {name: getattr(value, name) for name in ("st_mode", "st_dev", "st_ino", "st_size")}
        fields.update(updates)
        return SimpleNamespace(**fields)

    def fstat(fd):
        value = real_fstat(fd)
        return changed(value, st_mode=stat.S_IFDIR) if kind == "descriptor-directory" else value

    def named(value):
        if kind == "missing":
            raise FileNotFoundError(errno.ENOENT, "injected missing path")
        result = real_stat(value)
        changes = {
            "named-directory": {"st_mode": stat.S_IFDIR},
            "zero-inode": {"st_ino": 0},
            "mismatch": {"st_ino": result.st_ino + 1},
        }
        return changed(result, **changes.get(kind, {}))

    monkeypatch.setattr(ledger, "os", _OSProxy(fstat=fstat, stat=named))
    with pytest.raises(OSError):
        ledger.append_row(tmp_path, _row(tmp_path))
    assert path.read_bytes() == b"tail"
    assert not _guard_is_locked()


@pytest.mark.skipif(os.name != "posix", reason="POSIX replacement/alias qualification")
@pytest.mark.parametrize("change", ["replace", "remove", "resize"])
def test_observed_change_after_tail_read_fails_without_own_write(tmp_path, monkeypatch, change):
    path = _seed(tmp_path, b"tail")
    original_separator = ledger._ledger_append_separator
    moved = path.with_name("retained-original")
    writes = []
    real_write = os.write

    def separator(fd, size):
        result = original_separator(fd, size)
        if change == "replace":
            path.rename(moved)
            path.write_bytes(b"replacement")
        elif change == "remove":
            path.unlink()
        else:
            real_write(fd, b"external")
        return result

    def write(fd, data):
        writes.append(data)
        return real_write(fd, data)

    monkeypatch.setattr(ledger, "_ledger_append_separator", separator)
    monkeypatch.setattr(ledger, "os", _OSProxy(write=write))
    with pytest.raises(OSError):
        ledger.append_row(tmp_path, _row(tmp_path))
    assert writes == []
    if change == "replace":
        assert moved.read_bytes() == b"tail" and path.read_bytes() == b"replacement"
    elif change == "remove":
        assert not path.exists()
    else:
        assert path.read_bytes() == b"tailexternal"


@pytest.mark.skipif(os.name != "posix", reason="POSIX live-name replacement qualification")
def test_postwrite_replacement_cannot_report_success(tmp_path, monkeypatch):
    before = b"tail"
    path = _seed(tmp_path, before)
    moved = path.with_name("retained-original")
    real_write = os.write
    row = _row(tmp_path)

    def write(fd, data):
        result = real_write(fd, data)
        path.rename(moved)
        path.write_bytes(b"replacement")
        return result

    monkeypatch.setattr(ledger, "os", _OSProxy(write=write))
    with pytest.raises(OSError):
        ledger.append_row(tmp_path, row)
    assert moved.read_bytes() == before + b"\n" + _record(row)
    assert path.read_bytes() == b"replacement"
    assert not _guard_is_locked()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink privilege-independent fixture")
def test_stable_symlink_alias_is_preserved(tmp_path):
    target = tmp_path / "real-ledger"
    target.write_bytes(b"tail")
    path = _path(tmp_path)
    path.parent.mkdir()
    path.symlink_to(target)
    row = _row(tmp_path)
    ledger.append_row(tmp_path, row)
    assert path.is_symlink()
    assert target.read_bytes() == b"tail\n" + _record(row)


@pytest.mark.parametrize("count", [0, 1, 7])
def test_short_write_is_one_attempt_and_retains_partial_bytes(tmp_path, monkeypatch, count):
    before = b"tail"
    path = _seed(tmp_path, before)
    row = _row(tmp_path)
    real_write = os.write
    calls = []

    def write(fd, data):
        calls.append(data)
        return real_write(fd, data[:count])

    monkeypatch.setattr(ledger, "os", _OSProxy(write=write))
    with pytest.raises(OSError):
        ledger.append_row(tmp_path, row)
    payload = b"\n" + _record(row)
    assert calls == [payload]
    assert path.read_bytes() == before + payload[:count]
    assert not _guard_is_locked()


@pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt, SystemExit, MemoryError])
def test_write_failure_or_cancellation_unlocks_closes_and_releases(tmp_path, monkeypatch, failure_type):
    path = _seed(tmp_path, b"tail")
    events = []
    error = failure_type("injected write failure")
    _noop_backend(monkeypatch, lambda fd, mode: events.append("unlock" if mode == 102 else "lock"))
    opened, closed = _capture_descriptor(monkeypatch)
    close = ledger.os.close

    def write(fd, data):
        events.append("write")
        raise error

    def tracked_close(fd):
        events.append("close")
        close(fd)

    monkeypatch.setattr(ledger.os, "write", write, raising=False)
    monkeypatch.setattr(ledger.os, "close", tracked_close)
    with pytest.raises(failure_type) as raised:
        ledger.append_row(tmp_path, _row(tmp_path))
    assert raised.value is error
    assert events == ["lock", "write", "unlock", "close"]
    assert closed == [opened[0][0]] and not _guard_is_locked()
    assert path.read_bytes() == b"tail"


@pytest.mark.parametrize("cleanup_site", ["unlock", "close", "both"])
@pytest.mark.parametrize("primary_type", [None, OSError, KeyboardInterrupt, MemoryError])
def test_cleanup_failure_prevents_success_and_preserves_original(
    tmp_path, monkeypatch, cleanup_site, primary_type
):
    before = b"tail"
    path = _seed(tmp_path, before)
    primary = primary_type("primary write failure") if primary_type is not None else None
    unlock_error = OSError(errno.EACCES, "unlock failure")
    close_error = OSError(errno.EIO, "close failure")
    unlocks = []

    def lock(fd, mode):
        if mode == 102:
            unlocks.append(fd)
            if cleanup_site in {"unlock", "both"}:
                raise unlock_error

    _noop_backend(monkeypatch, lock)
    opened, closed = _capture_descriptor(
        monkeypatch,
        close_error=close_error if cleanup_site in {"close", "both"} else None,
    )
    if primary is not None:

        def write(fd, data):
            raise primary

        monkeypatch.setattr(ledger.os, "write", write, raising=False)
    with pytest.raises(BaseException) as raised:
        ledger.append_row(tmp_path, _row(tmp_path))
    if primary is not None:
        assert raised.value is primary
    else:
        assert raised.value is (unlock_error if cleanup_site in {"unlock", "both"} else close_error)
    retained = raised.value._ledger_cleanup_errors
    assert retained[0] is (unlock_error if cleanup_site in {"unlock", "both"} else None)
    assert retained[1] is (close_error if cleanup_site in {"close", "both"} else None)
    rendered = "".join(traceback.format_exception(raised.value))
    if cleanup_site in {"unlock", "both"}:
        assert "ledger: OS unlock failed during cleanup" in rendered
    if cleanup_site in {"close", "both"}:
        assert "ledger: descriptor close failed during cleanup" in rendered
    assert closed == [opened[0][0]] and unlocks == [opened[0][0]]
    assert not _guard_is_locked()
    expected = before if primary is not None else before + b"\n" + _record(_row(tmp_path))
    assert path.read_bytes() == expected


@pytest.mark.parametrize("primary_type", [OSError, KeyboardInterrupt, MemoryError])
@pytest.mark.parametrize("suppress_context", [False, True])
def test_reused_cleanup_exception_preserves_primary_metadata(
    tmp_path, monkeypatch, primary_type, suppress_context
):
    _seed(tmp_path, b"tail")
    primary = primary_type("original")
    cause = ValueError("explicit cause")
    context = RuntimeError("original context")
    primary.__cause__ = cause
    primary.__context__ = context
    primary.__suppress_context__ = suppress_context
    reused = OSError(errno.EIO, "same cleanup object")

    def lock(fd, mode):
        if mode == 102:
            raise reused

    def write(fd, data):
        raise primary

    _noop_backend(monkeypatch, lock)
    opened, closed = _capture_descriptor(monkeypatch, close_error=reused)
    monkeypatch.setattr(ledger.os, "write", write, raising=False)
    with pytest.raises(primary_type) as raised:
        ledger.append_row(tmp_path, _row(tmp_path))
    assert raised.value is primary
    assert primary.__cause__ is cause
    assert primary.__context__ is context
    assert primary.__suppress_context__ is suppress_context
    assert primary._ledger_cleanup_errors == (reused, reused)
    assert closed == [opened[0][0]]
    rendered = "".join(traceback.format_exception(primary))
    assert "explicit cause" in rendered
    assert "ledger: OS unlock failed during cleanup" in rendered
    assert "ledger: descriptor close failed during cleanup" in rendered
    assert not _guard_is_locked()


def test_cleanup_diagnostics_never_format_arbitrary_exceptions(tmp_path, monkeypatch):
    _seed(tmp_path, b"tail")
    formatted = []

    class UnformattableError(OSError):
        def __str__(self):
            formatted.append("str")
            raise AssertionError("cleanup diagnostic called str")

        def __repr__(self):
            formatted.append("repr")
            raise AssertionError("cleanup diagnostic called repr")

    primary = MemoryError("original")
    reused = UnformattableError()

    def lock(fd, mode):
        if mode == 102:
            raise reused

    def write(fd, data):
        raise primary

    _noop_backend(monkeypatch, lock)
    opened, closed = _capture_descriptor(monkeypatch, close_error=reused)
    monkeypatch.setattr(ledger.os, "write", write, raising=False)
    with pytest.raises(MemoryError) as raised:
        ledger.append_row(tmp_path, _row(tmp_path))
    assert raised.value is primary
    assert formatted == []
    errors = primary._ledger_cleanup_errors
    assert errors[0] is reused and errors[1] is reused
    assert closed == [opened[0][0]]
    assert not _guard_is_locked()


@pytest.mark.parametrize("cleanup_site", ["unlock", "close"])
def test_note_lookup_failure_preserves_primary_and_cleanup(tmp_path, monkeypatch, cleanup_site):
    before = b"tail"
    path = _seed(tmp_path, before)
    lookups, modes = [], []
    diagnostic = RuntimeError("controlled notes lookup failure")

    class PrimaryFailure(BaseException):
        def __getattribute__(self, name):
            if name == "__notes__":
                lookups.append(name)
                raise diagnostic
            return super().__getattribute__(name)

    primary = PrimaryFailure("original write failure")
    cleanup_error = OSError(errno.EIO, "injected cleanup failure")

    def lock(fd, mode):
        modes.append(mode)
        if mode == 102 and cleanup_site == "unlock":
            raise cleanup_error

    def write(fd, data):
        raise primary

    _noop_backend(monkeypatch, lock)
    opened, closed = _capture_descriptor(
        monkeypatch,
        close_error=cleanup_error if cleanup_site == "close" else None,
    )
    monkeypatch.setattr(ledger.os, "write", write, raising=False)
    with pytest.raises(BaseException) as raised:
        ledger.append_row(tmp_path, _row(tmp_path))
    assert raised.value is primary
    assert lookups == ["__notes__"] and modes == [101, 102]
    assert closed == [opened[0][0]] and not _guard_is_locked()
    with pytest.raises(OSError) as descriptor_error:
        os.fstat(opened[0][0])
    assert descriptor_error.value.errno == errno.EBADF
    assert path.read_bytes() == before


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, amount):
        assert 0 < amount <= 0.01
        self.sleeps.append(amount)
        self.now += amount


class _Guard:
    def __init__(self, clock, delay=0.0, succeeds=True):
        self.clock, self.delay, self.succeeds = clock, delay, succeeds
        self.held = False
        self.timeouts = []
        self.releases = 0

    def acquire(self, *, timeout):
        self.timeouts.append(timeout)
        self.clock.now += self.delay
        self.held = self.succeeds
        return self.succeeds

    def locked(self):
        return self.held

    def _is_owned(self):
        return self.held

    def release(self):
        assert self.held
        self.releases += 1
        self.held = False


def _inject_guard(monkeypatch, guard):
    """Use an explicit single-thread model, never a native capability claim."""
    monkeypatch.setattr(ledger, "_LEDGER_APPEND_LOCK", guard)
    monkeypatch.setattr(
        ledger, "_ledger_guard_backend", lambda: (guard.acquire, guard._is_owned, guard.release)
    )


def test_default_cooperative_budget_constants():
    assert ledger._LEDGER_LOCK_TIMEOUT_SECONDS == 1.0
    assert ledger._LEDGER_LOCK_POLL_SECONDS == 0.01


def test_one_deadline_covers_guard_and_os_wait(tmp_path, monkeypatch):
    path = _seed(tmp_path, b"tail")
    clock = _Clock()
    guard = _Guard(clock, delay=0.75)
    monkeypatch.setattr(ledger, "time", clock)
    _inject_guard(monkeypatch, guard)
    acquired_at = []

    def contend(fd, mode):
        assert mode == 101
        acquired_at.append(clock.now)
        raise BlockingIOError(errno.EAGAIN, "held elsewhere")

    _noop_backend(monkeypatch, contend)
    opened, closed = _capture_descriptor(monkeypatch)
    with pytest.raises(TimeoutError):
        ledger.append_row(tmp_path, _row(tmp_path))
    assert guard.timeouts == [1.0]
    assert acquired_at and min(acquired_at) == 0.75 and max(acquired_at) < 1.0
    assert sum(clock.sleeps) == pytest.approx(0.25)
    assert clock.now == pytest.approx(1.0)
    assert closed == [opened[0][0]] and guard.releases == 1 and not guard.held
    assert path.read_bytes() == b"tail"


@pytest.mark.parametrize(
    "platform,error_number",
    [
        ("posix", errno.EAGAIN),
        ("posix", errno.EINTR),
        ("nt", errno.EACCES),
        ("nt", errno.EINTR),
    ],
)
def test_deadline_expiring_inside_contended_attempt_never_sleeps_or_writes(
    tmp_path, monkeypatch, platform, error_number
):
    before = b"tail"
    path = _seed(tmp_path, before)
    clock = _Clock()
    guard = _Guard(clock)
    attempts = []

    def contend(fd, mode, *length):
        attempts.append((mode, length))
        clock.now = 1.0
        raise OSError(error_number, "attempt exhausted shared deadline")

    def forbidden_sleep(amount):
        clock.sleeps.append(amount)
        raise AssertionError("slept after contended attempt exhausted shared deadline")

    monkeypatch.setattr(ledger, "time", clock)
    _inject_guard(monkeypatch, guard)
    monkeypatch.setattr(clock, "sleep", forbidden_sleep)
    _noop_backend(monkeypatch, contend, platform)
    opened, closed = _capture_descriptor(monkeypatch)
    monkeypatch.setattr(ledger.os, "O_BINARY", getattr(os, "O_BINARY", 0), raising=False)
    with pytest.raises(BaseException) as raised:
        ledger.append_row(tmp_path, _row(tmp_path))
    assert isinstance(raised.value, TimeoutError)
    assert raised.value.args == ("ledger: lock acquisition timed out",)
    assert attempts == [(101, (1,) if platform == "nt" else ())]
    assert clock.sleeps == [] and clock.now == 1.0
    assert guard.timeouts == [1.0] and guard.releases == 1 and not guard.held
    assert closed == [opened[0][0]] and path.read_bytes() == before
    with pytest.raises(OSError) as descriptor_error:
        os.fstat(opened[0][0])
    assert descriptor_error.value.errno == errno.EBADF


@pytest.mark.parametrize("succeeds", [False, True])
def test_guard_timeout_never_opens_a_descriptor(tmp_path, monkeypatch, succeeds):
    clock = _Clock()
    guard = _Guard(clock, delay=1.0, succeeds=succeeds)
    opened = []

    def forbidden(*args):
        opened.append(args)
        raise AssertionError("descriptor opened after guard deadline")

    monkeypatch.setattr(ledger, "time", clock)
    _inject_guard(monkeypatch, guard)
    monkeypatch.setattr(ledger, "os", _OSProxy(open=forbidden))
    with pytest.raises(TimeoutError):
        ledger.append_row(tmp_path, _row(tmp_path))
    assert opened == [] and not _path(tmp_path).exists()
    assert guard.releases == int(succeeds) and not guard.held


def test_lock_obtained_after_deadline_is_unlocked_without_write(tmp_path, monkeypatch):
    path = _seed(tmp_path, b"tail")
    clock = _Clock()
    events = []
    monkeypatch.setattr(ledger, "time", clock)

    def lock(fd, mode):
        events.append(mode)
        if mode == 101:
            clock.now = 1.1

    _noop_backend(monkeypatch, lock)
    opened, closed = _capture_descriptor(monkeypatch)
    with pytest.raises(TimeoutError):
        ledger.append_row(tmp_path, _row(tmp_path))
    assert events == [101, 102]
    assert closed == [opened[0][0]] and not _guard_is_locked()
    assert path.read_bytes() == b"tail"


@pytest.mark.parametrize(
    "platform,error_number,retries",
    [
        ("posix", errno.EAGAIN, True),
        ("posix", errno.EACCES, True),
        ("posix", errno.EINTR, True),
        ("posix", errno.EIO, False),
        ("posix", errno.ENOSYS, False),
        ("nt", errno.EACCES, True),
        ("nt", errno.EINTR, True),
        ("nt", errno.EAGAIN, False),
    ],
)
def test_only_recognized_lock_errors_retry(tmp_path, monkeypatch, platform, error_number, retries):
    path = _seed(tmp_path, b"")
    clock = _Clock()
    calls = []
    error = OSError(error_number, "injected lock failure")
    monkeypatch.setattr(ledger, "time", clock)
    monkeypatch.setattr(ledger, "os", _OSProxy(open=_binary_open, O_BINARY=getattr(os, "O_BINARY", 0)))

    def lock(fd, mode, *length):
        calls.append((mode, length))
        if len(calls) == 1:
            raise error

    _noop_backend(monkeypatch, lock, platform)
    if retries:
        ledger.append_row(tmp_path, _row(tmp_path))
        assert [mode for mode, length in calls] == [101, 101, 102]
        assert clock.sleeps == [0.01]
        assert path.read_bytes() == _record(_row(tmp_path))
    else:
        with pytest.raises(OSError) as raised:
            ledger.append_row(tmp_path, _row(tmp_path))
        assert raised.value is error
        assert len(calls) == 1 and clock.sleeps == [] and path.read_bytes() == b""
    assert not _guard_is_locked()


def test_windows_seek_and_one_byte_range_on_each_attempt_and_unlock(tmp_path, monkeypatch):
    path = _seed(tmp_path, b"")
    calls = []
    clock = _Clock()
    monkeypatch.setattr(ledger, "time", clock)
    monkeypatch.setattr(ledger, "os", _OSProxy(O_BINARY=getattr(os, "O_BINARY", 0)))

    def lock(fd, mode, length):
        calls.append((mode, length, os.lseek(fd, 0, os.SEEK_CUR)))
        if len(calls) == 1:
            raise PermissionError(errno.EACCES, "busy")

    with ledger._locked_ledger_writer(path, ("nt", lock, 101, 102)) as fd:
        assert os.fstat(fd).st_size == 0
        os.lseek(fd, 9, os.SEEK_SET)
    assert calls == [(101, 1, 0), (101, 1, 0), (102, 1, 0)]
    assert path.read_bytes() == b""


def test_windows_lock_position_seek_denial_is_never_retried(tmp_path, monkeypatch):
    path = _seed(tmp_path, b"")
    seeks, locks = [], []
    error = PermissionError(errno.EACCES, "seek denied")

    def denied(fd, offset, whence):
        seeks.append((offset, whence))
        raise error

    monkeypatch.setattr(ledger, "os", _OSProxy(lseek=denied, O_BINARY=getattr(os, "O_BINARY", 0)))
    backend = ("nt", lambda *args: locks.append(args), 101, 102)
    with pytest.raises(PermissionError) as raised, ledger._locked_ledger_writer(path, backend):
        pytest.fail("yielded without lock")
    assert raised.value is error
    assert len(seeks) == 1 and locks == [] and not _guard_is_locked()


@pytest.mark.integration
def test_contending_thread_cannot_open_before_guard(tmp_path, monkeypatch):
    _seed(tmp_path, b"")
    attempted, opened = threading.Event(), threading.Event()
    failures = queue.Queue()
    real_open = os.open

    def open_file(*args):
        opened.set()
        return real_open(*args)

    def contender():
        attempted.set()
        try:
            ledger.append_row(tmp_path, _row(tmp_path))
        except BaseException as error:  # noqa: BLE001 - propagate every worker failure to its parent assertion.
            failures.put(error)

    monkeypatch.setattr(ledger, "os", _OSProxy(open=open_file))
    thread = threading.Thread(target=contender, daemon=True)
    ledger._LEDGER_APPEND_LOCK.acquire()
    try:
        thread.start()
        assert attempted.wait(3)
        assert not opened.wait(0.05)
    finally:
        ledger._LEDGER_APPEND_LOCK.release()
        thread.join(5)
    assert not thread.is_alive()
    assert failures.empty()
    assert opened.is_set()
    assert len(list(ledger.iter_rows(tmp_path, strict=True))) == 1


@pytest.mark.integration
@pytest.mark.parametrize("first_failure", [None, "lock", "close"])
def test_waiter_cannot_open_until_prior_close_attempt_finishes(tmp_path, monkeypatch, first_failure):
    _seed(tmp_path, b"")
    closing, finish_close, second_started = threading.Event(), threading.Event(), threading.Event()
    second_opened = threading.Event()
    results = queue.Queue()
    real_open, real_close = os.open, os.close
    events = []
    lock_error = OSError(errno.EIO, "first lock failed")
    close_error = OSError(errno.EIO, "first close failed")

    def lock(fd, mode):
        if first_failure == "lock" and threading.current_thread().name == "ledger-first" and mode == 101:
            raise lock_error

    def open_file(*args):
        if threading.current_thread().name == "ledger-second":
            events.append("second-open")
            second_opened.set()
        path, flags, mode = args
        return real_open(path, flags | getattr(os, "O_BINARY", 0), mode)

    def close_file(fd):
        assert _guard_is_locked()
        if threading.current_thread().name == "ledger-first":
            closing.set()
            try:
                assert finish_close.wait(5)
            finally:
                real_close(fd)
                events.append("first-close-finished")
            if first_failure == "close":
                raise close_error
        else:
            real_close(fd)

    def writer(which):
        if which == "second":
            second_started.set()
        try:
            ledger.append_row(tmp_path, _row(tmp_path, which))
        except BaseException as error:  # noqa: BLE001 - propagate every worker failure to its parent assertion.
            results.put((which, error))
        else:
            results.put((which, None))

    _noop_backend(monkeypatch, lock)
    monkeypatch.setattr(ledger, "os", _OSProxy(open=open_file, close=close_file))
    first = threading.Thread(target=writer, args=("first",), name="ledger-first", daemon=True)
    second = threading.Thread(target=writer, args=("second",), name="ledger-second", daemon=True)
    first.start()
    try:
        assert closing.wait(3)
        second.start()
        assert second_started.wait(3)
        assert not second_opened.wait(0.05)
    finally:
        finish_close.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert events == ["first-close-finished", "second-open"]
    outcomes = dict(results.get_nowait() for _ in range(2))
    assert outcomes["second"] is None
    assert outcomes["first"] is (
        lock_error if first_failure == "lock" else close_error if first_failure == "close" else None
    )
    assert not _guard_is_locked()


@pytest.mark.integration
@pytest.mark.parametrize("before", [b"", b'{"unfinished":'])
def test_real_threads_preserve_prefix_and_each_complete_row(tmp_path, before):
    path = _seed(tmp_path, before)
    count = 4
    start = threading.Barrier(count)
    errors = queue.Queue()

    def writer(index):
        try:
            start.wait(timeout=5)
            for number in range(4):
                ledger.append_row(
                    tmp_path, _row(tmp_path, f"thread-{index}-{number}", axis_claim="\u754c" * 2000)
                )
        except BaseException as error:  # noqa: BLE001 - propagate every worker failure to its parent assertion.
            errors.put(error)

    threads = [threading.Thread(target=writer, args=(i,), daemon=True) for i in range(count)]
    try:
        for thread in threads:
            thread.start()
    except BaseException:  # noqa: BLE001 - unblock and join workers after partial startup
        start.abort()
        raise
    finally:
        for thread in threads:
            if thread.ident is not None:
                thread.join(15)
    assert all(not thread.is_alive() for thread in threads)
    assert errors.empty()
    raw = path.read_bytes()
    prefix = before + (b"\n" if before else b"")
    assert raw.startswith(prefix)
    rows = raw[len(prefix) :].splitlines()
    assert len(rows) == count * 4 and all(rows)
    assert {json.loads(row)["fingerprint"] for row in rows} == {
        f"thread-{index}-{number}" for index in range(count) for number in range(4)
    }


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, MemoryError])
@pytest.mark.parametrize("delivery", ["native-return", "after-return-line"])
def test_guard_cancellation_after_native_acquire_releases_and_allows_next_append(
    tmp_path, monkeypatch, error_type, delivery
):
    guard = ledger._LEDGER_APPEND_LOCK
    previous_trace, previous_profile = sys.gettrace(), sys.getprofile()
    error = error_type("cancel acquisition boundary")
    function = ledger._locked_ledger_writer.__wrapped__
    lines, first = inspect.getsourcelines(function)
    target = first + next(
        index for index, line in enumerate(lines) if line.strip() == "if time.monotonic() >= deadline:"
    )
    fired = []
    opened, closed = _capture_descriptor(monkeypatch)

    def observe(frame, event, arg):
        same = frame.f_code is function.__code__
        at_return = (
            event == "c_return"
            and getattr(arg, "__self__", None) is guard
            and getattr(arg, "__name__", None) == "acquire"
        )
        at_line = event == "line" and frame.f_lineno == target
        if same and not fired and (at_return if delivery == "native-return" else at_line):
            fired.append(guard._is_owned())
            raise error
        return observe

    try:
        try:
            with pytest.raises(BaseException) as raised:
                if delivery == "native-return":
                    sys.setprofile(observe)
                else:
                    sys.settrace(observe)
                ledger.append_row(tmp_path, _row(tmp_path, "cancelled"))
        finally:
            if delivery == "native-return":
                sys.setprofile(previous_profile)
            else:
                sys.settrace(previous_trace)
        assert sys.gettrace() is previous_trace and sys.getprofile() is previous_profile
        assert raised.value is error and fired == [True]
        assert not guard._is_owned() and opened == closed == []
        assert not _path(tmp_path).exists()
        following = _row(tmp_path, "following")
        ledger.append_row(tmp_path, following)
        assert _path(tmp_path).read_bytes() == _record(following)
        assert closed == [opened[0][0]] and not guard._is_owned()
    finally:
        if delivery == "native-return":
            sys.setprofile(previous_profile)
        else:
            sys.settrace(previous_trace)
        if guard._is_owned():
            guard.release()


def test_same_thread_guard_reentry_preserves_outer_ownership(tmp_path):
    guard = ledger._LEDGER_APPEND_LOCK
    guard.acquire()
    try:
        with pytest.raises(BaseException) as raised:
            ledger.append_row(tmp_path, _row(tmp_path))
        assert isinstance(raised.value, TimeoutError)
        assert guard._is_owned() and not _path(tmp_path).exists()
    finally:
        guard.release()
    assert not _guard_is_locked()
    ledger.append_row(tmp_path, _row(tmp_path))
    assert list(ledger.iter_rows(tmp_path))[0].fingerprint == "fp-boundary"


@pytest.mark.parametrize(
    "invalid",
    ["primitive", "subclass", "missing-owner", "python-methods", "foreign-methods", "other-runtime"],
)
def test_unqualified_guard_capabilities_fail_before_mkdir(tmp_path, monkeypatch, invalid):
    class FakeGuard:
        def acquire(self, *, timeout):
            return True

        def _is_owned(self):
            return False

        def release(self):
            return None

    class NativeSubclass(type(ledger._LEDGER_APPEND_LOCK)):
        pass

    if invalid == "primitive":
        guard = threading.Lock()
    elif invalid == "subclass":
        guard = NativeSubclass()
    elif invalid == "missing-owner":
        guard = SimpleNamespace(acquire=_noop_lock, release=_noop_lock)
    elif invalid == "foreign-methods":
        other = threading.RLock()
        guard = SimpleNamespace(acquire=other.acquire, _is_owned=other._is_owned, release=other.release)
    else:
        guard = FakeGuard() if invalid == "python-methods" else ledger._LEDGER_APPEND_LOCK
    monkeypatch.setattr(ledger, "_LEDGER_APPEND_LOCK", guard)
    if invalid in {"missing-owner", "python-methods", "foreign-methods"}:
        monkeypatch.setattr(ledger, "_thread", SimpleNamespace(RLock=type(guard)))
    if invalid == "other-runtime":
        monkeypatch.setattr(ledger, "sys", SimpleNamespace(implementation=SimpleNamespace(name="other")))
    created = []

    def forbidden_mkdir(path, *args, **kwargs):
        created.append(path)
        raise AssertionError("unsupported process guard reached mkdir")

    monkeypatch.setattr(Path, "mkdir", forbidden_mkdir)
    with pytest.raises(BaseException) as raised:
        ledger.append_row(tmp_path, _row(tmp_path))
    assert type(raised.value) is OSError
    assert raised.value.args == ("ledger: native process locking unavailable",)
    assert created == [] and not _path(tmp_path).parent.exists()


@pytest.mark.integration
@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, MemoryError])
@pytest.mark.parametrize("delivery", ["before-call", "after-timeout"])
def test_interrupted_nonowner_never_releases_foreign_guard(tmp_path, monkeypatch, error_type, delivery):
    guard = ledger._LEDGER_APPEND_LOCK
    previous_trace, previous_profile = sys.gettrace(), sys.getprofile()
    ready, release, still_owned = threading.Event(), threading.Event(), []
    error = error_type("cancel foreign-owned acquisition")
    fired = []

    def hold():
        with guard:
            ready.set()
            assert release.wait(5)
            still_owned.append(guard._is_owned())

    def observe(frame, event, arg):
        if (
            frame.f_code.co_name == "_locked_ledger_writer"
            and event == ("c_call" if delivery == "before-call" else "c_return")
            and getattr(arg, "__self__", None) is guard
            and getattr(arg, "__name__", None) == "acquire"
            and not fired
        ):
            fired.append(guard._is_owned())
            raise error

    thread = threading.Thread(target=hold, daemon=True)
    monkeypatch.setattr(ledger, "_LEDGER_LOCK_TIMEOUT_SECONDS", 0.05)
    try:
        thread.start()
        assert ready.wait(3)
        try:
            with pytest.raises(BaseException) as raised:
                sys.setprofile(observe)
                ledger.append_row(tmp_path, _row(tmp_path))
        finally:
            sys.setprofile(previous_profile)
        assert sys.gettrace() is previous_trace and sys.getprofile() is previous_profile
        assert raised.value is error and fired == [False]
        assert _guard_is_locked() and not _path(tmp_path).exists()
    finally:
        sys.setprofile(previous_profile)
        release.set()
        if thread.ident is not None:
            thread.join(5)
    assert not thread.is_alive() and still_owned == [True] and not _guard_is_locked()


@pytest.mark.integration
def test_foreign_guard_timeout_preserves_owner_and_following_append(tmp_path):
    guard = ledger._LEDGER_APPEND_LOCK
    ready, release, still_owned = threading.Event(), threading.Event(), []

    def hold():
        with guard:
            ready.set()
            assert release.wait(5)
            still_owned.append(guard._is_owned())

    thread = threading.Thread(target=hold, daemon=True)
    try:
        thread.start()
        assert ready.wait(3)
        with pytest.raises(BaseException) as raised:
            ledger.append_row(tmp_path, _row(tmp_path))
        assert isinstance(raised.value, TimeoutError)
        assert _guard_is_locked() and not _path(tmp_path).exists()
    finally:
        release.set()
        if thread.ident is not None:
            thread.join(5)
    assert not thread.is_alive() and still_owned == [True] and not _guard_is_locked()
    ledger.append_row(tmp_path, _row(tmp_path))
    assert list(ledger.iter_rows(tmp_path))[0].fingerprint == "fp-boundary"


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, MemoryError])
@pytest.mark.parametrize("cleanup_site", ["unlock", "close"])
@pytest.mark.parametrize("has_primary", [False, True])
def test_cleanup_operation_cancellation_preserves_primary_and_releases(
    tmp_path, monkeypatch, error_type, cleanup_site, has_primary
):
    before = b"tail"
    path = _seed(tmp_path, before)
    cancellation = error_type("cancel cleanup operation")
    primary = ValueError("original write failure") if has_primary else None
    modes = []

    def lock(fd, mode):
        modes.append(mode)
        if mode == 102 and cleanup_site == "unlock":
            raise cancellation

    def write(fd, data):
        raise primary

    _noop_backend(monkeypatch, lock)
    opened, closed = _capture_descriptor(
        monkeypatch, close_error=cancellation if cleanup_site == "close" else None
    )
    if primary is not None:
        monkeypatch.setattr(ledger.os, "write", write, raising=False)
    with pytest.raises(BaseException) as raised:
        ledger.append_row(tmp_path, _row(tmp_path))
    assert raised.value is (primary if primary is not None else cancellation)
    assert modes == [101, 102] and closed == [opened[0][0]] and not _guard_is_locked()
    with pytest.raises(OSError) as descriptor_error:
        os.fstat(opened[0][0])
    assert descriptor_error.value.errno == errno.EBADF
    assert path.read_bytes() == (before if has_primary else before + b"\n" + _record(_row(tmp_path)))
    if primary is not None:
        assert primary.__cause__ is None and primary.__context__ is None


def test_guard_methods_remain_bound_through_close_if_global_replaced(tmp_path, monkeypatch):
    original, replacement = ledger._LEDGER_APPEND_LOCK, threading.RLock()
    path = _seed(tmp_path, b"")
    closed, failures = [], []
    real_write, real_close = os.write, os.close

    def write(fd, data):
        monkeypatch.setattr(ledger, "_LEDGER_APPEND_LOCK", replacement)
        return real_write(fd, data)

    def close(fd):
        assert original._is_owned()
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(ledger, "os", _OSProxy(write=write, close=close))
    try:
        try:
            ledger.append_row(tmp_path, _row(tmp_path))
        except BaseException as error:  # noqa: BLE001 - assert exact propagation after cleanup
            failures.append(error)
        assert failures == []
        assert len(closed) == 1 and not original._is_owned() and not replacement._is_owned()
        assert path.read_bytes() == _record(_row(tmp_path))
    finally:
        if original._is_owned():
            original.release()


@pytest.mark.integration
def test_partial_thread_start_aborts_barrier_and_joins_started_workers(tmp_path, monkeypatch):
    real_start, real_join, real_barrier = (
        threading.Thread.start,
        threading.Thread.join,
        threading.Barrier,
    )
    started, joined, barriers, aborts = [], [], [], []
    error = RuntimeError("controlled third worker start failure")

    def barrier(count):
        value = real_barrier(count)
        barriers.append(value)
        real_abort = value.abort

        def abort():
            aborts.append(True)
            real_abort()

        value.abort = abort
        return value

    def start(thread):
        if len(started) == 2:
            raise error
        real_start(thread)
        started.append(thread)

    def join(thread, timeout=None):
        joined.append(thread)
        return real_join(thread, timeout)

    monkeypatch.setattr(threading, "Barrier", barrier)
    monkeypatch.setattr(threading.Thread, "start", start)
    monkeypatch.setattr(threading.Thread, "join", join)
    try:
        with pytest.raises(RuntimeError) as raised:
            test_real_threads_preserve_prefix_and_each_complete_row(tmp_path, b"tail")
        assert raised.value is error
        assert len(started) == 2 and joined == started and aborts == [True]
        assert all(not thread.is_alive() for thread in started)
        assert _path(tmp_path).read_bytes() == b"tail"
    finally:
        for value in barriers:
            value.abort()
        for thread in started:
            real_join(thread, 6)


@pytest.mark.integration
@pytest.mark.parametrize(
    "target",
    ["guard-native-return", "guard-after-return-line", "foreign-before-call", "foreign-after-timeout"],
)
@pytest.mark.parametrize("force_failure", [False, True])
def test_cancellation_tests_restore_caller_hooks_on_success_and_failure(
    tmp_path, monkeypatch, target, force_failure
):
    caller_trace, caller_profile = sys.gettrace(), sys.getprofile()

    def sentinel_trace(frame, event, arg):
        return sentinel_trace

    def sentinel_profile(frame, event, arg):
        return None

    if force_failure:

        def broken_row(*args, **kwargs):
            raise ValueError("controlled observer-test failure")

        monkeypatch.setattr(sys.modules[__name__], "_row", broken_row)
    try:
        sys.settrace(sentinel_trace)
        sys.setprofile(sentinel_profile)
        failures = []
        try:
            if target.startswith("guard-"):
                test_guard_cancellation_after_native_acquire_releases_and_allows_next_append(
                    tmp_path,
                    monkeypatch,
                    KeyboardInterrupt,
                    target.removeprefix("guard-"),
                )
            else:
                test_interrupted_nonowner_never_releases_foreign_guard(
                    tmp_path,
                    monkeypatch,
                    KeyboardInterrupt,
                    target.removeprefix("foreign-"),
                )
        except AssertionError as error:
            failures.append(error)
        assert bool(failures) is force_failure
        assert sys.gettrace() is sentinel_trace and sys.getprofile() is sentinel_profile
    finally:
        sys.settrace(caller_trace)
        sys.setprofile(caller_profile)


def _process_append(root, index, ready, start, results):
    """Spawn target; product imports happen only during admitted runtime tests."""
    try:
        ready.set()
        if not start.wait(10):
            raise TimeoutError("process start barrier")
        root = Path(root)
        for number in range(4):
            ledger.append_row(root, _row(root, f"process-{index}-{number}", axis_claim="\u754c" * 2000))
    except BaseException as error:  # noqa: BLE001 - propagate every worker failure to its parent assertion.
        results.put((index, type(error).__name__, str(error)))
    else:
        results.put((index, None, None))


def _process_hold_lock(root, ready, release, results):
    try:
        with ledger._locked_ledger_writer(_path(Path(root)), ledger._ledger_lock_backend()):
            ready.set()
            if not release.wait(15):
                raise TimeoutError("lock-holder release barrier")
    except BaseException as error:  # noqa: BLE001 - propagate every worker failure to its parent assertion.
        results.put(("holder", type(error).__name__, str(error)))
    else:
        results.put(("holder", None, None))


def _stop_processes(processes):
    for process in processes:
        if process.is_alive():
            process.terminate()
        process.join(5)


@pytest.mark.integration
@pytest.mark.parametrize("before", [b"", b'{"unfinished":'])
def test_real_spawn_processes_preserve_prefix_and_each_complete_row(tmp_path, before):
    path = _seed(tmp_path, before)
    ctx = multiprocessing.get_context("spawn")
    start, results = ctx.Event(), ctx.Queue()
    ready = [ctx.Event() for _ in range(4)]
    processes = [
        ctx.Process(target=_process_append, args=(str(tmp_path), i, ready[i], start, results))
        for i in range(4)
    ]
    try:
        for process in processes:
            process.start()
        assert all(event.wait(10) for event in ready)
        start.set()
        for process in processes:
            process.join(20)
        assert all(not process.is_alive() and process.exitcode == 0 for process in processes)
        reports = [results.get(timeout=3) for _ in processes]
        assert all(error is None for index, error, detail in reports), reports
    finally:
        start.set()
        _stop_processes([process for process in processes if process.pid is not None])
        results.close()
        results.join_thread()
    raw = path.read_bytes()
    prefix = before + (b"\n" if before else b"")
    assert raw.startswith(prefix)
    rows = raw[len(prefix) :].splitlines()
    assert len(rows) == 16 and all(rows)
    assert {json.loads(row)["fingerprint"] for row in rows} == {
        f"process-{index}-{number}" for index in range(4) for number in range(4)
    }


@pytest.mark.integration
@pytest.mark.parametrize("before", [b"", b'{"unfinished":'])
def test_real_process_lock_times_out_then_append_succeeds_after_release(tmp_path, monkeypatch, before):
    path = _seed(tmp_path, before)
    ctx = multiprocessing.get_context("spawn")
    ready, release, results = ctx.Event(), ctx.Event(), ctx.Queue()
    holder = ctx.Process(target=_process_hold_lock, args=(str(tmp_path), ready, release, results))
    opened, closed = _capture_descriptor(monkeypatch)
    monkeypatch.setattr(ledger, "_LEDGER_LOCK_TIMEOUT_SECONDS", 0.15)
    holder.start()
    try:
        try:
            assert ready.wait(10)
            with pytest.raises(TimeoutError):
                ledger.append_row(tmp_path, _row(tmp_path, "timeout-must-not-exist"))
            assert closed == [opened[0][0]] and not _guard_is_locked()
        finally:
            release.set()
            holder.join(10)
            _stop_processes([holder])
        assert holder.exitcode == 0
        assert results.get(timeout=3) == ("holder", None, None)
        assert path.read_bytes() == before
        ledger.append_row(tmp_path, _row(tmp_path, "after-release"))
        expected = before + (b"\n" if before else b"") + _record(_row(tmp_path, "after-release"))
        assert path.read_bytes() == expected
        assert len(closed) == len(opened) == 2
    finally:
        results.close()
        results.join_thread()


@pytest.mark.integration
@pytest.mark.skipif(os.name != "nt", reason="must observe real Windows byte-lock reader denial")
def test_real_windows_first_byte_lock_temporarily_denies_ordinary_reader(tmp_path):
    row = _row(tmp_path)
    path = _seed(tmp_path, _record(row))
    ctx = multiprocessing.get_context("spawn")
    ready, release, results = ctx.Event(), ctx.Event(), ctx.Queue()
    holder = ctx.Process(target=_process_hold_lock, args=(str(tmp_path), ready, release, results))
    holder.start()
    try:
        try:
            assert ready.wait(10)
            with pytest.raises(OSError):
                list(ledger.iter_rows(tmp_path))
        finally:
            release.set()
            holder.join(10)
            _stop_processes([holder])
        assert holder.exitcode == 0
        assert results.get(timeout=3) == ("holder", None, None)
        assert list(ledger.iter_rows(tmp_path)) == [row]
        assert path.read_bytes() == _record(row)
    finally:
        results.close()
        results.join_thread()
