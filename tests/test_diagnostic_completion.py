"""Real CLI consumes live-owner outcomes; no disk-derived expectation or gate credit."""

from dataclasses import FrozenInstanceError
import threading
from uuid import uuid4

import pytest

from code_forge import _advisory_diagnostics as storage
from code_forge import _cross_repo_diagnostics as capture_module
from code_forge import _diagnostic_completion as handoff
from code_forge import cli
from code_forge.state import Verdict
from tests import test_cross_repo_persistent_diagnostic as persistent

graphs = persistent.graphs
persistent_cli = persistent.persistent_cli


@pytest.fixture
def invocation(persistent_cli, tmp_path, monkeypatch):
    tickets = tmp_path / "tickets"
    tickets.mkdir(mode=0o700)
    slots = []
    initialize = handoff.DiagnosticCompletion.__init__

    def initialize_and_retain(self):
        initialize(self)
        slots.append(self)

    monkeypatch.setattr(handoff.DiagnosticCompletion, "__init__", initialize_and_retain)
    dispatch = cli._cross_repo_verdict_or_none
    monkeypatch.setattr(
        cli,
        "_cross_repo_verdict_or_none",
        lambda **kwargs: dispatch(**kwargs, diagnostic_ticket_directory=tickets),
    )
    return persistent_cli, slots, tickets


def test_cli_consumes_original_publication_after_worker_cleanup(invocation, capsys):
    host, slots, tickets = invocation
    assert host.run(output=lambda message: None) is Verdict.PASS
    result = slots[0].read()
    assert result.worker_completed and result.capture_available
    assert not result.capture_cleanup_failed
    assert len(result.attempts) == 1
    attempt = result.attempts[0]
    assert attempt.publication.status == "published"
    assert (
        storage.read_observation(attempt.publication, attempt.expected).analysis_state
        == "no_named_targets"
    )
    assert all(not machine.cwd.exists() for machine in host.machines.values())
    names = [p.name for p in tickets.iterdir()]
    assert result.tickets == ((1, names[0]),)
    assert capsys.readouterr().out.count("diagnostic-ticket: exported " + names[0]) == 1
    with pytest.raises(FrozenInstanceError):
        result.worker_completed = False
    assert slots[0].read() is result


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(7)])
@pytest.mark.parametrize("committed", [False, True])
def test_interrupted_publication_is_unknown_and_original_control_propagates(
    invocation, monkeypatch, capsys, error, committed
):
    host, slots, tickets = invocation
    publish = storage.publish_observation
    committed_publications = []

    def interrupt(*args, **kwargs):
        if committed:
            committed_publications.append(publish(*args, **kwargs))
        raise error

    monkeypatch.setattr(storage, "publish_observation", interrupt)
    with pytest.raises(type(error)) as caught:
        host.run(output=lambda message: None)
    assert caught.value is error
    result = slots[0].read()
    assert result.capture_available and not result.worker_completed
    assert len(result.attempts) == 1
    attempt = result.attempts[0]
    assert attempt.publication.status == "unknown" and attempt.publication.locator is None
    assert not list(tickets.iterdir())
    assert "diagnostic-ticket: unavailable; no saved-ticket guarantee" in capsys.readouterr().out
    assert len(committed_publications) == int(committed)
    if committed:
        assert (
            storage.read_observation(committed_publications[0], attempt.expected).analysis_state
            == "no_named_targets"
        )


def test_setup_failure_is_not_successful_empty_history(invocation, monkeypatch, capsys):
    host, slots, _ = invocation
    error = RuntimeError("fixture setup failure")
    from code_forge import cross_repo

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(cross_repo, "derive_source_files", fail)
    with pytest.raises(RuntimeError) as caught:
        host.run(output=lambda message: None)
    assert caught.value is error
    result = slots[0].read()
    assert not result.capture_available and not result.worker_completed
    assert result.attempts == ()
    assert "diagnostic-ticket: unavailable" in capsys.readouterr().out


@pytest.mark.parametrize("fault", ["bind", "publish"])
def test_normal_review_retains_publication_failure_status(invocation, monkeypatch, fault):
    host, slots, _ = invocation

    def fail(*args, **kwargs):
        raise OSError("private unavailable fixture")

    monkeypatch.setattr(storage, "bind_storage" if fault == "bind" else "publish_observation", fail)
    assert host.run(output=lambda message: None) is Verdict.PASS
    result = slots[0].read()
    assert result.worker_completed and result.capture_available
    attempt = result.attempts[0]
    assert attempt.publication.status == ("not_published" if fault == "bind" else "unknown")
    assert attempt.publication.locator is None


def test_completion_signal_and_owner_are_required(tmp_path):
    root = tmp_path / "repo"
    root.mkdir(mode=0o700)
    execution = str(uuid4())
    capture = capture_module.DiagnosticCapture(
        execution_id=execution, source_hash="source", diff_text="diff", repo_root=root, excluded_roots=()
    )
    capture.finish("no_named_targets")
    capture.close()
    slot = handoff.DiagnosticCompletion()
    slot.claim(execution)
    with pytest.raises(ValueError):
        slot.claim(execution)
    completed = threading.Event()
    slot.seal(capture, worker_completed=True, cleanup_failed=False, completion_event=completed)
    assert slot.read() is None
    completed.set()
    result = slot.read()
    assert result is not None
    values = []
    worker = threading.Thread(target=lambda: values.append(slot.read()))
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive() and values == [None]
    slot.seal(None, worker_completed=False, cleanup_failed=True, completion_event=completed)
    assert slot.read() is result


def test_attempt_budget_and_prior_publication_survive_later_control(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir(mode=0o700)
    capture = capture_module.DiagnosticCapture(
        execution_id=str(uuid4()),
        source_hash="source",
        diff_text="diff",
        repo_root=root,
        excluded_roots=(),
    )
    first = capture.finish("no_named_targets")
    error = KeyboardInterrupt()

    def interrupt(*args):
        raise error

    monkeypatch.setattr(storage, "publish_observation", interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        capture.finish("targets_resolved")
    assert caught.value is error
    assert capture._publication_attempts[0].publication == first.publication
    assert capture._publication_attempts[1].publication.status == "unknown"
    for _ in range(storage.MAX_OBSERVATIONS + 2):
        try:
            capture.finish("targets_resolved")
        except KeyboardInterrupt:
            pass
    assert len(capture._publication_attempts) == storage.MAX_OBSERVATIONS
    assert (
        storage.read_observation(
            first.publication, capture._publication_attempts[0].expected
        ).analysis_state
        == "no_named_targets"
    )


def test_actual_cli_strict_reader_uses_owner_tuple_on_calling_thread(invocation, monkeypatch):
    host, slots, _ = invocation
    caller = threading.get_ident()
    reads = []
    read = storage.read_observation

    def track(publication, expected):
        if threading.get_ident() == caller:
            reads.append((publication, expected))
            assert all(not machine.cwd.exists() for machine in host.machines.values())
        return read(publication, expected)

    monkeypatch.setattr(storage, "read_observation", track)
    assert host.run(output=lambda message: None) is Verdict.PASS
    attempt = slots[0].read().attempts[0]
    assert reads == [(attempt.publication, attempt.expected)]


def test_removed_artifact_before_cli_consumption_is_unavailable(invocation, monkeypatch, capsys):
    host, slots, _ = invocation
    seal = handoff.DiagnosticCompletion.seal

    def seal_and_remove(self, *args, **kwargs):
        seal(self, *args, **kwargs)
        loc = self._result.attempts[0].publication.locator
        loc.binding.source_root.joinpath(*(name for name, _ in loc.directories), loc.filename).unlink()

    monkeypatch.setattr(handoff.DiagnosticCompletion, "seal", seal_and_remove)
    assert host.run(output=lambda message: None) is Verdict.PASS
    assert slots[0].read().attempts[0].publication.status == "published"
    output = capsys.readouterr().out
    assert "diagnostic-ticket: unavailable" in output
    assert "diagnostic-ticket: exported" not in output


@pytest.mark.parametrize(
    "error", [RuntimeError("after observation"), KeyboardInterrupt(), SystemExit(9)]
)
def test_prior_publication_survives_worker_error_and_notice_control(invocation, monkeypatch, error):
    host, slots, _ = invocation
    from code_forge.machine import StateMachine

    run = StateMachine.run

    def run_then_fail(self):
        result = run(self)
        if self.baseline_spec_repr == "primary":
            raise error
        return result

    def fail_notice(*args, **kwargs):
        raise KeyboardInterrupt("secondary notice interrupt")

    monkeypatch.setattr(StateMachine, "run", run_then_fail)
    monkeypatch.setattr(cli, "print", fail_notice, raising=False)
    with pytest.raises(type(error)) as caught:
        host.run(output=lambda message: None)
    assert caught.value is error
    result = slots[0].read()
    assert not result.worker_completed
    attempt = result.attempts[0]
    assert attempt.publication.status == "published"
    assert (
        storage.read_observation(attempt.publication, attempt.expected).analysis_state
        == "no_named_targets"
    )


@pytest.mark.parametrize("error", [RuntimeError("close failure"), SystemExit(10)])
def test_capture_close_failure_is_separate_from_prior_publication(invocation, monkeypatch, error):
    host, slots, _ = invocation

    def fail_close(self):
        raise error

    monkeypatch.setattr(capture_module.DiagnosticCapture, "close", fail_close)
    if isinstance(error, SystemExit):
        with pytest.raises(SystemExit) as caught:
            host.run(output=lambda message: None)
        assert caught.value is error
    else:
        assert host.run(output=lambda message: None) is Verdict.PASS
    result = slots[0].read()
    assert result.capture_cleanup_failed
    assert result.worker_completed is not isinstance(error, SystemExit)
    assert result.attempts[0].publication.status == "published"


def test_ticket_replacement_does_not_rebind_cli_expected(invocation, monkeypatch, tmp_path):
    from dataclasses import replace
    from code_forge import _diagnostic_history as history

    host, slots, tickets = invocation
    seal = handoff.DiagnosticCompletion.seal
    read = storage.read_observation
    caller = threading.get_ident()
    reads = []
    foreign_contexts = []

    def seal_and_replace_ticket(self, capture, **kwargs):
        seal(self, capture, **kwargs)
        attempt = self._result.attempts[0]
        foreign = replace(attempt.expected, execution_id=str(uuid4()))
        root = tmp_path / "other"
        root.mkdir(mode=0o700)
        publication = storage.publish_observation(
            storage.bind_storage(root), storage.make_observation(foreign, "targets_resolved")
        )
        other_ticket = history.export_ticket(tickets, publication, foreign)
        original = tickets / self._result.tickets[0][1]
        original.write_bytes(other_ticket.read_bytes())
        other_ticket.unlink()
        assert history.read_ticket(original).context == foreign
        foreign_contexts.append(foreign)

    def track(publication, expected):
        if threading.get_ident() == caller:
            assert slots[0]._completion_event.is_set()
            reads.append((publication, expected))
        return read(publication, expected)

    monkeypatch.setattr(handoff.DiagnosticCompletion, "seal", seal_and_replace_ticket)
    monkeypatch.setattr(storage, "read_observation", track)
    assert host.run(output=lambda message: None) is Verdict.PASS
    attempt = slots[0].read().attempts[0]
    assert reads == [(attempt.publication, attempt.expected)]
    assert attempt.expected != foreign_contexts[0]


@pytest.mark.parametrize("primary", [None, RuntimeError("primary worker"), KeyboardInterrupt()])
def test_cli_reader_control_propagates_or_preserves_primary(invocation, monkeypatch, primary):
    host, slots, _ = invocation
    from code_forge.machine import StateMachine

    run, read = StateMachine.run, storage.read_observation
    caller = threading.get_ident()
    control = SystemExit(12)

    def run_then_fail(self):
        result = run(self)
        if self.baseline_spec_repr == "primary" and primary is not None:
            raise primary
        return result

    def interrupt_caller_read(*args):
        if threading.get_ident() == caller:
            assert slots[0]._completion_event.is_set()
            raise control
        return read(*args)

    monkeypatch.setattr(StateMachine, "run", run_then_fail)
    monkeypatch.setattr(storage, "read_observation", interrupt_caller_read)
    expected = primary if primary is not None else control
    with pytest.raises(type(expected)) as caught:
        host.run(output=lambda message: None)
    assert caught.value is expected
    assert slots[0].read().attempts[0].publication.status == "published"
