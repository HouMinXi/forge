"""The feature switch exists on every link of the chain and defaults off.

These cases only assert existence, default and propagation; they do not assert any
scoped behaviour, because none is implemented yet.
"""

import types
import uuid
from pathlib import Path

import pytest

from code_forge import invocation_audit as audit
from code_forge import invocation_host as host_mod
from code_forge import invocation_owner as owner_mod
from code_forge.invocation_audit import InvocationContext


def context(snapshot="a" * 64):
    return InvocationContext(
        str(uuid.uuid4()), snapshot, str(uuid.uuid4()), None, None, None, None, "review", None
    )


def recorder(tmp_path, **kwargs):
    return audit.AttemptRecorder(
        Path(tmp_path),
        context=context(),
        monotonic=lambda: 1.0,
        utc_now=lambda: "2026-11-12T00:00:00Z",
        **kwargs,
    )


def test_recorder_defaults_off(tmp_path):
    built = recorder(tmp_path)
    assert built._scoped_fault_domain is False
    assert "_scoped_fault_domain" in vars(built), (
        "the flag must be instance state, not a class attribute"
    )


def test_recorder_accepts_explicit_on(tmp_path):
    built = recorder(tmp_path, scoped_fault_domain=True)
    assert built._scoped_fault_domain is True
    assert "_scoped_fault_domain" in vars(built)


@pytest.mark.parametrize("value", [1, "yes", None, 0])
def test_recorder_rejects_non_bool(tmp_path, value):
    with pytest.raises(TypeError):
        recorder(tmp_path, scoped_fault_domain=value)


def test_owner_create_defaults_off(tmp_path):
    created = owner_mod.InvocationOwner.create(
        artifact_root=Path(tmp_path),
        context=context(),
        monotonic=lambda: 1.0,
        utc_now=lambda: "2026-11-12T00:00:00Z",
        audit_schema_version=2,
    )
    assert created._scoped_fault_domain is False
    assert "_scoped_fault_domain" in vars(created), "the owner stores it on the instance"
    assert created._current.recorder._scoped_fault_domain is False


@pytest.mark.parametrize("value", [1, "yes", None])
def test_owner_rejects_non_bool_before_it_builds_a_recorder(tmp_path, monkeypatch, value):
    """The owner's guard must reject, not the recorder's backstop behind it."""
    built = []

    class Sentinel:
        def __init__(self, *args, **kwargs):
            built.append(kwargs)
            raise AssertionError("owner built a recorder for an invalid switch value")

    monkeypatch.setattr(owner_mod, "AttemptRecorder", Sentinel)
    with pytest.raises(TypeError):
        owner_mod.InvocationOwner.create(
            artifact_root=Path(tmp_path),
            context=context(),
            monotonic=lambda: 1.0,
            utc_now=lambda: "2026-11-12T00:00:00Z",
            audit_schema_version=2,
            scoped_fault_domain=value,
        )


def test_epoch_one_recorder_receives_the_switch(tmp_path):
    created = owner_mod.InvocationOwner.create(
        artifact_root=Path(tmp_path),
        context=context(),
        monotonic=lambda: 1.0,
        utc_now=lambda: "2026-11-12T00:00:00Z",
        audit_schema_version=2,
        scoped_fault_domain=True,
    )
    assert created._current.recorder._scoped_fault_domain is True


def unknown_context():
    """An unknown source needs no input manifest; a known one requires it."""
    return InvocationContext(str(uuid.uuid4()), None, None, None, None, None, None, "review", None)


def test_host_defaults_off(tmp_path):
    session = host_mod.HostAuditSession(artifact_root=Path(tmp_path), context=unknown_context())
    assert session._scoped_fault_domain is False
    assert "_scoped_fault_domain" in vars(session), "the flag must be instance state on the host too"


def test_host_accepts_explicit_on(tmp_path):
    session = host_mod.HostAuditSession(
        artifact_root=Path(tmp_path), context=unknown_context(), scoped_fault_domain=True
    )
    assert session._scoped_fault_domain is True
    assert "_scoped_fault_domain" in vars(session)


@pytest.mark.parametrize("value", [1, "yes", None])
def test_host_rejects_non_bool(tmp_path, value):
    with pytest.raises(TypeError):
        host_mod.HostAuditSession(
            artifact_root=Path(tmp_path), context=unknown_context(), scoped_fault_domain=value
        )


def test_host_forwards_the_switch_to_the_owner(tmp_path, monkeypatch):
    captured = {}

    real_create = owner_mod.InvocationOwner.create

    def spy(**kwargs):
        captured.update(kwargs)
        return real_create(**kwargs)

    monkeypatch.setattr(host_mod.InvocationOwner, "create", staticmethod(spy))
    session = host_mod.HostAuditSession(
        artifact_root=Path(tmp_path), context=unknown_context(), scoped_fault_domain=True
    )
    session._execute("open", (), 1e9)
    assert captured.get("scoped_fault_domain") is True


def _bound_context(payload=b"diff"):
    import hashlib

    return InvocationContext(
        str(uuid.uuid4()),
        hashlib.sha256(payload).hexdigest(),
        str(uuid.uuid4()),
        None,
        None,
        None,
        None,
        "review",
        None,
    )


def _manifest(ctx, payload=b"diff"):
    import hashlib

    return dict(
        schema_version=1,
        run_id=ctx.run_id,
        snapshot_id=ctx.snapshot_id,
        mode="diff",
        legacy_source_hash=ctx.source_hash,
        exact_diff_sha256=hashlib.sha256(payload).hexdigest(),
        diff_bytes=len(payload),
        files=[],
        repositories=[],
    )


BACKEND = audit.BackendObservation("openai", "fixture", "requested", None, None)
DIGEST = "b" * 64


def _operation(owner, purpose="review"):
    ctx = owner.context(
        round_index=0,
        pass_name="qodo-review",
        group_id=None,
        group_diff_sha256=None,
        purpose=purpose,
        parent_logical_id=None,
    )
    return owner.operation(ctx, cause="initial", parent=None, max_attempts=5)


def _complete(owner):
    lease = owner.begin_attempt(
        _operation(owner),
        backend=BACKEND,
        request_digest=DIGEST,
        request_bytes=7,
        timeout_s=600,
    )
    lease.entered("entered_api_send")
    assert lease.acquired(layer="wire", data=b"{}", complete=True).admitted
    assert lease.finish_once(outcome="completed", error_class=None, duration_s=3.0).admitted
    return lease


class _Ack:
    admitted = True


class _FakeRecorder:
    """Synthetic sink: records constructor kwargs, performs no storage work."""

    seen = []

    def __init__(self, root, **kwargs):
        self.kwargs = kwargs
        _FakeRecorder.seen.append(kwargs)
        self.manifest = None

    def open(self):
        return _Ack()

    def publish_input_manifest(self, manifest):
        self.manifest = dict(manifest)
        return _Ack()

    def scoped_snapshot(self):
        return types.SimpleNamespace(input_manifest=self.manifest)

    def finalize(self, outcome, **kwargs):
        return _Ack()


def test_second_epoch_recorder_receives_the_switch(monkeypatch):
    """White-box: the epoch-2 construction site must pass the owner's flag.

    The flow is synthetic (no storage admission); the assertion is about the keyword
    reaching the second recorder, which a create-only edit would silently break.
    """
    _FakeRecorder.seen = []
    monkeypatch.setattr(owner_mod, "AttemptRecorder", _FakeRecorder)
    first = _bound_context()
    owner = owner_mod.InvocationOwner.create(
        artifact_root=Path("/unused-synthetic-root"),
        context=first,
        monotonic=lambda: 1.0,
        utc_now=lambda: "2026-11-12T00:00:00Z",
        audit_schema_version=2,
        scoped_fault_domain=True,
    )
    assert _FakeRecorder.seen[0]["scoped_fault_domain"] is True
    owner._current.manifest = _manifest(first)
    second = _bound_context(b"new")
    owner.advance_source(second, manifest=_manifest(second, b"new"))
    assert len(_FakeRecorder.seen) == 2
    assert _FakeRecorder.seen[1]["scoped_fault_domain"] is True


def test_second_epoch_keeps_the_switch_off(monkeypatch):
    """The forwarding site must pass the owner's value, not a constant."""
    _FakeRecorder.seen = []
    monkeypatch.setattr(owner_mod, "AttemptRecorder", _FakeRecorder)
    first = _bound_context()
    owner = owner_mod.InvocationOwner.create(
        artifact_root=Path("/unused-synthetic-root"),
        context=first,
        monotonic=lambda: 1.0,
        utc_now=lambda: "2026-11-12T00:00:00Z",
        audit_schema_version=2,
    )
    owner._current.manifest = _manifest(first)
    second = _bound_context(b"new")
    owner.advance_source(second, manifest=_manifest(second, b"new"))
    assert len(_FakeRecorder.seen) == 2
    assert _FakeRecorder.seen[1]["scoped_fault_domain"] is False


def test_host_forwards_the_switch_off(tmp_path, monkeypatch):
    """The forwarding site must pass the host's value, not a constant."""
    captured = {}
    real_create = owner_mod.InvocationOwner.create

    def spy(**kwargs):
        captured.update(kwargs)
        return real_create(**kwargs)

    monkeypatch.setattr(host_mod.InvocationOwner, "create", staticmethod(spy))
    session = host_mod.HostAuditSession(artifact_root=Path(tmp_path), context=unknown_context())
    session._execute("open", (), 1e9)
    assert captured.get("scoped_fault_domain") is False
