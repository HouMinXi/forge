# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Private, thread-confined stage capture for the real cross-repository worker.

Only first-use native outcomes are retained. This is not an environment snapshot
or graph freshness claim. Failed capture never changes native analysis results.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from itertools import islice
import os
from pathlib import Path
import threading
from uuid import uuid4

from . import _advisory_diagnostics as storage
from .advisory import AdvisoryFinding

PERSISTENCE_WARNING = "cross-repo-impact: persistent diagnostic unavailable"
_MAX_CAPTURE_BYTES = 65536
_MAX_HISTORY_BYTES = 65536
_MAX_ENTRIES = 256
_MAX_STAGES = 512
_MAX_STRING = 4096


@dataclass(frozen=True, slots=True)
class _Failure:
    kind: str
    message: str


@dataclass(frozen=True, slots=True)
class _Registry:
    entries: tuple[tuple[tuple[str, str], ...], ...]


@dataclass(frozen=True, slots=True)
class Record:
    observation: storage.Observation | None
    publication: storage.PublicationResult
    findings: tuple[AdvisoryFinding, ...] | None
    errors: tuple[str, ...] | None
    stages: tuple[tuple[str, object], ...]
    analysis_state: str | None = None
    owner: object = None
    capture_supported: bool = True


@dataclass(frozen=True, slots=True)
class ScopeRecord:
    expected: storage.ExpectedContext
    publication: storage.PublicationResult
    snapshot: object


class DiagnosticCapture:
    def __init__(
        self,
        *,
        execution_id,
        source_hash,
        diff_text,
        repo_root,
        excluded_roots,
        ticket_directory=None,
        diagnostic_ticket_directory=None,
    ):
        self.execution_id = execution_id
        self.repository_id = str(uuid4())
        self.source_hash = source_hash
        self.diff_sha256 = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
        self._root = Path(repo_root)
        self._canonical = self._root.resolve()
        self._identity = (
            self.execution_id,
            self.repository_id,
            self.source_hash,
            self.diff_sha256,
            self._root,
            self._canonical,
        )
        self._excluded = tuple(islice(excluded_roots, _MAX_ENTRIES + 1))
        self._pid = os.getpid()
        self._thread = threading.get_ident()
        self._active = True
        self._supported = True
        self._capture_bytes = 0
        self._history_bytes = 0
        self._stages: dict[str, object] = {}
        self._stage_store = self._stages
        self._stage_seal = ()
        self._record_owner = object()
        self._attempts = 0
        self._called = False
        self._warned = False
        self._binding = None
        self._storage_failed = False
        self.records: list[Record] = []
        self.refusal: str | None = None
        self.delivery_status = "not_attempted"
        self.last_delivery_origin = None
        # Independent scope channel; never consumes v1 sequence or
        # changes its publication/delivery outcome.
        self._scope_attempts = 0
        self._scope_binding = None
        self._scope_failed = False
        self.scope_records: list[ScopeRecord] = []
        self.ticket_directory = ticket_directory
        self.scope_ticket_status = "not_requested" if ticket_directory is None else "no_receipt"
        self.scope_ticket_path = None
        self.diagnostic_ticket_directory = diagnostic_ticket_directory
        self.diagnostic_ticket_status = (
            "not_requested" if diagnostic_ticket_directory is None else "no_receipt"
        )
        self.diagnostic_ticket_path = None
        self._diagnostic_ticket_attempted = set()
        self.diagnostic_ticket_outcomes = {}
        self.scope_delivery_status = "not_attempted"
        if len(self._excluded) > _MAX_ENTRIES:
            self._storage_failed = True
            self._disable("storage_scope_limit")
            self._excluded = ()

    def present_scope(self, snapshot, diff_text, repo_root):
        """Strict in-execution read with owner-held expected, never a lookup API.

        This records query facts at the existing summary point, before overall
        analysis/output completion. It does not assert processing_completed.
        Failure leaves the caller's genuine immediate summary available.
        """
        self.scope_delivery_status = "not_delivered"
        if self.ticket_directory is not None:
            self.scope_ticket_status = "unavailable"
            self.scope_ticket_path = None
        try:
            from . import _query_scope_receipt as scope

            if (
                not self._active
                or not self._supported
                or self._scope_failed
                or os.getpid() != self._pid
                or threading.get_ident() != self._thread
                or self._stages is not self._stage_store
                or tuple(self._stages.items()) != self._stage_seal
                or (
                    self.execution_id,
                    self.repository_id,
                    self.source_hash,
                    self.diff_sha256,
                    self._root,
                    self._canonical,
                )
                != self._identity
                or Path(repo_root) != self._root
                or Path(repo_root).resolve() != self._canonical
                or hashlib.sha256(diff_text.encode("utf-8")).hexdigest() != self.diff_sha256
                or self._scope_attempts >= storage.MAX_OBSERVATIONS
            ):
                return None
            # Reserve before allocation/publication. v1 attempts are independent.
            self._scope_attempts += 1
            expected = storage.ExpectedContext(
                self.execution_id,
                self.repository_id,
                self.source_hash,
                self.diff_sha256,
                self._scope_attempts,
            )
            facts = {
                name: getattr(snapshot, name)
                for name in (
                    "scope",
                    "registered",
                    "attempted",
                    "completed",
                    "unavailable",
                    "with_callers",
                    "query_occurrences_accounted",
                )
            }
            wanted = scope.freeze(facts)
            if self._scope_binding is None:
                self._scope_binding = storage.bind_storage(self._canonical, self._excluded)
            binding = self._scope_binding
            if type(binding) is not storage.StorageBinding or binding.source_root != self._identity[5]:
                raise ValueError("foreign scope storage binding")
            storage._validate_binding(binding)
            # pathlib may retain oversized noncanonical constructor strings even
            # when parts/str/equality are bounded. Retain only our frozen root.
            binding = storage.StorageBinding(self._identity[5], binding.identity)
            self._scope_binding = binding
            publication = scope.publish(binding, expected, facts)
            # Reject oversized/malformed results before any owner history retains
            # them. This does not replace the later strict filesystem read.
            scope.validate_publication(publication, expected)
            if type(publication) is not storage.PublicationResult or (
                publication.status == "published"
                and (
                    type(publication.locator) is not storage.Locator
                    or publication.locator.binding != binding
                    or self._scope_binding != binding
                )
            ):
                raise ValueError("foreign scope storage result")
            if publication.status == "published":
                locator = publication.locator
                publication = storage.PublicationResult(
                    "published",
                    storage.Locator(
                        binding,
                        locator.directories,
                        locator.filename,
                        locator.identity,
                        locator.byte_count,
                        locator.digest,
                    ),
                    cleanup=publication.cleanup,
                )
            self._scope_binding = binding
            self.scope_records.append(ScopeRecord(expected, publication, wanted))
            actual = scope.read(publication, expected)
            if (
                type(actual) is not type(wanted)
                or actual != wanted
                or not self._active
                or self._stages is not self._stage_store
                or tuple(self._stages.items()) != self._stage_seal
                or os.getpid() != self._pid
                or threading.get_ident() != self._thread
                or (
                    self.execution_id,
                    self.repository_id,
                    self.source_hash,
                    self.diff_sha256,
                    self._root,
                    self._canonical,
                )
                != self._identity
            ):
                raise ValueError("unexpected scope reader result")
            self.scope_delivery_status = "read"
            if self.ticket_directory is not None:
                self.scope_ticket_status = "unavailable"
                self.scope_ticket_path = None
                try:
                    from ._scope_history import export_ticket

                    self.scope_ticket_path = export_ticket(self.ticket_directory, publication, expected)
                    self.scope_ticket_status = "exported"
                except Exception:  # noqa: BLE001 -- Optional ticket failure is not an analysis failure or proof of no file.
                    self.scope_ticket_status = "unavailable"
            return actual
        except Exception:  # noqa: BLE001 -- Scope failure must not alter native analysis or v1.
            self._scope_failed = True
            self._scope_binding = None
            return None
        except BaseException:
            self._scope_failed = True
            self._scope_binding = None
            raise

    def close(self):
        self._active = False

    def admit(self, diff_text, repo_root):
        """Refuse before any cache return or error clearing, without State errors."""
        self._called = True
        try:
            accepted = (
                self._active
                and self._stages is self._stage_store
                and len(self._stages) <= _MAX_STAGES
                and tuple(self._stages.items()) == self._stage_seal
                and (
                    self.execution_id,
                    self.repository_id,
                    self.source_hash,
                    self.diff_sha256,
                    self._root,
                    self._canonical,
                )
                == self._identity
                and os.getpid() == self._pid
                and threading.get_ident() == self._thread
                and Path(repo_root) == self._root
                and Path(repo_root).resolve() == self._canonical
                and hashlib.sha256(diff_text.encode("utf-8")).hexdigest() == self.diff_sha256
            )
        except Exception:  # noqa: BLE001 -- Failed diagnostic admission is not a gate failure.
            accepted = False
        self.delivery_status = "not_attempted" if accepted else "not_delivered"
        self.scope_delivery_status = "not_attempted" if accepted else "not_delivered"
        self.last_delivery_origin = None
        if not accepted:
            self.refusal = "context_unavailable"
        return accepted

    def _disable(self, reason="capture_unavailable"):
        self._supported = False
        self.refusal = reason
        self._stages.clear()
        self._stage_seal = ()

    def _string_bytes(self, value):
        if type(value) is not str or len(value) > _MAX_STRING:
            raise ValueError("capture limit")
        count = len(value.encode("utf-8", errors="strict"))
        if count > _MAX_STRING:
            raise ValueError("capture limit")
        return count

    def _freeze(self, value, kind):
        if kind == "registry":
            if type(value) is not list or len(value) > _MAX_ENTRIES:
                raise ValueError("capture limit")
            entries, size = [], 0
            for entry in value:
                fields = []
                for key in ("path", "alias", "data_dir"):
                    if key in entry:
                        size += self._string_bytes(entry[key])
                        if self._capture_bytes + size > _MAX_CAPTURE_BYTES:
                            raise ValueError("capture limit")
                        fields.append((key, entry[key]))
                entries.append(tuple(fields))
            return _Registry(tuple(entries)), size
        if isinstance(value, Path):
            if type(value) is not type(Path()):
                raise ValueError("unsupported path")
            return value, self._string_bytes(str(value))
        if value is None:
            return value, 0
        return value, self._string_bytes(value)

    @staticmethod
    def _thaw(value):
        if isinstance(value, _Registry):
            return [dict(entry) for entry in value.entries]
        return value

    def stage(self, key, operation, *, kind="value", error_type=ValueError):
        """Call once at its native branch; never re-call after capture overflow."""
        if not self._supported:
            return operation()
        if key in self._stages:
            value = self._stages[key]
            if isinstance(value, _Failure):
                if value.kind == "import":
                    raise ImportError(value.message)
                raise error_type(value.message)
            return self._thaw(value)
        if len(self._stages) >= _MAX_STAGES:
            self._disable("capture_limit")
            return operation()
        try:
            value = operation()
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            # Never retain exceptions: traceback/context/args may own arbitrary
            # mutable graphs. The first failure still reaches the native catch.
            try:
                message = str(exc)
                size = self._string_bytes(message)
                if self._capture_bytes + size > _MAX_CAPTURE_BYTES:
                    raise ValueError("capture limit")
                self._stages[key] = _Failure(
                    "import" if isinstance(exc, ImportError) else "native", message
                )
                self._capture_bytes += size
                self._stage_seal = tuple(self._stages.items())
            except Exception:  # noqa: BLE001 -- Capture must not replace the native failure.
                self._disable("capture_limit")
            raise
        try:
            frozen, size = self._freeze(value, kind)
            if self._capture_bytes + size > _MAX_CAPTURE_BYTES:
                raise ValueError("capture limit")
            self._stages[key] = frozen
            self._capture_bytes += size
            self._stage_seal = tuple(self._stages.items())
            return self._thaw(frozen)
        except Exception:  # noqa: BLE001 -- Use the result already obtained; never retry the native call.
            self._disable("capture_limit")
            return value

    def require_absolute(self, *paths):
        if any(not isinstance(path, Path) or not path.is_absolute() for path in paths):
            self._disable("relative_binding")

    def _snapshot(self, findings, errors):
        if len(findings) > 10 or len(errors) > _MAX_ENTRIES + 2:
            raise ValueError("history limit")
        size = 0
        for finding in findings:
            if type(finding) is not AdvisoryFinding:
                raise ValueError("unsupported finding")
            for value in (
                finding.id,
                finding.axis,
                finding.file,
                finding.description,
                finding.attribution,
            ):
                size += self._string_bytes(value)
        for error in errors:
            size += self._string_bytes(error)
        if self._history_bytes + size > _MAX_HISTORY_BYTES:
            raise ValueError("history limit")
        self._history_bytes += size
        return tuple(findings), tuple(errors)

    def finish(self, state, findings=(), errors=()):
        self._called = True
        self.delivery_status = "not_delivered"
        if self._attempts >= storage.MAX_OBSERVATIONS:
            self._disable("observation_limit")
            return Record(
                None,
                storage.PublicationResult("not_published", failure="observation_limit"),
                None,
                None,
                (),
                state,
                self._record_owner,
                False,
            )
        # Reserve before allocation or any publisher operation, even on failure.
        self._attempts += 1
        observation = None
        frozen_findings = frozen_errors = None
        publication = storage.PublicationResult("not_published", failure="capture_unavailable")
        publishing = False
        try:
            context = storage.ExpectedContext(
                self.execution_id, self.repository_id, self.source_hash, self.diff_sha256, self._attempts
            )
            observation = storage.make_observation(context, state)
            try:
                frozen_findings, frozen_errors = self._snapshot(findings, errors)
            except Exception:
                self._disable("history_limit")
                raise
            if self._supported and not self._storage_failed:
                if self._binding is None:
                    self._binding = storage.bind_storage(self._canonical, self._excluded)
                publishing = True
                publication = storage.publish_observation(self._binding, observation)
                storage.validate_publication(publication, context)
                if publication.status != "published":
                    self._storage_failed = True
        except Exception:  # noqa: BLE001 -- Diagnostic-only failures cannot enter State.infra_errors.
            self._storage_failed = True
            publication = storage.PublicationResult(
                "unknown" if publishing else "not_published", failure="diagnostic_unavailable"
            )
        record = Record(
            observation,
            publication,
            frozen_findings,
            frozen_errors,
            tuple(self._stages.items()),
            state,
            self._record_owner,
            self._supported,
        )
        self.records.append(record)
        return record

    def warning(self):
        if self._warned:
            return ()
        self._warned = True
        return (PERSISTENCE_WARNING,)

    def present(self, record, *, origin="actual"):
        # Delivery origin never changes the persisted original observation.
        self.last_delivery_origin = origin
        self.delivery_status = "not_delivered"
        if self.diagnostic_ticket_directory is not None and self.diagnostic_ticket_status != "exported":
            self.diagnostic_ticket_status = "unavailable"
        if origin not in {"actual", "cache"}:
            return self.warning()
        if type(record) is not Record or record.owner is not self._record_owner:
            self.refusal = "foreign_observation"
            return self.warning()
        observation = record.observation
        try:
            if observation is None:
                raise ValueError("missing observation")
            context = observation.context
            if (
                context.execution_id,
                context.repository_id,
                context.source_hash,
                context.diff_sha256,
            ) != self._identity[:4]:
                self.refusal = "foreign_observation"
                return self.warning()
            if self.diagnostic_ticket_directory is not None:
                storage._validate_context(context)
                self.diagnostic_ticket_outcomes.setdefault(context.observation_sequence, None)
            read = storage.read_observation(record.publication, observation.context)
            if type(read) is not storage.Observation or read.context != observation.context:
                raise ValueError("invalid reader return")
            if read.analysis_state != observation.analysis_state or type(read.diagnostics) is not tuple:
                raise ValueError("invalid reader return")
            if len(read.diagnostics) != int(read.analysis_state == "no_named_targets") or any(
                type(item) is not storage.Diagnostic for item in read.diagnostics
            ):
                raise ValueError("invalid reader return")
            messages = tuple(item.message for item in read.diagnostics)
            if any(
                type(message) is not str or len(message.encode("utf-8")) > 256 for message in messages
            ):
                raise ValueError("invalid presentation")
            self.delivery_status = "read"
            self._export_diagnostic_ticket(record, context)
            return messages
        except Exception:  # noqa: BLE001 -- Retain the genuine immediate warning when persistence cannot be consumed.
            immediate = (storage.MESSAGE,) if record.analysis_state == "no_named_targets" else ()
            return immediate + self.warning()

    def _export_diagnostic_ticket(self, record, context):
        if (
            self.diagnostic_ticket_directory is None
            or context.observation_sequence in self._diagnostic_ticket_attempted
        ):
            return
        # Export is a new side effect: closed/foreign callers may not create it.
        if (
            not self._active
            or os.getpid() != self._pid
            or threading.get_ident() != self._thread
            or (
                self.execution_id,
                self.repository_id,
                self.source_hash,
                self.diff_sha256,
                self._root,
                self._canonical,
            )
            != self._identity
        ):
            self.diagnostic_ticket_status = "unavailable"
            return
        self._diagnostic_ticket_attempted.add(context.observation_sequence)
        self.diagnostic_ticket_status = "unavailable"
        self.diagnostic_ticket_path = None
        try:
            from ._diagnostic_history import export_ticket

            path = export_ticket(self.diagnostic_ticket_directory, record.publication, context)
            self.diagnostic_ticket_path = path
            self.diagnostic_ticket_outcomes[context.observation_sequence] = path
            self.diagnostic_ticket_status = "exported"
        except Exception:  # noqa: BLE001 -- Optional history cannot change native diagnostic delivery.
            self.diagnostic_ticket_status = "unavailable"

    def complete_not_run(self):
        return None if self._called else self.finish("not_run")
