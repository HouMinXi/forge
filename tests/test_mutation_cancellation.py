"""Cancellation remains control flow after process and workspace teardown."""

import asyncio
import builtins
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from code_forge import _mutation_process as process
from code_forge import mutation
from code_forge._mutation_workspace import MutationWorkspaceError
from code_forge.machine import mutation_result_verdict


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
@pytest.mark.parametrize("cleanup", ["complete", "incomplete", "invalid", "stuck", "signal-error",
                                     "second-interrupt", "signal-interrupt", "report-interrupt", "attribute-interrupt"])
def test_owner_keeps_original_cancellation_and_secondary_cleanup(monkeypatch, exception, cleanup):
    cancellation = exception("CALLER_CANCELLED")
    report = {"cleanup_complete": cleanup != "incomplete", "cancelled": True,
              "owned": [{"pid": 11, "start_ticks": 12, "remaining": cleanup == "incomplete"}]}
    calls = []

    class Owner:
        pid = 123

        def communicate(self, *args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise cancellation
            if cleanup == "stuck":
                raise subprocess.TimeoutExpired("owner", 7)
            if cleanup == "second-interrupt":
                raise KeyboardInterrupt("SECOND_INTERRUPT")
            return b"invalid" if cleanup == "invalid" else json.dumps(report).encode(), b"diagnostic"

        def send_signal(self, signum):
            assert signum == signal.SIGTERM
            if cleanup == "signal-error":
                raise PermissionError("SIGNAL_REFUSED")
            if cleanup == "signal-interrupt":
                raise KeyboardInterrupt("SECOND_INTERRUPT_AT_SIGNAL")

    original_loads = process.json.loads

    def load(raw):
        if cleanup == "report-interrupt":
            raise KeyboardInterrupt("SECOND_INTERRUPT_AT_REPORT")
        if cleanup == "attribute-interrupt":
            class Report(dict):
                def get(self, key, default=None):
                    if key == "cleanup_complete":
                        raise SystemExit("SECOND_INTERRUPT_AT_ATTRIBUTE")
                    return super().get(key, default)
            return Report(report)
        return original_loads(raw)

    monkeypatch.setattr(process.subprocess, "Popen", lambda *_a, **_k: Owner())
    monkeypatch.setattr(process.json, "loads", load)
    with pytest.raises(exception) as caught:
        process.run_owned_command(["fixture"], timeout=1)
    assert caught.value is cancellation
    assert cancellation.cleanup_complete is (cleanup == "complete")
    assert cancellation.ownership == (report if cleanup in ("complete", "incomplete", "attribute-interrupt") else {})
    if cleanup != "complete":
        assert cancellation.cleanup_error
        assert "mutation cleanup failed" in cancellation.__notes__[0]
    if len(calls) == 2:
        assert calls[1]["timeout"] == process._CLEANUP_SECONDS + 2


def test_owner_keeps_original_when_success_metadata_is_interrupted(monkeypatch):
    secondary = KeyboardInterrupt("SECOND_INTERRUPT_AT_METADATA")

    class Cancellation(KeyboardInterrupt):
        bound = False

        def __getattribute__(self, key):
            if key == "__dict__" and not self.bound:
                self.bound = True
                raise secondary
            return super().__getattribute__(key)

    cancellation = Cancellation("CALLER_CANCELLED")
    report = {"cleanup_complete": True, "cancelled": True}

    class Owner:
        pid = 123
        calls = 0

        def communicate(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise cancellation
            return json.dumps(report).encode(), b""

        def send_signal(self, _signum):
            pass

    monkeypatch.setattr(process.subprocess, "Popen", lambda *_a, **_k: Owner())
    with pytest.raises(Cancellation) as caught:
        process.run_owned_command(["fixture"], timeout=1)
    assert caught.value is cancellation
    assert cancellation.cleanup_complete is False
    assert cancellation.ownership == report
    assert cancellation.cleanup_evidence_error is secondary


@pytest.mark.parametrize("workspace", [False, True])
def test_failure_evidence_interruption_keeps_original_cancellation(tmp_path, monkeypatch, workspace):
    secondary = SystemExit("SECOND_INTERRUPT_AT_NOTE")
    cleanup = PermissionError("CLEANUP_FAILED")

    class Cancellation(KeyboardInterrupt):
        def add_note(self, _note):
            raise secondary

    cancellation = Cancellation("CALLER_CANCELLED")

    class Owner:
        pid = 123

        def communicate(self, *args, **kwargs):
            raise cancellation

        def send_signal(self, _signum):
            raise cleanup

    if workspace:
        def cancel(*_args, **_kwargs):
            raise cancellation

        def release(_workspace):
            raise cleanup

        monkeypatch.setattr(mutation, "_run_mutation", cancel)
        monkeypatch.setattr(mutation.MutationWorkspace, "release", release)
        def call():
            return mutation.run_mutation([], [], cwd=tmp_path)
    else:
        monkeypatch.setattr(process.subprocess, "Popen", lambda *_a, **_k: Owner())
        def call():
            return process.run_owned_command(["fixture"], timeout=1)
    with pytest.raises(Cancellation) as caught:
        call()
    assert caught.value is cancellation
    assert cancellation.cleanup_complete is False
    assert cancellation.cleanup_evidence_error is secondary
    assert getattr(cancellation, "workspace_cleanup_error" if workspace else "cleanup_error") is cleanup
    if not workspace:
        assert cancellation.__cause__ is cleanup


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
@pytest.mark.parametrize("transport,site", [(False, "report"), (True, "signal"),
                                           (True, "communicate"), (True, "report")])
def test_owner_preserves_new_cancellation_during_cleanup(monkeypatch, exception, transport, site):
    cancellation = exception("CANCEL_DURING_CLEANUP")
    primary = RuntimeError("TRANSPORT_FAILURE") if transport else None
    report = {"cleanup_complete": True, "cancelled": False}

    class Owner:
        pid = 123
        calls = 0

        def communicate(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1 and primary is not None:
                raise primary
            if site == "communicate":
                raise cancellation
            return json.dumps(report).encode(), b""

        def send_signal(self, _signum):
            if site == "signal":
                raise cancellation

    original_loads = process.json.loads

    def load(raw):
        if site == "report":
            raise cancellation
        return original_loads(raw)

    monkeypatch.setattr(process.subprocess, "Popen", lambda *_a, **_k: Owner())
    monkeypatch.setattr(process.json, "loads", load)
    with pytest.raises(exception) as caught:
        process.run_owned_command(["fixture"], timeout=1)
    assert caught.value is cancellation and cancellation.cleanup_complete is False
    assert cancellation.ownership == {}
    if primary is not None:
        assert cancellation.cleanup_error is primary
        assert cancellation.__cause__ is primary
        assert "TRANSPORT_FAILURE" in cancellation.__notes__[0]


@pytest.mark.parametrize("cleanup", ["complete", "signal-error", "invalid", "stuck"])
def test_ordinary_owner_transport_failure_is_not_cancellation(monkeypatch, cleanup):
    failure = RuntimeError("TRANSPORT_FAILURE")
    calls = []

    class Owner:
        pid = 123

        def communicate(self, *args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise failure
            if cleanup == "stuck":
                raise subprocess.TimeoutExpired("owner", 7)
            if cleanup == "invalid":
                return b"invalid", b"diagnostic"
            return json.dumps({"cleanup_complete": True, "cancelled": True}).encode(), b""

        def send_signal(self, signum):
            assert signum == signal.SIGTERM
            if cleanup == "signal-error":
                raise PermissionError("SIGNAL_REFUSED")

    monkeypatch.setattr(process.subprocess, "Popen", lambda *_a, **_k: Owner())
    with pytest.raises(process.MutationProcessError) as caught:
        process.run_owned_command(["fixture"], timeout=1)
    assert caught.value.cleanup_complete is (cleanup == "complete")
    assert caught.value.__cause__ is failure
    if cleanup == "complete":
        assert "communication failed" in str(caught.value)
    else:
        assert caught.value.cleanup_error
        assert "TRANSPORT_FAILURE" in str(caught.value) and "cleanup:" in str(caught.value)


def _inventory(root):
    target = root / "mutants/src/calc.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("def x_allows__mutmut_1(n):\n    return n >= 1\n")
    target.with_name(target.name + ".meta").write_text(
        json.dumps({"exit_code_by_key": {"calc.x_allows__mutmut_1": 1}})
    )


@pytest.mark.parametrize("site", ["deletion", "baseline", "retry", "probe", "run", "results"])
@pytest.mark.parametrize("complete", [True, False, None, 0, 1, "true", "missing"])
def test_public_mutation_preserves_cancellation_and_workspace_truth(tmp_path, monkeypatch, site, complete):
    (tmp_path / "src").mkdir()
    if site == "deletion":
        (tmp_path / ".git").mkdir()
    else:
        (tmp_path / "src/calc.py").write_text("value = 1\n")
    original = b"[user]\nretained = true\n"
    (tmp_path / "setup.cfg").write_bytes(original)
    (tmp_path / "mutants").mkdir()
    (tmp_path / "mutants/old").write_bytes(b"USER_MIRROR")
    cancellation = KeyboardInterrupt("CALLER_CANCELLED")
    report = {"cleanup_complete": complete, "cancelled": True}
    if complete != "missing":
        cancellation.cleanup_complete = complete
    cancellation.ownership = report
    commands = []

    def execute(argv, **kwargs):
        current = ("deletion" if argv[0] == "git" else "run" if "run" in argv else
                   "results" if "results" in argv else "probe" if "-c" in argv else "baseline")
        commands.append(current)
        if current == "run":
            _inventory(tmp_path)
        if site == "retry" and current == "baseline" and commands.count("baseline") == 1:
            return subprocess.CompletedProcess(argv, 1, "", "No module named pytest")
        if current == site or (site == "retry" and current == "baseline"):
            raise cancellation
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(mutation, "run_owned_command", execute)
    if site == "retry":
        monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "venv"))
    evidence = {}
    with pytest.raises(KeyboardInterrupt) as caught:
        mutation.run_mutation(["src/calc.py"], [sys.executable, "-m", "pytest"],
                              cwd=tmp_path, _evidence=evidence)
    assert caught.value is cancellation
    assert evidence["cancelled"] and evidence["process_failure"] == report
    assert not evidence["baseline_passed"] and not evidence["completed_measurement"]
    assert commands[-1] == ("baseline" if site == "retry" else site)
    if site == "retry":
        assert commands.count("baseline") == 2
    if site != "deletion":
        journal = json.loads((tmp_path / ".code-forge/mutation-owner.lock").read_text())
        assert journal["phase"] == ("complete" if complete is True else "incomplete")
    if complete is True or site not in ("run", "results"):
        assert (tmp_path / "setup.cfg").read_bytes() == original
        assert (tmp_path / "mutants/old").read_bytes() == b"USER_MIRROR"
    else:
        assert "configuration retained" not in str(cancellation)
        assert isinstance(cancellation.workspace_cleanup_error, MutationWorkspaceError)
        assert "retained for recovery" in str(cancellation.workspace_cleanup_error)
        assert "quarantine=" in evidence["cleanup_error"]
        assert (tmp_path / "setup.cfg").read_bytes() != original
        assert Path(journal["quarantine"], "old").read_bytes() == b"USER_MIRROR"


@pytest.mark.parametrize("exception", [MutationWorkspaceError, KeyboardInterrupt, SystemExit])
def test_workspace_release_failure_keeps_cancellation_primary(tmp_path, monkeypatch, exception):
    cancellation = KeyboardInterrupt("CALLER_CANCELLED")
    cleanup = exception("WORKSPACE_RELEASE_FAILED")

    def cancel(*_args, **_kwargs):
        raise cancellation

    def release(_workspace):
        raise cleanup

    monkeypatch.setattr(mutation, "_run_mutation", cancel)
    monkeypatch.setattr(mutation.MutationWorkspace, "release", release)
    evidence = {}
    with pytest.raises(KeyboardInterrupt) as caught:
        mutation.run_mutation([], [], cwd=tmp_path, _evidence=evidence)
    assert caught.value is cancellation
    assert not cancellation.cleanup_complete
    assert cancellation.workspace_cleanup_error is cleanup
    assert evidence["cleanup_error"] == "WORKSPACE_RELEASE_FAILED"
    assert not evidence["baseline_passed"] and not evidence["completed_measurement"]


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
@pytest.mark.parametrize("secondary_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("site", ["note-format", "evidence-format", "evidence-write", "binding",
                                  "workspace-write", "release-access", "proof-write"])
def test_workspace_reporting_interruption_keeps_original(tmp_path, monkeypatch, exception, secondary_type, site):
    cancellation = exception("FIRST_CANCEL")
    cancellation.cleanup_complete = True
    cancellation.ownership = {"cleanup_complete": True}
    secondary = secondary_type("SECOND_CANCEL")
    fired = False
    released = []

    def interrupt():
        nonlocal fired
        if not fired:
            fired = True
            raise secondary

    class CleanupError(OSError):
        strings = 0

        def __str__(self):
            self.strings += 1
            if (site == "note-format" and self.strings == 1 or
                    site == "evidence-format" and self.strings == 2):
                interrupt()
            return "JOURNAL_WRITE_FAILED"

    cleanup = CleanupError()

    class Evidence(dict):
        def __setitem__(self, key, value):
            if site == "evidence-write" and key == "cleanup_error":
                interrupt()
            return super().__setitem__(key, value)

        def update(self, *args, **kwargs):
            if site == "proof-write" and "cancelled" in kwargs:
                interrupt()
            return super().update(*args, **kwargs)

    class Workspace(mutation.MutationWorkspace):
        def __setattr__(self, key, value):
            if site == "workspace-write" and released and key == "cleanup_complete" and value is False:
                interrupt()
            super().__setattr__(key, value)

        def __getattribute__(self, key):
            if site == "release-access" and key == "release":
                interrupt()
            return super().__getattribute__(key)

        def release(self):
            released.append(self.cleanup_complete)
            if site != "proof-write":
                raise cleanup

    bind = mutation._bind_cancellation_evidence

    def bind_evidence(error, **kwargs):
        if site == "binding" and "note" in kwargs:
            interrupt()
        return bind(error, **kwargs)

    def cancel(*_args, **_kwargs):
        raise cancellation

    monkeypatch.setattr(mutation, "MutationWorkspace", Workspace)
    monkeypatch.setattr(mutation, "_bind_cancellation_evidence", bind_evidence)
    monkeypatch.setattr(mutation, "_run_mutation", cancel)
    evidence = Evidence()
    with pytest.raises(exception) as caught:
        mutation.run_mutation([], [], cwd=tmp_path, _evidence=evidence)
    assert caught.value is cancellation and fired
    assert cancellation.cleanup_complete is False
    if site != "release-access":
        assert cancellation.cleanup_evidence_error is secondary
    assert evidence["cancelled"] and not evidence["baseline_passed"] and not evidence["completed_measurement"]
    if site == "proof-write":
        assert released == [False] and evidence["process_failure"] == {}
    elif site == "release-access":
        assert released == [] and cancellation.workspace_cleanup_error is secondary
    else:
        assert released == [True] and cancellation.workspace_cleanup_error is cleanup


@pytest.mark.parametrize("release_error", [None, OSError("JOURNAL_FAILED"), MutationWorkspaceError("RECOVERY")])
def test_workspace_reporting_preserves_ordinary_outcome_policy(tmp_path, monkeypatch, release_error):
    releases = []

    def release(_workspace):
        releases.append(True)
        if release_error is not None:
            raise release_error

    monkeypatch.setattr(mutation.MutationWorkspace, "release", release)
    monkeypatch.setattr(mutation, "_run_mutation", lambda *_args, **_kwargs: ([], []))
    evidence = {}
    findings, infra = mutation.run_mutation([], [], cwd=tmp_path, _evidence=evidence)
    assert releases == [True]
    if release_error is None:
        assert findings == [] and infra == [] and evidence == {}
    else:
        assert [finding.id for finding in findings] == ["MUTATION_ERROR"]
        assert infra == [str(release_error)]
        assert not evidence["baseline_passed"] and not evidence["completed_measurement"]


@pytest.mark.parametrize("attribute", ["cleanup_complete", "ownership"])
def test_public_cleanup_evidence_read_interruption_retains_recovery(tmp_path, monkeypatch, attribute):
    secondary = SystemExit("SECOND_INTERRUPT_AT_EVIDENCE_READ")

    class Cancellation(KeyboardInterrupt):
        interrupted = False

        def __getattribute__(self, key):
            if key == attribute and not self.interrupted:
                self.interrupted = True
                raise secondary
            return super().__getattribute__(key)

    cancellation = Cancellation("CALLER_CANCELLED")
    cancellation.cleanup_complete = False
    cancellation.ownership = {"cleanup_complete": False}
    original = b"[user]\nretained = true\n"
    (tmp_path / "setup.cfg").write_bytes(original)
    (tmp_path / "mutants").mkdir()
    (tmp_path / "mutants/old").write_bytes(b"USER_MIRROR")

    def cancel(*_args, _workspace, **_kwargs):
        _workspace.acquire()
        _workspace.prepare()
        _workspace.install_configs(b"# managed-by-code-forge-mutation\n[mutmut]\n", lambda _data: None)
        raise cancellation

    monkeypatch.setattr(mutation, "_run_mutation", cancel)
    evidence = {}
    with pytest.raises(Cancellation) as caught:
        mutation.run_mutation([], [], cwd=tmp_path, _evidence=evidence)
    assert caught.value is cancellation
    assert cancellation.cleanup_complete is False
    assert cancellation.cleanup_evidence_error is secondary
    assert evidence["cancelled"] and not evidence["completed_measurement"] and not evidence["baseline_passed"]
    journal = json.loads((tmp_path / ".code-forge/mutation-owner.lock").read_text())
    assert journal["phase"] == "incomplete"
    assert (tmp_path / "setup.cfg").read_bytes() != original
    assert (tmp_path / journal["quarantine"] / "old").read_bytes() == b"USER_MIRROR"


@pytest.mark.parametrize("exception", [RuntimeError, SystemExit])
def test_unexpected_workspace_release_error_keeps_existing_exception_policy(tmp_path, monkeypatch, exception):
    failure = exception("UNEXPECTED_RELEASE_FAILURE")

    def release(_workspace):
        raise failure

    monkeypatch.setattr(mutation, "_run_mutation", lambda *_args, **_kwargs: ([], []))
    monkeypatch.setattr(mutation.MutationWorkspace, "release", release)
    evidence = {}
    with pytest.raises(exception) as caught:
        mutation.run_mutation([], [], cwd=tmp_path, _evidence=evidence)
    assert caught.value is failure
    if exception is RuntimeError:
        assert not evidence.get("cancelled", False)
    else:
        assert evidence["cancelled"]


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_detached_cancellation_publishes_error_and_reraises(tmp_path, monkeypatch, run_detached_payload, exception):
    captured = {}

    class Launcher:
        def wait(self, **_kwargs):
            return 0

    def launch(argv, **_kwargs):
        captured["script"] = argv[2]
        return Launcher()

    monkeypatch.setattr(mutation.subprocess, "Popen", launch)
    result = tmp_path / "mutation-result.json"
    assert mutation.launch_detached_mutation(["src/calc.py"], ["pytest"], tmp_path, result)
    cancellation = exception("CALLER_CANCELLED")
    cancellation.cleanup_complete = False

    def cancel(**kwargs):
        kwargs["_evidence"].update(process_failure={"cleanup_complete": False}, cleanup_error="RECOVERY")
        raise cancellation

    monkeypatch.setattr(mutation, "run_mutation", cancel)
    with pytest.raises(exception) as caught:
        run_detached_payload(captured["script"])
    assert caught.value is cancellation
    data = json.loads(result.read_text())
    assert data["status"] == "error" and data["cancelled"]
    assert data["cancellation_type"] == exception.__name__
    assert data["cleanup_complete"] is False and data["cleanup_error"] == "RECOVERY"
    assert not data["baseline_passed"] and not data["completed_measurement"] and not data["survivors"]
    assert mutation_result_verdict(data) is None


@pytest.mark.parametrize("site", ["callsite", "cleanup", "evidence", "message", "assembly", "open", "dump", "close"])
@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
def test_detached_secondary_publication_keeps_original_and_terminal_error(
    tmp_path, monkeypatch, run_detached_payload, site, exception,
):
    captured = {}

    class Launcher:
        def wait(self, **_kwargs):
            return 0

    def launch(argv, **_kwargs):
        captured["script"] = argv[2]
        return Launcher()

    monkeypatch.setattr(mutation.subprocess, "Popen", launch)
    result = tmp_path / "mutation-result.json"
    assert mutation.launch_detached_mutation([], [], tmp_path, result)
    secondary = SystemExit("SECOND_PUBLICATION_INTERRUPT")
    fired = False

    def interrupt():
        nonlocal fired
        if not fired:
            fired = True
            raise secondary

    class Cancellation(exception):
        def __getattribute__(self, key):
            if site == "cleanup" and key == "cleanup_complete":
                interrupt()
            return super().__getattribute__(key)

        def __str__(self):
            if site == "message":
                interrupt()
            return super().__str__()

    cancellation = Cancellation("FIRST_CANCELLATION")
    cancellation.cleanup_complete = True

    def cancel(**kwargs):
        kwargs["_evidence"]["process_failure"] = {"cleanup_complete": True}
        raise cancellation

    monkeypatch.setattr(mutation, "run_mutation", cancel)
    publisher = mutation._publish_mutation_error

    class Evidence(dict):
        def get(self, *args):
            if site == "evidence":
                interrupt()
            return super().get(*args)

    class Data(dict):
        def update(self, *args, **kwargs):
            if site == "assembly":
                interrupt()
            return super().update(*args, **kwargs)

    def publish(path, data, error, evidence, publication_error=None):
        if site == "callsite":
            interrupt()
        return publisher(path, Data(data), error, Evidence(evidence), publication_error)

    monkeypatch.setattr(mutation, "_publish_mutation_error", publish)
    real_open = builtins.open

    class Writer:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self.stream.__enter__()

        def __exit__(self, *args):
            answer = self.stream.__exit__(*args)
            if site == "close":
                interrupt()
            return answer

    def open_result(*args, **kwargs):
        if site == "open":
            interrupt()
        return Writer(real_open(*args, **kwargs))

    monkeypatch.setattr(mutation, "open", open_result, raising=False)
    dump = mutation.json.dump

    def write(data, stream, *args, **kwargs):
        if site == "dump" and data.get("status") == "error":
            interrupt()
        return dump(data, stream, *args, **kwargs)

    monkeypatch.setattr(mutation.json, "dump", write)
    with pytest.raises(exception) as caught:
        run_detached_payload(captured["script"])
    assert caught.value is cancellation
    assert fired and cancellation.detached_publication_error is secondary
    assert cancellation.cleanup_complete is True
    data = json.loads(result.read_text())
    assert data["status"] == "error" and data["cancelled"]
    assert data["cleanup_complete"] is True and data["process_failure"]["cleanup_complete"]
    assert "SECOND_PUBLICATION_INTERRUPT" in data["publication_error"]
    assert not data["baseline_passed"] and not data["completed_measurement"] and data["survivors"] == []
    assert mutation_result_verdict(data) is None


@pytest.mark.parametrize("exception", [KeyboardInterrupt, RuntimeError])
def test_detached_persistent_write_failure_reports_honest_disk_and_keeps_policy(
    tmp_path, monkeypatch, run_detached_payload, exception,
):
    captured = {}

    class Launcher:
        def wait(self, **_kwargs):
            return 0

    def launch(argv, **_kwargs):
        captured["script"] = argv[2]
        return Launcher()

    monkeypatch.setattr(mutation.subprocess, "Popen", launch)
    result = tmp_path / "mutation-result.json"
    assert mutation.launch_detached_mutation([], [], tmp_path, result)
    primary = exception("ORIGINAL_FAILURE")
    failure = OSError("RESULT_UNWRITABLE")
    writes = []

    def fail(**_kwargs):
        raise primary

    def open_result(*_args, **_kwargs):
        writes.append(True)
        raise failure

    monkeypatch.setattr(mutation, "run_mutation", fail)
    monkeypatch.setattr(mutation, "open", open_result, raising=False)
    if exception is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt) as caught:
            run_detached_payload(captured["script"])
        assert caught.value is primary
    else:
        run_detached_payload(captured["script"])
    assert len(writes) == 2
    assert primary.detached_publication_error is failure
    assert primary.detached_publication_retry_error is failure
    data = json.loads(result.read_text())
    assert data["status"] == "running" and not data.get("completed_measurement")
    assert mutation_result_verdict(data) is None


@pytest.mark.parametrize("cancel_publication", [False, True])
def test_detached_ordinary_error_and_new_publication_cancellation_keep_policy(
    tmp_path, monkeypatch, run_detached_payload, cancel_publication,
):
    captured = {}

    class Launcher:
        def wait(self, **_kwargs):
            return 0

    def launch(argv, **_kwargs):
        captured["script"] = argv[2]
        return Launcher()

    monkeypatch.setattr(mutation.subprocess, "Popen", launch)
    result = tmp_path / "mutation-result.json"
    assert mutation.launch_detached_mutation([], [], tmp_path, result)
    ordinary = RuntimeError("ORDINARY_FAILURE")
    cancellation = KeyboardInterrupt("PUBLICATION_CANCELLED")
    calls = []

    def fail(**_kwargs):
        raise ordinary

    def open_result(*args, **kwargs):
        calls.append(True)
        if cancel_publication and len(calls) == 1:
            raise cancellation
        return builtins.open(*args, **kwargs)

    monkeypatch.setattr(mutation, "run_mutation", fail)
    monkeypatch.setattr(mutation, "open", open_result, raising=False)
    if cancel_publication:
        with pytest.raises(KeyboardInterrupt) as caught:
            run_detached_payload(captured["script"])
        assert caught.value is cancellation and cancellation.__cause__ is ordinary
    else:
        run_detached_payload(captured["script"])
    data = json.loads(result.read_text())
    assert data["status"] == "error" and data["cancelled"] is cancel_publication
    assert data["cleanup_complete"] is None
    assert not data["baseline_passed"] and not data["completed_measurement"] and not data["survivors"]
    assert mutation_result_verdict(data) is None


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("site", ["note", "binder", "secondary-binding", "retry-note", "retry-binder",
                                 "retry-secondary-binding", "retry-promotion", "diagnostic-promotion"])
def test_detached_publication_promotion_survives_secondary_reporting(
    tmp_path, monkeypatch, run_detached_payload, exception, site,
):
    captured = {}

    class Launcher:
        def wait(self, **_kwargs):
            return 0

    def launch(argv, **_kwargs):
        captured["script"] = argv[2]
        return Launcher()

    monkeypatch.setattr(mutation.subprocess, "Popen", launch)
    result = tmp_path / "mutation-result.json"
    assert mutation.launch_detached_mutation([], [], tmp_path, result)
    ordinary = RuntimeError("ORDINARY_MUTATION_FAILURE")
    secondary = exception("SECOND_REPORTING_CANCELLATION")
    fired = []

    class Cancellation(exception):
        def __str__(self):
            if site in ("note", "retry-promotion") and not fired:
                fired.append("note")
                raise secondary
            return super().__str__()

    first = Cancellation("FIRST_PUBLICATION_CANCELLATION")

    class PublicationFailure(OSError):
        def __str__(self):
            if site == "retry-note" and not fired:
                fired.append("retry-note")
                raise secondary
            if site == "diagnostic-promotion" and not fired:
                fired.append("diagnostic-promotion")
                raise first
            return super().__str__()

    failure = PublicationFailure("PUBLICATION_UNWRITABLE")
    publisher = mutation._publish_mutation_error
    calls = []

    def publish(*args):
        calls.append(True)
        if len(calls) == 1:
            raise failure if site in ("retry-promotion", "diagnostic-promotion") else first
        if site == "retry-promotion":
            raise first
        if site.startswith("retry-"):
            raise failure
        return publisher(*args)

    binder = mutation._bind_cancellation_evidence
    bindings = []

    def bind(error, **evidence):
        bindings.append(error)
        target = 2 if site in ("retry-binder", "retry-secondary-binding") else 1
        if site in ("binder", "secondary-binding", "retry-binder", "retry-secondary-binding"):
            if len(bindings) == target:
                fired.append("binder")
                if "secondary-binding" in site:
                    raise OSError("FIRST_DIAGNOSTIC_BINDING_FAILURE")
                raise secondary
            if "secondary-binding" in site and len(bindings) == target + 1:
                fired.append("fallback")
                raise secondary
        return binder(error, **evidence)

    def fail(**kwargs):
        kwargs["_evidence"]["process_failure"] = {"cleanup_complete": True}
        raise ordinary

    monkeypatch.setattr(mutation, "run_mutation", fail)
    monkeypatch.setattr(mutation, "_publish_mutation_error", publish)
    monkeypatch.setattr(mutation, "_bind_cancellation_evidence", bind)
    with pytest.raises(exception) as caught:
        run_detached_payload(captured["script"])
    assert caught.value is first and first.__cause__ is ordinary
    assert fired and len(calls) == 2
    if "secondary-binding" in site:
        assert "fallback" in fired
    if "secondary-binding" not in site:
        assert first.detached_publication_diagnostic_error is (first if site == "diagnostic-promotion" else secondary)
    data = json.loads(result.read_text())
    if site.startswith("retry-"):
        assert data["status"] == "running" and not data.get("completed_measurement")
    else:
        assert data["status"] == "error" and data["cancelled"]
        assert data["cleanup_complete"] is None
        assert data["process_failure"]["cleanup_complete"] is True
        assert not data["baseline_passed"] and not data["completed_measurement"]
    assert data["survivors"] == [] and mutation_result_verdict(data) is None


def test_actual_sigint_retains_original_object_and_reaps_owner(tmp_path):
    ready = tmp_path / "ready.json"
    script = tmp_path / "blocker.py"
    script.write_text("import json,os,sys,time\nfrom pathlib import Path\n"
                      "Path(sys.argv[1]).write_text(json.dumps({'child':os.getpid(),'owner':os.getppid()}))\n"
                      "time.sleep(10)\n")
    cancellation = KeyboardInterrupt("ACTUAL_SIGINT")
    errors = []

    def on_signal(_signum, _frame):
        raise cancellation

    def interrupt():
        deadline = time.monotonic() + 3
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not ready.exists():
            errors.append("child did not become ready")
            return
        fd = os.pidfd_open(os.getpid())
        try:
            signal.pidfd_send_signal(fd, signal.SIGINT)
        finally:
            os.close(fd)

    original = signal.signal(signal.SIGINT, on_signal)
    thread = threading.Thread(target=interrupt)
    thread.start()
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            process.run_owned_command([sys.executable, str(script), str(ready)], timeout=4)
        assert caught.value is cancellation
        assert cancellation.cleanup_complete and cancellation.ownership["cancelled"]
        assert not errors
        ids = json.loads(ready.read_text())
        assert all(not Path(f"/proc/{pid}").exists() for pid in ids.values())
    finally:
        thread.join(timeout=4)
        signal.signal(signal.SIGINT, original)
    assert not thread.is_alive()


def test_actual_detached_sigint_closes_nonmeasurement_result(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/calc.py").write_text("value = 1\n")
    ready = tmp_path / "ready.json"
    script = tmp_path / "blocker.py"
    script.write_text("import json,os,sys,time\nfrom pathlib import Path\n"
                      "Path(sys.argv[1]).write_text(json.dumps({'child':os.getpid(),'owner':os.getppid()}))\n"
                      "time.sleep(10)\n")
    result = tmp_path / "mutation-result.json"
    assert mutation.launch_detached_mutation(["src/calc.py"], [sys.executable, str(script), str(ready)],
                                             tmp_path, result, baseline_timeout=5, timeout=5)
    deadline = time.monotonic() + 4
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists(), "owned baseline child must be running before cancellation"
    data = json.loads(result.read_text())
    assert data["status"] == "running" and data["pid"] > 0
    fd = os.pidfd_open(data["pid"])
    try:
        signal.pidfd_send_signal(fd, signal.SIGINT)
    finally:
        os.close(fd)
    while time.monotonic() < deadline:
        try:
            data = json.loads(result.read_text())
        except json.JSONDecodeError:
            continue
        if data["status"] == "error":
            break
        time.sleep(0.01)
    assert data["status"] == "error" and data["cancelled"]
    assert data["cancellation_type"] == "KeyboardInterrupt" and data["cleanup_complete"]
    assert data["process_failure"]["cancelled"]
    assert not data["baseline_passed"] and not data["completed_measurement"] and not data["survivors"]
    assert mutation_result_verdict(data) is None
    assert all(not Path(f"/proc/{pid}").exists() for pid in json.loads(ready.read_text()).values())
    journal = json.loads((tmp_path / ".code-forge/mutation-owner.lock").read_text())
    assert journal["phase"] == "complete"


@pytest.mark.parametrize("pending_type", [RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("diagnostic_type", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("site", ["note", "metadata", "fallback-call", "fallback-metadata"])
def test_detached_first_diagnostic_control_is_authoritative(
    tmp_path, monkeypatch, run_detached_payload, pending_type, diagnostic_type, site,
):
    captured = {}

    class Launcher:
        def wait(self, **_kwargs):
            return 0

    def launch(argv, **_kwargs):
        captured["script"] = argv[2]
        return Launcher()

    monkeypatch.setattr(mutation.subprocess, "Popen", launch)
    result = tmp_path / "mutation-result.json"
    assert mutation.launch_detached_mutation([], [], tmp_path, result)
    diagnostic = diagnostic_type("DIAGNOSTIC_CANCELLATION")
    fired = []

    class Pending(pending_type):
        def __getattribute__(self, name):
            if site in ("metadata", "fallback-metadata") and name == "__dict__" and not fired:
                fired.append(site)
                raise diagnostic
            return super().__getattribute__(name)

        def add_note(self, note):
            if site == "note" and not fired:
                fired.append(site)
                raise diagnostic
            return super().add_note(note)

    pending = Pending("ORIGINAL_MUTATION_FAILURE")

    class PublicationFailure(OSError):
        strings = 0

        def __str__(self):
            self.strings += 1
            if site.startswith("fallback-") and self.strings == 1:
                raise OSError("ORDINARY_DIAGNOSTIC_FAILURE")
            return "ORDINARY_PUBLICATION_FAILURE"

    publication_failure = PublicationFailure()
    publisher = mutation._publish_mutation_error
    attempts = []

    def fail(**_kwargs):
        raise pending

    def publish(*args):
        attempts.append(True)
        if len(attempts) == 1:
            raise publication_failure
        return publisher(*args)

    binder = mutation._bind_cancellation_evidence

    def bind(error, **kwargs):
        if site == "fallback-call" and "note" not in kwargs and not fired:
            fired.append(site)
            raise diagnostic
        return binder(error, **kwargs)

    monkeypatch.setattr(mutation, "run_mutation", fail)
    monkeypatch.setattr(mutation, "_publish_mutation_error", publish)
    monkeypatch.setattr(mutation, "_bind_cancellation_evidence", bind)
    selected = diagnostic if isinstance(pending, Exception) else pending
    with pytest.raises(type(selected)) as caught:
        run_detached_payload(captured["script"])
    assert caught.value is selected and fired == [site]
    assert selected.__cause__ is (pending if isinstance(pending, Exception) else None)
    assert len(attempts) == 2
    data = json.loads(result.read_text())
    assert data["status"] == "error" and data["cancelled"]
    assert data["cleanup_complete"] in (None, False) and data["process_failure"] == {}
    assert not data["baseline_passed"] and not data["completed_measurement"] and data["survivors"] == []
    assert mutation_result_verdict(data) is None


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
@pytest.mark.parametrize("site", ["process-proof", "process-report", "process-evidence", "process-message",
                                 "workspace-message", "oserror-message", "baseline-write",
                                 "completed-write", "infra-write"])
def test_public_ordinary_diagnostic_first_control_survives_release(tmp_path, monkeypatch, exception, site):
    first = exception("FIRST_DIAGNOSTIC_CONTROL")
    second = SystemExit("SECOND_RELEASE_CONTROL")
    fired = []
    base = (MutationWorkspaceError if site == "workspace-message" else OSError
            if site == "oserror-message" else process.MutationProcessError)

    def interrupt():
        if not fired:
            fired.append(site)
            raise first

    class Failure(base):
        def __getattribute__(self, key):
            if (site == "process-proof" and key == "cleanup_complete" or
                    site == "process-report" and key == "report"):
                interrupt()
            return super().__getattribute__(key)

        def __str__(self):
            if site.endswith("message"):
                interrupt()
            return super().__str__()

    failure = (Failure("ORDINARY_FAILURE", cleanup_complete=True, report={})
               if base is process.MutationProcessError else Failure("ORDINARY_FAILURE"))
    keys = {"process-evidence": "process_failure", "baseline-write": "baseline_passed",
            "completed-write": "completed_measurement", "infra-write": "infra_errors"}

    class Evidence(dict):
        def __setitem__(self, key, value):
            if key == keys.get(site):
                interrupt()
            return super().__setitem__(key, value)

    def fail(*_args, **_kwargs):
        raise failure

    releases = []

    def release(workspace):
        releases.append(workspace.cleanup_complete)
        raise second

    monkeypatch.setattr(mutation, "_run_mutation", fail)
    monkeypatch.setattr(mutation.MutationWorkspace, "release", release)
    evidence = Evidence()
    with pytest.raises(exception) as caught:
        mutation.run_mutation([], [], cwd=tmp_path, _evidence=evidence)
    assert caught.value is first and fired == [site]
    assert first.__context__ is failure and first.__cause__ is None
    assert first.workspace_cleanup_error is second and first.cleanup_complete is False
    assert releases == [False]
    assert evidence["cancelled"] and not evidence["baseline_passed"] and not evidence["completed_measurement"]


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
@pytest.mark.parametrize("proof", [True, False, None, "true"])
def test_public_diagnostic_after_workspace_start_requires_exact_proof(tmp_path, monkeypatch, exception, proof):
    first = exception("FIRST_DIAGNOSTIC_CONTROL")
    first.cleanup_complete = proof
    first.ownership = {"cleanup_complete": proof}
    fired = []
    original = b"[user]\nretained = true\n"
    (tmp_path / "setup.cfg").write_bytes(original)
    (tmp_path / "mutants").mkdir()
    (tmp_path / "mutants/old").write_bytes(b"USER_MIRROR")

    class Failure(process.MutationProcessError):
        def __str__(self):
            if not fired:
                fired.append(True)
                raise first
            return super().__str__()

    failure = Failure("ORDINARY_FAILURE", cleanup_complete=True)

    def fail(*_args, _workspace, **_kwargs):
        _workspace.acquire()
        _workspace.prepare()
        _workspace.install_configs(b"# managed-by-code-forge-mutation\n[mutmut]\n", lambda _data: None)
        raise failure

    monkeypatch.setattr(mutation, "_run_mutation", fail)
    evidence = {}
    with pytest.raises(exception) as caught:
        mutation.run_mutation([], [], cwd=tmp_path, _evidence=evidence)
    assert caught.value is first and fired == [True]
    assert first.__context__ is failure and evidence["cancelled"]
    assert not evidence["baseline_passed"] and not evidence["completed_measurement"]
    journal = json.loads((tmp_path / ".code-forge/mutation-owner.lock").read_text())
    if proof is True:
        assert journal["phase"] == "complete"
        assert (tmp_path / "setup.cfg").read_bytes() == original
        assert (tmp_path / "mutants/old").read_bytes() == b"USER_MIRROR"
    else:
        assert journal["phase"] == "incomplete"
        assert (tmp_path / "setup.cfg").read_bytes() != original
        assert (tmp_path / journal["quarantine"] / "old").read_bytes() == b"USER_MIRROR"


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
@pytest.mark.parametrize("site", ["release-control", "message", "baseline-write", "completed-write", "infra-write"])
def test_public_first_release_diagnostic_control_is_selected(tmp_path, monkeypatch, exception, site):
    first = exception("FIRST_RELEASE_DIAGNOSTIC_CONTROL")
    fired = []

    def interrupt():
        if not fired:
            fired.append(site)
            raise first

    class Failure(OSError):
        def __str__(self):
            if site == "message":
                interrupt()
            return super().__str__()

    failure = Failure("ORDINARY_RELEASE_FAILURE")
    keys = {"baseline-write": "baseline_passed", "completed-write": "completed_measurement",
            "infra-write": "infra_errors"}

    class Evidence(dict):
        def __setitem__(self, key, value):
            if key == keys.get(site):
                interrupt()
            return super().__setitem__(key, value)

    releases = []

    def release(_workspace):
        releases.append(True)
        if site == "release-control":
            interrupt()
        raise failure

    monkeypatch.setattr(mutation, "_run_mutation", lambda *_a, **_k: ([], []))
    monkeypatch.setattr(mutation.MutationWorkspace, "release", release)
    evidence = Evidence()
    with pytest.raises(exception) as caught:
        mutation.run_mutation([], [], cwd=tmp_path, _evidence=evidence)
    assert caught.value is first and fired == [site] and releases == [True]
    assert first.__context__ is (None if site == "release-control" else failure)
    assert evidence["cancelled"] and evidence["process_failure"] == {}
    assert not evidence["baseline_passed"] and not evidence["completed_measurement"]


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
@pytest.mark.parametrize("site", ["measurement", "release"])
def test_public_selection_dispatch_keeps_authoritative_control(tmp_path, monkeypatch, exception, site):
    first = exception("FIRST_CONTROL_BEFORE_DISPATCH")
    second = SystemExit("SECOND_CONTROL_AT_DISPATCH")
    fired = []
    messages = []
    owned_workspaces = []
    dispatch_proofs = []

    class Failure(OSError):
        def __str__(self):
            if not messages:
                messages.append(True)
                raise first
            return super().__str__()

    ordinary = Failure("ORIGINAL_ORDINARY_FAILURE")

    def run(*_args, _workspace, **_kwargs):
        owned_workspaces.append(_workspace)
        if site == "measurement":
            raise ordinary
        return [], []

    workspaces = []

    def release(workspace):
        workspaces.append(workspace)
        if site == "release":
            raise ordinary

    monkeypatch.setattr(mutation, "_run_mutation", run)
    monkeypatch.setattr(mutation.MutationWorkspace, "release", release)
    helper = next(code for code in mutation.run_mutation.__code__.co_consts
                  if hasattr(code, "co_name") and code.co_name in ("select_cancellation", "record_cancellation"))
    token = helper.co_name + ("(exc" if site == "measurement" else "(evidence_exc")
    target = next(i for i, line in enumerate(Path(mutation.__file__).read_text().splitlines(), 1)
                  if line.strip().startswith(token))
    tool = 4
    sys.monitoring.use_tool_id(tool, "public-selection-dispatch")

    def cut(code, line):
        if code is mutation.run_mutation.__code__ and line == target and not fired:
            fired.append(True)
            dispatch_proofs.append(owned_workspaces[0].cleanup_complete)
            raise second

    sys.monitoring.register_callback(tool, sys.monitoring.events.LINE, cut)
    sys.monitoring.set_local_events(tool, mutation.run_mutation.__code__, sys.monitoring.events.LINE)
    evidence = {}
    try:
        with pytest.raises(exception) as caught:
            mutation.run_mutation([], [], cwd=tmp_path, _evidence=evidence)
    finally:
        sys.monitoring.set_local_events(tool, mutation.run_mutation.__code__, 0)
        sys.monitoring.register_callback(tool, sys.monitoring.events.LINE, None)
        sys.monitoring.free_tool_id(tool)
    assert caught.value is first and fired == messages == [True]
    assert dispatch_proofs == [False]
    assert first.__context__ is ordinary or first.__cause__ is ordinary
    assert first.cleanup_evidence_error is second
    assert workspaces and workspaces[0].cleanup_complete is False
    assert evidence["cancelled"] and not evidence["baseline_passed"] and not evidence["completed_measurement"]
