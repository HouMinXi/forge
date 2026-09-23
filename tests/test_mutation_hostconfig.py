"""Tests for the operator host-configuration loader."""

import hashlib
import json
import os

import pytest

from code_forge.mutation_engines.hostconfig import (
    HostConfig,
    HostUnavailable,
    load_host_config,
)

BUDGET = {
    "total_seconds": 600,
    "baseline_seconds": 120,
    "mutant_seconds": 30,
    "concurrency": 1,
    "memory_mb": 4096,
    "processes": 256,
    "workspace_mb": 2048,
    "evidence_mb": 512,
}

WORKER = {
    "schema_version": 1,
    "id": "worker-1",
    "concurrency": 1,
    "swap_mb": 0,
    "memory_mb": 8192,
    "pids": 512,
    "supervisor_memory_mb": 512,
    "supervisor_pids": 64,
    "delegated_cgroup_root": "/sys/fs/cgroup/forge",
    "state_root": "/var/lib/forge/state",
}

PROFILE = {
    "schema_version": 1,
    "id": "linux-isolated",
    "backend": "linux-isolated",
    "allowed_environments": ["python-core-v1"],
    "ceilings": dict(BUDGET),
    "delegated_cgroup_root": "/sys/fs/cgroup/forge",
    "state_root": "/var/lib/forge/state",
    "process_headroom_mb": 256,
    "evidence_quota_mb": 1024,
    "legacy_budget": None,
    "legacy_python_environment": None,
}

ENV_CONTENT_DIGEST = hashlib.sha256(b"immutable tree").hexdigest()

ENVIRONMENT = {
    "schema_version": 1,
    "id": "python-core-v1",
    "content_digest": ENV_CONTENT_DIGEST,
    "manifest_path": "/opt/forge/envs/python-core-v1/manifest.json",
    "runtime_root": "/opt/forge/envs/python-core-v1/root",
    "executables": {"python": "/usr/bin/python3"},
    "dependencies": [
        {
            "source": "/opt/forge/deps/site-packages",
            "destination": "/usr/lib/python3/site-packages",
            "digest": hashlib.sha256(b"deps").hexdigest(),
        }
    ],
    "environment": {"PATH": "/usr/bin"},
    "image_archives": [],
}

REPO = "forge-main"


def _write(path, payload, mode=0o644, raw=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    path.write_bytes(data)
    os.chmod(path, mode)
    return data


def _digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def config_dir(tmp_path):
    root = tmp_path / "mutation"
    worker_raw = _write(root / "workers" / "worker-1.json", WORKER)
    profile_raw = _write(root / "profiles" / "linux-isolated.json", PROFILE)
    _write(root / "environments" / "python-core-v1.json", ENVIRONMENT)
    approval = {
        "schema_version": 1,
        "repository_id": REPO,
        "worker": {"id": "worker-1", "digest": _digest(worker_raw)},
        "profile": {"id": "linux-isolated", "digest": _digest(profile_raw)},
        "environment": {
            "id": "python-core-v1",
            "content_digest": ENV_CONTENT_DIGEST,
        },
    }
    _write(root / "approvals" / (REPO + ".json"), approval)
    return root


def _load(root, **overrides):
    args = dict(
        worker="worker-1",
        profile="linux-isolated",
        environment="python-core-v1",
        repository_id=REPO,
    )
    args.update(overrides)
    return load_host_config(root, **args)


def test_happy_path_returns_bound_config(config_dir):
    result = _load(config_dir)
    assert isinstance(result, HostConfig)
    assert result.worker.id == "worker-1"
    assert result.profile.backend == "linux-isolated"
    assert result.environment.id == "python-core-v1"
    assert result.approval.repository_id == REPO


def test_missing_worker_maps_to_worker_missing(config_dir):
    (config_dir / "workers" / "worker-1.json").unlink()
    result = _load(config_dir)
    assert isinstance(result, HostUnavailable)
    assert result.reason == "worker_missing"


def test_missing_profile_and_environment_reasons(config_dir):
    (config_dir / "profiles" / "linux-isolated.json").unlink()
    assert _load(config_dir).reason == "profile_missing"
    (config_dir / "environments" / "python-core-v1.json").unlink()
    assert _load(config_dir, profile="linux-isolated").reason in (
        "profile_missing",
        "environment_missing",
    )


def test_missing_approval_is_execution_not_authorized(config_dir):
    (config_dir / "approvals" / (REPO + ".json")).unlink()
    result = _load(config_dir)
    assert result.reason == "execution_not_authorized"


def test_symlink_rejected(config_dir):
    target = config_dir / "workers" / "worker-1.json"
    link = config_dir / "workers" / "evil.json"
    link.symlink_to(target)
    result = _load(config_dir, worker="evil")
    assert result.reason == "worker_invalid"
    assert "symlink" in result.detail


@pytest.mark.parametrize("mode", [0o664, 0o646, 0o666])
def test_foreign_writable_rejected(config_dir, mode):
    os.chmod(config_dir / "profiles" / "linux-isolated.json", mode)
    result = _load(config_dir)
    assert result.reason == "profile_invalid"
    assert "writable" in result.detail


def test_duplicate_keys_rejected(config_dir):
    raw = b'{"schema_version": 1, "schema_version": 1, "id": "worker-1"}'
    _write(config_dir / "workers" / "worker-1.json", None, raw=raw)
    result = _load(config_dir)
    assert result.reason == "worker_invalid"
    assert "duplicate" in result.detail


def test_unknown_field_rejected(config_dir):
    payload = dict(WORKER, extra_key=1)
    _write(config_dir / "workers" / "worker-1.json", payload)
    assert _load(config_dir).reason == "worker_invalid"


def test_oversized_file_rejected(config_dir):
    big = json.dumps(dict(WORKER, padding="x" * 70000)).encode("utf-8")
    # unknown-key rejection would also fire; size must fire first or jointly
    _write(config_dir / "workers" / "worker-1.json", None, raw=big)
    result = _load(config_dir)
    assert result.reason == "worker_invalid"


def test_malformed_json_rejected(config_dir):
    _write(config_dir / "profiles" / "linux-isolated.json", None, raw=b"{nope")
    assert _load(config_dir).reason == "profile_invalid"


@pytest.mark.parametrize("name", ["../x", "a/b", "UPPER", "..", "a\\b"])
def test_non_identifier_name_rejected_without_filesystem_read(config_dir, name):
    result = _load(config_dir, worker=name)
    assert result.reason == "worker_invalid"


def test_worker_schema_version_two_rejected(config_dir):
    _write(config_dir / "workers" / "worker-1.json", dict(WORKER, schema_version=2))
    assert _load(config_dir).reason == "worker_invalid"


def test_profile_unknown_backend_rejected(config_dir):
    _write(
        config_dir / "profiles" / "linux-isolated.json",
        dict(PROFILE, backend="windows-isolated"),
    )
    assert _load(config_dir).reason == "profile_invalid"


def test_profile_incomplete_ceilings_rejected(config_dir):
    ceilings = dict(BUDGET)
    del ceilings["memory_mb"]
    _write(
        config_dir / "profiles" / "linux-isolated.json",
        dict(PROFILE, ceilings=ceilings),
    )
    assert _load(config_dir).reason == "profile_invalid"


def test_profile_legacy_budget_incomplete_rejected(config_dir):
    ceilings = dict(BUDGET)
    del ceilings["processes"]
    _write(
        config_dir / "profiles" / "linux-isolated.json",
        dict(PROFILE, legacy_budget=ceilings),
    )
    assert _load(config_dir).reason == "profile_invalid"


def test_environment_non_absolute_executable_rejected(config_dir):
    payload = dict(ENVIRONMENT, executables={"python": "usr/bin/python3"})
    _write(config_dir / "environments" / "python-core-v1.json", payload)
    assert _load(config_dir).reason == "environment_invalid"


def test_environment_dependency_missing_digest_rejected(config_dir):
    dep = {"source": "/a", "destination": "/b"}
    _write(
        config_dir / "environments" / "python-core-v1.json",
        dict(ENVIRONMENT, dependencies=[dep]),
    )
    assert _load(config_dir).reason == "environment_invalid"


def test_id_mismatch_with_filename_rejected(config_dir):
    _write(
        config_dir / "profiles" / "linux-isolated.json",
        dict(PROFILE, id="other-profile"),
    )
    assert _load(config_dir).reason == "profile_invalid"


def _rewrite_approval(config_dir, **changes):
    path = config_dir / "approvals" / (REPO + ".json")
    approval = json.loads(path.read_text())
    approval.update(changes)
    _write(path, approval)


def test_stale_worker_digest_is_not_authorized(config_dir):
    _rewrite_approval(
        config_dir, worker={"id": "worker-1", "digest": "0" * 64}
    )
    assert _load(config_dir).reason == "execution_not_authorized"


def test_stale_environment_digest_is_not_authorized(config_dir):
    _rewrite_approval(
        config_dir,
        environment={"id": "python-core-v1", "content_digest": "0" * 64},
    )
    assert _load(config_dir).reason == "execution_not_authorized"


def test_approval_for_other_repository_is_not_authorized(config_dir):
    result = _load(config_dir, repository_id="other-repo")
    assert result.reason in ("execution_not_authorized", "approval_missing")


def test_approval_wrong_worker_id_is_not_authorized(config_dir):
    _rewrite_approval(
        config_dir, worker={"id": "worker-2", "digest": "0" * 64}
    )
    assert _load(config_dir).reason == "execution_not_authorized"


def test_malformed_approval_is_approval_invalid(config_dir):
    _write(config_dir / "approvals" / (REPO + ".json"), None, raw=b"[1,2]")
    assert _load(config_dir).reason == "approval_invalid"


def test_unavailable_carries_detail(config_dir):
    (config_dir / "workers" / "worker-1.json").unlink()
    result = _load(config_dir)
    assert result.detail
    assert "worker-1" in result.detail
