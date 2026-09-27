"""Operator host-configuration loader (spec host-files section).

Loads the worker, profile, environment and approval records from the
operator-resolved configuration directory.  The directory is supplied
by the caller; this module never reads a project-controlled location
or environment variable.  Record names are identifiers, not paths.

File-level rejections shared by every record kind: symlinks, files
writable by group or other, duplicate JSON keys, unknown fields,
oversized files and malformed JSON.  A missing or invalid record maps
to the specification's unavailable reason codes; a missing or stale
approval maps to execution_not_authorized.

The approval record binds the worker and profile file digests, the
environment content digest and the repository identity.  Any digest
or identifier mismatch is a stale approval, not a degraded run.

Environment content-drift checking (the immutable tree against its
manifest before launch, reason environment_changed) belongs to the
execution supervisor and is intentionally out of scope here.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from code_forge.mutation_engines.schemas import (
    _BUDGET_KEYS,
    Budget,
    WorkerConfig,
    valid_identifier,
)

MAX_CONFIG_BYTES = 65536
MAX_STRING = 512
MAX_LIST = 256

_BACKENDS = frozenset({"linux-isolated", "linux-builder"})

REASON_WORKER_MISSING = "worker_missing"
REASON_WORKER_INVALID = "worker_invalid"
REASON_PROFILE_MISSING = "profile_missing"
REASON_PROFILE_INVALID = "profile_invalid"
REASON_ENVIRONMENT_MISSING = "environment_missing"
REASON_ENVIRONMENT_INVALID = "environment_invalid"
REASON_APPROVAL_INVALID = "approval_invalid"
REASON_NOT_AUTHORIZED = "execution_not_authorized"


@dataclass(frozen=True)
class ProfileConfig:
    """Operator execution profile (spec profile-v1 field set)."""

    schema_version: int
    id: str
    backend: str
    allowed_environments: tuple[str, ...]
    ceilings: Budget
    delegated_cgroup_root: str
    state_root: str
    process_headroom_mb: int
    evidence_quota_mb: int
    legacy_budget: Budget | None
    legacy_python_environment: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProfileConfig:
        required = {
            "schema_version", "id", "backend", "allowed_environments",
            "ceilings", "delegated_cgroup_root", "state_root",
            "process_headroom_mb", "evidence_quota_mb",
            "legacy_budget", "legacy_python_environment",
        }
        _check_keys(data, required, "profile")
        if data["schema_version"] != 1:
            raise ValueError("profile schema_version must be 1")
        if not valid_identifier(data["id"]):
            raise ValueError("profile id must be a valid identifier")
        if data["backend"] not in _BACKENDS:
            raise ValueError(
                "profile backend must be one of %s, got %r"
                % (sorted(_BACKENDS), data["backend"])
            )
        envs = data["allowed_environments"]
        if not isinstance(envs, list) or len(envs) > MAX_LIST:
            raise ValueError("profile allowed_environments must be a bounded list")
        for name in envs:
            if not isinstance(name, str) or not valid_identifier(name):
                raise ValueError(
                    "profile allowed_environments entry must be an identifier, "
                    "got %r" % (name,)
                )
        ceilings = _budget_from(data["ceilings"], "profile.ceilings")
        legacy_budget = data["legacy_budget"]
        if legacy_budget is not None:
            legacy_budget = _budget_from(legacy_budget, "profile.legacy_budget")
        legacy_env = data["legacy_python_environment"]
        if legacy_env is not None and not valid_identifier(legacy_env):
            raise ValueError("profile legacy_python_environment must be an identifier")
        for field in ("delegated_cgroup_root", "state_root"):
            value = data[field]
            if not isinstance(value, str) or not value.startswith("/"):
                raise ValueError("profile.%s must be an absolute path" % field)
        for field in ("process_headroom_mb", "evidence_quota_mb"):
            _positive_int(data[field], "profile.%s" % field)
        return cls(
            schema_version=1,
            id=data["id"],
            backend=data["backend"],
            allowed_environments=tuple(envs),
            ceilings=ceilings,
            delegated_cgroup_root=data["delegated_cgroup_root"],
            state_root=data["state_root"],
            process_headroom_mb=data["process_headroom_mb"],
            evidence_quota_mb=data["evidence_quota_mb"],
            legacy_budget=legacy_budget,
            legacy_python_environment=legacy_env,
        )


@dataclass(frozen=True)
class Dependency:
    source: str
    destination: str
    digest: str


@dataclass(frozen=True)
class ImageArchive:
    source: str
    digest: str
    image_digest: str


@dataclass(frozen=True)
class EnvironmentConfig:
    """Operator prepared environment (spec environment-v1 field set)."""

    schema_version: int
    id: str
    content_digest: str
    manifest_path: str
    runtime_root: str
    executables: MappingProxyType
    dependencies: tuple[Dependency, ...]
    environment: MappingProxyType
    image_archives: tuple[ImageArchive, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnvironmentConfig:
        required = {
            "schema_version", "id", "content_digest", "manifest_path",
            "runtime_root", "executables", "dependencies", "environment",
            "image_archives",
        }
        _check_keys(data, required, "environment")
        if data["schema_version"] != 1:
            raise ValueError("environment schema_version must be 1")
        if not valid_identifier(data["id"]):
            raise ValueError("environment id must be a valid identifier")
        _digest_shape(data["content_digest"], "environment.content_digest")
        for field in ("manifest_path", "runtime_root"):
            value = data[field]
            if not isinstance(value, str) or not value.startswith("/"):
                raise ValueError("environment.%s must be an absolute path" % field)
        executables = data["executables"]
        if not isinstance(executables, dict):
            raise TypeError("environment.executables must be a mapping")
        for alias, target in executables.items():
            if not isinstance(alias, str) or not alias or len(alias) > MAX_STRING:
                raise ValueError("environment.executables alias must be bounded")
            if not isinstance(target, str) or not target.startswith("/"):
                raise ValueError(
                    "environment.executables[%r] must be an absolute sandbox path"
                    % alias
                )
        dependencies = data["dependencies"]
        if not isinstance(dependencies, list) or len(dependencies) > MAX_LIST:
            raise ValueError("environment.dependencies must be a bounded list")
        parsed_deps = []
        for entry in dependencies:
            if not isinstance(entry, dict):
                raise TypeError("environment.dependencies entry must be a mapping")
            _check_keys(entry, {"source", "destination", "digest"},
                        "environment.dependencies entry")
            for field in ("source", "destination"):
                if not isinstance(entry[field], str) or not entry[field].startswith("/"):
                    raise ValueError(
                        "environment.dependencies entry %s must be absolute" % field
                    )
            _digest_shape(entry["digest"], "environment.dependencies digest")
            parsed_deps.append(
                Dependency(entry["source"], entry["destination"], entry["digest"])
            )
        env_map = data["environment"]
        if not isinstance(env_map, dict):
            raise TypeError("environment.environment must be a mapping")
        for key, value in env_map.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("environment.environment entries must be strings")
            if len(key) > MAX_STRING or len(value) > MAX_STRING * 8:
                raise ValueError("environment.environment entries must be bounded")
        archives = data["image_archives"]
        if not isinstance(archives, list) or len(archives) > MAX_LIST:
            raise ValueError("environment.image_archives must be a bounded list")
        parsed_archives = []
        for entry in archives:
            if not isinstance(entry, dict):
                raise TypeError("environment.image_archives entry must be a mapping")
            _check_keys(entry, {"source", "digest", "image_digest"},
                        "environment.image_archives entry")
            if not isinstance(entry["source"], str) or not entry["source"].startswith("/"):
                raise ValueError("environment.image_archives source must be absolute")
            _digest_shape(entry["digest"], "environment.image_archives digest")
            _digest_shape(
                entry["image_digest"], "environment.image_archives image_digest"
            )
            parsed_archives.append(
                ImageArchive(entry["source"], entry["digest"], entry["image_digest"])
            )
        return cls(
            schema_version=1,
            id=data["id"],
            content_digest=data["content_digest"],
            manifest_path=data["manifest_path"],
            runtime_root=data["runtime_root"],
            executables=MappingProxyType(dict(executables)),
            dependencies=tuple(parsed_deps),
            environment=MappingProxyType(dict(env_map)),
            image_archives=tuple(parsed_archives),
        )


@dataclass(frozen=True)
class ApprovalRecord:
    """Operator approval binding (spec host-files section).

    Binds worker and profile file digests, the environment content
    digest and the repository identity.  Any mismatch against the
    loaded records is a stale approval.
    """

    schema_version: int
    repository_id: str
    worker_id: str
    worker_digest: str
    profile_id: str
    profile_digest: str
    environment_id: str
    environment_content_digest: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApprovalRecord:
        _check_keys(
            data,
            {"schema_version", "repository_id", "worker", "profile", "environment"},
            "approval",
        )
        if data["schema_version"] != 1:
            raise ValueError("approval schema_version must be 1")
        if not valid_identifier(data["repository_id"]):
            raise ValueError("approval repository_id must be a valid identifier")
        bound = {}
        for section, id_key, digest_key in (
            ("worker", "worker_id", "worker_digest"),
            ("profile", "profile_id", "profile_digest"),
            ("environment", "environment_id", "environment_content_digest"),
        ):
            entry = data[section]
            if not isinstance(entry, dict):
                raise TypeError("approval.%s must be a mapping" % section)
            wanted = {"id", "digest"} if section != "environment" else {
                "id", "content_digest"
            }
            _check_keys(entry, wanted, "approval.%s" % section)
            if not valid_identifier(entry["id"]):
                raise ValueError("approval.%s id must be a valid identifier" % section)
            _digest_shape(
                entry["digest"] if section != "environment"
                else entry["content_digest"],
                "approval.%s digest" % section,
            )
            bound[id_key] = entry["id"]
            bound[digest_key] = (
                entry["digest"] if section != "environment"
                else entry["content_digest"]
            )
        return cls(
            schema_version=1,
            repository_id=data["repository_id"],
            **bound,
        )


@dataclass(frozen=True)
class HostConfig:
    """Fully loaded and approval-bound host configuration."""

    worker: WorkerConfig
    profile: ProfileConfig
    environment: EnvironmentConfig
    approval: ApprovalRecord


@dataclass(frozen=True)
class HostUnavailable:
    """A mapped unavailable reason, never a degraded local run."""

    reason: str
    detail: str


def _check_keys(data: dict[str, Any], required: set[str], what: str) -> None:
    if not isinstance(data, dict):
        raise TypeError("%s must be a mapping" % what)
    missing = required - data.keys()
    if missing:
        raise ValueError(
            "%s missing required keys: %s" % (what, ", ".join(sorted(missing)))
        )
    extra = set(data.keys()) - required
    if extra:
        raise ValueError(
            "%s has unknown keys: %s" % (what, ", ".join(sorted(extra)))
        )


def _positive_int(value: Any, what: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("%s must be a positive integer, got %r" % (what, value))


def _digest_shape(value: Any, what: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("%s must be 64 lowercase hex characters" % what)


def _budget_from(data: Any, what: str) -> Budget:
    if not isinstance(data, dict):
        raise TypeError("%s must be a complete budget mapping" % what)
    keys = set(data.keys())
    if keys != set(_BUDGET_KEYS):
        raise ValueError(
            "%s must contain exactly the budget keys %s"
            % (what, sorted(_BUDGET_KEYS))
        )
    return Budget(**data)


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key %r" % key)
        result[key] = value
    return result


class _LoadError(Exception):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _record_path(config_dir: Path, kind: str, name: str) -> Path:
    if not valid_identifier(name):
        raise _LoadError(
            *_reasons(kind, invalid=True),
        )
    return config_dir / kind / (name + ".json")


def _reasons(kind: str, invalid: bool, detail: str = "") -> tuple[str, str]:
    missing_reasons = {
        "workers": REASON_WORKER_MISSING,
        "profiles": REASON_PROFILE_MISSING,
        "environments": REASON_ENVIRONMENT_MISSING,
        "approvals": REASON_NOT_AUTHORIZED,
    }
    invalid_reasons = {
        "workers": REASON_WORKER_INVALID,
        "profiles": REASON_PROFILE_INVALID,
        "environments": REASON_ENVIRONMENT_INVALID,
        "approvals": REASON_APPROVAL_INVALID,
    }
    reason = (invalid_reasons if invalid else missing_reasons)[kind]
    return reason, detail or kind


def _read_record(config_dir: Path, kind: str, name: str) -> tuple[bytes, dict]:
    """Read one host record with the shared file-level rejections."""
    path = _record_path(config_dir, kind, name)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        reason, _ = _reasons(kind, invalid=False)
        raise _LoadError(reason, "%s record %r not found" % (kind, name)) from None
    if stat.S_ISLNK(st.st_mode):
        reason, _ = _reasons(kind, invalid=True)
        raise _LoadError(reason, "%s record %r is a symlink" % (kind, name))
    if not stat.S_ISREG(st.st_mode):
        reason, _ = _reasons(kind, invalid=True)
        raise _LoadError(reason, "%s record %r is not a regular file" % (kind, name))
    if st.st_mode & 0o022:
        reason, _ = _reasons(kind, invalid=True)
        raise _LoadError(
            reason,
            "%s record %r is writable by group or other" % (kind, name),
        )
    if st.st_size > MAX_CONFIG_BYTES:
        reason, _ = _reasons(kind, invalid=True)
        raise _LoadError(
            reason,
            "%s record %r exceeds %d bytes" % (kind, name, MAX_CONFIG_BYTES),
        )
    raw = path.read_bytes()
    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_object)
    except (UnicodeDecodeError, ValueError) as exc:
        reason, _ = _reasons(kind, invalid=True)
        raise _LoadError(
            reason, "%s record %r is not valid JSON: %s" % (kind, name, exc)
        ) from None
    if not isinstance(parsed, dict):
        reason, _ = _reasons(kind, invalid=True)
        raise _LoadError(reason, "%s record %r must be a JSON object" % (kind, name))
    return raw, parsed


def _parse(kind: str, name: str, parsed: dict, parser) -> Any:
    try:
        record = parser(parsed)
    except (ValueError, TypeError) as exc:
        reason, _ = _reasons(kind, invalid=True)
        raise _LoadError(reason, "%s record %r invalid: %s" % (kind, name, exc)) from None
    record_id = getattr(record, "id", getattr(record, "repository_id", None))
    if record_id != name:
        reason, _ = _reasons(kind, invalid=True)
        raise _LoadError(
            reason,
            "%s record id %r does not match file name %r" % (kind, record_id, name),
        )
    return record


def load_host_config(
    config_dir: str | Path,
    *,
    worker: str,
    profile: str,
    environment: str,
    repository_id: str,
) -> HostConfig | HostUnavailable:
    """Load and approval-bind the operator host configuration.

    Returns HostConfig on success or HostUnavailable with the mapped
    reason code.  Never falls back to a degraded local run.
    """
    root = Path(config_dir)
    try:
        worker_raw, worker_parsed = _read_record(root, "workers", worker)
        worker_cfg = _parse("workers", worker, worker_parsed, WorkerConfig.from_dict)
        profile_raw, profile_parsed = _read_record(root, "profiles", profile)
        profile_cfg = _parse("profiles", profile, profile_parsed, ProfileConfig.from_dict)
        _env_raw, env_parsed = _read_record(root, "environments", environment)
        env_cfg = _parse(
            "environments", environment, env_parsed, EnvironmentConfig.from_dict
        )
        if not valid_identifier(repository_id):
            raise _LoadError(
                REASON_NOT_AUTHORIZED,
                "repository id %r is not a valid identifier" % repository_id,
            )
        _appr_raw, appr_parsed = _read_record(root, "approvals", repository_id)
        approval = _parse(
            "approvals", repository_id, appr_parsed, ApprovalRecord.from_dict
        )
    except _LoadError as exc:
        return HostUnavailable(exc.reason, exc.detail)

    stale = []
    if approval.worker_id != worker_cfg.id or (
        approval.worker_digest != hashlib.sha256(worker_raw).hexdigest()
    ):
        stale.append("worker")
    if approval.profile_id != profile_cfg.id or (
        approval.profile_digest != hashlib.sha256(profile_raw).hexdigest()
    ):
        stale.append("profile")
    if approval.environment_id != env_cfg.id or (
        approval.environment_content_digest != env_cfg.content_digest
    ):
        stale.append("environment")
    if stale:
        return HostUnavailable(
            REASON_NOT_AUTHORIZED,
            "approval does not bind current %s record(s)" % ", ".join(stale),
        )
    return HostConfig(
        worker=worker_cfg,
        profile=profile_cfg,
        environment=env_cfg,
        approval=approval,
    )
