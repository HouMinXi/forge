# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Record which test files reached the call phase, for one named execution.

Load with ``-p code_forge.e2e_record``. Two environment variables drive it:

``FORGE_E2E_RECORD``
    Where to write the record. The reader refuses a record inside the reviewed
    tree, so this path belongs outside it.
``FORGE_E2E_EXECUTION_ID``
    The identifier the caller allocated for this run. The reader is told the
    same value and refuses any other, so an older run of the same commit cannot
    stand in for this one.

Without both, the plugin does nothing. A run that is interrupted leaves no
record, so an unfinished run cannot be mistaken for evidence.

A file counts only when a test in it produced a report for the call phase with
an outcome of passed or failed. pytest emits no call report for a test that was
skipped before or during setup, so those never reach the record.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import warnings
import pytest

from datetime import datetime
from pathlib import Path

SCHEMA = "forge-e2e-execution-v1"

# Exit statuses that mean the session ran to the end. pytest reports 0 for all
# passed and 1 for failures; 2 is an interrupt, 3 an internal error, 4 a usage
# error, 5 no tests collected. None of those can say what executed.
_FINISHED_STATUSES = (0, 1)

_CALL_OUTCOMES = ("passed", "failed")


def _git(root: Path, *args: str) -> str | None:
    """Stripped stdout of one git command, or None when it cannot be read."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            # encoding puts the pipe in text mode on its own, and check
            # already defaults to False, so neither is repeated here.
            # A different encoding only changes a byte under a non-UTF-8
            # locale, which the suite does not build.
            encoding="utf-8",  # pragma: no mutate
            errors="replace",
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _as_posix_relative(root: Path, path: str) -> str | None:
    """``path`` as a repo-relative POSIX path, or None when it escapes root."""
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(root)
        except ValueError:
            return None
    text = candidate.as_posix()
    parts = Path(text).parts
    # An empty path becomes "." once it goes through Path, which has no parts,
    # so both cases land on the same guard. Path also normalises a "." segment
    # away, which leaves ".." as the only part that still has to be refused.
    # A path that is still absolute here cannot happen: relative_to either made
    # it relative or refused it.
    if not text or not parts or ".." in parts:
        return None
    return text


class Recorder:
    """Collects call-phase files for one run and writes them at the end."""

    def __init__(self, out_path: Path, execution_id: str, root: Path) -> None:
        self.out_path = out_path
        self.execution_id = execution_id
        self.root = root
        self.commit = _git(root, "rev-parse", "HEAD")
        # Tracked changes only, the same rule the reader applies. Leftover
        # artifacts from an earlier run would otherwise mark every later run
        # dirty and make the record unusable in the tree it was written for.
        status = _git(root, "status", "--porcelain", "--untracked-files=no")
        self.dirty = None if status is None else bool(status)
        self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.finished_at: str | None = None
        self.status: int | None = None
        self.files: set[str] = set()
        # Why write() declined, so the message can say it.
        self.refusal = ""

    def note(self, report: object) -> None:
        """Add the test's file when this report is its call phase result.

        A subtest reports ``when="call"`` too: pytest-subtests wraps an ordinary
        report into a ``SubtestReport`` before the hook sees it, so the concrete
        type is what separates a test that ran from a subtest inside a fixture.
        Exact type, not ``isinstance``: the subclass is the one to exclude.
        """
        if type(report) is not pytest.TestReport:  # noqa: E721 - see the docstring
            return
        if report.when != "call" or report.outcome not in _CALL_OUTCOMES:
            return
        # A report always carries its file; an empty one names nothing.
        relative = _as_posix_relative(self.root, str(report.fspath))
        if relative is not None:
            self.files.add(relative)

    def snapshot_drift(self) -> str | None:
        """None when the tree still says what it said at start, else the reason."""
        commit_now = _git(self.root, "rev-parse", "HEAD")
        status_now = _git(self.root, "status", "--porcelain", "--untracked-files=no")
        if commit_now is None or status_now is None:
            # Swallowing this into the drift message would tell the caller the
            # tree moved when all that happened is that git could not answer.
            return "the snapshot could not be read again"
        if commit_now != self.commit or (status_now == "") is not (self.dirty is False):
            return "the tree no longer matches %s" % self.commit
        return None

    def matches_its_snapshot(self) -> bool:
        """Whether HEAD and the tracked tree still say what they said at start."""
        return self.snapshot_drift() is None

    def payload(self) -> dict:
        return {
            "schema": SCHEMA,
            "execution_id": self.execution_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "complete": True,
            "session_exitstatus": self.status,
            "snapshot": {"commit": self.commit, "dirty": self.dirty},
            "executed_files": sorted(self.files),
        }

    def write(self) -> bool:
        """Write the record unless this run cannot say what it tested."""
        if self.status not in _FINISHED_STATUSES:
            self.refusal = "the run had not finished"
            return False
        if self.commit is None or self.dirty is None:
            self.refusal = "the snapshot could not be read"
            return False
        # The snapshot was read when the session started. A test that edits a
        # tracked file leaves the tree in a state this record would misdescribe,
        # so read it again before signing anything.
        drift = self.snapshot_drift()
        if drift is not None:
            self.refusal = drift
            return False
        self.finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
        body = json.dumps(self.payload(), indent=2, sort_keys=True) + "\n"
        try:
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            handle, temp = tempfile.mkstemp(
                prefix=self.out_path.name + ".",
                suffix=".tmp",
                dir=str(self.out_path.parent),
            )
        except (OSError, ValueError):
            self.refusal = "the record could not be written"
            return False
        try:
            # Same locale argument as _git: the payload is ASCII apart from the
            # paths recorded, and those come back as UTF-8 either way.
            with os.fdopen(handle, "w", encoding="utf-8") as stream:  # pragma: no mutate
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.out_path)
        except (OSError, ValueError):
            # Anything that failed before os.replace leaves the temporary file
            # beside the record; a half-written one there is worse than none.
            try:
                os.unlink(temp)
            except OSError:
                pass
            self.refusal = "the record could not be written"
            return False
        return True


class _Session:
    """What this process is recording, if anything.

    Object form, as the mutation plugin already uses, so the hook wrappers stay
    module-level and no hook needs a global statement.
    """

    def __init__(self) -> None:
        self.recorder: Recorder | None = None

    def pytest_sessionstart(self, session) -> None:  # noqa: ANN001 - pytest hook
        """Open a recorder when the caller named both the path and the run."""
        raw_path = os.environ.get("FORGE_E2E_RECORD", "").strip()
        execution_id = os.environ.get("FORGE_E2E_EXECUTION_ID", "").strip()
        if not raw_path or not execution_id:
            self.recorder = None
            return
        # The fallback is looked up only when it is needed: evaluating it as the
        # getattr default reads rootdir even when rootpath is there, and a
        # config object without rootdir would raise before the fallback matters.
        # A config carrying neither is not something to crash the session on, so
        # the run falls back to the directory it was started from.
        # Only a missing attribute falls through; a value that happens to be
        # falsy is still the caller's answer.
        rootpath = getattr(session.config, "rootpath", None)
        rootdir = getattr(session.config, "rootdir", None)
        if rootpath is not None:
            root = Path(rootpath)
        elif rootdir is not None:
            root = Path(rootdir)
        else:
            root = Path.cwd()
        self.recorder = Recorder(Path(raw_path), execution_id, root)

    def pytest_runtest_logreport(self, report) -> None:  # noqa: ANN001 - pytest hook
        if self.recorder is not None:
            self.recorder.note(report)

    def pytest_sessionfinish(self, session, exitstatus) -> None:  # noqa: ANN001
        if self.recorder is None:
            return
        self.recorder.status = exitstatus if isinstance(exitstatus, int) else None
        if not self.recorder.write():
            # The run was asked to leave evidence and did not: say so, instead
            # of letting the caller find an absent record later.
            # A diagnostic must not change what the run reports. Under
            # `-W error` the call below would be raised instead of printed, so
            # the filter is set for this one message and restored afterwards.
            with warnings.catch_warnings():
                # No category argument on either call: the default is the
                # UserWarning this message already is, and naming it adds a
                # decision with no effect.
                warnings.simplefilter("always")
                warnings.warn(
                    "the execution record was asked for but not written: %s (%s)"
                    % (self.recorder.out_path, self.recorder.refusal),
                    stacklevel=2,  # pragma: no mutate - attribution only
                )


_HOOKS = _Session()


def pytest_sessionstart(session) -> None:  # noqa: ANN001 - pytest hook signature
    _HOOKS.pytest_sessionstart(session)


def pytest_runtest_logreport(report) -> None:  # noqa: ANN001 - pytest hook signature
    _HOOKS.pytest_runtest_logreport(report)


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ANN001 - hook signature
    # The delegate reads only exitstatus; the session object is there for the
    # hook signature.
    _HOOKS.pytest_sessionfinish(session, exitstatus)  # pragma: no mutate
