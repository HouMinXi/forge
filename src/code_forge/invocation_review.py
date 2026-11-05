"""Partial ordinary-review recording, without whole-review strict authority.

The CLI owns one fixed acquired-source lifecycle. Immutable round views only
select the normal OpenAI L1 and falsifier calls; they confer no review credit.
"""

from dataclasses import dataclass, field
import hashlib
import sys
from uuid import uuid4


_UNAVAILABLE = "code-forge: invocation audit unavailable; recording is incomplete"
_PARTIAL = (
    "code-forge: invocation audit covers ordinary OpenAI L1 and falsifier calls only; "
    "whole-review recording is incomplete"
)


def provider_send_budget(max_attempts, *, l1):
    """Current recovery graph: m retries + 2 continuation + 1 headroom.

    Correction spends a retry slot. L1 can additionally invoke one excerpt
    repair with m=1 and the same recovery branches, hence four more sends.
    Redirect hops and aggregate host quotas are accounted separately.
    """
    if type(max_attempts) is not int or max_attempts < 1 or type(l1) is not bool:
        raise ValueError("invalid invocation audit budget")
    return max_attempts + (7 if l1 else 3)


def failure_label(error):
    from .llm_invoke import _retry_cause

    return _retry_cause(error, audited=True)


def strict_failure(scope, error):
    from .llm_invoke import AuditPersistenceError

    return isinstance(error, AuditPersistenceError) or (
        scope is not None and scope.strict_failed is True
    )


@dataclass
class ReviewAuditDiagnostics:
    """One actual escaping primary, selected on the main thread only."""

    _primary: BaseException | None = field(default=None, repr=False)

    def clear(self):
        self._primary = None

    def note(self, primary):
        self._primary = primary

    def contains(self, primary):
        return self._primary is not None and self._primary is primary


@dataclass(frozen=True)
class ReviewAuditRound:
    _review: "OrdinaryReviewAudit" = field(repr=False)
    round_index: int

    def l1_scopes(self, max_attempts):
        budget = provider_send_budget(max_attempts, l1=True)
        return tuple(
            self._review._scope(self.round_index, name, "review", budget)
            for name in ("qodo", "expert", "adversarial")
        )

    def falsifier_scope(self):
        return self._review._scope(self.round_index, None, "falsify", provider_send_budget(5, l1=False))

    def note_escape(self, primary):
        self._review.diagnostics.note(primary)


class _UnavailableSendAudit:
    """No capture admission, but retain selected transport privacy/lifecycle."""

    def __init__(self, review):
        self._review = review
        self.strict = review.strict

    @property
    def strict_failed(self):
        return self.strict

    @property
    def lifecycle_closed(self):
        return self._review._closed

    def start_send(self, _request):
        from .invocation_send_audit import SendLifecycleClosed

        if self.lifecycle_closed:
            raise SendLifecycleClosed()
        return None

    def warn(self, _code):
        self._review._warning()


class OrdinaryReviewAudit:
    """Lazy, fixed-diff lifecycle; public ordinary strict stays unsupported."""

    def __init__(self, *, source, environ, diagnostics=None, strict=False):
        if type(source) is not str or not source or type(strict) is not bool:
            raise ValueError("invalid ordinary audit source")
        self._source, self._environ = source, environ
        self.diagnostics = diagnostics if diagnostics is not None else ReviewAuditDiagnostics()
        self.strict = strict
        self._host = None
        self._failed = self._warned = self._closed = False

    @staticmethod
    def report_partial_coverage():
        print(_PARTIAL, file=sys.stderr)

    def _warning(self, primary=None):
        self._failed = True
        if self._warned:
            return
        self._warned = True
        try:
            print(_UNAVAILABLE, file=sys.stderr)
        except BaseException as error:  # noqa: BLE001 - preserve entering primary
            if primary is None and (isinstance(error, MemoryError) or not isinstance(error, Exception)):
                raise

    def round(self, round_index):
        if type(round_index) is not int or round_index < 0:
            raise ValueError("invalid invocation audit round")
        return ReviewAuditRound(self, round_index)

    def _open_host(self):
        from .invocation_audit import InvocationContext
        from .invocation_host import HostAuditSession
        from .source import compute_source_hash

        context = InvocationContext(
            str(uuid4()),
            compute_source_hash(git_diff=self._source),
            str(uuid4()),
            None,
            None,
            None,
            None,
            "review",
            None,
        )
        encoded = self._source.encode("utf-8")
        manifest = dict(
            schema_version=1,
            run_id=context.run_id,
            snapshot_id=context.snapshot_id,
            mode="diff",
            legacy_source_hash=context.source_hash,
            exact_diff_sha256=hashlib.sha256(encoded).hexdigest(),
            diff_bytes=len(encoded),
            files=[],
            repositories=[],
        )
        return HostAuditSession.from_user_state(
            context=context,
            manifest=manifest,
            strict=self.strict,
            environ=self._environ,
        )

    def _scope(self, round_index, pass_name, purpose, budget):
        from .invocation_send_audit import SendLifecycleClosed

        if self._closed:
            raise SendLifecycleClosed()
        if not self._failed:
            try:
                if self._host is None:
                    self._host = self._open_host()
                return self._host.scope(
                    round_index=round_index,
                    pass_name=pass_name,
                    purpose=purpose,
                    max_provider_sends=budget,
                )
            except MemoryError:
                raise
            except Exception:  # noqa: BLE001 - keep selected privacy on setup failure
                self._warning()
        return _UnavailableSendAudit(self)

    def finalize(self, primary=None):
        if self._closed:
            return
        try:
            if self._host is not None:
                self._host.finalize(
                    outcome="completed" if primary is None else "failed",
                    primary=primary,
                    maintain=False,
                )
        except BaseException as error:  # noqa: BLE001 - cleanup never replaces a primary
            fatal = isinstance(error, MemoryError) or not isinstance(error, Exception)
            preserved = primary if primary is not None else error if fatal or self.strict else None
            try:
                self._warning(primary=preserved)
            except BaseException as warning_error:
                self.diagnostics.note(warning_error)
                raise
            if primary is None and (
                self.strict or isinstance(error, MemoryError) or not isinstance(error, Exception)
            ):
                self.diagnostics.note(error)
                raise
        finally:
            self._closed = True
