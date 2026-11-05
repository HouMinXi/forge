"""Pure finalization logic and in-memory storage stubs, NOT native admission.

The real terminal event builder and fold are exercised. _guard and storage I/O
are replaced; these controls do not prove filesystem qualification or durability.
"""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_forge import invocation_audit as audit
from tests.test_invocation_stage_contract import history


@pytest.fixture
def memory_recorder(monkeypatch):
    events = history()[:1]
    recorder = audit.AttemptRecorder(
        Path("/mock-not-native"),
        context=audit.InvocationContext(**events[0]["context"]),
        monotonic=lambda: 0.0,
        utc_now=lambda: "2026-11-04T00:00:00Z",
        audit_schema_version=2,
    )
    recorder._events = events
    recorder._owner = events[0]["owner"]
    recorder._opened = True
    publications = []
    monkeypatch.setattr(recorder, "_guard", lambda phase, action: action(SimpleNamespace(fd=-1)))
    monkeypatch.setattr(recorder, "_checked", lambda *a, **k: dict(frozen=False, ordinary=0))
    monkeypatch.setattr(recorder, "_append_event", lambda handle, fd, event: events.append(event))
    monkeypatch.setattr(audit, "_run_dir", lambda *a: nullcontext(-1))
    monkeypatch.setattr(audit, "_inventory", lambda *a: None)
    monkeypatch.setattr(audit, "_space", lambda *a, **k: None)
    monkeypatch.setattr(audit, "_publish", lambda *a, **k: publications.append(a))
    monkeypatch.setattr(audit, "_revalidate", lambda *a: None)
    monkeypatch.setattr(audit, "os", SimpleNamespace(fsync=lambda *a: None))
    return recorder, events, publications


@pytest.mark.parametrize("incomplete", [False, True])
def test_explicit_evidence_controls_single_terminal_event_not_outcome(memory_recorder, incomplete):
    recorder, events, publications = memory_recorder
    assert recorder.finalize("incomplete", audit_incomplete=incomplete).admitted
    terminal = audit._fold(events)[3]
    assert terminal["outcome"] == "incomplete"
    assert terminal["audit_complete"] is (not incomplete)
    assert not audit._fold(events)[5]  # A false terminal does not require an audit_fault.
    projection = recorder.scoped_snapshot()
    assert projection.audit_complete is (not incomplete)
    assert len(publications) == 1
    assert recorder.finalize("incomplete").admitted
    assert len(events) == 2 and len(publications) == 1
    assert recorder.scoped_snapshot().audit_complete is (not incomplete)


def test_committed_complete_terminal_cannot_be_invalidated_in_memory(memory_recorder):
    recorder, events, publications = memory_recorder
    assert recorder.finalize("incomplete").admitted
    ack = recorder.finalize("incomplete", audit_incomplete=True)
    assert not ack.admitted and ack.fault.code == "identity_conflict"
    assert recorder.scoped_snapshot().audit_complete
    assert events[-1]["audit_complete"] and len(publications) == 1


@pytest.mark.parametrize("invalid", [0, 1, None, "true"])
def test_explicit_evidence_requires_exact_bool_without_io(memory_recorder, invalid):
    recorder, events, publications = memory_recorder
    with pytest.raises(TypeError):
        recorder.finalize("incomplete", audit_incomplete=invalid)
    assert len(events) == 1 and not publications and recorder._complete


def test_v1_rejects_explicit_v2_evidence_without_io(memory_recorder):
    recorder, events, publications = memory_recorder
    recorder._audit_schema_version = 1
    with pytest.raises(ValueError):
        recorder.finalize("incomplete", audit_incomplete=True)
    assert len(events) == 1 and not publications and recorder._complete


def test_failed_terminal_write_has_no_complete_claim_or_retry(memory_recorder, monkeypatch):
    recorder, events, publications = memory_recorder
    calls = []

    def unknown_append(*args):
        calls.append(True)
        recorder._append_uncertain = True
        raise OSError("simulated unknown terminal append")

    monkeypatch.setattr(recorder, "_append_event", unknown_append)
    with pytest.raises(OSError):  # The pure action stub intentionally has no _guard recovery.
        recorder.finalize("incomplete", audit_incomplete=True)
    assert not recorder._complete and not recorder._closed
    assert len(events) == 1 and calls == [True] and not publications
    # Exercise the real pre-I/O refusal, not the fixture's permissive storage stub.
    with pytest.raises(audit._Rejected):
        audit.AttemptRecorder._checked(recorder, None, -1, allow_orphans=True)
    assert calls == [True]


def test_failed_final_marker_does_not_republish_terminal(memory_recorder, monkeypatch):
    recorder, events, _ = memory_recorder
    calls = []

    def unknown_publish(*args, **kwargs):
        calls.append(True)
        raise OSError("simulated unknown final marker publication")

    monkeypatch.setattr(audit, "_publish", unknown_publish)
    with pytest.raises(OSError):
        recorder.finalize("incomplete", audit_incomplete=True)
    assert not recorder._closed and not recorder._complete
    assert audit._fold(events)[3]["audit_complete"] is False
    with pytest.raises(audit._Rejected):
        recorder.finalize("incomplete", audit_incomplete=True)
    assert len(events) == 2 and calls == [True]
