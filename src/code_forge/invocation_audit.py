"""Bounded, local observations of host-owned invocation attempts."""

from __future__ import annotations

from contextlib import contextmanager
import copy
from dataclasses import dataclass, fields
import datetime
import errno
import hashlib
import json
import math
import os
import re
import stat
import sys
import threading
import time
from types import MappingProxyType
import uuid
from pathlib import Path
from typing import Callable, Mapping


@dataclass(frozen=True)
class InvocationContext:
    run_id: str
    source_hash: str | None
    snapshot_id: str | None
    round_index: int | None
    pass_name: str | None
    group_id: str | None
    group_diff_sha256: str | None
    purpose: str
    parent_logical_id: str | None


@dataclass(frozen=True)
class BackendObservation:
    format: str
    requested_backend: str
    requested_model: str | None
    requested_effort: str | None
    endpoint_fingerprint: str | None
    observed_backend: str | None = None
    observed_model: str | None = None
    observed_effort: str | None = None


@dataclass(frozen=True)
class UsageObservation:
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    reasoning_tokens: int | None
    availability: str
    input_semantics: str
    output_semantics: str
    snapshot_kind: str
    observation_source: str
    scope: str
    native_usage: Mapping[str, int | None]


@dataclass(frozen=True)
class AuditCapability:
    profile_id: str
    filesystem_type: str
    root_device: int
    root_inode: int
    root_mount_id: int
    runtime_fingerprint: str


@dataclass(frozen=True)
class AuditFault:
    code: str
    phase: str
    attempt_id: str | None


@dataclass(frozen=True)
class StartAcknowledgement:
    admitted: bool
    attempt_id: str | None
    fault: AuditFault | None


@dataclass(frozen=True)
class RawRef:
    artifact_id: str
    layer: str
    sha256: str
    retained_bytes: int
    original_bytes: int | None
    partial: bool


@dataclass(frozen=True)
class AcquiredAcknowledgement:
    admitted: bool
    observation_id: str | None
    ref: RawRef | None
    fault: AuditFault | None


@dataclass(frozen=True)
class AuditAcknowledgement:
    admitted: bool
    fault: AuditFault | None


@dataclass(frozen=True)
class RecorderSnapshot:
    schema_version: int
    run_id: str
    state: str
    audit_complete: bool
    faults: tuple[AuditFault, ...]
    capability: AuditCapability | None
    summary: dict
    attempt_contexts: Mapping[str, InvocationContext]


@dataclass(frozen=True)
class ResponseProjection:
    observation_id: str
    source_observation_id: str
    frame_offset: int
    frame_length: int
    usage: UsageObservation | None
    observed_backend: BackendObservation | None


@dataclass(frozen=True)
class AttemptProjection:
    attempt_id: str
    logical_id: str
    parent_id: str | None
    cause: str
    context: InvocationContext
    requested_backend: BackendObservation
    response_observations: tuple[ResponseProjection, ...]
    raw_refs: tuple[RawRef, ...]
    outcome: str
    dispatch_state: str
    duration_s: float | None
    usage: Mapping


@dataclass(frozen=True)
class ScopedInvocationSnapshot:
    schema_version: int
    run_id: str
    state: str
    audit_complete: bool
    faults: tuple[AuditFault, ...]
    scope: Mapping
    attempts: tuple[AttemptProjection, ...]
    summary: Mapping
    input_manifest: Mapping | None


ROOT_BYTES = 512 * 1024 * 1024
RUN_BYTES = 16 * 1024 * 1024
MARKER_BYTES = 16 * 1024
BODY_BYTES = 256 * 1024
EVENT_BYTES = 2 * 1024
RETAIN_SECONDS = 7 * 86400
_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens")
_NATIVE = frozenset(
    (
        *_FIELDS,
        "prompt_tokens",
        "completion_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "promptTokenCount",
        "candidatesTokenCount",
        "cachedContentTokenCount",
        "thoughtsTokenCount",
    )
)
_OUTCOMES = frozenset(("completed", "failed", "refused", "cancelled", "incomplete"))
_TERMINAL = _OUTCOMES - {"refused"}
_DISPATCH = frozenset(
    ("not_dispatched", "entered_api_send", "entered_cli_launch", "possibly_sent", "unknown")
)
_LAYERS = frozenset(("wire", "stdout", "stderr", "decoded"))
_CAUSES = frozenset(
    ("initial", "retry", "correction", "continuation", "headroom", "excerpt_repair", "tool")
)
_MARKERS = frozenset(("final", "fault", "freeze", "tombstone"))
_COMMON = frozenset(("schema_version", "run_id", "event_id", "sequence", "event", "utc", "elapsed_s"))
_EXTRA = {
    "run_open": {"context", "capability", "owner", "reservation"},
    "start": {
        "context",
        "attempt_id",
        "logical_id",
        "parent_id",
        "cause",
        "backend",
        "request_digest",
        "request_bytes",
    },
    "acquired": {"attempt_id", "observation_id", "layer", "ref", "complete", "usage"},
    "settled_acquired": {
        "attempt_id", "observation_id", "layer", "ref", "complete", "usage", "dispatch_state",
    },
    "response_observed": {
        "attempt_id", "observation_id", "source_observation_id", "frame_offset", "frame_length",
        "usage", "observed_backend",
    },
    "input_manifest": {"sha256", "bytes", "source_hash", "snapshot_id"},
    "finish": {"attempt_id", "outcome", "error_class", "duration_s", "dispatch_state"},
    "run_final": {"outcome", "audit_complete"},
    "freeze": {"frozen"},
    "audit_fault": {"code", "phase", "attempt_id"},
}
_FAULTS = frozenset(
    (
        "invalid_input",
        "identity_conflict",
        "finalized",
        "unsupported_profile",
        "lock_timeout",
        "quota_refused",
        "persistence_error",
        "integrity_error",
    )
)
_PHASES = frozenset(
    ("open", "start", "acquired", "finish", "finalize", "freeze", "replay", "maintenance")
)


class _Rejected(Exception):
    def __init__(self, code, *, preserve=False):
        self.code = code
        self.preserve = preserve


def _require(condition, code="invalid_input"):
    if not condition:
        raise _Rejected(code)


def _uuid(value, optional=False):
    if value is None and optional:
        return value
    _require(type(value) is str and len(value) == 36)
    try:
        _require(str(uuid.UUID(value)) == value)
    except ValueError:
        raise _Rejected("invalid_input") from None
    return value


def _hash(value, optional=False):
    _require(
        (value is None and optional)
        or (type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None)
    )
    return value


def _text(value, optional=False):
    if value is None and optional:
        return value
    _require(
        type(value) is str
        and 0 < len(value) <= 128
        and value.isascii()
        and all(32 <= ord(c) < 127 for c in value)
        and not any(c in value for c in ("://", "?", "@", "\\"))
    )
    return value


def _number(value, optional=False, integer=False):
    if value is None and optional:
        return value
    _require(type(value) is int if integer else type(value) in (int, float))
    _require(value >= 0)
    if not integer:
        try:
            finite = math.isfinite(value)
        except OverflowError:
            raise _Rejected("invalid_input") from None
        _require(finite)
    return value


def _vocabulary(value, allowed):
    _require(type(value) is str and value in allowed)
    return value


def _record(value, cls):
    _require(type(value) is cls)
    return {f.name: getattr(value, f.name) for f in fields(cls)}


def _context(value):
    data = _record(value, InvocationContext)
    _uuid(data["run_id"])
    _hash(data["source_hash"], True)
    _uuid(data["snapshot_id"], True)
    _require((data["source_hash"] is None) == (data["snapshot_id"] is None))
    _number(data["round_index"], True, True)
    _text(data["pass_name"], True)
    _uuid(data["group_id"], True)
    _hash(data["group_diff_sha256"], True)
    _require((data["group_id"] is None) == (data["group_diff_sha256"] is None))
    _text(data["purpose"])
    _uuid(data["parent_logical_id"], True)
    return data


def _backend(value):
    data = _record(value, BackendObservation)
    _vocabulary(data["format"], ("openai", "anthropic", "vertex", "cli", "tool"))
    for name in (
        "requested_backend",
        "requested_model",
        "requested_effort",
        "observed_backend",
        "observed_model",
        "observed_effort",
    ):
        _text(data[name], name != "requested_backend")
    _hash(data["endpoint_fingerprint"], True)
    return data


def _usage(value):
    if value is None:
        return None
    data = _record(value, UsageObservation)
    for name in _FIELDS:
        _number(data[name], True, True)
    _vocabulary(data["availability"], ("known", "partial", "unknown", "invalid"))
    count = sum(data[n] is not None for n in _FIELDS)
    _require(
        (data["availability"] == "known" and count == 4)
        or (data["availability"] == "partial" and 0 < count < 4)
        or (data["availability"] in ("unknown", "invalid") and count == 0)
    )
    _vocabulary(data["input_semantics"], ("total_includes_cache", "uncached_excludes_cache", "unknown"))
    _vocabulary(
        data["output_semantics"], ("total_includes_reasoning", "visible_excludes_reasoning", "unknown")
    )
    _vocabulary(data["snapshot_kind"], ("final", "cumulative", "incremental"))
    _vocabulary(data["observation_source"], ("native", "derived"))
    _vocabulary(data["scope"], ("attempt", "cli_process"))
    _require(type(data["native_usage"]) in (dict, MappingProxyType))
    native = data["native_usage"]
    _require(len(native) <= len(_NATIVE) and all(type(k) is str and k in _NATIVE for k in native))
    data["native_usage"] = {k: _number(v, True, True) for k, v in native.items()}
    for total, subset, sem, included in (
        ("input_tokens", "cached_input_tokens", "input_semantics", "total_includes_cache"),
        ("output_tokens", "reasoning_tokens", "output_semantics", "total_includes_reasoning"),
    ):
        if data[sem] == included and data[total] is not None and data[subset] is not None:
            _require(data[subset] <= data[total])
    return data


def _closed(value, keys):
    _require(
        type(value) is dict and all(type(key) is str for key in value) and set(value) == set(keys),
        "integrity_error",
    )
    return value


def _immutable(value):
    if type(value) is dict:
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_immutable(item) for item in value)
    return value


def _manifest(value, context):
    _closed(value, (
        "schema_version", "run_id", "snapshot_id", "mode", "legacy_source_hash",
        "exact_diff_sha256", "diff_bytes", "files", "repositories",
    ))
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1)
    _require(context["source_hash"] is not None)
    _uuid(value["run_id"])
    _uuid(value["snapshot_id"])
    _hash(value["legacy_source_hash"])
    _require(
        value["run_id"] == context["run_id"]
        and value["snapshot_id"] == context["snapshot_id"]
        and value["legacy_source_hash"] == context["source_hash"]
    )
    _vocabulary(value["mode"], ("diff", "files", "joint"))
    _require(type(value["files"]) is list and type(value["repositories"]) is list)
    _require(len(value["files"]) <= 512 and len(value["repositories"]) <= 512)
    paths = []
    for row in value["files"]:
        _closed(row, ("path", "sha256", "bytes"))
        path = row["path"]
        _require(
            type(path) is str and 0 < len(path.encode("utf-8")) <= 1024
            and not path.startswith("/") and "\\" not in path and "\0" not in path
            and all(part not in ("", ".", "..") for part in path.split("/"))
        )
        _hash(row["sha256"])
        _require(_number(row["bytes"], integer=True) <= (1 << 63) - 1)
        paths.append(path)
    _require(paths == sorted(set(paths)))
    labels = set()
    for row in value["repositories"]:
        _closed(row, ("label", "revision", "sha256", "bytes"))
        for key in ("label", "revision"):
            text = row[key]
            _require(type(text) is str and 0 < len(text.encode("utf-8")) <= 128 and "\0" not in text)
        _require(row["label"] not in labels)
        labels.add(row["label"])
        _hash(row["sha256"])
        _require(_number(row["bytes"], integer=True) <= (1 << 63) - 1)
    if value["mode"] == "diff":
        _hash(value["exact_diff_sha256"])
        _require(_number(value["diff_bytes"], integer=True) <= (1 << 63) - 1)
        _require(not value["files"] and not value["repositories"])
    else:
        _require(value["exact_diff_sha256"] is None and value["diff_bytes"] is None)
        _require(not value["repositories"] if value["mode"] == "files" else not value["files"])
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    _require(len(data) <= BODY_BYTES, "quota_refused")
    return data


def _manifest_event(events):
    return next((event for event in events if event["event"] == "input_manifest"), None)


def _manifest_contents(fd, events):
    event = _manifest_event(events)
    if event is None:
        return None
    data = _read_file(fd, "input-manifest.json", BODY_BYTES)
    value = _decode_json(data)
    encoded = _manifest(value, events[0]["context"])
    _require(
        data == encoded and len(data) == event["bytes"]
        and hashlib.sha256(data).hexdigest() == event["sha256"], "integrity_error",
    )
    return value


def _response_projection(event, attempts, observations):
    _uuid(event["observation_id"])
    _uuid(event["source_observation_id"])
    attempt = _admit_acquired(event["attempt_id"], attempts, code="integrity_error")
    raw = observations.get(event["source_observation_id"])
    _require(
        raw is not None and raw["event"] in ("acquired", "settled_acquired")
        and raw["attempt_id"] == event["attempt_id"] and raw["layer"] in ("wire", "stdout"),
        "integrity_error",
    )
    offset = _number(event["frame_offset"], integer=True)
    length = _number(event["frame_length"], integer=True)
    _require(length > 0 and offset + length <= raw["ref"]["retained_bytes"], "integrity_error")
    usage = event["usage"]
    backend = event["observed_backend"]
    _require(usage is not None or backend is not None, "integrity_error")
    if usage is not None:
        _usage(UsageObservation(**usage))
        _require(usage["observation_source"] == "native", "integrity_error")
        scope = "cli_process" if attempt["start"]["backend"]["format"] == "cli" else "attempt"
        _require(usage["scope"] == scope, "integrity_error")
    if backend is not None:
        _backend(BackendObservation(**backend))
        requested = attempt["start"]["backend"]
        _require(
            all(backend[key] == requested[key] for key in requested if not key.startswith("observed_"))
            and any(backend[key] is not None for key in backend if key.startswith("observed_")),
            "integrity_error",
        )
    return attempt, raw


def _response_frame(data, offset, length):
    frame = data[offset : offset + length]
    if frame.startswith(b"data:"):
        _require(frame.endswith((b"\n\n", b"\r\n\r\n")), "integrity_error")
        frame = frame[5:].strip()
    value = _decode_json(frame)
    _require(type(value) is dict, "integrity_error")


def _dispatch_observation(attempt, dispatch):
    _vocabulary(dispatch, _DISPATCH - {"not_dispatched"})
    old = attempt["dispatch_observed"]
    known = ("entered_api_send", "entered_cli_launch")
    _require(old not in known or dispatch not in known or old == dispatch, "integrity_error")
    if old not in known:
        attempt["dispatch_observed"] = dispatch


def _owner_identity(value):
    value = _closed(value, ("pid", "start_ticks", "owner_id"))
    _number(value["pid"], integer=True)
    _require(value["pid"] > 0, "integrity_error")
    ticks = value["start_ticks"]
    _require(type(ticks) is str and ticks.isascii() and ticks.isdecimal(), "integrity_error")
    _uuid(value["owner_id"])
    return value


def _capability_projection(value):
    value = _closed(value, (f.name for f in fields(AuditCapability)))
    _require(value["profile_id"] == "linux-local-audit-v1", "integrity_error")
    _vocabulary(value["filesystem_type"], ("ext4", "btrfs"))
    for name in ("root_device", "root_inode", "root_mount_id"):
        _number(value[name], integer=True)
    _require(value["root_inode"] > 0 and value["root_mount_id"] > 0, "integrity_error")
    _hash(value["runtime_fingerprint"])
    return value


def _open_projection(event):
    ctx = _closed(event["context"], (f.name for f in fields(InvocationContext)))
    _context(InvocationContext(**ctx))
    _require(ctx["run_id"] == event["run_id"], "integrity_error")
    _capability_projection(event["capability"])
    _owner_identity(event["owner"])
    policy = {
        "run_bytes": RUN_BYTES,
        "marker_bytes": MARKER_BYTES,
        "root_bytes": ROOT_BYTES,
        "body_bytes": BODY_BYTES,
    }
    reservation = _closed(event["reservation"], policy)
    _require(
        all(type(reservation[n]) is int and reservation[n] == v for n, v in policy.items()),
        "integrity_error",
    )


_MARKER_FIELDS = frozenset(
    (
        "schema_version",
        "run_id",
        "owner",
        "event_id",
        "utc",
        "terminal",
        "frozen",
        "tombstone",
        "ordinary_bytes",
        "marker_partition_bytes",
    )
)


def _marker_projection(value):
    value = _closed(value, _MARKER_FIELDS)
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1, "integrity_error")
    _uuid(value["run_id"])
    _owner_identity(value["owner"])
    _uuid(value["event_id"])
    _utc(value["utc"])
    if value["terminal"] is not None:
        _vocabulary(value["terminal"], _TERMINAL)
    _require(type(value["frozen"]) is bool and type(value["tombstone"]) is bool, "integrity_error")
    _number(value["ordinary_bytes"], integer=True)
    _require(value["ordinary_bytes"] <= RUN_BYTES - MARKER_BYTES, "integrity_error")
    _require(
        type(value["marker_partition_bytes"]) is int and value["marker_partition_bytes"] == MARKER_BYTES,
        "integrity_error",
    )
    return value


def _marker_projections(metadata, events):
    if not metadata:
        return
    opened = events[0] if events and events[0]["event"] == "run_open" else None
    _require(opened is not None, "integrity_error")
    by_kind = {
        kind: next((e for e in events if e["event"] == kind), None)
        for kind in ("run_final", "freeze", "audit_fault")
    }
    final = by_kind["run_final"]
    final_index = events.index(final) if final else len(events)
    freeze = by_kind["freeze"]
    freeze_index = events.index(freeze) if freeze else len(events)
    expected = {"admission": opened, "final": final, "fault": by_kind["audit_fault"], "freeze": freeze}
    for name, raw in metadata.items():
        value = _marker_projection(raw)
        _require(
            value["run_id"] == opened["run_id"] and value["owner"] == opened["owner"], "integrity_error"
        )
        frozen = name == "freeze" or (name == "final" and freeze_index < final_index)
        _require(
            value["frozen"] is frozen and value["tombstone"] is (name == "tombstone"), "integrity_error"
        )
        if name == "tombstone":
            _require(final is not None and freeze is None, "integrity_error")
            _require(
                value["terminal"] == final["outcome"]
                and _utc(value["utc"]) - _utc(final["utc"]) > RETAIN_SECONDS,
                "integrity_error",
            )
            _require(value["event_id"] not in {e["event_id"] for e in events}, "integrity_error")
            continue
        event = expected[name]
        if name == "freeze":
            terminal = final["outcome"] if final and final_index < freeze_index else None
        else:
            terminal = final["outcome"] if name == "final" and final else None
        _require(value["terminal"] == terminal, "integrity_error")
        if name in ("admission", "fault"):
            _require(value["ordinary_bytes"] == 0, "integrity_error")
        # A published pending freeze marker remains full charge until its journal event exists.
        if name == "freeze" and event is None:
            continue
        _require(
            event is not None
            and value["event_id"] == event["event_id"]
            and value["utc"] == event["utc"],
            "integrity_error",
        )


def _utc(value):
    _require(type(value) is str and len(value) <= 40)
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _Rejected("invalid_input") from None
    _require(parsed.utcoffset() == datetime.timedelta(0))
    return parsed.timestamp()


def _json(data):
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _frame(event):
    payload = _json(event)
    framed = ("%08x " % len(payload)).encode("ascii") + payload + b"\n"
    _require(len(framed) <= EVENT_BYTES, "quota_refused")
    if event["event"] in ("run_final", "freeze", "audit_fault"):
        _require(len(framed) <= 1024, "quota_refused")
    return framed


def _owner():
    fields_ = Path("/proc/self/stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
    return {"pid": os.getpid(), "start_ticks": fields_[19], "owner_id": str(uuid.uuid4())}


@dataclass
class _RootHandle:
    path: Path
    fd: int
    capability: AuditCapability

    def close(self):
        fd, self.fd = self.fd, -1
        if fd >= 0:
            os.close(fd)


def _directory(fd):
    value = os.fstat(fd)
    _require(stat.S_ISDIR(value.st_mode), "integrity_error")
    return value


def _qualified_dir(fd):
    value = _directory(fd)
    _require(value.st_uid == os.getuid() and stat.S_IMODE(value.st_mode) == 0o700, "integrity_error")
    return value


def _read_bounded(path, bound):
    with open(path, "rb") as stream:
        content = stream.read(bound + 1)
    _require(len(content) <= bound, "unsupported_profile")
    return content.decode("ascii")


def _profile(fd):
    info = _read_bounded("/proc/self/fdinfo/" + str(fd), 16 * 1024)
    ids = [line.split(":", 1)[1].strip() for line in info.splitlines() if line.startswith("mnt_id:")]
    _require(len(ids) == 1 and ids[0].isdecimal(), "unsupported_profile")
    mount_id = int(ids[0])
    mounts = _read_bounded("/proc/self/mountinfo", 1024 * 1024)
    matches = []
    for line in mounts.splitlines():
        parts = line.split(" - ")
        _require(len(parts) == 2, "unsupported_profile")
        left, right = parts[0].split(), parts[1].split()
        _require(len(left) >= 6 and len(right) >= 3 and left[0].isdecimal(), "unsupported_profile")
        if int(left[0]) == mount_id:
            matches.append(right[0])
    _require(len(matches) == 1 and matches[0] in ("ext4", "btrfs"), "unsupported_profile")
    return matches[0], mount_id


def _prepare_audit_root(root: Path, *, create: bool) -> _RootHandle | AuditFault:
    fd = -1
    result = None
    try:
        _require(sys.platform.startswith("linux"), "unsupported_profile")
        _require(type(root) is Path or isinstance(root, Path))
        _require(type(create) is bool and root.is_absolute() and ".." not in root.parts)
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        for component in root.parts[1:]:
            try:
                next_fd = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
                )
            except FileNotFoundError:
                _require(create, "integrity_error")
                os.mkdir(component, 0o700, dir_fd=fd)
                os.fsync(fd)
                next_fd = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
                )
            old_fd, fd = fd, next_fd
            os.close(old_fd)
        identity = _qualified_dir(fd)
        filesystem, mount_id = _profile(fd)
        os.fsync(fd)
        capability = AuditCapability(
            "linux-local-audit-v1",
            filesystem,
            identity.st_dev,
            identity.st_ino,
            mount_id,
            hashlib.sha256(
                ("invocation-audit-v1:" + sys.version + ":" + sys.platform).encode()
            ).hexdigest(),
        )
        handle = _RootHandle(root, fd, capability)
        fd = -1
        return handle
    except _Rejected as error:
        result = AuditFault(error.code, "open", None)
    except (OSError, UnicodeError, ValueError):
        result = AuditFault("persistence_error", "open", None)
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
    return result


def _regular(fd, empty=False):
    value = os.fstat(fd)
    _require(
        stat.S_ISREG(value.st_mode)
        and value.st_nlink == 1
        and value.st_uid == os.getuid()
        and stat.S_IMODE(value.st_mode) == 0o600
        and (not empty or value.st_size == 0),
        "integrity_error",
    )
    return value


def _revalidate(handle):
    current = os.stat(handle.path, follow_symlinks=False)
    held = _qualified_dir(handle.fd)
    _require((current.st_dev, current.st_ino) == (held.st_dev, held.st_ino), "integrity_error")
    filesystem, mount_id = _profile(handle.fd)
    cap = handle.capability
    _require(
        (held.st_dev, held.st_ino, filesystem, mount_id)
        == (cap.root_device, cap.root_inode, cap.filesystem_type, cap.root_mount_id),
        "integrity_error",
    )


@contextmanager
def _root_lock(handle, *, deadline=None, monotonic=time.monotonic, create=False):
    import fcntl

    fd = -1
    locked = False
    started = time.monotonic()
    if deadline is not None:
        _number(deadline)
        remaining = deadline - _number(monotonic())
        _require(remaining > 0, "lock_timeout")
        budget = min(1.0, remaining)
    else:
        budget = 1.0
    try:
        _revalidate(handle)
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        if create:
            flags |= os.O_CREAT
        fd = os.open("root.lock", flags, 0o600, dir_fd=handle.fd)
        held = _regular(fd, True)
        if create:
            os.fsync(handle.fd)
        while True:
            _require(time.monotonic() - started < budget, "lock_timeout")
            if deadline is not None:
                _require(_number(monotonic()) < deadline, "lock_timeout")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except BlockingIOError:
                time.sleep(min(0.005, budget))
        path = os.stat("root.lock", dir_fd=handle.fd, follow_symlinks=False)
        _require((held.st_dev, held.st_ino) == (path.st_dev, path.st_ino), "integrity_error")
        _revalidate(handle)
        yield
    finally:
        if fd >= 0:
            try:
                if locked:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


@contextmanager
def _run_dir(handle, run_id):
    _uuid(run_id)
    fd = os.open(run_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=handle.fd)
    try:
        _qualified_dir(fd)
        yield fd
    finally:
        os.close(fd)


def _read_file(fd, name, bound=RUN_BYTES):
    opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
    try:
        identity = _regular(opened)
        _require(identity.st_size <= bound, "integrity_error")
        parts = []
        remaining = bound + 1
        while remaining:
            piece = os.read(opened, min(remaining, 65536))
            if not piece:
                break
            parts.append(piece)
            remaining -= len(piece)
        data = b"".join(parts)
        _require(len(data) <= bound, "integrity_error")
        return data
    finally:
        os.close(opened)


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError(errno.EIO, "short write")
        view = view[count:]


def _append(fd, event):
    data = _frame(event)
    existed = "journal" in _entries(fd)
    opened = os.open(
        "journal",
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
        0o600,
        dir_fd=fd,
    )
    try:
        _regular(opened)
        _write_all(opened, data)
        os.fsync(opened)
    finally:
        os.close(opened)
    if not existed:
        os.fsync(fd)


def _publish(fd, name, data, *, replace=False):
    temp = "temp-" + name + "-" + str(uuid.uuid4())
    opened = os.open(
        temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd
    )
    try:
        _write_all(opened, data)
        os.fsync(opened)
    finally:
        os.close(opened)
    _require(replace or name not in _entries(fd), "identity_conflict")
    os.rename(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)


def _decode_json(data):
    try:
        return json.loads(data)
    except RecursionError:
        raise _Rejected("integrity_error") from None


def _read_events(data, run_id):
    events = []
    position = 0
    seen = {}
    marker = 0
    torn = False
    invalid = False
    while position < len(data):
        framed = False
        try:
            _require(len(data) - position >= 10, "integrity_error")
            size = int(data[position : position + 8], 16)
            _require(
                0 < size <= EVENT_BYTES - 10 and data[position + 8 : position + 9] == b" ",
                "integrity_error",
            )
            end = position + size + 10
            _require(end <= len(data) and data[end - 1 : end] == b"\n", "integrity_error")
            framed = True
            event = _decode_json(data[position + 9 : end - 1])
            _require(
                type(event) is dict and type(event.get("event")) is str and event["event"] in _EXTRA,
                "integrity_error",
            )
            _require(set(event) == _COMMON | _EXTRA[event["event"]], "integrity_error")
            _require(
                event["schema_version"] == 1
                and type(event["schema_version"]) is int
                and event["run_id"] == run_id,
                "integrity_error",
            )
            _uuid(event["event_id"])
            _utc(event["utc"])
            _number(event["elapsed_s"])
            _number(event["sequence"], integer=True)
            encoded = _frame(event)
            identity = event["event_id"]
            if identity in seen:
                _require(seen[identity] == encoded, "integrity_error")
            else:
                _require(event["sequence"] == len(events), "integrity_error")
                if events:
                    _require(event["elapsed_s"] >= events[-1]["elapsed_s"], "integrity_error")
                events.append(event)
                seen[identity] = encoded
            if event["event"] in ("run_final", "freeze", "audit_fault"):
                marker += end - position
            position = end
        except (ValueError, UnicodeError, _Rejected, TypeError):
            torn = True
            invalid |= framed
            break
    return events, len(data) - marker, marker, torn, invalid


def _disk_run(fd, run_id):
    # Candidate facts: terminal capacity/replay requires the lock-held _qualified_run barrier.
    names = _entries(fd)
    ordinary = 0
    marker = 0
    events = []
    torn = False
    metadata = {}
    orphan_raw = False
    schema_invalid = False
    for name in names:
        if name == "journal":
            data = _read_file(fd, name)
            events, normal, fixed, cut, invalid = _read_events(data, run_id)
            schema_invalid |= invalid
            ordinary += normal
            marker += fixed
            torn |= cut
        elif name == "admission":
            data = _read_file(fd, name, EVENT_BYTES)
            ordinary += len(data)
            try:
                metadata[name] = _decode_json(data)
            except (ValueError, UnicodeError, _Rejected):
                torn = True
                schema_invalid = True
        elif name == "input-manifest.json":
            data = _read_file(fd, name, BODY_BYTES)
            ordinary += len(data)
        elif name in _MARKERS:
            data = _read_file(fd, name, 1024)
            marker += len(data)
            try:
                metadata[name] = _decode_json(data)
            except (ValueError, UnicodeError, _Rejected):
                torn = True
                schema_invalid = True
        elif name.startswith("raw-"):
            _uuid(name[4:])
            ordinary += len(_read_file(fd, name))
        elif name.startswith("temp-"):
            # Interrupted owned temps remain charged; they are never deleted for admission.
            _uuid(name[-36:])
            size = len(_read_file(fd, name))
            if any(name.startswith("temp-" + m + "-") for m in _MARKERS):
                marker += size
                if name.startswith("temp-fault-"):
                    orphan_raw = True
                else:
                    torn = True
            else:
                ordinary += size
                orphan_raw = True
        else:
            raise _Rejected("integrity_error")
    _require(ordinary <= RUN_BYTES - MARKER_BYTES and marker <= MARKER_BYTES, "integrity_error")
    try:
        attempts, _, _, _, _, _ = _fold(events)
        _marker_projections(metadata, events)
        _manifest_contents(fd, events)
    except (_Rejected, TypeError, ValueError, KeyError):
        # Preserve the measured prefix; malformed nested records never fund admission.
        events = _valid_prefix(events)
        attempts, _, _, _, _, _ = _fold(events)
        schema_invalid = True
    structural_torn = torn or schema_invalid
    torn |= schema_invalid or any(a["usage_invalid"] for a in attempts.values())
    opened = events[0] if events and events[0]["event"] == "run_open" else None
    admission = metadata.get("admission")
    qualified = bool(
        not schema_invalid
        and opened
        and type(admission) is dict
        and admission.get("event_id") == opened["event_id"]
        and admission.get("run_id") == run_id
        and admission.get("owner") == opened["owner"]
    )
    final = next((e for e in reversed(events) if e["event"] == "run_final"), None)
    final_marker = metadata.get("final")
    terminal = bool(
        qualified
        and final
        and type(final_marker) is dict
        and final_marker.get("event_id") == final["event_id"]
        and final_marker.get("terminal") == final["outcome"]
    )
    frozen = (
        any(e["event"] == "freeze" for e in events)
        or "freeze" in names
        or any(n.startswith("temp-freeze-") for n in names)
    )
    tombstone = metadata.get("tombstone") if not schema_invalid else None
    if tombstone is not None:
        _require(
            type(tombstone) is dict
            and tombstone.get("run_id") == run_id
            and tombstone.get("tombstone") is True,
            "integrity_error",
        )
    freeze_event = next((e for e in events if e["event"] == "freeze"), None)
    freeze_marker = metadata.get("freeze")
    if frozen and not (
        freeze_event
        and type(freeze_marker) is dict
        and freeze_marker.get("event_id") == freeze_event["event_id"]
    ):
        torn = True
        structural_torn = True
    committed_ordinary = final_marker.get("ordinary_bytes", ordinary) if terminal else ordinary
    if tombstone is not None:
        committed_ordinary = tombstone.get("ordinary_bytes")
    _number(committed_ordinary, integer=True)
    _require(committed_ordinary <= RUN_BYTES - MARKER_BYTES, "integrity_error")
    referenced = {
        "raw-" + e["ref"]["artifact_id"] for e in events
        if e["event"] in ("acquired", "settled_acquired")
    }
    orphan_raw |= "input-manifest.json" in names and _manifest_event(events) is None
    orphan_raw |= any(n.startswith("raw-") and n not in referenced for n in names)
    settleable = qualified and not torn
    torn |= orphan_raw
    charge = (
        max(ordinary, committed_ordinary) + MARKER_BYTES
        if terminal and not frozen and not torn
        else RUN_BYTES
    )
    return {
        "events": events,
        "schema_invalid": schema_invalid,
        "ordinary": ordinary,
        "marker": marker,
        "torn": torn or not qualified,
        "settleable": settleable,
        "storage_settleable": qualified and not structural_torn,
        "terminal": terminal,
        "frozen": frozen,
        "charge": charge,
        "metadata": metadata,
        "final": final,
        "tombstone": tombstone,
    }


def _raw_contents(fd, disk):
    _manifest_contents(fd, disk["events"])
    names = set(_entries(fd))
    for event in disk["events"]:
        if event["event"] in ("acquired", "settled_acquired"):
            ref = event["ref"]  # The shared fold has already validated the closed projection.
            name = "raw-" + ref["artifact_id"]
            if disk["tombstone"] and name not in names:
                continue
            data = _read_file(fd, name)
            _require(
                len(data) == ref["retained_bytes"] and hashlib.sha256(data).hexdigest() == ref["sha256"],
                "integrity_error",
            )
    observations = {
        event["observation_id"]: event for event in disk["events"]
        if event["event"] in ("acquired", "settled_acquired")
    }
    for event in disk["events"]:
        if event["event"] == "response_observed":
            source = observations[event["source_observation_id"]]
            name = "raw-" + source["ref"]["artifact_id"]
            if disk["tombstone"] and name not in names:
                continue
            _response_frame(_read_file(fd, name), event["frame_offset"], event["frame_length"])


def _sync_file(fd, name):
    opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
    try:
        held = _regular(opened)
        os.fsync(opened)
        current = os.stat(name, dir_fd=fd, follow_symlinks=False)
        _require((held.st_dev, held.st_ino) == (current.st_dev, current.st_ino), "integrity_error")
    finally:
        os.close(opened)


def _qualified_run(handle, fd, run_id, disk=None, *, settlement=False):
    # Callers hold root.lock. Visibility alone never authorizes durable terminal facts.
    disk = _disk_run(fd, run_id) if disk is None else disk
    if disk["torn"] and not (settlement and disk["storage_settleable"]):
        return disk
    _revalidate(handle)
    recorded = disk["events"][0]["capability"]
    actual = _record(handle.capability, AuditCapability)
    _require(
        all(
            recorded[n] == actual[n]
            for n in ("profile_id", "filesystem_type", "root_device", "root_inode", "root_mount_id")
        ),
        "integrity_error",
    )
    held = _qualified_dir(fd)
    current = os.stat(run_id, dir_fd=handle.fd, follow_symlinks=False)
    _require((held.st_dev, held.st_ino) == (current.st_dev, current.st_ino), "integrity_error")
    _raw_contents(fd, disk)
    for name in _entries(fd):
        _sync_file(fd, name)
    os.fsync(fd)
    os.fsync(handle.fd)
    _revalidate(handle)
    current = os.stat(run_id, dir_fd=handle.fd, follow_symlinks=False)
    _require((held.st_dev, held.st_ino) == (current.st_dev, current.st_ino), "integrity_error")
    reread = _disk_run(fd, run_id)
    _require(reread == disk, "integrity_error")
    _raw_contents(fd, reread)
    return reread


def _entries(fd):
    # A directory description opened before a lock wait can retain an old listing.
    fresh = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
    try:
        old, new = _directory(fd), _directory(fresh)
        _require((old.st_dev, old.st_ino) == (new.st_dev, new.st_ino), "integrity_error")
        return os.listdir(fresh)
    finally:
        os.close(fresh)


def _inventory(handle):
    runs = {}
    for name in _entries(handle.fd):
        if name == "root.lock":
            fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=handle.fd
            )
            try:
                _regular(fd, True)
            finally:
                os.close(fd)
            continue
        _uuid(name)
        with _run_dir(handle, name) as fd:
            runs[name] = _qualified_run(handle, fd, name)
            _require(not runs[name]["schema_invalid"], "integrity_error")
    return runs, sum(row["charge"] for row in runs.values())


def _space(fd, run_id, ordinary_delta=0, marker_delta=0):
    current = _disk_run(fd, run_id)
    _require(
        current["ordinary"] + ordinary_delta <= RUN_BYTES - MARKER_BYTES
        and current["marker"] + marker_delta <= MARKER_BYTES,
        "quota_refused",
    )


def _marker(event, owner, *, terminal=None, frozen=False, tombstone=False, ordinary=0):
    data = {
        "schema_version": 1,
        "run_id": event["run_id"],
        "owner": owner,
        "event_id": event["event_id"],
        "utc": event["utc"],
        "terminal": terminal,
        "frozen": frozen,
        "tombstone": tombstone,
        "ordinary_bytes": ordinary,
        "marker_partition_bytes": MARKER_BYTES,
    }
    encoded = _json(data)
    _require(len(encoded) <= 1024, "quota_refused")
    return encoded


def _streams(observations):
    result = {
        n: {
            "value": None,
            "kind": None,
            "semantics": None,
            "sealed": False,
            "invalid": False,
            "final_available": None,
        }
        for n in _FIELDS
    }
    invalid = False
    for observation in observations:
        if observation is None:
            continue
        prior = {name: stream.copy() for name, stream in result.items()}
        for name in _FIELDS:
            value = observation[name]
            stream = result[name]
            if observation["availability"] == "invalid":
                stream["invalid"] = True
                invalid = True
            if observation["snapshot_kind"] == "final":
                stream["final_available"] = value is not None
            if value is None:
                continue
            kind = "incremental" if observation["snapshot_kind"] == "incremental" else "cumulative"
            semantics = (
                observation["input_semantics"]
                if name in ("input_tokens", "cached_input_tokens")
                else observation["output_semantics"]
            )
            bad = (
                (stream["kind"] is not None and stream["kind"] != kind)
                or (stream["semantics"] is not None and stream["semantics"] != semantics)
                or (stream["sealed"] and value != stream["value"])
                or (kind == "cumulative" and stream["value"] is not None and value < stream["value"])
            )
            if bad:
                stream["invalid"] = True
                invalid = True
                continue
            stream["kind"] = kind
            stream["semantics"] = semantics
            stream["value"] = (stream["value"] or 0) + value if kind == "incremental" else value
            stream["sealed"] |= observation["snapshot_kind"] == "final"
        for total, subset, semantics in (
            ("input_tokens", "cached_input_tokens", "total_includes_cache"),
            ("output_tokens", "reasoning_tokens", "total_includes_reasoning"),
        ):
            enclosing, included = result[total], result[subset]
            if (
                enclosing["semantics"] == semantics
                and enclosing["value"] is not None
                and included["value"] is not None
                and included["value"] > enclosing["value"]
            ):
                # A missing field preserves the prior measurement, including its invariant.
                for name in (total, subset):
                    result[name] = prior[name]
                    result[name]["invalid"] = True
                invalid = True
    return result, invalid


def _admit_start(ctx, logical_id, parent_id, cause, attempts, logical, fixed, *, code):
    _require(all(ctx[n] == fixed[n] for n in ("run_id", "source_hash", "snapshot_id")), code)
    parent = attempts.get(parent_id)
    _require(parent_id is None or parent is not None, code)
    if logical_id in logical:
        _require(
            ctx == logical[logical_id]
            and parent is not None
            and parent["start"]["logical_id"] == logical_id,
            code,
        )
        _require(cause == "retry", code)
    else:
        _require(ctx["parent_logical_id"] == (parent["start"]["logical_id"] if parent else None), code)
        _require(cause != "retry", code)


def _admit_acquired(attempt_id, attempts, *, code):
    attempt = attempts.get(attempt_id)
    _require(attempt is not None and attempt["finish"] is None, code)
    return attempt


def _work_s(attempts):
    work = 0.0
    for attempt in attempts.values():
        end = attempt["finish"]
        if end is not None and end["dispatch_state"] != "not_dispatched":
            duration = end["duration_s"]
            if duration is not None:
                work += duration
    _number(work)
    return work


def _body_bytes(observations, attempt_id, layer):
    return sum(
        event["ref"]["retained_bytes"]
        for event in observations.values()
        if event["event"] in ("acquired", "settled_acquired")
        and event["attempt_id"] == attempt_id
        and ((event["layer"] == "decoded") == (layer == "decoded"))
    )


def _fold(events):
    attempts = {}
    logical = {}
    observations = {}
    terminal = None
    frozen = False
    faults = []
    opened = None
    manifest = None
    for index, event in enumerate(events):
        kind = event["event"]
        _require(kind == "run_open" or opened is not None, "integrity_error")
        if kind == "run_open":
            _require(index == 0 and opened is None, "integrity_error")
            _open_projection(event)
            opened = event
        elif kind == "start":
            ctx = _context(InvocationContext(**event["context"]))
            _uuid(event["attempt_id"])
            _uuid(event["logical_id"])
            _uuid(event["parent_id"], True)
            _vocabulary(event["cause"], _CAUSES)
            _backend(BackendObservation(**event["backend"]))
            _hash(event["request_digest"])
            _number(event["request_bytes"], integer=True)
            _require(event["attempt_id"] not in attempts and terminal is None, "integrity_error")
            _admit_start(
                ctx,
                event["logical_id"],
                event["parent_id"],
                event["cause"],
                attempts,
                logical,
                events[0]["context"],
                code="integrity_error",
            )
            logical.setdefault(event["logical_id"], ctx)
            attempts[event["attempt_id"]] = {
                "start": event,
                "finish": None,
                "observations": [],
                "responses": [],
                "dispatch_observed": None,
                "invalid": False,
            }
        elif kind in ("acquired", "settled_acquired"):
            _uuid(event["observation_id"])
            _vocabulary(event["layer"], _LAYERS)
            ref = event["ref"]
            keys = {field.name for field in fields(RawRef)}
            _require(
                type(ref) is dict and set(ref) in (keys, keys | {"preview", "preview_sensitive"}),
                "integrity_error",
            )
            _uuid(ref["artifact_id"])
            _hash(ref["sha256"])
            _vocabulary(ref["layer"], _LAYERS)
            _require(ref["layer"] == event["layer"], "integrity_error")
            _number(ref["retained_bytes"], integer=True)
            _number(ref["original_bytes"], True, True)
            _require(
                type(ref["partial"]) is bool and ref["retained_bytes"] <= BODY_BYTES, "integrity_error"
            )
            _require(
                ref["original_bytes"] is None or ref["retained_bytes"] <= ref["original_bytes"],
                "integrity_error",
            )
            if "preview" in ref:
                _require(
                    type(ref["preview"]) is str
                    and ref["preview"].isascii()
                    and len(ref["preview"]) <= 400
                    and ref["preview_sensitive"] is True,
                    "integrity_error",
                )
            _require(terminal is None, "integrity_error")
            attempt = _admit_acquired(event["attempt_id"], attempts, code="integrity_error")
            if kind == "settled_acquired":
                _dispatch_observation(attempt, event["dispatch_state"])
            _require(type(event["complete"]) is bool, "integrity_error")
            if ref["original_bytes"] is not None:
                _require(
                    ref["partial"]
                    == (not event["complete"] or ref["retained_bytes"] < ref["original_bytes"]),
                    "integrity_error",
                )
            else:
                _require(event["complete"] or ref["partial"], "integrity_error")
            if event["usage"] is not None:
                observation = _usage(UsageObservation(**event["usage"]))
            else:
                observation = None
            identity = event["observation_id"]
            if identity in observations:
                _require(observations[identity] == event, "integrity_error")
            else:
                _require(
                    _body_bytes(observations, event["attempt_id"], event["layer"])
                    + ref["retained_bytes"]
                    <= BODY_BYTES,
                    "integrity_error",
                )
                observations[identity] = event
                attempt["observations"].append(observation)
            if ref["partial"]:
                # The committed prefix remains incomplete even if its fault append failed.
                attempt["invalid"] = True
                faults.append(AuditFault("quota_refused", "acquired", event["attempt_id"]))
        elif kind == "response_observed":
            _require(terminal is None, "integrity_error")
            attempt, _ = _response_projection(event, attempts, observations)
            identity = event["observation_id"]
            _require(identity not in observations, "integrity_error")
            observations[identity] = event
            attempt["observations"].append(event["usage"])
            attempt["responses"].append(event)
        elif kind == "input_manifest":
            _require(manifest is None and not attempts and terminal is None, "integrity_error")
            _hash(event["sha256"])
            _require(0 < _number(event["bytes"], integer=True) <= BODY_BYTES, "integrity_error")
            _require(
                event["source_hash"] == opened["context"]["source_hash"] is not None
                and event["snapshot_id"] == opened["context"]["snapshot_id"], "integrity_error",
            )
            manifest = event
        elif kind == "finish":
            attempt = attempts.get(event["attempt_id"])
            _require(attempt is not None and terminal is None, "integrity_error")
            _vocabulary(event["outcome"], _OUTCOMES)
            _vocabulary(event["dispatch_state"], _DISPATCH)
            _text(event["error_class"], True)
            _number(event["duration_s"], True)
            _require(attempt["finish"] is None, "integrity_error")
            observed = attempt["dispatch_observed"]
            _require(
                observed not in ("entered_api_send", "entered_cli_launch")
                or event["dispatch_state"] == observed, "integrity_error",
            )
            attempt["finish"] = event
        elif kind == "run_final":
            _vocabulary(event["outcome"], _TERMINAL)
            _require(
                terminal is None and all(a["finish"] is not None for a in attempts.values()),
                "integrity_error",
            )
            _require(type(event["audit_complete"]) is bool, "integrity_error")
            terminal = event
        elif kind == "freeze":
            _require(event["frozen"] is True and not frozen, "integrity_error")
            frozen = True
        elif kind == "audit_fault":
            _require(terminal is None, "integrity_error")
            _vocabulary(event["code"], _FAULTS)
            _vocabulary(event["phase"], _PHASES)
            _uuid(event["attempt_id"], True)
            fault = AuditFault(event["code"], event["phase"], event["attempt_id"])
            if fault not in faults:
                faults.append(fault)
            if fault.attempt_id in attempts and fault.phase == "acquired":
                attempts[fault.attempt_id]["invalid"] = True
    for attempt in attempts.values():
        attempt["usage_invalid"] = _streams(attempt["observations"])[1]
        attempt["invalid"] |= attempt["usage_invalid"]
    _require(
        terminal is None
        or terminal["outcome"] != "completed"
        or (
            terminal["audit_complete"]
            and not faults
            and not any(a["invalid"] for a in attempts.values())
        ),
        "integrity_error",
    )
    _work_s(attempts)
    return attempts, logical, observations, terminal, frozen, faults


def _valid_prefix(events):
    prefix = []
    for event in events:
        try:
            _fold(prefix + [event])
        except (_Rejected, TypeError, ValueError, KeyError):
            break
        prefix.append(event)
    return prefix


def _summary(run_id, context, events, faults, state, complete, *, selected=None):
    attempts, logical, observations, terminal, _, _ = _fold(events)
    if selected is not None:
        attempts = {identity: value for identity, value in attempts.items() if identity in selected}
        logical = {a["start"]["logical_id"] for a in attempts.values()}
        observations = {identity: e for identity, e in observations.items() if e["attempt_id"] in attempts}
    wall = None
    if terminal:
        opened_elapsed = events[0]["elapsed_s"]
        if type(opened_elapsed) is float and opened_elapsed.is_integer():
            opened_elapsed = int(opened_elapsed)
        wall = terminal["elapsed_s"] - opened_elapsed
    outcomes = {n: 0 for n in ("completed", "failed", "refused", "cancelled", "incomplete", "unknown")}
    usage = {
        n: {"known_subtotal": 0, "total": None, "unknown_attempt_count": 0, "invalid_attempt_count": 0}
        for n in _FIELDS
    }
    api = cli = unknown = 0
    work = _work_s(attempts)
    unknown_duration = 0
    measured = 0
    for attempt in attempts.values():
        end = attempt["finish"]
        dispatch = end["dispatch_state"] if end else attempt["dispatch_observed"] or "unknown"
        outcomes[end["outcome"] if end else "unknown"] += 1
        api += dispatch == "entered_api_send"
        cli += dispatch == "entered_cli_launch"
        unknown += dispatch in ("unknown", "possibly_sent")
        if dispatch == "not_dispatched":
            continue
        measured += 1
        duration = end["duration_s"] if end else None
        if duration is None:
            unknown_duration += 1
        streams, _ = _streams(attempt["observations"])
        values = {n: streams[n]["value"] for n in _FIELDS}
        normalized = values.copy()
        available = {
            n: values[n] is not None and streams[n]["final_available"] is not False for n in _FIELDS
        }
        for total, subset, included, excluded in (
            ("input_tokens", "cached_input_tokens", "total_includes_cache", "uncached_excludes_cache"),
            (
                "output_tokens",
                "reasoning_tokens",
                "total_includes_reasoning",
                "visible_excludes_reasoning",
            ),
        ):
            semantics = streams[total]["semantics"]
            if semantics == excluded:
                normalized[total] = (values[total] or 0) + (values[subset] or 0)
                available[total] = available[total] and available[subset]
            elif semantics != included:
                available[total] = False
        for name in _FIELDS:
            row = usage[name]
            row["known_subtotal"] += normalized[name] or 0
            invalid = streams[name]["invalid"] or attempt["invalid"]
            if invalid:
                row["invalid_attempt_count"] += 1
            elif not available[name] or dispatch in ("unknown", "possibly_sent"):
                row["unknown_attempt_count"] += 1
    for row in usage.values():
        if measured and not row["unknown_attempt_count"] and not row["invalid_attempt_count"]:
            row["total"] = row["known_subtotal"]
    return {
        "schema_version": 1,
        "run_id": run_id,
        "state": state,
        "audit_complete": complete,
        "source_binding": "bound" if context and context["source_hash"] is not None else "unknown",
        "logical_call_count": len(logical),
        "admitted_attempt_count": len(attempts),
        "observed_api_send_count": api,
        "observed_cli_launch_count": cli,
        "unknown_dispatch_count": unknown,
        "outcome_counts": outcomes,
        "observation_count": len(observations),
        "usage": usage,
        "timing": {
            "work_s": work if measured and not unknown_duration else None,
            "wall_s": wall,
            "known_work_s": work,
            "unknown_duration_count": unknown_duration,
        },
        "cost": None,
        "fault_codes": list(dict.fromkeys(f.code for f in faults)),
    }


def _scope(value):
    if value is None:
        return {}
    _require(
        type(value) is dict and all(type(key) is str for key in value)
        and set(value) <= {"round_index", "pass_name", "group_id", "purpose"}
    )
    for key, item in value.items():
        if key == "round_index":
            _number(item, True, True)
        elif key == "group_id":
            _uuid(item, True)
        else:
            _text(item, True)
    return value.copy()


def _native_usage_fault(events):
    attempts, _, _, _, _, _ = _fold(events)
    return any(attempt["usage_invalid"] and attempt["responses"] for attempt in attempts.values())


def _scoped_projection(context, events, faults, state, complete, manifest, scope, *, run_id=""):
    attempts, _, observations, _, _, _ = _fold(events)
    faults = list(faults)
    try:
        selector = _scope(scope)
    except (_Rejected, TypeError, ValueError):
        selector = {}
        faults.append(AuditFault("invalid_input", "replay", None))
        complete = False
    identity = context["run_id"] if context else run_id
    selected = {
        key for key, attempt in attempts.items()
        if all(attempt["start"]["context"][name] == value for name, value in selector.items())
    }
    projections = []
    for key, attempt in attempts.items():
        if key not in selected:
            continue
        start = attempt["start"]
        end = attempt["finish"]
        response_rows = []
        for response in attempt["responses"]:
            usage = response["usage"]
            if usage is not None:
                usage = dict(usage, native_usage=_immutable(usage["native_usage"]))
            response_rows.append(ResponseProjection(
                response["observation_id"], response["source_observation_id"],
                response["frame_offset"], response["frame_length"],
                UsageObservation(**usage) if usage is not None else None,
                BackendObservation(**response["observed_backend"])
                if response["observed_backend"] is not None else None,
            ))
        refs = tuple(
            RawRef(**{f.name: event["ref"][f.name] for f in fields(RawRef)})
            for event in observations.values()
            if event["attempt_id"] == key and event["event"] in ("acquired", "settled_acquired")
        )
        summary = _summary(identity, context, events, faults, state, complete, selected={key})
        projections.append(AttemptProjection(
            key, start["logical_id"], start["parent_id"], start["cause"],
            InvocationContext(**start["context"]), BackendObservation(**start["backend"]),
            tuple(response_rows), refs, end["outcome"] if end else "unknown",
            end["dispatch_state"] if end else attempt["dispatch_observed"] or "unknown",
            end["duration_s"] if end else None, _immutable(summary["usage"]),
        ))
    final_state = state if complete else "incomplete"
    summary = _summary(identity, context, events, faults, final_state, complete, selected=selected)
    return ScopedInvocationSnapshot(
        1, identity, final_state, complete, tuple(faults), _immutable(selector), tuple(projections),
        _immutable(summary), _immutable(manifest) if manifest is not None else None,
    )


def _replay(root, run_id):
    safe_run_id = run_id if type(run_id) is str and re.fullmatch("[0-9a-f-]{36}", run_id) else ""
    handle = None
    events = []
    faults = []
    capability = None
    context = None
    state = "incomplete"
    complete = False
    manifest = None
    try:
        _uuid(run_id)
        handle = _prepare_audit_root(root, create=False)
        if isinstance(handle, AuditFault):
            faults.append(AuditFault(handle.code, "replay", None))
            handle = None
        else:
            capability = handle.capability
            with _root_lock(handle):
                with _run_dir(handle, run_id) as fd:
                    disk = _disk_run(fd, run_id)
                    events = disk["events"]
                    disk = _qualified_run(handle, fd, run_id, disk)
                    _require(events and events[0]["event"] == "run_open", "integrity_error")
                    context = _context(InvocationContext(**events[0]["context"]))
                    attempts, _, _, terminal, _, faults = _fold(events)
                    manifest = _manifest_contents(fd, events)
                    _require(
                        not disk["torn"] and (terminal is None or disk["terminal"]),
                        "integrity_error",
                    )
                    complete = not faults
                    state = terminal["outcome"] if terminal else "active"
                    if terminal and not terminal["audit_complete"]:
                        complete = False
    except _Rejected as error:
        faults.append(AuditFault(error.code, "replay", None))
        complete = False
    except (OSError, TypeError, ValueError, KeyError):
        faults.append(AuditFault("integrity_error", "replay", None))
        complete = False
    finally:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                faults.append(AuditFault("persistence_error", "replay", None))
                complete = False
    if not complete:
        state = "incomplete"
    return safe_run_id, context, events, faults, capability, state, complete, manifest


class AttemptRecorder:
    """One process owns a run; typed durable acknowledgements gate its caller."""

    def __init__(
        self,
        root: Path,
        *,
        context: InvocationContext,
        monotonic: Callable[[], float],
        utc_now: Callable[[], str],
        preview_enabled: bool = False,
    ):
        self.root = root
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._mutex = threading.Lock()
        self._pid = os.getpid()
        self._events = []
        self._append_uncertain = False
        self._faults = []
        self._capability = None
        self._opened = False
        self._closed = False
        self._frozen = False
        self._complete = True
        self._state = "new"
        self._construction_fault = None
        self._context = None
        self._owner = None
        self._origin = None
        self._preview = False
        self._input_manifest = None
        try:
            self._context = _context(context)
            _require(isinstance(root, Path) and root.is_absolute())
            _require(callable(monotonic) and callable(utc_now) and type(preview_enabled) is bool)
            self._origin = _number(monotonic())
            _utc(utc_now())
            self._preview = preview_enabled
        except Exception:  # noqa: BLE001 - caller validation never invokes unsafe diagnostics
            self._construction_fault = "invalid_input"
            self._complete = False

    def _event(self, kind, **extra):
        now = _number(self._monotonic())
        elapsed = now - self._origin
        _number(elapsed)
        if self._events:
            _require(elapsed >= self._events[-1]["elapsed_s"])
        utc = self._utc_now()
        _utc(utc)
        event = {
            "schema_version": 1,
            "run_id": self._context["run_id"],
            "event_id": str(uuid.uuid4()),
            "sequence": len(self._events),
            "event": kind,
            "utc": utc,
            "elapsed_s": elapsed,
            **extra,
        }
        _require(set(event) == _COMMON | _EXTRA[kind])
        _frame(event)
        return event

    def _journal_history(self, handle, fd, alternatives):
        # root.lock is held. An exact read alone is not a durable reconciliation.
        run_id = self._context["run_id"]
        _revalidate(handle)
        held = _qualified_dir(fd)
        current = os.stat(run_id, dir_fd=handle.fd, follow_symlinks=False)
        _require((held.st_dev, held.st_ino) == (current.st_dev, current.st_ino), "integrity_error")
        journal = os.stat("journal", dir_fd=fd, follow_symlinks=False)
        data = _read_file(fd, "journal")
        expected = next((history for encoded, history in alternatives if data == encoded), None)
        _require(expected is not None, "integrity_error")
        observed, _, _, torn, invalid = _read_events(data, run_id)
        _require(not torn and not invalid and observed == expected, "integrity_error")
        _fold(observed)
        _raw_contents(fd, {"events": observed, "tombstone": None})
        _sync_file(fd, "journal")
        os.fsync(fd)
        os.fsync(handle.fd)
        _revalidate(handle)
        current = os.stat(run_id, dir_fd=handle.fd, follow_symlinks=False)
        _require((held.st_dev, held.st_ino) == (current.st_dev, current.st_ino), "integrity_error")
        _require(_read_file(fd, "journal") == data, "integrity_error")
        current = os.stat("journal", dir_fd=fd, follow_symlinks=False)
        _require((journal.st_dev, journal.st_ino) == (current.st_dev, current.st_ino), "integrity_error")
        _raw_contents(fd, {"events": observed, "tombstone": None})
        return observed

    def _append_event(self, handle, fd, event):
        _require(not self._append_uncertain, "integrity_error")
        try:
            _append(fd, event)
        except OSError:
            try:
                prior = b"".join(_frame(value) for value in self._events)
                observed = self._journal_history(
                    handle, fd,
                    ((prior, self._events), (prior + _frame(event), self._events + [event])),
                )
                self._events = observed
            except Exception:  # noqa: BLE001 - retain the original append error and uncertain bytes
                self._append_uncertain = True
            raise
        self._events.append(event)

    def _denied(self, code, phase, attempt_id=None, handle=None, *, preserve=False):
        fault = AuditFault(code, phase, attempt_id)
        if preserve:
            return AuditAcknowledgement(False, fault)
        self._complete = False
        self._state = "incomplete"
        if fault not in self._faults:
            self._faults.append(fault)
        if (
            handle is not None
            and self._opened
            and not self._closed
            and not self._append_uncertain
            and not any(event["event"] == "run_final" for event in self._events)
        ):
            try:
                with _run_dir(handle, self._context["run_id"]) as fd:
                    prior = b"".join(_frame(value) for value in self._events)
                    self._journal_history(handle, fd, ((prior, self._events),))
                    if "fault" not in _entries(fd) and not any(
                        e["event"] == "audit_fault" for e in self._events
                    ):
                        event = self._event("audit_fault", code=code, phase=phase, attempt_id=attempt_id)
                        marker = _marker(event, self._owner)
                        _space(
                            fd,
                            self._context["run_id"],
                            marker_delta=len(_frame(event)) + 2 * len(marker),
                        )
                        self._append_event(handle, fd, event)
                        _publish(fd, "fault", marker)
            except Exception:  # noqa: BLE001 - a secondary sink failure cannot replace the caller error
                self._complete = False
        return AuditAcknowledgement(False, fault)

    def _guard(self, phase, action, *, attempt_id=None, deadline=None, create=False):
        try:
            safe_attempt = _uuid(attempt_id, True)
        except _Rejected:
            safe_attempt = None
        if os.getpid() != self._pid:
            # A forked mutex may be locked by a thread that no longer exists.
            return AuditAcknowledgement(False, AuditFault("identity_conflict", phase, safe_attempt))
        with self._mutex:
            if self._construction_fault:
                return self._denied(self._construction_fault, phase, safe_attempt)
            handle = _prepare_audit_root(self.root, create=create)
            if isinstance(handle, AuditFault):
                return self._denied(handle.code, phase, safe_attempt)
            try:
                if self._capability is not None:
                    _require(handle.capability == self._capability, "integrity_error")
                with _root_lock(handle, deadline=deadline, monotonic=self._monotonic, create=create):
                    try:
                        result = action(handle)
                    except _Rejected as error:
                        result = self._denied(
                            error.code, phase, safe_attempt, handle, preserve=error.preserve
                        )
                    except OSError:
                        result = self._denied("persistence_error", phase, safe_attempt, handle)
                    except (TypeError, ValueError, OverflowError):
                        result = self._denied("invalid_input", phase, safe_attempt, handle)
            except _Rejected as error:
                result = self._denied(error.code, phase, safe_attempt)
            except OSError:
                result = self._denied("persistence_error", phase, safe_attempt)
            except (TypeError, ValueError, OverflowError):
                result = self._denied("invalid_input", phase, safe_attempt)
            finally:
                try:
                    handle.close()
                except OSError:
                    result = self._denied("persistence_error", phase, safe_attempt)
            return result

    def _writable(self, *, allow_fault=False):
        _require(self._opened, "identity_conflict")
        _require(not self._closed, "finalized")
        _require(self._complete or allow_fault, "persistence_error")

    def _checked(self, handle, fd, *, allow_orphans=False, allow_response_faults=False):
        _require(not self._append_uncertain, "integrity_error")
        _revalidate(handle)
        disk = _qualified_run(
            handle, fd, self._context["run_id"], settlement=(allow_orphans or allow_response_faults) and not self._complete,
        )
        _require(not self._closed or disk["terminal"], "integrity_error")
        _require(
            disk["events"] == self._events
            and (
                not disk["torn"] or (allow_orphans and not self._complete and disk["settleable"])
                or (allow_response_faults and not self._complete and disk["storage_settleable"]
                    and _native_usage_fault(self._events))
            ),
            "integrity_error",
        )
        return disk

    def open(self) -> AuditAcknowledgement:
        def action(handle):
            _require(not self._opened and not self._closed, "identity_conflict")
            _, charge = _inventory(handle)
            _require(charge + RUN_BYTES <= ROOT_BYTES, "quota_refused")
            run_id = self._context["run_id"]
            _require(run_id not in _entries(handle.fd), "identity_conflict")
            self._owner = _owner()
            event = self._event(
                "run_open",
                context=self._context.copy(),
                capability=_record(handle.capability, AuditCapability),
                owner=self._owner.copy(),
                reservation={
                    "run_bytes": RUN_BYTES,
                    "marker_bytes": MARKER_BYTES,
                    "root_bytes": ROOT_BYTES,
                    "body_bytes": BODY_BYTES,
                },
            )
            marker = _marker(event, self._owner)
            _require(len(_frame(event)) + 2 * len(marker) <= RUN_BYTES - MARKER_BYTES, "quota_refused")
            os.mkdir(run_id, 0o700, dir_fd=handle.fd)
            with _run_dir(handle, run_id) as fd:
                self._append_event(handle, fd, event)
                _publish(fd, "admission", marker)
                os.fsync(fd)
            os.fsync(handle.fd)
            _revalidate(handle)
            self._capability = handle.capability
            self._opened = True
            self._state = "active"
            return AuditAcknowledgement(True, None)

        return self._guard("open", action, create=True)

    def start(
        self,
        context: InvocationContext,
        *,
        logical_id: str,
        parent_id: str | None,
        cause: str,
        backend: BackendObservation,
        request_digest: str,
        request_bytes: int,
        action_deadline: float,
    ) -> StartAcknowledgement:
        def action(handle):
            self._writable()
            ctx = _context(context)
            _uuid(logical_id)
            _uuid(parent_id, True)
            _vocabulary(cause, _CAUSES)
            projected = _backend(backend)
            _hash(request_digest)
            _number(request_bytes, integer=True)
            attempts, logical, _, _, _, _ = _fold(self._events)
            _admit_start(
                ctx,
                logical_id,
                parent_id,
                cause,
                attempts,
                logical,
                self._context,
                code="identity_conflict",
            )
            attempt_id = str(uuid.uuid4())
            event = self._event(
                "start",
                context=ctx,
                attempt_id=attempt_id,
                logical_id=logical_id,
                parent_id=parent_id,
                cause=cause,
                backend=projected,
                request_digest=request_digest,
                request_bytes=request_bytes,
            )
            with _run_dir(handle, self._context["run_id"]) as fd:
                self._checked(handle, fd)
                _require(self._context["source_hash"] is None or _manifest_event(self._events) is not None,
                         "integrity_error")
                _inventory(handle)
                _space(fd, self._context["run_id"], ordinary_delta=len(_frame(event)))
                self._append_event(handle, fd, event)
                _require(_number(self._monotonic()) < action_deadline, "lock_timeout")
                _revalidate(handle)
            return StartAcknowledgement(True, attempt_id, None)

        ack = self._guard("start", action, deadline=action_deadline)
        return (
            ack
            if isinstance(ack, StartAcknowledgement)
            else StartAcknowledgement(False, None, ack.fault)
        )

    def acquired(
        self,
        attempt_id: str,
        *,
        layer: str,
        data: bytes,
        complete: bool,
        usage: UsageObservation | None,
        observation_id: str | None = None,
    ) -> AcquiredAcknowledgement:
        return self._capture(
            attempt_id, layer=layer, data=data, complete=complete, usage=usage,
            observation_id=observation_id,
        )

    def settle_acquired(
        self,
        attempt_id: str,
        *,
        layer: str,
        data: bytes,
        complete: bool,
        usage: UsageObservation | None,
        dispatch_state: str,
        observation_id: str | None = None,
    ) -> AcquiredAcknowledgement:
        return self._capture(
            attempt_id, layer=layer, data=data, complete=complete, usage=usage,
            observation_id=observation_id, settlement=dispatch_state, settling=True,
        )

    def _capture(
        self,
        attempt_id: str,
        *,
        layer: str,
        data: bytes,
        complete: bool,
        usage: UsageObservation | None,
        observation_id: str | None = None,
        settlement: str | None = None,
        settling: bool = False,
    ) -> AcquiredAcknowledgement:
        def action(handle):
            self._writable(allow_fault=settling)
            if settling:
                _vocabulary(settlement, _DISPATCH - {"not_dispatched"})
                _require(not any(f.code in ("identity_conflict", "integrity_error") for f in self._faults),
                         "integrity_error")
            kind = "settled_acquired" if settling else "acquired"
            _uuid(attempt_id)
            _vocabulary(layer, _LAYERS)
            _require(type(data) is bytes and type(complete) is bool)
            observation = _usage(usage)
            attempts, _, observations, _, _, _ = _fold(self._events)
            attempt = _admit_acquired(attempt_id, attempts, code="identity_conflict")
            if observation_id is not None:
                _uuid(observation_id)
                previous = observations.get(observation_id)
                _require(
                    previous is not None and previous["attempt_id"] == attempt_id, "identity_conflict"
                )
                _require(previous["event"] == kind, "identity_conflict")
                _require(kind != "settled_acquired" or previous["dispatch_state"] == settlement,
                         "integrity_error")
                ref = previous["ref"]
                same = (
                    previous["layer"] == layer
                    and previous["complete"] == complete
                    and previous["usage"] == observation
                )
                same &= (
                    ref["original_bytes"] == len(data)
                    and ref["sha256"] == hashlib.sha256(data[: ref["retained_bytes"]]).hexdigest()
                )
                _require(same, "integrity_error")
                with _run_dir(handle, self._context["run_id"]) as fd:
                    self._checked(handle, fd, allow_orphans=settling, allow_response_faults=settling)
                    raw = _read_file(fd, "raw-" + ref["artifact_id"])
                    _require(hashlib.sha256(raw).hexdigest() == ref["sha256"], "integrity_error")
                return AcquiredAcknowledgement(
                    not ref["partial"],
                    observation_id,
                    RawRef(**{f.name: ref[f.name] for f in fields(RawRef)}),
                    None if not ref["partial"] else AuditFault("quota_refused", "acquired", attempt_id),
                )
            _, invalid = _streams(attempt["observations"] + [observation])
            _require(
                not invalid or (settling and observation is None and attempt["usage_invalid"]),
                "invalid_input",
            )
            observation_id_ = str(uuid.uuid4())
            artifact_id = str(uuid.uuid4())
            used = _body_bytes(observations, attempt_id, layer)
            retain = min(len(data), max(0, BODY_BYTES - used))
            partial = retain < len(data) or not complete
            ref = RawRef(
                artifact_id, layer, hashlib.sha256(data[:retain]).hexdigest(), retain, len(data), partial
            )
            payload = _record(ref, RawRef)
            if self._preview:
                # Preview bytes are explicitly sensitive and part of the charged ref projection.
                payload["preview"] = json.dumps(
                    data[:retain].decode("utf-8", "replace"), ensure_ascii=True
                )[1:-1][:400]
                payload["preview_sensitive"] = True
            event = self._event(
                kind,
                attempt_id=attempt_id,
                observation_id=observation_id_,
                layer=layer,
                ref=payload,
                complete=complete,
                usage=observation,
                **({"dispatch_state": settlement} if settling else {}),
            )
            _fold(self._events + [event])
            with _run_dir(handle, self._context["run_id"]) as fd:
                self._checked(handle, fd, allow_orphans=settling, allow_response_faults=settling)
                _inventory(handle)
                _space(fd, self._context["run_id"], ordinary_delta=retain + len(_frame(event)))
                _publish(fd, "raw-" + artifact_id, data[:retain])
                self._append_event(handle, fd, event)
                _revalidate(handle)
            if partial:
                denied = self._denied("quota_refused", "acquired", attempt_id, handle)
                return AcquiredAcknowledgement(False, observation_id_, ref, denied.fault)
            return AcquiredAcknowledgement(True, observation_id_, ref, None)

        ack = self._guard("acquired", action, attempt_id=attempt_id)
        return (
            ack
            if isinstance(ack, AcquiredAcknowledgement)
            else AcquiredAcknowledgement(False, None, None, ack.fault)
        )

    def publish_input_manifest(self, manifest: dict) -> AuditAcknowledgement:
        def action(handle):
            data = _manifest(manifest, self._context)
            _require(self._opened, "identity_conflict")
            previous = _manifest_event(self._events)
            with _run_dir(handle, self._context["run_id"]) as fd:
                self._checked(handle, fd)
                if previous is not None:
                    _require(_read_file(fd, "input-manifest.json", BODY_BYTES) == data, "identity_conflict")
                    return AuditAcknowledgement(True, None)
                self._writable()
                _require(not any(e["event"] == "start" for e in self._events), "identity_conflict")
                event = self._event(
                    "input_manifest", sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                    source_hash=self._context["source_hash"], snapshot_id=self._context["snapshot_id"],
                )
                _fold(self._events + [event])
                _inventory(handle)
                _space(fd, self._context["run_id"], ordinary_delta=2 * len(data) + len(_frame(event)))
                _publish(fd, "input-manifest.json", data)
                self._input_manifest = _decode_json(data)
                self._append_event(handle, fd, event)
                self._checked(handle, fd)
                _require(_manifest_contents(fd, self._events) == self._input_manifest, "integrity_error")
            return AuditAcknowledgement(True, None)

        return self._guard("start", action)

    def observe_response(
        self,
        attempt_id: str,
        *,
        source_observation_id: str,
        frame_offset: int,
        frame_length: int,
        usage: UsageObservation | None,
        observed_backend: BackendObservation | None,
        observation_id: str | None = None,
    ) -> AuditAcknowledgement:
        def action(handle):
            self._writable(allow_fault=True)
            _require(not any(f.code in ("integrity_error", "identity_conflict") for f in self._faults),
                     "integrity_error")
            _uuid(attempt_id)
            _uuid(source_observation_id)
            _uuid(observation_id, True)
            native = _usage(usage)
            backend = _backend(observed_backend) if observed_backend is not None else None
            attempts, _, observations, _, _, _ = _fold(self._events)
            fields_ = dict(
                attempt_id=attempt_id, source_observation_id=source_observation_id,
                frame_offset=frame_offset, frame_length=frame_length, usage=native, observed_backend=backend,
            )
            previous = observations.get(observation_id) if observation_id is not None else None
            if observation_id is not None:
                _require(previous is not None and previous["event"] == "response_observed",
                         "identity_conflict")
                _require(all(previous[key] == value for key, value in fields_.items()), "integrity_error")
                event = previous
            else:
                event = self._event("response_observed", observation_id=str(uuid.uuid4()), **fields_)
            _, raw = _response_projection(event, attempts, observations)
            with _run_dir(handle, self._context["run_id"]) as fd:
                self._checked(handle, fd, allow_orphans=True, allow_response_faults=True)
                content = _read_file(fd, "raw-" + raw["ref"]["artifact_id"])
                _response_frame(content, frame_offset, frame_length)
                if previous is not None:
                    invalid = _native_usage_fault(self._events[:self._events.index(previous) + 1])
                    return AuditAcknowledgement(
                        not invalid, AuditFault("invalid_input", "acquired", attempt_id) if invalid else None,
                    )
                _fold(self._events + [event])
                _inventory(handle)
                _space(fd, self._context["run_id"], ordinary_delta=len(_frame(event)))
                self._append_event(handle, fd, event)
                _revalidate(handle)
            if _native_usage_fault(self._events):
                return self._denied("invalid_input", "acquired", attempt_id, handle)
            return AuditAcknowledgement(True, None)

        return self._guard("acquired", action, attempt_id=attempt_id)

    def scoped_snapshot(self, *, scope: dict | None = None) -> ScopedInvocationSnapshot:
        with self._mutex:
            return _scoped_projection(
                self._context, self._events, self._faults, self._state, self._complete,
                self._input_manifest if _manifest_event(self._events) is not None else None, scope,
            )

    @staticmethod
    def reopen_scoped(root: Path, run_id: str, *, scope: dict | None = None) -> ScopedInvocationSnapshot:
        identity, context, events, faults, _, state, complete, manifest = _replay(root, run_id)
        return _scoped_projection(context, events, faults, state, complete, manifest, scope, run_id=identity)

    def finish(
        self,
        attempt_id: str,
        *,
        outcome: str,
        error_class: str | None,
        duration_s: float | None,
        dispatch_state: str,
    ) -> AuditAcknowledgement:
        def action(handle):
            _uuid(attempt_id)
            _vocabulary(outcome, _OUTCOMES)
            _text(error_class, True)
            _number(duration_s, True)
            _vocabulary(dispatch_state, _DISPATCH)
            attempts, _, _, _, _, _ = _fold(self._events)
            _require(attempt_id in attempts, "identity_conflict")
            previous = attempts[attempt_id]["finish"]
            args = {
                "attempt_id": attempt_id,
                "outcome": outcome,
                "error_class": error_class,
                "duration_s": duration_s,
                "dispatch_state": dispatch_state,
            }
            if previous:
                _require(all(previous[k] == v for k, v in args.items()), "identity_conflict")
                with _run_dir(handle, self._context["run_id"]) as fd:
                    self._checked(handle, fd, allow_orphans=True,
                                  allow_response_faults=_native_usage_fault(self._events))
                return AuditAcknowledgement(True, None)
            self._writable(allow_fault=True)
            event = self._event("finish", **args)
            _fold(self._events + [event])
            with _run_dir(handle, self._context["run_id"]) as fd:
                self._checked(handle, fd, allow_orphans=True, allow_response_faults=_native_usage_fault(self._events))
                _inventory(handle)
                _space(fd, self._context["run_id"], ordinary_delta=len(_frame(event)))
                self._append_event(handle, fd, event)
                _revalidate(handle)
            return AuditAcknowledgement(True, None)

        return self._guard("finish", action, attempt_id=attempt_id)

    def summary(self) -> dict:
        return self.snapshot().summary

    def snapshot(self) -> RecorderSnapshot:
        with self._mutex:
            run_id = self._context["run_id"] if self._context else ""
            summary = _summary(
                run_id, self._context, self._events, self._faults, self._state, self._complete
            )
            attempts, _, _, _, _, _ = _fold(self._events)
            contexts = MappingProxyType(
                {
                    identity: InvocationContext(**attempt["start"]["context"].copy())
                    for identity, attempt in attempts.items()
                }
            )
            return RecorderSnapshot(
                1,
                run_id,
                self._state,
                self._complete,
                tuple(self._faults),
                self._capability,
                copy.deepcopy(summary),
                contexts,
            )

    def finalize(self, outcome: str) -> AuditAcknowledgement:
        def action(handle):
            _vocabulary(outcome, _TERMINAL)
            attempts, _, _, previous, _, _ = _fold(self._events)
            if previous:
                _require(previous["outcome"] == outcome, "identity_conflict")
                _require(self._closed, "integrity_error")
                with _run_dir(handle, self._context["run_id"]) as fd:
                    self._checked(handle, fd, allow_orphans=True,
                                  allow_response_faults=_native_usage_fault(self._events))
                return AuditAcknowledgement(True, None)
            self._writable(allow_fault=True)
            _require(all(a["finish"] is not None for a in attempts.values()), "identity_conflict")
            _require(outcome != "completed" or self._complete, "persistence_error")
            event = self._event("run_final", outcome=outcome, audit_complete=self._complete)
            with _run_dir(handle, self._context["run_id"]) as fd:
                disk = self._checked(handle, fd, allow_orphans=True,
                                     allow_response_faults=_native_usage_fault(self._events))
                _inventory(handle)
                marker = _marker(
                    event,
                    self._owner,
                    terminal=outcome,
                    frozen=disk["frozen"],
                    ordinary=disk["ordinary"],
                )
                _space(fd, self._context["run_id"], marker_delta=len(_frame(event)) + 2 * len(marker))
                self._append_event(handle, fd, event)
                _publish(fd, "final", marker)
                os.fsync(fd)
                os.fsync(handle.fd)
                _revalidate(handle)
            self._closed = True
            self._state = outcome
            return AuditAcknowledgement(True, None)

        return self._guard("finalize", action)

    def set_frozen(self, frozen: bool) -> AuditAcknowledgement:
        def action(handle):
            _require(type(frozen) is bool)
            _require(self._opened, "identity_conflict")
            if frozen == self._frozen:
                with _run_dir(handle, self._context["run_id"]) as fd:
                    disk = self._checked(handle, fd)
                    _require(disk["frozen"] is frozen, "integrity_error")
                return AuditAcknowledgement(True, None)
            _require(frozen, "finalized")
            with _run_dir(handle, self._context["run_id"]) as fd:
                disk = self._checked(handle, fd)
                _require(disk["final"] is None or self._closed, "finalized")
                _require(disk["tombstone"] is None, "finalized")
                _, charge = _inventory(handle)
                delta = RUN_BYTES - disk["charge"] if self._closed else 0
                _require(delta >= 0, "integrity_error")
                if charge + delta > ROOT_BYTES:
                    raise _Rejected("quota_refused", preserve=self._closed)
                event = self._event("freeze", frozen=True)
                _fold(self._events + [event])
                marker = _marker(
                    event,
                    self._owner,
                    terminal=disk["final"]["outcome"] if self._closed else None,
                    frozen=True,
                    ordinary=disk["ordinary"],
                )
                _space(fd, self._context["run_id"], marker_delta=len(_frame(event)) + 2 * len(marker))
                # A pending marker keeps the pre-admitted full envelope even if the later append tears.
                _publish(fd, "freeze", marker)
                self._append_event(handle, fd, event)
                os.fsync(fd)
                os.fsync(handle.fd)
                _revalidate(handle)
            self._frozen = True
            return AuditAcknowledgement(True, None)

        return self._guard("freeze", action)

    @staticmethod
    def reopen(root: Path, run_id: str) -> RecorderSnapshot:
        identity, context, events, faults, capability, state, complete, _ = _replay(root, run_id)
        summary = _summary(identity, context, events, faults, state, complete)
        attempts, _, _, _, _, _ = _fold(events)
        contexts = MappingProxyType({
            key: InvocationContext(**value["start"]["context"].copy())
            for key, value in attempts.items()
        })
        return RecorderSnapshot(1, identity, state, complete, tuple(faults), capability, summary, contexts)


def _qualified_prune(handle, fd, run_id):
    disk = _qualified_run(handle, fd, run_id)
    _require(
        disk["terminal"]
        and not disk["frozen"]
        and not disk["torn"]
        and not disk["schema_invalid"]
        and disk["tombstone"] is not None,
        "integrity_error",
    )
    return disk


def maintain_audit_root(
    root: Path, *, monotonic: Callable[[], float], utc_now: Callable[[], str], action_deadline: float
) -> AuditAcknowledgement:
    """Prune eligible raw files while retaining terminal tombstones and their charge.

    Tombstones retain their ordinary metadata bytes plus the 16 KiB marker reserve
    indefinitely. They consume root capacity and can eventually refuse new runs.
    Keeping the known directory avoids publishing an uncommitted final deletion.
    """
    handle = None
    try:
        _require(callable(monotonic) and callable(utc_now))
        transaction_utc = utc_now()
        now = _utc(transaction_utc)
        handle = _prepare_audit_root(root, create=False)
        if isinstance(handle, AuditFault):
            return AuditAcknowledgement(False, AuditFault(handle.code, "maintenance", None))
        with _root_lock(handle, deadline=action_deadline, monotonic=monotonic):
            runs, _ = _inventory(handle)
            for run_id, disk in runs.items():
                if not disk["terminal"] or disk["frozen"] or disk["torn"]:
                    continue
                _require(now >= _utc(disk["final"]["utc"]), "integrity_error")
                with _run_dir(handle, run_id) as fd:
                    final = disk["final"]
                    if disk["tombstone"]:
                        tombstone = disk["tombstone"]
                        _require(now >= _utc(tombstone["utc"]), "integrity_error")
                        _require(tombstone["ordinary_bytes"] >= disk["ordinary"], "integrity_error")
                        if tombstone["ordinary_bytes"] == disk["ordinary"] and not any(
                            name.startswith("raw-") for name in _entries(fd)
                        ):
                            continue
                        tomb_event = dict(final, event_id=tombstone["event_id"], utc=tombstone["utc"])
                    else:
                        if now - _utc(final["utc"]) <= RETAIN_SECONDS:
                            continue
                        tomb_event = dict(final, event_id=str(uuid.uuid4()), utc=transaction_utc)
                        marker = _marker(
                            tomb_event,
                            disk["metadata"]["admission"]["owner"],
                            terminal=final["outcome"],
                            tombstone=True,
                            ordinary=disk["ordinary"],
                        )
                        _marker_projections(
                            dict(disk["metadata"], tombstone=_decode_json(marker)), disk["events"]
                        )
                        _space(fd, run_id, marker_delta=2 * len(marker))
                        _publish(fd, "tombstone", marker)
                        os.fsync(fd)
                    _qualified_prune(handle, fd, run_id)
                    for name in _entries(fd):
                        if name.startswith("raw-"):
                            _uuid(name[4:])
                            _read_file(fd, name)
                            os.unlink(name, dir_fd=fd)
                    os.fsync(fd)
                    # Only a synced retained-charge marker publishes the shrink.
                    current = _qualified_prune(handle, fd, run_id)
                    retained_marker = _marker(
                        tomb_event,
                        disk["metadata"]["admission"]["owner"],
                        terminal=final["outcome"],
                        tombstone=True,
                        ordinary=current["ordinary"],
                    )
                    _space(fd, run_id, marker_delta=len(retained_marker))
                    _publish(fd, "tombstone", retained_marker, replace=True)
                    _qualified_prune(handle, fd, run_id)
            _revalidate(handle)
        result = AuditAcknowledgement(True, None)
    except _Rejected as error:
        result = AuditAcknowledgement(False, AuditFault(error.code, "maintenance", None))
    except (OSError, ValueError, TypeError, KeyError):
        result = AuditAcknowledgement(False, AuditFault("persistence_error", "maintenance", None))
    finally:
        if isinstance(handle, _RootHandle):
            try:
                handle.close()
            except OSError:
                if result.admitted:
                    result = AuditAcknowledgement(
                        False, AuditFault("persistence_error", "maintenance", None)
                    )
    return result
