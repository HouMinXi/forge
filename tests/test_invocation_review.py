"""Pure ordinary-adapter contracts with an explicit fake host factory boundary.

These tests do not qualify a filesystem, admit recorder storage, send HTTP,
invoke a provider, or establish whole-review recording/strict guarantees.
"""

from dataclasses import FrozenInstanceError, dataclass
import hashlib
import builtins
import os
import threading
from types import SimpleNamespace
from urllib.request import Request

import pytest

from code_forge import invocation_review as module
from code_forge.invocation_host import HostAuditSession
from code_forge.invocation_send_audit import SendAuditScope, SendLifecycleClosed
from code_forge.invocation_stream import CaptureUnavailable
from code_forge.llm_invoke import AuditPersistenceError, LLMInvokeError, SendDeadlineError, Usage


_SOURCE = "diff --git a/sample.py b/sample.py\r\n+value = '雪'  \r\n"
_WARNING = "code-forge: invocation audit unavailable; recording is incomplete\n"


@dataclass(frozen=True)
class _ScopeCall:
    """Only capture the adapter's arguments; not a simulated storage ACK."""

    round_index: int
    pass_name: str | None
    purpose: str
    max_provider_sends: int


class _FakeHost:
    def __init__(self):
        self.scope_calls = []
        self.finalize_calls = []
        self.scope_error = None
        self.finalize_error = None

    def scope(self, **kwargs):
        if self.scope_error is not None:
            raise self.scope_error
        call = _ScopeCall(**kwargs)
        self.scope_calls.append(call)
        return call

    def finalize(self, **kwargs):
        self.finalize_calls.append(kwargs)
        if self.finalize_error is not None:
            raise self.finalize_error


class _FakeFactoryBoundary:
    def __init__(self):
        self.calls = []
        self.host = _FakeHost()
        self.error = None

    def create(self, cls, **kwargs):
        self.calls.append((cls, kwargs))
        if self.error is not None:
            raise self.error
        return self.host


@pytest.fixture(autouse=True)
def pure_host_boundary(monkeypatch):
    """Replace only the classmethod boundary, never filesystem admission."""
    boundary = _FakeFactoryBoundary()

    def from_user_state(cls, **kwargs):
        return boundary.create(cls, **kwargs)

    monkeypatch.setattr(HostAuditSession, "from_user_state", classmethod(from_user_state))
    return boundary


def _review(**kwargs):
    return module.OrdinaryReviewAudit(source=_SOURCE, environ={}, **kwargs)


def _request():
    # Request construction is pure. No opener or transport is called.
    return Request("https://provider.invalid/review", data=b"synthetic request", method="POST")


def _start(scope):
    return scope.start(
        _request(), cause="initial", backend_name="fixture", model="fixture", deadline=10.0
    )


def _chain(error):
    return error.__cause__, error.__context__, error.__suppress_context__


def test_pure_construction_round_selection_and_unused_finalize_are_lazy(
    pure_host_boundary, capsys, monkeypatch
):
    class UnreadEnvironment(dict):
        def get(self, *_args, **_kwargs):
            raise AssertionError("construction must not select a storage root")

    def forbidden_io(*_args, **_kwargs):
        raise AssertionError("unused adapter lifecycle must not open storage or start workers")

    # Tripwires only reject accidental I/O. They do not grant storage admission.
    with monkeypatch.context() as guards:
        guards.setattr(builtins, "open", forbidden_io)
        guards.setattr(os, "open", forbidden_io)
        guards.setattr(threading.Thread, "start", forbidden_io)
        review = module.OrdinaryReviewAudit(source=_SOURCE, environ=UnreadEnvironment())
        first = review.round(0)
        later = review.round(7)
        assert first.round_index == 0
        assert later.round_index == 7
        assert pure_host_boundary.calls == []
        review.finalize()
        review.finalize()
    assert pure_host_boundary.calls == []
    assert pure_host_boundary.host.finalize_calls == []
    with pytest.raises(SendLifecycleClosed):
        first.l1_scopes(5)
    assert capsys.readouterr() == ("", "")


def test_pure_one_host_binds_exact_diff_manifest_across_repeated_rounds(pure_host_boundary):
    environ = {"FIXTURE_SETTING": "opaque setting"}
    review = module.OrdinaryReviewAudit(source=_SOURCE, environ=environ)
    first = review.round(0)
    later = review.round(9)
    first.l1_scopes(5)
    later.l1_scopes(2)
    first.falsifier_scope()
    later.falsifier_scope()
    first.l1_scopes(5)
    assert len(pure_host_boundary.calls) == 1
    cls, options = pure_host_boundary.calls[0]
    assert cls is HostAuditSession
    assert set(options) == {"context", "manifest", "strict", "environ"}
    assert options["environ"] is environ
    assert options["strict"] is False
    context = options["context"]
    normalized = "diff --git a/sample.py b/sample.py\n+value = '雪'"
    legacy_hash = hashlib.sha256(b"mode=git\n" + normalized.encode("utf-8")).hexdigest()
    exact_bytes = _SOURCE.encode("utf-8")
    assert context.source_hash == legacy_hash
    assert context.round_index is context.pass_name is context.group_id is None
    assert context.group_diff_sha256 is context.parent_logical_id is None
    assert context.purpose == "review"
    assert context.run_id and context.snapshot_id
    assert options["manifest"] == {
        "schema_version": 1,
        "run_id": context.run_id,
        "snapshot_id": context.snapshot_id,
        "mode": "diff",
        "legacy_source_hash": legacy_hash,
        "exact_diff_sha256": hashlib.sha256(exact_bytes).hexdigest(),
        "diff_bytes": len(exact_bytes),
        "files": [],
        "repositories": [],
    }
    assert (
        options["manifest"]["exact_diff_sha256"]
        != hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    )
    assert options["manifest"]["diff_bytes"] > len(_SOURCE)


def test_pure_round_view_is_frozen_and_has_no_ambient_round_selection(pure_host_boundary):
    review = _review()
    earlier = review.round(2)
    later = review.round(8)
    with pytest.raises(FrozenInstanceError):
        earlier.round_index = 8
    with pytest.raises(FrozenInstanceError):
        earlier._review = _review()
    later.l1_scopes(1)
    scopes = earlier.l1_scopes(1)
    assert [scope.round_index for scope in scopes] == [2, 2, 2]
    assert [scope.round_index for scope in pure_host_boundary.host.scope_calls] == [8, 8, 8, 2, 2, 2]


def test_pure_l1_and_falsifier_calls_receive_fresh_independent_scopes(pure_host_boundary):
    view = _review().round(3)
    first = view.l1_scopes(5)
    second = view.l1_scopes(2)
    falsifiers = (view.falsifier_scope(), view.falsifier_scope())
    assert type(first) is type(second) is tuple
    assert [scope.pass_name for scope in first] == ["qodo", "expert", "adversarial"]
    assert [scope.pass_name for scope in second] == ["qodo", "expert", "adversarial"]
    assert [scope.max_provider_sends for scope in first] == [12, 12, 12]
    assert [scope.max_provider_sends for scope in second] == [9, 9, 9]
    assert all(scope.round_index == 3 and scope.purpose == "review" for scope in first + second)
    assert all(
        scope.round_index == 3
        and scope.pass_name is None
        and scope.purpose == "falsify"
        and scope.max_provider_sends == 8
        for scope in falsifiers
    )
    all_scopes = first + second + falsifiers
    assert len({id(scope) for scope in all_scopes}) == 8
    assert pure_host_boundary.host.scope_calls == list(all_scopes)


@pytest.mark.parametrize(
    ("attempts", "l1", "expected"),
    [(1, True, 8), (5, True, 12), (1, False, 4), (5, False, 8)],
    ids=["l1-minimum", "l1-default", "falsify-minimum", "falsify-default"],
)
def test_pure_provider_budget_keeps_recovery_headroom_separate_from_redirects(attempts, l1, expected):
    assert module.provider_send_budget(attempts, l1=l1) == expected


@pytest.mark.parametrize(
    ("attempts", "l1"),
    [(0, True), (-1, False), (True, True), (1.0, True), ("5", True), (5, 1), (5, None)],
    ids=["zero", "negative", "bool-attempts", "float-attempts", "text-attempts", "int-l1", "none-l1"],
)
def test_pure_invalid_budget_is_rejected(attempts, l1, pure_host_boundary):
    with pytest.raises(ValueError, match="invalid invocation audit budget"):
        module.provider_send_budget(attempts, l1=l1)
    assert pure_host_boundary.calls == []


def test_pure_invalid_l1_budget_cannot_open_host(pure_host_boundary):
    with pytest.raises(ValueError, match="invalid invocation audit budget"):
        _review().round(0).l1_scopes(0)
    assert pure_host_boundary.calls == []


@pytest.mark.parametrize(
    "round_index", [-1, True, 1.0, "1", None], ids=["negative", "bool", "float", "text", "none"]
)
def test_pure_invalid_round_cannot_open_host(round_index, pure_host_boundary):
    with pytest.raises(ValueError, match="invalid invocation audit round"):
        _review().round(round_index)
    assert pure_host_boundary.calls == []


@pytest.mark.parametrize(
    ("source", "strict"),
    [("", False), (None, False), (b"diff", False), (_SOURCE, 1)],
    ids=["empty-source", "missing-source", "bytes-source", "non-bool-strict"],
)
def test_pure_invalid_configuration_cannot_open_host(source, strict, pure_host_boundary):
    with pytest.raises(ValueError, match="invalid ordinary audit source"):
        module.OrdinaryReviewAudit(source=source, environ={}, strict=strict)
    assert pure_host_boundary.calls == []


@pytest.mark.parametrize("failure_site", ["factory", "scope"], ids=["factory", "scope"])
def test_pure_setup_failure_keeps_non_none_protocol_and_one_fixed_warning(
    failure_site, pure_host_boundary, capsys
):
    private_error = OSError("PRIVATE setup detail")
    if failure_site == "factory":
        pure_host_boundary.error = private_error
    else:
        pure_host_boundary.host.scope_error = private_error
    review = _review()
    view = review.round(0)
    scopes = view.l1_scopes(5) + (view.falsifier_scope(),)
    assert all(scope is not None for scope in scopes)
    assert len({id(scope) for scope in scopes}) == 4
    for scope in scopes:
        assert scope.strict is False
        assert scope.strict_failed is False
        assert scope.lifecycle_closed is False
        assert scope.start_send(object()) is None
        scope.warn("PRIVATE warning code")
        wrapped = SendAuditScope(scope)
        assert _start(wrapped) is None
        assert wrapped.disabled is True
        assert wrapped.parent is None
    # A transient factory/scope recovery must not silently revive capture.
    pure_host_boundary.error = None
    pure_host_boundary.host.scope_error = None
    later = review.round(4).falsifier_scope()
    assert later.start_send(object()) is None
    assert len(pure_host_boundary.calls) == 1
    assert pure_host_boundary.host.scope_calls == []
    assert capsys.readouterr() == ("", _WARNING)
    review.finalize()
    expected_finalization = (
        []
        if failure_site == "factory"
        else [{"outcome": "completed", "primary": None, "maintain": False}]
    )
    assert pure_host_boundary.host.finalize_calls == expected_finalization
    assert later.lifecycle_closed is True
    with pytest.raises(SendLifecycleClosed):
        later.start_send(object())
    with pytest.raises(SendLifecycleClosed):
        view.l1_scopes(5)
    with pytest.raises(SendLifecycleClosed):
        review.round(8).falsifier_scope()


def test_pure_disabled_send_scope_still_refuses_after_lifecycle_closure(pure_host_boundary):
    pure_host_boundary.error = OSError("PRIVATE unavailable")
    review = _review()
    scope = SendAuditScope(review.round(0).falsifier_scope())
    assert _start(scope) is None
    assert scope.disabled is True
    review.finalize()
    with pytest.raises(SendLifecycleClosed):
        _start(scope)
    assert len(pure_host_boundary.calls) == 1


def test_pure_internal_strict_setup_failure_is_non_none_and_refuses_admission(
    pure_host_boundary, capsys
):
    pure_host_boundary.error = OSError("PRIVATE unavailable")
    review = _review(strict=True)
    host_scope = review.round(0).falsifier_scope()
    assert host_scope is not None
    assert host_scope.strict is True
    assert host_scope.strict_failed is True
    assert host_scope.start_send(object()) is None
    with pytest.raises(CaptureUnavailable, match="audit_send_unavailable"):
        _start(SendAuditScope(host_scope))
    assert module.strict_failure(host_scope, ValueError("PRIVATE error")) is True
    assert capsys.readouterr() == ("", _WARNING)


@pytest.mark.parametrize(
    "error_type",
    [MemoryError, KeyboardInterrupt, SystemExit],
    ids=["memory", "interrupt", "system-exit"],
)
def test_pure_fatal_setup_error_propagates_by_identity(error_type, pure_host_boundary, capsys):
    error = error_type("PRIVATE fatal")
    pure_host_boundary.error = error
    review = _review()
    with pytest.raises(error_type) as caught:
        review.round(0).falsifier_scope()
    assert caught.value is error
    # Only the main-thread escaping boundary selects a terminal primary.
    assert review.diagnostics.contains(error) is False
    assert capsys.readouterr() == ("", "")


def test_pure_successful_finalize_is_once_only_and_does_not_maintain_storage(pure_host_boundary):
    review = _review()
    view = review.round(0)
    view.l1_scopes(5)
    review.finalize()
    review.finalize(primary=RuntimeError("unrelated later error"))
    assert pure_host_boundary.host.finalize_calls == [
        {"outcome": "completed", "primary": None, "maintain": False}
    ]
    with pytest.raises(SendLifecycleClosed):
        view.falsifier_scope()


def test_pure_default_finalize_failure_warns_and_preserves_success(pure_host_boundary, capsys):
    error = OSError("PRIVATE cleanup")
    pure_host_boundary.host.finalize_error = error
    review = _review()
    view = review.round(0)
    view.falsifier_scope()
    assert review.finalize() is None
    review.finalize()
    assert len(pure_host_boundary.host.finalize_calls) == 1
    assert review.diagnostics.contains(error) is False
    assert capsys.readouterr() == ("", _WARNING)
    with pytest.raises(SendLifecycleClosed):
        view.falsifier_scope()


@pytest.mark.parametrize(
    "error_type",
    [MemoryError, KeyboardInterrupt, SystemExit],
    ids=["memory", "interrupt", "system-exit"],
)
def test_pure_fatal_finalize_without_primary_propagates_and_marks_exact_error(
    error_type, pure_host_boundary, capsys
):
    error = error_type("PRIVATE fatal cleanup")
    error.__cause__ = ValueError("PRIVATE cause")
    error.__context__ = LookupError("PRIVATE context")
    before = _chain(error)
    pure_host_boundary.host.finalize_error = error
    review = _review()
    view = review.round(0)
    view.falsifier_scope()
    with pytest.raises(error_type) as caught:
        review.finalize()
    assert caught.value is error
    assert _chain(error) == before
    assert review.diagnostics.contains(error) is True
    assert review.diagnostics.contains(error_type("PRIVATE fatal cleanup")) is False
    review.finalize()
    assert len(pure_host_boundary.host.finalize_calls) == 1
    assert capsys.readouterr() == ("", _WARNING)
    with pytest.raises(SendLifecycleClosed):
        view.falsifier_scope()


def test_pure_internal_strict_finalize_raises_original_error(pure_host_boundary):
    error = OSError("PRIVATE strict cleanup")
    pure_host_boundary.host.finalize_error = error
    review = _review(strict=True)
    review.round(0).falsifier_scope()
    with pytest.raises(OSError) as caught:
        review.finalize()
    assert caught.value is error
    assert review.diagnostics.contains(error) is True
    assert pure_host_boundary.calls[0][1]["strict"] is True


@pytest.mark.parametrize(
    "error_type",
    [OSError, MemoryError, KeyboardInterrupt, SystemExit],
    ids=["ordinary", "memory", "interrupt", "system-exit"],
)
@pytest.mark.parametrize("strict", [False, True], ids=["default", "strict"])
def test_pure_entering_primary_and_chain_win_over_all_finalize_errors(
    error_type, strict, pure_host_boundary, capsys
):
    primary = LLMInvokeError(
        "PRIVATE primary", stderr="PRIVATE stderr", retryable=False, observed_usage=Usage(3, 7)
    )
    primary.__cause__ = ValueError("PRIVATE cause")
    primary.__context__ = LookupError("PRIVATE context")
    primary.__suppress_context__ = False
    before = (primary.args, _chain(primary), dict(vars(primary)))
    secondary = error_type("PRIVATE cleanup")
    pure_host_boundary.host.finalize_error = secondary
    review = _review(strict=strict)
    view = review.round(0)
    view.falsifier_scope()
    view.note_escape(primary)
    with pytest.raises(LLMInvokeError) as caught:
        try:
            raise primary
        finally:
            review.finalize(primary=primary)
    assert caught.value is primary
    assert (primary.args, _chain(primary), vars(primary)) == before
    assert review.diagnostics.contains(primary) is True
    assert review.diagnostics.contains(secondary) is False
    assert pure_host_boundary.host.finalize_calls == [
        {"outcome": "failed", "primary": primary, "maintain": False}
    ]
    review.finalize()
    assert len(pure_host_boundary.host.finalize_calls) == 1
    assert capsys.readouterr() == ("", _WARNING)


@pytest.mark.parametrize(
    "error_type", [OSError, MemoryError, SystemExit], ids=["ordinary", "memory", "system-exit"]
)
def test_pure_warning_double_fault_cannot_replace_entering_primary(
    error_type, pure_host_boundary, monkeypatch
):
    primary = RuntimeError("PRIVATE primary")
    primary.__cause__ = ValueError("PRIVATE cause")
    primary.__context__ = LookupError("PRIVATE context")
    before = _chain(primary)
    pure_host_boundary.host.finalize_error = OSError("PRIVATE cleanup")
    review = _review()
    review.round(0).falsifier_scope()
    calls = []

    def broken_print(*args, **kwargs):
        calls.append((args, kwargs))
        raise error_type("PRIVATE warning failure")

    monkeypatch.setattr(module, "print", broken_print, raising=False)
    with pytest.raises(RuntimeError) as caught:
        try:
            raise primary
        finally:
            review.finalize(primary=primary)
    assert caught.value is primary
    assert _chain(primary) == before
    review.finalize()
    assert len(calls) == 1
    assert calls[0][0] == (_WARNING.rstrip("\n"),)
    assert len(pure_host_boundary.host.finalize_calls) == 1


@pytest.mark.parametrize("error_type", [MemoryError, SystemExit], ids=["memory", "system-exit"])
def test_pure_fatal_warning_without_entering_primary_propagates_and_marks_identity(
    error_type, pure_host_boundary, monkeypatch
):
    ordinary = OSError("PRIVATE ordinary cleanup")
    fatal = error_type("PRIVATE fatal warning")
    fatal.__cause__ = ValueError("PRIVATE original cause")
    fatal.__context__ = LookupError("PRIVATE original context")
    before = _chain(fatal)
    pure_host_boundary.host.finalize_error = ordinary
    review = _review()
    view = review.round(0)
    view.falsifier_scope()
    calls = []

    def broken_print(*args, **kwargs):
        calls.append((args, kwargs))
        raise fatal

    monkeypatch.setattr(module, "print", broken_print, raising=False)
    with pytest.raises(error_type) as caught:
        review.finalize()
    assert caught.value is fatal
    assert fatal.__cause__ is before[0]
    assert fatal.__suppress_context__ is before[2]
    # This is the warning's first raise, inside the ordinary cleanup handler.
    # Its natural Python context must not be stripped or replaced by a wrapper.
    assert fatal.__context__ is ordinary
    assert review.diagnostics.contains(fatal) is True
    assert review.diagnostics.contains(ordinary) is False
    review.finalize()
    assert len(calls) == 1
    assert len(pure_host_boundary.host.finalize_calls) == 1
    with pytest.raises(SendLifecycleClosed):
        view.falsifier_scope()


@pytest.mark.parametrize("error_type", [MemoryError, SystemExit], ids=["memory", "system-exit"])
def test_pure_first_fatal_finalize_error_wins_over_second_fatal_warning(
    error_type, pure_host_boundary, monkeypatch
):
    first = error_type("PRIVATE first fatal")
    first.__cause__ = ValueError("PRIVATE original cause")
    first.__context__ = LookupError("PRIVATE original context")
    before = _chain(first)
    second = MemoryError("PRIVATE second fatal")
    pure_host_boundary.host.finalize_error = first
    review = _review()
    review.round(0).falsifier_scope()

    def broken_print(*_args, **_kwargs):
        raise second

    monkeypatch.setattr(module, "print", broken_print, raising=False)
    with pytest.raises(error_type) as caught:
        review.finalize()
    assert caught.value is first
    assert _chain(first) == before
    assert review.diagnostics.contains(first) is True
    assert review.diagnostics.contains(second) is False
    review.finalize()
    assert len(pure_host_boundary.host.finalize_calls) == 1


def test_pure_partial_coverage_notice_is_fixed_and_does_not_open_host(pure_host_boundary, capsys):
    module.OrdinaryReviewAudit.report_partial_coverage()
    assert capsys.readouterr() == (
        "",
        "code-forge: invocation audit covers ordinary OpenAI L1 and falsifier calls only; "
        "whole-review recording is incomplete\n",
    )
    assert pure_host_boundary.calls == []


@pytest.mark.parametrize(
    "flag", [False, None, 1, "yes", object()], ids=["false", "none", "int", "text", "object"]
)
def test_pure_strict_failure_requires_exact_true_not_truthy_attribute(flag):
    assert module.strict_failure(SimpleNamespace(strict_failed=flag), ValueError("private")) is False


def test_pure_strict_failure_keeps_typed_persistence_and_ordinary_deadline_distinct():
    assert module.strict_failure(None, AuditPersistenceError()) is True
    assert module.strict_failure(None, SendDeadlineError()) is False
    assert module.strict_failure(None, LLMInvokeError("private")) is False
    assert module.strict_failure(SimpleNamespace(strict_failed=True), ValueError("private")) is True


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"kind": "conn"}, "provider connection failed"),
        ({"is_timeout": True}, "provider request timed out"),
        ({"kind": "PRIVATE unknown kind"}, "provider request failed"),
        ({"kind": ["PRIVATE nested kind"]}, "provider request failed"),
        ({"exit_code": 503}, "provider HTTP 503"),
    ],
    ids=["connection", "timeout", "unknown-kind", "non-text-kind", "http-status"],
)
def test_pure_failure_label_is_closed_without_mutating_original(options, expected):
    error = LLMInvokeError("PRIVATE message", stderr="PRIVATE stderr", **options)
    error.__cause__ = ValueError("PRIVATE cause")
    before = error.args, dict(vars(error)), _chain(error)
    assert module.failure_label(error) == expected
    assert (error.args, vars(error), _chain(error)) == before


def test_pure_generic_failure_label_never_reads_exception_text():
    class UnprintableError(Exception):
        def __str__(self):
            raise AssertionError("private exception text must not be read")

    assert module.failure_label(UnprintableError()) == "provider request failed"


def test_pure_terminal_diagnostics_retain_only_one_primary_by_identity():
    class NeverCompareError(Exception):
        __hash__ = Exception.__hash__

        def __eq__(self, _other):
            raise AssertionError("terminal identity must not use equality")

    diagnostics = module.ReviewAuditDiagnostics()
    first = NeverCompareError("PRIVATE same text")
    second = NeverCompareError("PRIVATE same text")
    assert diagnostics.contains(None) is False
    assert diagnostics.contains(first) is False
    diagnostics.note(first)
    assert diagnostics.contains(first) is True
    assert diagnostics.contains(second) is False
    assert "PRIVATE" not in repr(diagnostics)
    diagnostics.note(second)
    assert diagnostics.contains(first) is False
    assert diagnostics.contains(second) is True
    diagnostics.clear()
    assert diagnostics.contains(None) is False
    assert diagnostics.contains(first) is False
    assert diagnostics.contains(second) is False


def test_pure_round_escape_uses_explicit_shared_diagnostic_context_only():
    diagnostics = module.ReviewAuditDiagnostics()
    review = _review(diagnostics=diagnostics)
    other_review = _review()
    primary = RuntimeError("PRIVATE primary")
    assert review.diagnostics is diagnostics
    review.round(3).note_escape(primary)
    assert diagnostics.contains(primary) is True
    assert other_review.diagnostics.contains(primary) is False
    diagnostics.clear()
    assert review.diagnostics.contains(primary) is False
