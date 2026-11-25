# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Execution evidence: the record a test run writes and the reader's judgement.

The four contrasts that define acceptance (same run, older run of the same
commit, skipped or setup-failed only, unbound review) each run a real pytest
subprocess against a real repository and then call the real reader. Nothing
here mocks the run, because the property under test is what a run writes.
"""

from __future__ import annotations

import contextvars
import json
import os
import subprocess
import warnings
import sys
import threading
from pathlib import Path

import pytest

from types import SimpleNamespace

from _pytest.subtests import SubtestReport as _SubtestReport

from code_forge import e2e_evidence, e2e_record

from code_forge.e2e_evidence import (
    MAX_RECORD_BYTES,
    _git as _evidence_git,
    _is_commit,
    _same,
    _valid_paths,
    verify,
)
from code_forge.e2e_record import SCHEMA, Recorder, _as_posix_relative, _Session

SRC = str(Path(__file__).resolve().parent.parent / "src")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}

_PASSING = """def test_ok():
    assert True
"""

_SKIP_AND_SETUP_FAILURE = """import pytest


@pytest.mark.skip(reason="not today")
def test_skipped():
    assert True


@pytest.fixture
def boom():
    raise RuntimeError("setup exploded")


def test_setup_failure(boom):
    assert True
"""

_SKIP_IN_BODY = """import pytest


def test_skips_midway():
    pytest.skip("decided halfway")
"""


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **_GIT_ENV},
    )
    if result.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (" ".join(args), root, result.stderr.strip()))
    return result.stdout.strip()


def _make_repo(root: Path, files: dict[str, str]) -> str:
    root.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "fixture")
    return _git(root, "rev-parse", "HEAD")


def _run_pytest(
    repo: Path,
    record: Path | None,
    execution_id: str | None,
    *,
    target: str = "tests",
    extra_args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": SRC}
    env.pop("FORGE_E2E_RECORD", None)
    env.pop("FORGE_E2E_EXECUTION_ID", None)
    # A runner that mutates the source sets its own control variables; passing
    # them on makes the inner pytest think it is the mutant being measured.
    for name in ("MUTANT_UNDER_TEST", "MUTMUT_DEBUG", "MUTANT_NAME"):
        env.pop(name, None)
    if record is not None:
        env["FORGE_E2E_RECORD"] = str(record)
    if execution_id is not None:
        env["FORGE_E2E_EXECUTION_ID"] = execution_id
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            target,
            "-q",
            "-p",
            "code_forge.e2e_record",
            *extra_args,
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=120,
    )


def _report(
    path: str = "tests/test_ok.py",
    *,
    when: str = "call",
    outcome: str = "passed",
    kind: type = pytest.TestReport,
):
    """A real report of the given type; the hook judges the concrete type."""
    return kind(
        nodeid="%s::test_body" % path,
        location=(path, 1, "test_body"),
        keywords={},
        outcome=outcome,
        longrepr=None,
        when=when,
    )


def _record_path(base: Path, name: str) -> Path:
    return base / "records" / ("%s.json" % name)


class TestRecorderWrites:
    def test_a_run_names_the_file_whose_test_reached_the_call_phase(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "run-a")

        run = _run_pytest(repo, record, "run-a")
        assert run.returncode == 0, run.stdout + run.stderr

        document = json.loads(record.read_text(encoding="utf-8"))
        assert document["schema"] == SCHEMA
        assert document["execution_id"] == "run-a"
        assert document["complete"] is True
        assert document["snapshot"] == {"commit": commit, "dirty": False}
        assert document["executed_files"] == ["tests/test_ok.py"]

    def test_a_skipped_or_setup_failed_test_never_reaches_the_record(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_none_run.py": _SKIP_AND_SETUP_FAILURE})
        record = _record_path(tmp_path, "run-none")

        _run_pytest(repo, record, "run-none")

        document = json.loads(record.read_text(encoding="utf-8"))
        assert document["executed_files"] == []

    def test_a_test_that_skips_inside_its_body_is_not_counted(self, tmp_path):
        # pytest does report the call phase here, with outcome skipped. It ran
        # part of the test and the record cannot say how much.
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_halfway.py": _SKIP_IN_BODY})
        record = _record_path(tmp_path, "run-half")

        _run_pytest(repo, record, "run-half")

        document = json.loads(record.read_text(encoding="utf-8"))
        assert document["executed_files"] == []

    def test_a_failing_test_still_counts_as_executed(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_bad.py": "def test_bad():\n    assert 1 == 2\n"})
        record = _record_path(tmp_path, "run-fail")

        run = _run_pytest(repo, record, "run-fail")

        assert run.returncode == 1
        document = json.loads(record.read_text(encoding="utf-8"))
        assert document["executed_files"] == ["tests/test_bad.py"]

    def test_one_run_names_only_the_files_that_executed(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(
            repo,
            {
                "tests/test_ok.py": _PASSING,
                "tests/test_none_run.py": _SKIP_AND_SETUP_FAILURE,
            },
        )
        record = _record_path(tmp_path, "run-mixed")

        _run_pytest(repo, record, "run-mixed")

        document = json.loads(record.read_text(encoding="utf-8"))
        assert document["executed_files"] == ["tests/test_ok.py"]

    def test_without_the_two_variables_no_record_is_written(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "run-off")

        _run_pytest(repo, None, None)

        assert not record.exists()

    def test_a_dirty_tree_is_recorded_as_dirty(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        (repo / "tests/test_ok.py").write_text(_PASSING + "\n# edited\n", encoding="utf-8")
        record = _record_path(tmp_path, "run-dirty")

        _run_pytest(repo, record, "run-dirty")

        document = json.loads(record.read_text(encoding="utf-8"))
        assert document["snapshot"]["dirty"] is True


class TestRecorderRefuses:
    def test_an_unfinished_session_leaves_no_record(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "run-interrupted")
        recorder = Recorder(record, "run-interrupted", repo)
        recorder.status = 2  # pytest's interrupted status

        assert recorder.write() is False
        assert not record.exists()
        # The timestamp is only set by a write that goes ahead, so a refused
        # one must leave it empty rather than claim the run finished.
        assert recorder.finished_at is None

    def test_a_refused_write_from_a_finished_run_warns(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        os.environ["FORGE_E2E_RECORD"] = str(blocker / "run.json")
        os.environ["FORGE_E2E_EXECUTION_ID"] = "finished-but-refused"
        try:
            session = _Session()
            session.pytest_sessionstart(SimpleNamespace(config=SimpleNamespace(rootpath=str(repo))))
            with pytest.warns(UserWarning):
                session.pytest_sessionfinish(None, 0)
        finally:
            os.environ.pop("FORGE_E2E_RECORD", None)
            os.environ.pop("FORGE_E2E_EXECUTION_ID", None)

    def test_a_run_outside_a_repository_leaves_no_record(self, tmp_path):
        plain = tmp_path / "plain"
        (plain / "tests").mkdir(parents=True)
        (plain / "tests/test_ok.py").write_text(_PASSING, encoding="utf-8")
        record = _record_path(tmp_path, "run-norepo")

        _run_pytest(plain, record, "run-norepo")

        assert not record.exists()


class TestReaderStates:
    def test_same_run_is_bound(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "run-a")
        _run_pytest(repo, record, "run-a")

        evidence = verify(
            record_path=record,
            execution_id="run-a",
            reviewed_commit=commit,
            review_root=repo,
        )

        assert evidence.state == "bound"
        assert evidence.bound is True
        assert evidence.executed_files == ("tests/test_ok.py",)

    def test_artifacts_left_by_the_run_do_not_unbind_it(self, tmp_path):
        # A test run writes __pycache__ and report files. Those are not part of
        # the commit under review, so they must not read as a dirty tree.
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "run-cache")
        _run_pytest(repo, record, "run-cache")
        (repo / "tests/__pycache__").mkdir(exist_ok=True)
        (repo / "tests/__pycache__/junk.pyc").write_bytes(b"\x00")

        evidence = verify(
            record_path=record,
            execution_id="run-cache",
            reviewed_commit=commit,
            review_root=repo,
        )

        assert evidence.state == "bound"

    def test_an_older_run_of_the_same_commit_is_refused(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        older = _record_path(tmp_path, "run-old")
        _run_pytest(repo, older, "run-old")
        newer = _record_path(tmp_path, "run-new")
        _run_pytest(repo, newer, "run-new")

        # Both records name the same commit, so only the id tells them apart.
        assert json.loads(older.read_text())["snapshot"]["commit"] == commit
        evidence = verify(
            record_path=older,
            execution_id="run-new",
            reviewed_commit=commit,
            review_root=repo,
        )

        assert evidence.state == "wrong_execution"
        assert evidence.bound is False

    def test_a_record_inside_the_reviewed_tree_is_refused(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        inside = repo / "run.json"
        _run_pytest(repo, inside, "run-inside")

        evidence = verify(
            record_path=inside,
            execution_id="run-inside",
            reviewed_commit=commit,
            review_root=repo,
        )

        assert evidence.state == "in_tree"

    def test_a_named_commit_that_is_not_head_is_refused(self, tmp_path):
        repo = tmp_path / "repo"
        first = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "run-a")
        _run_pytest(repo, record, "run-a")
        (repo / "other.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "second")

        evidence = verify(
            record_path=record,
            execution_id="run-a",
            reviewed_commit=first,
            review_root=repo,
        )

        second = _git(repo, "rev-parse", "HEAD")

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "unbound_review",
            "detail": "HEAD is %s, and the review names %s"
            % (second[: e2e_evidence._ID_WIDTH], first[: e2e_evidence._ID_WIDTH]),
            "bound": False,
            "executed_files": [],
            "review_commit": first,
            "record": str(record),
        }

    def test_a_dirty_tree_is_not_a_bound_review(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "run-a")
        _run_pytest(repo, record, "run-a")
        (repo / "tests/test_ok.py").write_text(_PASSING + "\n# edited\n", encoding="utf-8")

        evidence = verify(
            record_path=record,
            execution_id="run-a",
            reviewed_commit=commit,
            review_root=repo,
        )

        assert evidence.state == "unbound_review"

    def test_a_relative_record_path_belongs_to_the_review_root(self, tmp_path, monkeypatch):
        # Resolving against the caller's directory would compare the wrong file
        # when the caller runs from somewhere else, which is the normal case.
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        inside = repo / "run.json"
        _run_pytest(repo, inside, "relative")

        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        evidence = verify(
            record_path="run.json",
            execution_id="relative",
            reviewed_commit=commit,
            review_root=repo,
        )

        assert evidence.state == "in_tree"

    def test_a_record_from_another_commit_is_stale(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "run-a")
        _run_pytest(repo, record, "run-a")
        (repo / "other.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "second")
        second = _git(repo, "rev-parse", "HEAD")

        evidence = verify(
            record_path=record,
            execution_id="run-a",
            reviewed_commit=second,
            review_root=repo,
        )

        assert evidence.state == "stale"

    def test_a_record_from_a_dirty_run_is_refused(self, tmp_path):
        # The tree is clean now, so the only thing saying "not a clean run" is
        # the record itself. Without that check this would read as bound.
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "dirty-run")
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "execution_id": "dirty-run",
                    "complete": True,
                    "snapshot": {"commit": commit, "dirty": True},
                    "executed_files": ["tests/test_ok.py"],
                }
            ),
            encoding="utf-8",
        )

        evidence = verify(
            record_path=record,
            execution_id="dirty-run",
            reviewed_commit=commit,
            review_root=repo,
        )

        assert evidence.state == "dirty_run"


class TestReaderRefusesBadRecords:
    def _write(self, tmp_path, body) -> Path:
        path = _record_path(tmp_path, "custom")
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def _repo(self, tmp_path) -> tuple[Path, str]:
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        return repo, commit

    def test_no_record_is_absent(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        evidence = verify(record_path=None, execution_id="x", reviewed_commit=commit, review_root=repo)
        assert (evidence.state, evidence.detail, evidence.review_commit) == (
            "absent",
            "no execution record was configured",
            commit,
        )
        assert evidence.executed_files == ()

    def test_a_missing_file_is_absent(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        evidence = verify(
            record_path=tmp_path / "nope.json",
            execution_id="x",
            reviewed_commit=commit,
            review_root=repo,
        )
        assert evidence.state == "absent"

    def test_broken_json_is_unusable(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = self._write(tmp_path, b"{not json")
        evidence = verify(record_path=path, execution_id="x", reviewed_commit=commit, review_root=repo)
        try:
            json.loads("{not json")
        except ValueError as exc:
            expected = "the record is not JSON: %s" % exc
        else:  # pragma: no cover - the fixture is invalid by construction
            raise AssertionError("the fixture parsed, so it cannot test this state")
        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "unusable",
            "detail": expected,
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": str(path),
        }

    def test_another_schema_is_foreign(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = self._write(tmp_path, {"schema": "someone-else-v1"})
        evidence = verify(record_path=path, execution_id="x", reviewed_commit=commit, review_root=repo)
        assert evidence.state == "foreign"

    def test_a_record_that_is_not_complete_proves_nothing(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = self._write(
            tmp_path,
            {
                "schema": SCHEMA,
                "execution_id": "x",
                "complete": False,
                "snapshot": {"commit": commit, "dirty": False},
                "executed_files": [],
            },
        )
        evidence = verify(record_path=path, execution_id="x", reviewed_commit=commit, review_root=repo)
        assert evidence.state == "incomplete"

    @pytest.mark.parametrize(
        "bad_path",
        ["/etc/passwd", "../outside.py", "tests\\win.py", "tests//double.py", ""],
    )
    def test_a_bad_path_entry_makes_the_record_unusable(self, tmp_path, bad_path):
        repo, commit = self._repo(tmp_path)
        path = self._write(
            tmp_path,
            {
                "schema": SCHEMA,
                "execution_id": "x",
                "complete": True,
                "snapshot": {"commit": commit, "dirty": False},
                "executed_files": [bad_path],
            },
        )
        evidence = verify(record_path=path, execution_id="x", reviewed_commit=commit, review_root=repo)
        assert evidence.state == "unusable"

    def test_a_commit_that_is_not_hex_is_unbound(self, tmp_path):
        repo, _ = self._repo(tmp_path)
        evidence = verify(record_path=None, execution_id="x", reviewed_commit="main", review_root=repo)
        assert evidence.state == "unbound_review"


class TestProducerUnits:
    """Called directly, because a subprocess run leaves mutation without tests.

    The end-to-end tests above exercise the plugin through a spawned pytest, so
    a mutant inside these helpers is never imported by the test that covers it.
    """

    def test_a_relative_path_stays_relative(self, tmp_path):
        assert _as_posix_relative(tmp_path, "tests/x.py") == "tests/x.py"

    def test_an_absolute_path_inside_the_root_becomes_relative(self, tmp_path):
        inside = tmp_path / "tests" / "x.py"
        assert _as_posix_relative(tmp_path, str(inside)) == "tests/x.py"

    @pytest.mark.parametrize("outside", ["/etc/passwd", "tests/../x.py", "", ".."])
    def test_a_path_that_is_not_under_the_root_is_refused(self, tmp_path, outside):
        assert _as_posix_relative(tmp_path, outside) is None

    def _recorder(self, tmp_path, body: dict[str, str]) -> Recorder:
        repo = tmp_path / "repo"
        _make_repo(repo, body)
        return Recorder(_record_path(tmp_path, "unit"), "unit", repo)

    def test_only_a_call_phase_result_names_a_file(self, tmp_path):
        recorder = self._recorder(tmp_path, {"tests/test_ok.py": _PASSING})
        recorder.note(_report())
        assert recorder.files == {"tests/test_ok.py"}

        for index, (when, outcome) in enumerate(
            (("setup", "passed"), ("teardown", "passed"), ("call", "skipped"))
        ):
            fresh = self._recorder(tmp_path / str(index), {"tests/test_ok.py": _PASSING})
            fresh.note(_report(when=when, outcome=outcome))
            assert fresh.files == set(), (when, outcome)

    def test_a_report_without_a_path_is_ignored(self, tmp_path):
        recorder = self._recorder(tmp_path, {"tests/test_ok.py": _PASSING})

        recorder.note(_report(path=""))  # must not raise

        assert recorder.files == set()

    def test_a_tracked_change_marks_the_run_dirty(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        (repo / "tests/test_ok.py").write_text(_PASSING + "\n# edited\n", encoding="utf-8")

        recorder = Recorder(_record_path(tmp_path, "tracked"), "tracked", repo)

        assert recorder.dirty is True

    def test_a_parent_that_is_not_a_directory_leaves_no_record(self, tmp_path):
        # A permission bit would be ignored when the suite runs as root; a path
        # whose parent is a regular file fails for every user.
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")

        recorder = Recorder(blocker / "run.json", "blocked", repo)
        recorder.status = 0

        assert recorder.write() is False
        assert recorder.out_path.exists() is False

    def test_a_run_outside_a_repository_writes_nothing(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        recorder = Recorder(_record_path(tmp_path, "plain"), "plain", plain)
        recorder.status = 0
        assert recorder.commit is None
        assert recorder.dirty is None
        assert recorder.write() is False
        assert not _record_path(tmp_path, "plain").exists()

    def test_a_finished_run_writes_the_record_in_process(self, tmp_path):
        # The end-to-end tests reach this through a subprocess, where mutation
        # cannot see it. This keeps the writer covered in-process.
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        recorder = Recorder(_record_path(tmp_path, "inproc"), "inproc", repo)
        recorder.files.add("tests/test_ok.py")
        recorder.status = 0

        assert recorder.write() is True

        document = json.loads(_record_path(tmp_path, "inproc").read_text(encoding="utf-8"))
        assert document["complete"] is True
        assert document["execution_id"] == "inproc"
        assert document["snapshot"] == {"commit": commit, "dirty": False}
        assert document["executed_files"] == ["tests/test_ok.py"]

    def test_the_payload_names_the_run_the_snapshot_and_the_files(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        recorder = Recorder(_record_path(tmp_path, "payload"), "payload", repo)
        recorder.files.update({"tests/b.py", "tests/a.py"})
        recorder.status = 1
        recorder.finished_at = "2026-01-01T00:00:00-05:00"

        payload = recorder.payload()

        assert payload["started_at"] == recorder.started_at
        assert payload["started_at"].startswith("20")
        payload_without_times = dict(payload)
        payload_without_times.pop("started_at")
        assert payload_without_times == {
            "schema": SCHEMA,
            "execution_id": "payload",
            "finished_at": "2026-01-01T00:00:00-05:00",
            "complete": True,
            "session_exitstatus": 1,
            "snapshot": {"commit": commit, "dirty": False},
            "executed_files": ["tests/a.py", "tests/b.py"],
        }

    def test_leftover_artifacts_from_an_earlier_run_do_not_mark_it_dirty(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        (repo / "tests/__pycache__").mkdir()
        (repo / "tests/__pycache__/old.pyc").write_bytes(b"\x00")

        recorder = Recorder(_record_path(tmp_path, "leftover"), "leftover", repo)

        assert recorder.dirty is False

    def test_a_finish_status_that_is_not_a_number_writes_nothing(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "no-status")
        session = _Session()
        session.recorder = Recorder(record, "no-status", repo)

        session.pytest_sessionfinish(None, None)  # must not raise

        assert not record.exists()


class TestHookWiring:
    """The hook object and the module-level wrappers around it.

    A mutant in a hook that no test calls is a mutant nobody can judge, so each
    one is called here directly instead of only through a spawned pytest.
    """

    def _session(self, root: Path) -> SimpleNamespace:
        return SimpleNamespace(config=SimpleNamespace(rootpath=root, rootdir=root))

    def test_the_hook_opens_a_recorder_when_both_variables_are_named(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "wired")
        monkeypatch.setenv("FORGE_E2E_RECORD", str(record))
        monkeypatch.setenv("FORGE_E2E_EXECUTION_ID", "wired")

        hooks = _Session()
        hooks.pytest_sessionstart(self._session(repo))

        assert hooks.recorder is not None
        assert hooks.recorder.execution_id == "wired"

        hooks.pytest_runtest_logreport(_report())
        hooks.pytest_sessionfinish(None, 0)

        document = json.loads(record.read_text(encoding="utf-8"))
        assert document["executed_files"] == ["tests/test_ok.py"]

    @pytest.mark.parametrize("missing", ["FORGE_E2E_RECORD", "FORGE_E2E_EXECUTION_ID"])
    def test_the_hook_stays_closed_when_either_variable_is_missing(self, tmp_path, monkeypatch, missing):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        monkeypatch.setenv("FORGE_E2E_RECORD", str(_record_path(tmp_path, "x")))
        monkeypatch.setenv("FORGE_E2E_EXECUTION_ID", "x")
        monkeypatch.delenv(missing)

        hooks = _Session()
        hooks.pytest_sessionstart(self._session(repo))

        assert hooks.recorder is None

    def test_the_module_wrappers_reach_the_shared_hook(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "wrapped")
        monkeypatch.setenv("FORGE_E2E_RECORD", str(record))
        monkeypatch.setenv("FORGE_E2E_EXECUTION_ID", "wrapped")

        try:
            e2e_record.pytest_sessionstart(self._session(repo))
            e2e_record.pytest_runtest_logreport(_report())
            e2e_record.pytest_sessionfinish(None, 0)
        finally:
            # The hook object is process-wide; a recorder left installed would
            # let a later test write a record it never asked for.
            e2e_record._HOOKS.recorder = None

        document = json.loads(record.read_text(encoding="utf-8"))
        assert document["execution_id"] == "wrapped"
        assert document["executed_files"] == ["tests/test_ok.py"]


class TestCommandLine:
    def _bound(self, tmp_path) -> tuple[Path, str, Path]:
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "cli")
        _run_pytest(repo, record, "cli")
        return repo, commit, record

    def test_a_bound_record_exits_zero(self, tmp_path, capsys):
        repo, commit, record = self._bound(tmp_path)

        code = e2e_evidence.main(
            [
                "--record",
                str(record),
                "--execution-id",
                "cli",
                "--reviewed-commit",
                commit,
                "--root",
                str(repo),
            ]
        )

        assert code == 0
        assert json.loads(capsys.readouterr().out)["state"] == "bound"

    def test_an_unbound_record_exits_one(self, tmp_path, capsys):
        repo, commit, record = self._bound(tmp_path)

        code = e2e_evidence.main(
            [
                "--record",
                str(record),
                "--execution-id",
                "another-run",
                "--reviewed-commit",
                commit,
                "--root",
                str(repo),
            ]
        )

        assert code == 1
        assert json.loads(capsys.readouterr().out)["state"] == "wrong_execution"

    def test_a_missing_required_argument_stops_the_command(self, tmp_path):
        import pytest as _pytest

        repo, commit, record = self._bound(tmp_path)
        with _pytest.raises(SystemExit) as raised:
            e2e_evidence.main(["--record", str(record), "--reviewed-commit", commit])
        assert raised.value.code == 2

    def test_an_empty_execution_id_is_a_usage_error(self, tmp_path, capsys):
        repo, commit, record = self._bound(tmp_path)

        code = e2e_evidence.main(
            [
                "--record",
                str(record),
                "--execution-id",
                "   ",
                "--reviewed-commit",
                commit,
                "--root",
                str(repo),
            ]
        )

        assert code == 2
        assert capsys.readouterr().err.strip() == ("code-forge: e2e-evidence: --execution-id is empty")


class TestReaderRootProblems:
    def test_a_review_root_that_does_not_exist_is_unbound(self, tmp_path):
        record = _record_path(tmp_path, "any")
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("{}", encoding="utf-8")

        evidence = verify(
            record_path=record,
            execution_id="x",
            reviewed_commit="a" * 40,
            review_root=tmp_path / "missing",
        )

        assert evidence.state == "unbound_review"
        assert "not a git worktree" in evidence.detail


class TestErrorStatesAndOddInputs:
    """The error states, their messages, and the inputs that produce them.

    The states that carry the whole report are in
    ``TestEveryStateCarriesItsFields``; these are the remaining branches and the
    shapes a caller can hand in.
    """

    def _repo(self, tmp_path) -> tuple[Path, str]:
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        return repo, commit

    def _write(self, tmp_path, document) -> Path:
        path = _record_path(tmp_path, "case")
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(document, (bytes, str)):
            path.write_text(
                document if isinstance(document, str) else document.decode(), encoding="utf-8"
            )
        else:
            path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def _document(self, commit: str, **over) -> dict:
        document = {
            "schema": SCHEMA,
            "execution_id": "run",
            "complete": True,
            "snapshot": {"commit": commit, "dirty": False},
            "executed_files": ["tests/test_ok.py"],
        }
        document.update(over)
        return document

    @pytest.mark.parametrize(
        ("over", "detail"),
        [
            ({"snapshot": "not a mapping"}, "the record carries no snapshot"),
            ({"execution_id": ""}, "the record carries no execution id"),
            (
                {"snapshot": {"commit": "nope", "dirty": False}},
                "the snapshot has no commit or no clean flag",
            ),
            ({"executed_files": "tests/x.py"}, "executed_files is not a list of repository paths"),
        ],
    )
    def test_unusable_fields(self, tmp_path, over, detail):
        repo, commit = self._repo(tmp_path)
        path = self._write(tmp_path, self._document(commit, **over))

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert (evidence.state, evidence.detail) == ("unusable", detail)

    def test_a_document_that_is_not_an_object(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = self._write(tmp_path, [1, 2, 3])

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert (evidence.state, evidence.detail) == ("unusable", "the record is not a JSON object")

    def test_absent_with_a_configured_path(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        missing = _record_path(tmp_path, "nothing-here")

        evidence = verify(
            record_path=missing, execution_id="run", reviewed_commit=commit, review_root=repo
        )

        assert (evidence.state, evidence.detail) == ("absent", "no record at %s" % missing)

    def test_unbound_without_a_commit(self, tmp_path):
        repo, _ = self._repo(tmp_path)

        evidence = verify(record_path=None, execution_id="run", reviewed_commit="", review_root=repo)

        assert (evidence.state, evidence.detail) == (
            "unbound_review",
            "no 40-hex commit was named for the review",
        )

    def test_unbound_when_a_tracked_file_differs(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = self._write(tmp_path, self._document(commit))
        (repo / "tests/test_ok.py").write_text(_PASSING + "\n# edited\n", encoding="utf-8")

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "unbound_review",
            "detail": "tracked files differ from the commit, so the reviewed content is not one commit",
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": str(path),
        }

    def test_a_record_over_the_size_limit_names_the_limit(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = self._write(tmp_path, b"x" * (MAX_RECORD_BYTES + 1))

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "unusable",
            "detail": "the record is %d bytes, over the %d byte limit"
            % (MAX_RECORD_BYTES + 1, MAX_RECORD_BYTES),
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": str(path),
        }

    def test_a_record_that_is_not_utf8_says_so(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = _record_path(tmp_path, "binary")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xfe")

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        try:
            b"\xff\xfe".decode("utf-8")
        except UnicodeError as exc:
            expected = "cannot read the record: %s" % exc
        else:  # pragma: no cover - the bytes are invalid by construction
            raise AssertionError("the fixture decoded, so it cannot test this state")
        assert (evidence.state, evidence.detail) == ("unusable", expected)

    def test_a_root_that_is_not_a_repository_says_so(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        record = _record_path(tmp_path, "any")
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("{}", encoding="utf-8")

        evidence = verify(
            record_path=record, execution_id="run", reviewed_commit="a" * 40, review_root=plain
        )

        assert evidence.state == "unbound_review"
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(plain),
            capture_output=True,
            text=True,
            check=False,
        )
        # A failing git explains itself on stderr; losing that text would leave
        # the reader with a colon and nothing after it.
        assert result.stderr.strip()
        assert evidence.detail == "not a git worktree: %s" % result.stderr.strip()
        assert evidence.review_commit == "a" * 40
        assert evidence.record is None

    def test_the_json_report_carries_every_field(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = self._write(tmp_path, self._document(commit))

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "bound",
            "detail": "the record describes this run of this commit",
            "bound": True,
            "executed_files": ["tests/test_ok.py"],
            "review_commit": commit,
            "record": str(path),
        }


class TestReaderHelpers:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("a" * 40, True),
            ("A" * 40, True),
            ("a" * 39, False),
            ("g" * 40, False),
            (None, False),
            (1234, False),
        ],
    )
    def test_a_commit_is_forty_hex_characters(self, value, expected):
        assert _is_commit(value) is expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (["a/b.py"], ("a/b.py",)),
            (["b.py", "a.py", "b.py"], ("a.py", "b.py")),
            ([], ()),
            (["/abs.py"], None),
            (["a/../b.py"], None),
            (["a\\b.py"], None),
            (["a//b.py"], None),
            (["a/./b.py"], None),
            ([1], None),
            ("a/b.py", None),
        ],
    )
    def test_a_file_list_is_cleaned_or_refused(self, value, expected):
        assert _valid_paths(value) == expected

    def test_two_ids_are_compared_without_short_circuiting(self):
        assert _same("abc", "abc") is True
        assert _same("abc", "abd") is False

    def test_a_value_that_is_not_a_string_is_not_a_match(self):
        assert _same(None, "abc") is False
        assert _same("abc", None) is False


class TestHelpersOnTheirOwn:
    """The path helpers are reached through two callers, and each caller filters
    something before handing the value over. Testing only the caller leaves the
    helper's own guards unpinned, which is where the surviving mutants were."""

    @pytest.mark.parametrize(
        ("root", "value", "expected"),
        [
            ("/repo", "/repo/tests/a.py", "tests/a.py"),
            ("/repo", "/elsewhere/a.py", None),
            ("/repo", "tests/a.py", "tests/a.py"),
            ("/repo", "", None),
            ("/repo", ".", None),
            ("/repo", "tests/./a.py", "tests/a.py"),  # Path normalises the dot away
            ("/repo", "tests/../a.py", None),
            ("/repo", "/", None),
        ],
    )
    def test_relative_paths_are_accepted_only_inside_the_root(self, root, value, expected):
        assert _as_posix_relative(Path(root), value) == expected

    def test_an_absolute_path_outside_the_root_is_refused_before_joining(self):
        assert _as_posix_relative(Path("/repo"), "/repo-evil/a.py") is None

    def test_a_nul_byte_in_a_path_is_refused(self):
        assert _valid_paths(["tests/a\x00b.py"]) is None

    def test_a_dot_segment_is_refused(self):
        assert _valid_paths(["tests/./a.py"]) is None


class TestWhatCountsAsATestThatRan:
    """The hook sees several report shapes, and only one of them means the
    test body executed."""

    def _recorder(self, tmp_path) -> Recorder:
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        return Recorder(_record_path(tmp_path, "note"), "note", repo)

    def test_a_call_report_names_its_file(self, tmp_path):
        recorder = self._recorder(tmp_path)

        recorder.note(_report())

        assert recorder.files == {"tests/test_ok.py"}

    def test_something_that_is_not_a_report_is_ignored(self, tmp_path):
        recorder = self._recorder(tmp_path)

        recorder.note(SimpleNamespace(when="call", outcome="passed", fspath="tests/test_ok.py"))

        assert recorder.files == set()

    def test_a_subtest_report_is_ignored(self, tmp_path):
        # pytest-subtests wraps a synthetic call report in a SubtestReport, so a
        # subtest that passes inside a fixture looks like a call-phase result
        # unless the concrete type is checked.
        recorder = self._recorder(tmp_path)
        subtest = _report(kind=_SubtestReport)

        assert type(subtest) is not pytest.TestReport
        recorder.note(subtest)

        assert recorder.files == set()

    def test_an_absolute_path_is_made_relative_to_the_recorded_root(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        recorder = Recorder(_record_path(tmp_path, "absolute"), "absolute", repo)

        recorder.note(_report(path=str(repo / "tests/test_ok.py")))

        assert recorder.files == {"tests/test_ok.py"}
        assert recorder.root == repo

    def test_a_report_outside_the_root_is_ignored(self, tmp_path):
        recorder = self._recorder(tmp_path)

        recorder.note(_report(path="/etc/passwd"))

        assert recorder.files == set()


class TestTheRecorderStartsEmpty:
    def test_every_field_starts_at_its_resting_value(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})

        recorder = Recorder(_record_path(tmp_path, "rest"), "rest", repo)

        assert recorder.root == repo
        assert recorder.commit == commit
        assert recorder.dirty is False
        assert recorder.status is None
        assert recorder.finished_at is None
        assert recorder.files == set()

    def test_a_session_holds_no_recorder_before_the_run_starts(self):
        assert _Session().recorder is None

    def test_a_session_without_a_rootpath_falls_back_to_rootdir(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        config = SimpleNamespace(rootdir=str(repo))
        record = _record_path(tmp_path, "fallback")
        os.environ["FORGE_E2E_RECORD"] = str(record)
        os.environ["FORGE_E2E_EXECUTION_ID"] = "fallback"
        try:
            session = _Session()
            session.pytest_sessionstart(SimpleNamespace(config=config))
        finally:
            os.environ.pop("FORGE_E2E_RECORD", None)
            os.environ.pop("FORGE_E2E_EXECUTION_ID", None)

        assert session.recorder is not None
        assert session.recorder.root == repo

    def test_a_finish_status_that_is_not_an_integer_stays_unset(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        os.environ["FORGE_E2E_RECORD"] = str(_record_path(tmp_path, "unset"))
        os.environ["FORGE_E2E_EXECUTION_ID"] = "unset"
        try:
            session = _Session()
            session.pytest_sessionstart(SimpleNamespace(config=SimpleNamespace(rootpath=str(repo))))
            session.pytest_sessionfinish(None, object())
        finally:
            os.environ.pop("FORGE_E2E_RECORD", None)
            os.environ.pop("FORGE_E2E_EXECUTION_ID", None)

        assert session.recorder.status is None


class TestTheReviewGateMessages:
    def test_a_long_execution_id_is_shortened_to_twelve_characters(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        recorded = "recorded-execution-id-0001"
        expected = "expected-execution-id-0002"
        path = _record_path(tmp_path, "long")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "execution_id": recorded,
                    "complete": True,
                    "snapshot": {"commit": commit, "dirty": False},
                    "executed_files": [],
                }
            ),
            encoding="utf-8",
        )

        evidence = verify(
            record_path=path, execution_id=expected, reviewed_commit=commit, review_root=repo
        )

        assert evidence.detail == "the record belongs to execution %s, not %s" % (
            recorded[: e2e_evidence._ID_WIDTH],
            expected[: e2e_evidence._ID_WIDTH],
        )

    def test_a_working_tree_git_cannot_read_is_reported_as_unbound(self, tmp_path):
        # `rev-parse` still works, so the failure is in the status call itself.
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        path = _record_path(tmp_path, "broken")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "execution_id": "run",
                    "complete": True,
                    "snapshot": {"commit": commit, "dirty": False},
                    "executed_files": [],
                }
            ),
            encoding="utf-8",
        )
        (repo / ".git" / "index").write_bytes(b"not an index")

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.state == "unbound_review"
        assert evidence.detail.startswith("cannot read the working tree state: ")
        assert evidence.detail != "cannot read the working tree state: %s"
        assert evidence.review_commit == commit
        assert evidence.record is None


class TestEveryStateCarriesItsFields:
    """The state and the sentence are not the whole answer: the report also
    names the commit it judged and the record it read. Asserting only the state
    leaves those fields free to disappear."""

    def _repo(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        return repo, commit

    def _record(self, tmp_path, document, name="case"):
        path = _record_path(tmp_path, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def _document(self, commit, **over):
        document = {
            "schema": SCHEMA,
            "execution_id": "run",
            "complete": True,
            "snapshot": {"commit": commit, "dirty": False},
            "executed_files": ["tests/test_ok.py"],
        }
        document.update(over)
        return document

    def test_bound(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = self._record(tmp_path, self._document(commit))

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "bound",
            "detail": "the record describes this run of this commit",
            "bound": True,
            "executed_files": ["tests/test_ok.py"],
            "review_commit": commit,
            "record": str(path),
        }

    def test_foreign(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = self._record(tmp_path, self._document(commit, schema="other-v1"))

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "foreign",
            "detail": "the record names schema 'other-v1', not 'forge-e2e-execution-v1'",
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": str(path),
        }

    def test_incomplete(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = self._record(tmp_path, self._document(commit, complete=False))

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "incomplete",
            "detail": "the run did not finish writing this record",
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": str(path),
        }

    def test_wrong_execution(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = self._record(tmp_path, self._document(commit, execution_id="another-run"))

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "wrong_execution",
            "detail": "the record belongs to execution another-run, not run",
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": str(path),
        }

    def test_dirty_run(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = self._record(tmp_path, self._document(commit, snapshot={"commit": commit, "dirty": True}))

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "dirty_run",
            "detail": "the tests ran on a working tree that was not clean",
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": str(path),
        }

    def test_stale(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        older = "b" * 40
        path = self._record(tmp_path, self._document(older))

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "stale",
            "detail": "the tests ran at %s, and the review is of %s"
            % (older[: e2e_evidence._ID_WIDTH], commit[: e2e_evidence._ID_WIDTH]),
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": str(path),
        }

    def test_unusable(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        path = self._record(tmp_path, self._document(commit, snapshot="not a mapping"))

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "unusable",
            "detail": "the record carries no snapshot",
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": str(path),
        }

    def test_in_tree(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        inside = repo / "run.json"
        inside.write_text(json.dumps(self._document(commit)), encoding="utf-8")

        evidence = verify(
            record_path=inside, execution_id="run", reviewed_commit=commit, review_root=repo
        )

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "in_tree",
            "detail": "the record is inside the reviewed tree, so the reviewed change supplied it",
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": str(inside),
        }

    def test_absent_without_a_path(self, tmp_path):
        repo, commit = self._repo(tmp_path)

        evidence = verify(record_path=None, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "absent",
            "detail": "no execution record was configured",
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": None,
        }

    def test_absent_for_an_empty_path_too(self, tmp_path):
        repo, commit = self._repo(tmp_path)

        evidence = verify(record_path="", execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict()["state"] == "absent"
        assert evidence.detail == "no execution record was configured"

    def test_absent_for_a_path_that_is_not_there(self, tmp_path):
        repo, commit = self._repo(tmp_path)
        missing = _record_path(tmp_path, "gone")

        evidence = verify(
            record_path=missing, execution_id="run", reviewed_commit=commit, review_root=repo
        )

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "absent",
            "detail": "no record at %s" % missing,
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": str(missing),
        }

    def test_unbound_review_names_no_commit(self, tmp_path):
        repo, _ = self._repo(tmp_path)

        evidence = verify(
            record_path=None, execution_id="run", reviewed_commit="not-a-commit", review_root=repo
        )

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "unbound_review",
            "detail": "no 40-hex commit was named for the review",
            "bound": False,
            "executed_files": [],
            "review_commit": None,
            "record": None,
        }


class TestGitOutputIsDecoded:
    """The runner reads git through a text pipe. A quotes-off status can carry
    bytes that are not UTF-8, and the read must not raise on them."""

    def test_a_file_name_that_is_not_utf8_does_not_break_the_read(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        _git(repo, "config", "core.quotePath", "false")
        broken = os.path.join(os.fsencode(str(repo)), b"caf\xe9.txt")
        with open(broken, "wb") as handle:
            handle.write(b"x")

        # Untracked on purpose: the point is the raw bytes git prints, not the
        # tracked-file filter the reader uses.
        code, status = _evidence_git(repo, "status", "--porcelain")

        assert code == 0
        assert isinstance(status, str)
        assert commit
        # The handler replaced the undecodable byte, which is the whole point:
        # strict would have raised, and any other result would not carry U+FFFD.
        assert "caf" in status
        assert "\ufffd" in status


class TestTheRecorderOnlyWritesWhatItCanSayItTested:
    def test_a_repository_without_a_commit_writes_nothing(self, tmp_path):
        # commit is unknown while the tree is still clean: neither half may be
        # dropped, and an empty repository is exactly that pair.
        repo = tmp_path / "empty"
        repo.mkdir()
        _git(repo, "init", "-q")
        record = _record_path(tmp_path, "empty")

        recorder = Recorder(record, "empty", repo)
        recorder.status = 0

        assert recorder.commit is None
        assert recorder.dirty is False
        assert recorder.write() is False
        assert record.exists() is False

    def test_the_written_document_carries_an_iso_timestamp(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "stamp")

        recorder = Recorder(record, "stamp", repo)
        recorder.status = 0
        assert recorder.write() is True

        document = json.loads(record.read_text(encoding="utf-8"))
        finished = document["finished_at"]
        assert isinstance(finished, str)
        assert len(finished) >= 19
        assert finished[4] == "-" and finished[10] == "T"

    def test_a_record_two_directories_deep_is_written(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = tmp_path / "a" / "b" / "run.json"

        recorder = Recorder(record, "deep", repo)
        recorder.status = 0

        assert recorder.write() is True
        assert record.is_file()


class TestTheRecorderWritesTheFileItSays:
    """The reader tolerates any strict JSON, but the file is also meant to be
    read by a person in a diff, which is what the layout is for."""

    def _written(self, tmp_path) -> str:
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "layout")
        recorder = Recorder(record, "layout", repo)
        recorder.status = 0
        assert recorder.write() is True
        return record.read_text(encoding="utf-8")

    def test_the_file_is_indented_two_spaces(self, tmp_path):
        body = self._written(tmp_path)

        assert body.startswith('{\n  "')
        assert '\n    "' in body

    def test_the_keys_are_written_in_sorted_order(self, tmp_path):
        body = self._written(tmp_path)
        first = body.splitlines()[1].strip().split(":")[0].strip('"')

        # complete sorts before execution_id, which sorts before schema.
        assert first == "complete"

    def test_the_file_ends_with_a_newline(self, tmp_path):
        assert self._written(tmp_path).endswith("}\n")


class TestTheRecordCannotBeMeasured:
    def test_a_record_whose_size_cannot_be_read_is_unusable(self, tmp_path, monkeypatch):
        # is_file() already returned True, so the only way here is a stat that
        # fails between the check and the read.
        import pathlib as _pathlib

        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        path = _record_path(tmp_path, "denied")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

        real_stat = _pathlib.Path.stat
        real_is_file = _pathlib.Path.is_file

        def fake_stat(self, *args, **kwargs):
            if self == path:
                raise OSError("denied")
            return real_stat(self, *args, **kwargs)

        def fake_is_file(self):
            if self == path:
                return True
            return real_is_file(self)

        monkeypatch.setattr(_pathlib.Path, "stat", fake_stat)
        monkeypatch.setattr(_pathlib.Path, "is_file", fake_is_file)

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "unusable",
            "detail": "cannot stat the record: denied",
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": str(path),
        }

    def test_a_record_of_exactly_the_limit_is_still_read(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        path = self._record_exactly(tmp_path, commit)

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.state != "unusable"
        assert "byte limit" not in evidence.detail

    def _record_exactly(self, tmp_path, commit):
        body = json.dumps(
            {
                "schema": SCHEMA,
                "execution_id": "run",
                "complete": True,
                "snapshot": {"commit": commit, "dirty": False},
                "executed_files": [],
            }
        )
        padding = MAX_RECORD_BYTES - len(body)
        assert padding > 0
        # Whitespace after the object keeps it valid JSON at an exact size.
        body = body + " " * padding
        path = _record_path(tmp_path, "exact")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        assert path.stat().st_size == MAX_RECORD_BYTES
        return path


class TestTheCommandLineNamesItsArguments:
    def test_help_lists_every_argument(self, capsys):
        with pytest.raises(SystemExit) as raised:
            e2e_evidence.main(["--help"])

        assert raised.value.code == 0
        printed = capsys.readouterr().out
        for flag in ("--record", "--execution-id", "--reviewed-commit", "--root"):
            assert flag in printed


class TestTheRecordCannotBeRead:
    def test_a_record_that_cannot_be_read_names_the_error(self, tmp_path, monkeypatch):
        import pathlib as _pathlib

        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        path = _record_path(tmp_path, "unreadable")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

        real_read = _pathlib.Path.read_text

        def fake_read(self, *args, **kwargs):
            if self == path:
                raise OSError("unreadable")
            return real_read(self, *args, **kwargs)

        monkeypatch.setattr(_pathlib.Path, "read_text", fake_read)

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=commit, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "unusable",
            "detail": "cannot read the record: unreadable",
            "bound": False,
            "executed_files": [],
            "review_commit": commit,
            "record": str(path),
        }


class TestGitItselfIsMissing:
    def test_a_git_that_cannot_start_is_reported_with_its_error(self, tmp_path, monkeypatch):
        import subprocess as _subprocess

        repo = tmp_path / "repo"
        repo.mkdir()

        def refuse(*args, **kwargs):
            raise FileNotFoundError("no git here")

        monkeypatch.setattr(_subprocess, "run", refuse)

        code, text = _evidence_git(repo, "rev-parse", "HEAD")

        assert code == 1
        assert text == "no git here"


class TestTheCommandLineRefusesWhatItCannotJudge:
    def _bound(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "cli")
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "execution_id": "run",
                    "complete": True,
                    "snapshot": {"commit": commit, "dirty": False},
                    "executed_files": ["tests/test_ok.py"],
                }
            ),
            encoding="utf-8",
        )
        return repo, commit, record

    def test_an_execution_id_of_nothing_is_a_usage_error(self, tmp_path, capsys):
        repo, commit, record = self._bound(tmp_path)

        code = e2e_evidence.main(
            [
                "--record",
                str(record),
                "--execution-id",
                "",
                "--reviewed-commit",
                commit,
                "--root",
                str(repo),
            ]
        )

        assert code == 2
        assert capsys.readouterr().err.strip() == ("code-forge: e2e-evidence: --execution-id is empty")

    @pytest.mark.parametrize("missing", ["--execution-id", "--reviewed-commit"])
    def test_every_required_argument_is_required(self, tmp_path, missing):
        repo, commit, record = self._bound(tmp_path)
        arguments = {
            "--record": str(record),
            "--execution-id": "run",
            "--reviewed-commit": commit,
            "--root": str(repo),
        }
        arguments.pop(missing)
        ordered = []
        for flag, value in arguments.items():
            ordered.extend([flag, value])

        with pytest.raises(SystemExit) as raised:
            e2e_evidence.main(ordered)

        assert raised.value.code == 2

    def test_the_root_defaults_to_the_working_directory(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "default-root")
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "execution_id": "run",
                    "complete": True,
                    "snapshot": {"commit": commit, "dirty": False},
                    "executed_files": ["tests/test_ok.py"],
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.chdir(repo)

        code = e2e_evidence.main(
            ["--record", str(record), "--execution-id", "run", "--reviewed-commit", commit]
        )

        assert code == 0

    def test_the_summary_is_indented_and_sorted_like_the_record(self, tmp_path, capsys):
        repo, commit, record = self._bound(tmp_path)

        code = e2e_evidence.main(
            [
                "--record",
                str(record),
                "--execution-id",
                "run",
                "--reviewed-commit",
                commit,
                "--root",
                str(repo),
            ]
        )

        assert code == 0
        printed = capsys.readouterr().out
        assert printed.startswith('{\n  "')
        # as_dict's keys sorted: bound comes first, schema last but one.
        assert printed.splitlines()[1].strip().startswith('"bound"')
        assert json.loads(printed)["state"] == "bound"


class TestTheReviewNamesTheWrongCommit:
    def test_a_commit_that_is_not_head_is_refused_and_named(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        other = "c" * 40
        path = _record_path(tmp_path, "wrong-head")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "execution_id": "run",
                    "complete": True,
                    "snapshot": {"commit": commit, "dirty": False},
                    "executed_files": ["tests/test_ok.py"],
                }
            ),
            encoding="utf-8",
        )

        evidence = verify(record_path=path, execution_id="run", reviewed_commit=other, review_root=repo)

        assert evidence.as_dict() == {
            "schema": SCHEMA,
            "state": "unbound_review",
            "detail": "HEAD is %s, and the review names %s"
            % (commit[: e2e_evidence._ID_WIDTH], other[: e2e_evidence._ID_WIDTH]),
            "bound": False,
            "executed_files": [],
            "review_commit": other,
            "record": str(path),
        }


class TestTheRecordIsWrittenAtomically:
    """The temp file has to live beside the record: os.replace is only atomic
    within one filesystem, which is the reason for the temporary file at all."""

    def test_the_temporary_file_sits_beside_the_record(self, tmp_path, monkeypatch):
        import tempfile as _tempfile

        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "atomic")
        seen = {}
        real_mkstemp = _tempfile.mkstemp

        def spy(**kwargs):
            seen.update(kwargs)
            return real_mkstemp(**kwargs)

        monkeypatch.setattr(_tempfile, "mkstemp", spy)

        recorder = Recorder(record, "atomic", repo)
        recorder.status = 0
        assert recorder.write() is True

        assert seen["dir"] == str(record.parent)
        assert seen["suffix"] == ".tmp"
        assert seen["prefix"] == record.name + "."

    def test_writing_twice_to_the_same_path_succeeds(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "twice")

        first = Recorder(record, "twice", repo)
        first.status = 0
        assert first.write() is True
        second = Recorder(record, "twice", repo)
        second.status = 0

        assert second.write() is True

    def test_no_temporary_file_is_left_behind(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "leftovers")

        recorder = Recorder(record, "leftovers", repo)
        recorder.status = 0
        assert recorder.write() is True

        assert [p.name for p in record.parent.iterdir()] == [record.name]


class TestTheRecordReaderSurvivesAGitThatPrintsRawBytes:
    def test_a_file_name_that_is_not_utf8_does_not_break_the_record_read(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        _git(repo, "config", "core.quotePath", "false")
        broken = os.path.join(os.fsencode(str(repo)), b"caf\xe9.txt")
        with open(broken, "wb") as handle:
            handle.write(b"x")

        # The module's reader, not this file's fixture helper: the helper uses
        # check=True and no error handler, so it cannot show the difference.
        text = e2e_record._git(repo, "status", "--porcelain")

        assert isinstance(text, str)
        assert "caf" in text
        assert "\ufffd" in text


class TestTheReviewedCommitIsTrimmed:
    def test_a_commit_padded_with_spaces_still_binds(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING})
        path = _record_path(tmp_path, "padded")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "execution_id": "run",
                    "complete": True,
                    "snapshot": {"commit": commit, "dirty": False},
                    "executed_files": ["tests/test_ok.py"],
                }
            ),
            encoding="utf-8",
        )

        evidence = verify(
            record_path=path,
            execution_id="run",
            reviewed_commit="  %s \n" % commit,
            review_root=repo,
        )

        assert evidence.state == "bound"
        assert evidence.review_commit == commit


class TestAConfiguredRunThatProducesNothingSaysSo:
    def test_a_refused_write_warns_with_the_path(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        os.environ["FORGE_E2E_RECORD"] = str(blocker / "run.json")
        os.environ["FORGE_E2E_EXECUTION_ID"] = "refused"
        try:
            session = _Session()
            session.pytest_sessionstart(SimpleNamespace(config=SimpleNamespace(rootpath=str(repo))))
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                session.pytest_sessionfinish(None, 0)
        finally:
            os.environ.pop("FORGE_E2E_RECORD", None)
            os.environ.pop("FORGE_E2E_EXECUTION_ID", None)

        assert len(caught) == 1
        assert str(caught[0].message) == (
            "the execution record was asked for but not written: %s "
            "(the record could not be written)" % (blocker / "run.json")
        )

    def test_a_config_with_neither_root_does_not_end_the_session(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        os.environ["FORGE_E2E_RECORD"] = str(_record_path(tmp_path, "noroot"))
        os.environ["FORGE_E2E_EXECUTION_ID"] = "noroot"
        try:
            session = _Session()
            session.pytest_sessionstart(SimpleNamespace(config=SimpleNamespace()))
        finally:
            os.environ.pop("FORGE_E2E_RECORD", None)
            os.environ.pop("FORGE_E2E_EXECUTION_ID", None)

        assert session.recorder is not None
        assert session.recorder.root == elsewhere


class TestTheSessionRoot:
    def test_the_root_comes_from_rootpath_even_when_the_cwd_differs(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        os.environ["FORGE_E2E_RECORD"] = str(_record_path(tmp_path, "rootpath"))
        os.environ["FORGE_E2E_EXECUTION_ID"] = "rootpath"
        try:
            session = _Session()
            session.pytest_sessionstart(SimpleNamespace(config=SimpleNamespace(rootpath=str(repo))))
        finally:
            os.environ.pop("FORGE_E2E_RECORD", None)
            os.environ.pop("FORGE_E2E_EXECUTION_ID", None)

        assert session.recorder.root == repo
        assert session.recorder.root != elsewhere

    def test_a_run_that_writes_no_warning_stays_quiet(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        os.environ["FORGE_E2E_RECORD"] = str(_record_path(tmp_path, "quiet"))
        os.environ["FORGE_E2E_EXECUTION_ID"] = "quiet"
        try:
            session = _Session()
            session.pytest_sessionstart(SimpleNamespace(config=SimpleNamespace(rootpath=str(repo))))
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                session.pytest_sessionfinish(None, 0)
        finally:
            os.environ.pop("FORGE_E2E_RECORD", None)
            os.environ.pop("FORGE_E2E_EXECUTION_ID", None)

        assert caught == []


class TestAFailedWriteLeavesNothingBehind:
    def _recorder(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        recorder = Recorder(_record_path(tmp_path, "failed"), "failed", repo)
        recorder.status = 0
        return recorder, recorder.out_path

    def test_a_replace_that_fails_removes_the_temporary_file(self, tmp_path, monkeypatch):
        import os as _os

        recorder, record = self._recorder(tmp_path)

        def refuse(*args, **kwargs):
            raise OSError("no room")

        monkeypatch.setattr(_os, "replace", refuse)

        assert recorder.write() is False
        assert record.exists() is False
        assert [p.name for p in record.parent.iterdir()] == []

    def test_a_flush_that_raises_something_else_still_cleans_up(self, tmp_path, monkeypatch):
        import os as _os

        recorder, record = self._recorder(tmp_path)

        def refuse(*args, **kwargs):
            raise ValueError("not encodable")

        monkeypatch.setattr(_os, "fsync", refuse)

        assert recorder.write() is False
        assert [p.name for p in record.parent.iterdir()] == []


class TestTheSessionRootIsOnlyReplacedWhenItIsMissing:
    def test_an_empty_rootpath_is_not_replaced_by_rootdir(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        os.environ["FORGE_E2E_RECORD"] = str(_record_path(tmp_path, "emptyroot"))
        os.environ["FORGE_E2E_EXECUTION_ID"] = "emptyroot"
        try:
            session = _Session()
            session.pytest_sessionstart(
                SimpleNamespace(config=SimpleNamespace(rootpath="", rootdir=str(repo)))
            )
        finally:
            os.environ.pop("FORGE_E2E_RECORD", None)
            os.environ.pop("FORGE_E2E_EXECUTION_ID", None)

        # An empty path is the current directory, not a signal to use rootdir.
        assert session.recorder.root == Path(".")
        assert session.recorder.root.resolve() == elsewhere
        assert session.recorder.root.resolve() != repo


class TestTheSnapshotIsCheckedAgainAtTheEnd:
    """The start snapshot says what the session began with; a test that edits a
    tracked file and leaves it means the record would describe a tree that no
    longer exists."""

    def _recorder(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        recorder = Recorder(_record_path(tmp_path, "drift"), "drift", repo)
        recorder.status = 0
        return recorder, repo

    def test_a_tracked_edit_after_the_start_refuses_the_record(self, tmp_path):
        recorder, repo = self._recorder(tmp_path)
        (repo / "tests/test_ok.py").write_text(_PASSING + "\n# edited\n", encoding="utf-8")

        assert recorder.matches_its_snapshot() is False
        assert recorder.write() is False
        assert recorder.out_path.exists() is False

    def test_a_commit_made_after_the_start_refuses_the_record(self, tmp_path):
        recorder, repo = self._recorder(tmp_path)
        (repo / "later.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "later")

        assert recorder.matches_its_snapshot() is False
        assert recorder.write() is False

    def test_an_untracked_file_is_not_drift(self, tmp_path):
        # A run leaves caches and reports behind; none of that is part of the
        # commit the record names.
        recorder, repo = self._recorder(tmp_path)
        (repo / "junk.txt").write_text("x\n", encoding="utf-8")

        assert recorder.matches_its_snapshot() is True
        assert recorder.write() is True

    def test_a_tree_that_did_not_move_is_accepted(self, tmp_path):
        recorder, _ = self._recorder(tmp_path)

        assert recorder.matches_its_snapshot() is True
        assert recorder.write() is True

    def test_a_run_that_started_dirty_stays_refused_when_it_ends_clean(self, tmp_path):
        # Cleaning the tree mid-run is drift too: the start snapshot said dirty.
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        (repo / "tests/test_ok.py").write_text(_PASSING + "\n# edited\n", encoding="utf-8")
        recorder = Recorder(_record_path(tmp_path, "cleaned"), "cleaned", repo)
        recorder.status = 0
        _git(repo, "checkout", "--", "tests/test_ok.py")

        assert recorder.dirty is True
        assert recorder.matches_its_snapshot() is False
        assert recorder.write() is False


class TestASubtestInsideAFixtureIsNotTheTestBody:
    def test_a_skip_after_a_fixture_subtest_records_no_file(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(
            repo,
            {
                "tests/test_sub.py": """import pytest


@pytest.fixture
def prepared(subtests):
    with subtests.test("inside the fixture"):
        assert True
    pytest.skip("body skipped after the fixture ran")


def test_body(prepared):
    assert True
""",
            },
        )
        record = _record_path(tmp_path, "subtest")

        finished = _run_pytest(repo, record, "subtest")

        assert "1 subtests passed" in finished.stdout
        assert record.exists()
        document = json.loads(record.read_text(encoding="utf-8"))
        assert document["executed_files"] == []
        assert document["complete"] is True

    def test_a_silenced_warning_is_still_printed(self, tmp_path):
        # The opposite filter setting: the caller asked for warnings to be
        # dropped, and the diagnostic must survive that too.
        repo = tmp_path / "repo"
        commit = _make_repo(
            repo,
            {
                "pytest.ini": "[pytest]\nasyncio_default_fixture_loop_scope = function\n",
                "tests/test_ok.py": """from pathlib import Path


def test_edits_a_tracked_file():
    Path("tracked.txt").write_text("changed by the test\\n", encoding="utf-8")
    assert True
""",
                "tracked.txt": "original\n",
            },
        )
        record = _record_path(tmp_path, "quiet")

        finished = _run_pytest(
            repo,
            record,
            "quiet",
            extra_args=(
                "-W",
                "ignore::UserWarning",
                "-W",
                "default::pytest.PytestConfigWarning",
                "-W",
                "default::pytest.PytestDeprecationWarning",
            ),
        )

        assert finished.returncode == 0
        assert record.exists() is False
        assert (
            "the execution record was asked for but not written: %s (the tree no longer matches %s)"
            % (record, commit)
        ) in finished.stdout + finished.stderr

    def test_warnings_as_errors_do_not_change_the_exit_code(self, tmp_path):
        # The run refuses the record, and the diagnostic must not turn that into
        # a failure: `-W error` promotes every warning it is told about.
        repo = tmp_path / "repo"
        commit = _make_repo(
            repo,
            {
                "pytest.ini": "[pytest]\nasyncio_default_fixture_loop_scope = function\n",
                "tests/test_ok.py": """from pathlib import Path


def test_edits_a_tracked_file():
    Path("tracked.txt").write_text("changed by the test\\n", encoding="utf-8")
    assert True
""",
                "tracked.txt": "original\n",
            },
        )
        record = _record_path(tmp_path, "werror")

        # pytest's own config and deprecation warnings would otherwise be the
        # ones escalated; keep them informative and let ours be the only error.
        finished = _run_pytest(
            repo,
            record,
            "werror",
            extra_args=(
                "-W",
                "error",
                "-W",
                "default::pytest.PytestConfigWarning",
                "-W",
                "default::pytest.PytestDeprecationWarning",
            ),
        )

        # The channel is pytest's business: what this feature promises is that
        # the caller can see the message, in full, including why.
        assert finished.returncode == 0
        assert record.exists() is False
        assert (
            "the execution record was asked for but not written: %s (the tree no longer matches %s)"
            % (record, commit)
        ) in finished.stdout + finished.stderr

    def test_a_test_that_edits_a_tracked_file_leaves_no_record(self, tmp_path):
        # The fixture edits a tracked file and leaves the change in place: the
        # tree no longer matches the commit the record would name.
        repo = tmp_path / "repo"
        commit = _make_repo(
            repo,
            {
                "tests/test_ok.py": """from pathlib import Path


def test_edits_a_tracked_file():
    Path("tracked.txt").write_text("changed by the test\\n", encoding="utf-8")
    assert True
""",
                "tracked.txt": "original\n",
            },
        )
        record = _record_path(tmp_path, "drifted")

        finished = _run_pytest(repo, record, "drifted")

        assert "1 passed" in finished.stdout
        assert record.exists() is False
        # Which channel carries the message is pytest's business; the reason is
        # not: a write that failed for another cause would leave the tree clean.
        assert (
            "the execution record was asked for but not written: %s (the tree no longer matches %s)"
            % (record, commit)
        ) in finished.stdout + finished.stderr


class TestTheDiagnosticLeavesTheCallersFiltersAlone:
    @pytest.mark.parametrize("policy", ["error", "ignore"])
    def test_another_threads_filter_is_unchanged_during_emission(self, tmp_path, monkeypatch, policy):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        record = blocker / "run.json"
        monkeypatch.setenv("FORGE_E2E_RECORD", str(record))
        monkeypatch.setenv("FORGE_E2E_EXECUTION_ID", "filters")
        session = _Session()
        session.pytest_sessionstart(SimpleNamespace(config=SimpleNamespace(rootpath=str(repo))))
        real_warn = warnings.warn
        outcomes = []
        emitted = []
        filters_during = []
        workers = []

        def emit_unrelated():
            try:
                real_warn("unrelated-thread", RuntimeWarning, stacklevel=2)
            except RuntimeWarning:
                outcomes.append("raised")
            else:
                displayed = any(category is RuntimeWarning for _, category in emitted)
                outcomes.append("displayed" if displayed else "ignored")

        def probe_policy():
            if filters_during:
                return
            filters_during.append(list(warnings.filters))
            worker = threading.Thread(target=policy_context.run, args=(emit_unrelated,))
            workers.append(worker)
            worker.start()
            worker.join(10)

        def warn_with_probe(message, *args, **kwargs):
            # Probe before warn takes its display lock. Waiting from its
            # display callback would measure that lock, not the warning policy.
            probe_policy()
            return real_warn(message, *args, **kwargs)

        def show_with_probe(message, category, filename, lineno, file=None, line=None):
            if str(message).startswith("the execution record was asked for"):
                # Direct display takes no warning-selection lock. The old
                # warn route has already probed at its pre-lock boundary.
                probe_policy()
            emitted.append((str(message), category))

        with warnings.catch_warnings():
            warnings.simplefilter(policy)
            before = list(warnings.filters)
            # Newer Python can isolate warning filters per context. Capture
            # the unrelated caller's context before entering the diagnostic.
            policy_context = contextvars.copy_context()
            monkeypatch.setattr(warnings, "warn", warn_with_probe)
            monkeypatch.setattr(warnings, "showwarning", show_with_probe)
            try:
                session.pytest_sessionfinish(None, 0)
            finally:
                for worker in workers:
                    worker.join(10)
            assert all(not worker.is_alive() for worker in workers)
            assert warnings.filters == before

        assert outcomes == ["raised" if policy == "error" else "ignored"]
        assert filters_during == [before]
        assert emitted == [
            (
                "the execution record was asked for but not written: %s "
                "(the record could not be written)" % record,
                UserWarning,
            )
        ]
        assert record.exists() is False

    def test_the_filter_list_is_the_same_afterwards(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        record = blocker / "run.json"
        monkeypatch.setenv("FORGE_E2E_RECORD", str(record))
        monkeypatch.setenv("FORGE_E2E_EXECUTION_ID", "filters")
        session = _Session()
        session.pytest_sessionstart(SimpleNamespace(config=SimpleNamespace(rootpath=str(repo))))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("error")
            before = list(warnings.filters)
            session.pytest_sessionfinish(None, 0)
            assert warnings.filters == before

        assert len(caught) == 1
        assert caught[0].category is UserWarning
        assert str(caught[0].message) == (
            "the execution record was asked for but not written: %s "
            "(the record could not be written)" % record
        )
        assert record.exists() is False


class TestEveryRefusalSaysWhy:
    """The message tells the caller which of the ways it declined happened."""

    def _run(self, tmp_path, repo, *, status: int | None = 0, record=None):
        record = record or _record_path(tmp_path, "why")
        with pytest.MonkeyPatch.context() as environment:
            environment.setenv("FORGE_E2E_RECORD", str(record))
            environment.setenv("FORGE_E2E_EXECUTION_ID", "why")
            session = _Session()
            session.pytest_sessionstart(SimpleNamespace(config=SimpleNamespace(rootpath=str(repo))))
            with pytest.warns(UserWarning) as caught:
                session.pytest_sessionfinish(None, status)
        return str(caught[0].message), record

    @pytest.mark.parametrize("previous", [None, "", "caller-value"])
    def test_a_helper_restores_preexisting_environment_values(self, tmp_path, monkeypatch, previous):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        names = ("FORGE_E2E_RECORD", "FORGE_E2E_EXECUTION_ID")
        for name in names:
            if previous is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, previous)
        self._run(tmp_path, repo, status=None)
        assert {name: os.environ.get(name) for name in names} == dict.fromkeys(names, previous)

    def test_a_helper_restores_environment_when_the_hook_raises(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        monkeypatch.setenv("FORGE_E2E_RECORD", "caller-record.json")
        monkeypatch.setenv("FORGE_E2E_EXECUTION_ID", "caller-execution")

        def fail_start(self, session):
            raise RuntimeError("hook failed")

        monkeypatch.setattr(_Session, "pytest_sessionstart", fail_start)
        with pytest.raises(RuntimeError, match="hook failed"):
            self._run(tmp_path, repo)
        assert os.environ["FORGE_E2E_RECORD"] == "caller-record.json"
        assert os.environ["FORGE_E2E_EXECUTION_ID"] == "caller-execution"

    def test_a_fresh_recorder_has_no_refusal_yet(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})

        assert Recorder(_record_path(tmp_path, "fresh"), "fresh", repo).refusal == ""

    def test_a_write_that_fails_after_the_temp_file_says_so(self, tmp_path):
        # The record path is a directory: the temporary file is created, the
        # replace fails, and the temporary file must not survive.
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "occupied")
        record.parent.mkdir(parents=True, exist_ok=True)
        record.mkdir()

        message, _ = self._run(tmp_path, repo, record=record)

        assert message == (
            "the execution record was asked for but not written: %s "
            "(the record could not be written)" % record
        )
        assert [x.name for x in record.parent.iterdir()] == ["occupied.json"]

    def test_a_run_that_did_not_finish_says_so(self, tmp_path):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})

        message, record = self._run(tmp_path, repo, status=None)

        assert message == (
            "the execution record was asked for but not written: %s (the run had not finished)" % record
        )
        assert record.exists() is False

    def test_a_snapshot_that_could_not_be_read_says_so(self, tmp_path):
        plain = tmp_path / "not-a-repo"
        plain.mkdir()

        message, record = self._run(tmp_path, plain)

        assert message == (
            "the execution record was asked for but not written: %s "
            "(the snapshot could not be read)" % record
        )
        assert record.exists() is False

    def test_a_moved_tree_names_the_commit_it_left(self, tmp_path):
        repo = tmp_path / "repo"
        commit = _make_repo(repo, {"tests/test_ok.py": _PASSING, "tracked.txt": "original\n"})
        record = _record_path(tmp_path, "moved")
        recorder = Recorder(record, "moved", repo)
        recorder.status = 0
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")

        assert recorder.write() is False
        assert recorder.refusal == "the tree no longer matches %s" % commit

    def test_a_half_read_snapshot_is_not_a_snapshot(self, tmp_path):
        # The commit read succeeded and the status read did not: still no record.
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        recorder = Recorder(_record_path(tmp_path, "half"), "half", repo)
        recorder.status = 0
        recorder.dirty = None

        assert recorder.write() is False
        assert recorder.refusal == "the snapshot could not be read"

    @pytest.mark.parametrize("missing", ["rev-parse", "status"])
    @pytest.mark.parametrize("phase", ["start", "finish"])
    def test_one_unanswered_git_call_is_enough(self, tmp_path, monkeypatch, missing, phase):
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        record = _record_path(tmp_path, "one")
        real_git = e2e_record._git

        def half_answer(root, *args):
            return None if args[0] == missing else real_git(root, *args)

        monkeypatch.setenv("FORGE_E2E_RECORD", str(record))
        monkeypatch.setenv("FORGE_E2E_EXECUTION_ID", "one")
        if phase == "start":
            monkeypatch.setattr(e2e_record, "_git", half_answer)
        session = _Session()
        session.pytest_sessionstart(SimpleNamespace(config=SimpleNamespace(rootpath=str(repo))))
        if phase == "finish":
            monkeypatch.setattr(e2e_record, "_git", half_answer)
        with pytest.warns(UserWarning) as caught:
            session.pytest_sessionfinish(None, 0)

        reason = (
            "the snapshot could not be read"
            if phase == "start"
            else "the snapshot could not be read again"
        )
        assert str(caught[0].message) == (
            "the execution record was asked for but not written: %s (%s)" % (record, reason)
        )
        assert record.exists() is False

    def test_a_git_that_stops_answering_is_not_a_moved_tree(self, tmp_path, monkeypatch):
        # The second reading is what can fail; saying the tree moved would name
        # the wrong cause.
        repo = tmp_path / "repo"
        _make_repo(repo, {"tests/test_ok.py": _PASSING})
        recorder = Recorder(_record_path(tmp_path, "silent"), "silent", repo)
        recorder.status = 0
        monkeypatch.setattr(e2e_record, "_git", lambda *a, **k: None)

        assert recorder.snapshot_drift() == "the snapshot could not be read again"
        assert recorder.write() is False
        assert recorder.refusal == "the snapshot could not be read again"
        assert recorder.out_path.exists() is False
