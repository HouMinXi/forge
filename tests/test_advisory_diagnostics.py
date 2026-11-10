# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Strict protocol and real descriptor-relative publication boundaries."""

from dataclasses import FrozenInstanceError, replace
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

import pytest

from code_forge import _advisory_diagnostics as records


@pytest.fixture
def context():
    return records.ExpectedContext(str(uuid4()), str(uuid4()), "source", "a" * 64, 1)


@pytest.fixture
def observation(context):
    return records.make_observation(context, "no_named_targets")


@pytest.fixture
def storage(tmp_path):
    root = tmp_path / "primary"
    root.mkdir(mode=0o700)
    return records.bind_storage(root)


def final_path(publication):
    locator = publication.locator
    return locator.binding.source_root.joinpath(
        *(name for name, _ in locator.directories), locator.filename
    )


def document(observation):
    return json.loads(records._payload(observation))


def encoded(data):
    return json.dumps(data, ensure_ascii=True).encode("utf-8")


@pytest.mark.parametrize("state", sorted(records._STATES))
def test_every_closed_state_round_trips_after_publisher_descriptors_close(storage, context, state):
    observation = records.make_observation(context, state)
    before = len(os.listdir("/proc/self/fd"))
    publication = records.publish_observation(storage, observation)
    assert publication.status == "published"
    assert publication.cleanup == "complete"
    assert publication.failure is None
    assert records.read_observation(publication, context) == observation
    assert len(os.listdir("/proc/self/fd")) == before
    path = final_path(publication)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_nlink == 1
    for parent in list(path.parents)[:5]:
        assert parent.stat().st_mode & 0o777 == 0o700
    assert list(path.parent.iterdir()) == [path]
    assert bool(observation.diagnostics) == (state == "no_named_targets")


def test_context_observation_and_results_are_immutable(storage, observation):
    publication = records.publish_observation(storage, observation)
    for target, field in (
        (observation, "analysis_state"),
        (observation.context, "source_hash"),
        (observation.diagnostics[0], "message"),
        (publication, "status"),
        (publication.locator, "digest"),
        (storage, "source_root"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, field, "changed")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_id", "../escape"),
        ("execution_id", "A" * 36),
        ("execution_id", "00000000-0000-0000-0000-000000000000"),
        ("execution_id", "1b671a64-40d5-491e-99b0-da01ff1f3341 "),
        ("repository_id", None),
        ("repository_id", 1),
        ("source_hash", ""),
        ("source_hash", "é" * 129),
        ("source_hash", "\ud800"),
        ("source_hash", False),
        ("diff_sha256", "A" * 64),
        ("diff_sha256", "a" * 63),
        ("diff_sha256", "a" * 64 + "\n"),
        ("observation_sequence", True),
        ("observation_sequence", 1.0),
        ("observation_sequence", "1"),
        ("observation_sequence", 0),
        ("observation_sequence", 65),
    ],
)
def test_independent_context_rejected_even_before_parsing(context, field, value, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid independent context must not reach JSON parsing")

    monkeypatch.setattr(records.json, "loads", forbidden)
    with pytest.raises(records.DiagnosticError):
        records.decode_observation(b"{}", replace(context, **{field: value}))


@pytest.mark.parametrize("field", sorted(records._FIELDS))
def test_every_missing_field_is_rejected(observation, context, field):
    data = document(observation)
    del data[field]
    with pytest.raises(records.DiagnosticError):
        records.decode_observation(encoded(data), context)


@pytest.mark.parametrize("where", ["root", "repository", "diagnostic"])
def test_extra_fields_rejected_at_each_object_level(observation, context, where):
    data = document(observation)
    target = (
        data
        if where == "root"
        else data["repository_binding"]
        if where == "repository"
        else data["diagnostics"][0]
    )
    target["future"] = None
    with pytest.raises(records.DiagnosticError):
        records.decode_observation(encoded(data), context)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", 1.0),
        ("schema_version", "1"),
        ("schema_version", 2),
        ("schema_version", None),
        ("axis", "other"),
        ("analysis_state", "success"),
        ("analysis_state", None),
        ("diagnostics", None),
        ("diagnostics", {}),
        ("diagnostics", []),
        ("repository_binding", None),
        ("repository_binding", {"kind": "path", "id": "/private"}),
        ("source_hash", "\ud800"),
        ("source_hash", "é" * 129),
    ],
)
def test_schema_wrong_types_versions_enums_combinations(observation, context, field, value):
    data = document(observation)
    data[field] = value
    with pytest.raises(records.DiagnosticError):
        records.decode_observation(encoded(data), context)


@pytest.mark.parametrize("state", ["not_requested", "not_run", "targets_resolved", "unavailable"])
def test_only_no_named_targets_may_have_diagnostic(observation, context, state):
    data = document(observation)
    data["analysis_state"] = state
    with pytest.raises(records.DiagnosticError):
        records.decode_observation(encoded(data), context)


@pytest.mark.parametrize("change", ["message", "code", "second"])
def test_fixed_diagnostic_cannot_be_fabricated(observation, context, change):
    data = document(observation)
    if change == "second":
        data["diagnostics"].append(data["diagnostics"][0].copy())
    else:
        data["diagnostics"][0][change] = "arbitrary external text"
    with pytest.raises(records.DiagnosticError):
        records.decode_observation(encoded(data), context)


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"{} trailing",
        b"\xff",
        b"\xef\xbb\xbf{}",
        b"[]",
        b"null",
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b'{"x":1e309}',
        b'{"x":12345678901234567890}',
        b'{"x":1,"x":2}',
        b'{"x":{"y":1,"y":2}}',
        b'{"x":"\\ud800"}',
        b'{"x":"\\udfff"}',
        b'{"x":[[[]]]}',
        b"[" * 1000,
    ],
)
def test_malformed_and_nonfinite_json_rejected(context, raw):
    with pytest.raises(records.DiagnosticError):
        records.decode_observation(raw, context)


@pytest.mark.parametrize("kind", ["bytes", "depth"])
def test_large_or_deep_document_rejected_before_json_load(context, monkeypatch, kind):
    def forbidden(*args, **kwargs):
        pytest.fail("bounds must run before JSON parsing")

    monkeypatch.setattr(records.json, "loads", forbidden)
    raw = b" " * (records.MAX_BYTES + 1) if kind == "bytes" else b'{"a":[[{}]]}'
    with pytest.raises(records.DiagnosticError):
        records.decode_observation(raw, context)


def test_quoted_depth_and_escapes_are_not_containers(context):
    context = replace(context, source_hash='[[[[{{{{\\"}}}]]]')
    observation = records.make_observation(context, "no_named_targets")
    assert records.decode_observation(records._payload(observation), context) == observation


@pytest.mark.parametrize("sequence", [1, 64])
def test_maximum_utf8_string_and_sequence_remain_within_payload_budget(context, sequence):
    context = replace(context, source_hash="\0" * 256, observation_sequence=sequence)
    observation = records.make_observation(context, "no_named_targets")
    raw = records._payload(observation)
    assert len(raw) <= 2050 < records.MAX_BYTES
    assert records.decode_observation(raw, context) == observation
    raw += b" " * (records.MAX_BYTES - len(raw))
    assert records.decode_observation(raw, context) == observation


@pytest.mark.parametrize(
    "field", ["execution_id", "repository_id", "source_hash", "diff_sha256", "observation_sequence"]
)
def test_wrong_independent_context_never_returns_empty_success(storage, context, observation, field):
    values = {
        "execution_id": str(uuid4()),
        "repository_id": str(uuid4()),
        "source_hash": "other",
        "diff_sha256": "b" * 64,
        "observation_sequence": 2,
    }
    publication = records.publish_observation(storage, observation)
    with pytest.raises(records.DiagnosticError):
        records.read_observation(publication, replace(context, **{field: values[field]}))


def test_new_execution_with_identical_source_and_diff_cannot_reuse_record(storage, context, observation):
    publication = records.publish_observation(storage, observation)
    new = replace(context, execution_id=str(uuid4()), repository_id=str(uuid4()))
    with pytest.raises(records.DiagnosticError):
        records.read_observation(publication, new)
    second = records.publish_observation(storage, records.make_observation(new, "no_named_targets"))
    assert second.status == "published"
    assert final_path(publication) != final_path(second)


@pytest.mark.parametrize("same_bytes", [False, True])
def test_same_key_always_refused_without_replacing_original(storage, context, observation, same_bytes):
    first = records.publish_observation(storage, observation)
    second = records.publish_observation(
        storage, observation if same_bytes else records.make_observation(context, "targets_resolved")
    )
    assert second == records.PublicationResult(
        "not_published", failure="name_conflict", cleanup="not_needed"
    )
    assert records.read_observation(first, context) == observation
    assert len(list(final_path(first).parent.iterdir())) == 1


def test_no_record_under_temporary_worker_cwd(storage, context, observation, tmp_path):
    cwd = tmp_path / "worker"
    cwd.mkdir()
    binding = records.bind_storage(storage.source_root, (cwd,))
    publication = records.publish_observation(binding, observation)
    cwd.rmdir()
    assert records.read_observation(publication, context) == observation


@pytest.mark.parametrize("kind", ["source", "descendant", "alias"])
def test_durable_root_cannot_overlap_temporary_cwd(storage, tmp_path, kind):
    excluded = storage.source_root
    if kind == "descendant":
        excluded = storage.source_root / ".code-forge/advisory-diagnostics/worker"
    elif kind == "alias":
        excluded = tmp_path / "alias"
        excluded.symlink_to(storage.source_root, target_is_directory=True)
    with pytest.raises(records.DiagnosticError):
        records.bind_storage(storage.source_root, (excluded,))


def test_source_symlink_and_shared_permissions_are_rejected_without_chmod(storage, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(storage.source_root, target_is_directory=True)
    with pytest.raises(records.DiagnosticError):
        records.bind_storage(alias)
    storage.source_root.chmod(0o770)
    with pytest.raises(records.DiagnosticError):
        records.bind_storage(storage.source_root)
    assert stat.S_IMODE(storage.source_root.stat().st_mode) == 0o770


def test_replaced_source_root_cannot_be_rebound_by_existing_binding(storage, observation):
    storage.source_root.rename(storage.source_root.with_name("old"))
    storage.source_root.mkdir(mode=0o700)
    publication = records.publish_observation(storage, observation)
    assert publication.status == "not_published"
    assert not list(storage.source_root.iterdir())


@pytest.mark.parametrize("position", range(5))
def test_every_namespace_directory_rejects_symlink(storage, context, observation, tmp_path, position):
    components = (
        ".code-forge",
        "advisory-diagnostics",
        context.execution_id,
        context.repository_id,
        records._AXIS,
    )
    parent = storage.source_root
    for component in components[:position]:
        parent = parent / component
        parent.mkdir(mode=0o700)
    foreign = tmp_path / "foreign"
    foreign.mkdir(mode=0o700)
    (parent / components[position]).symlink_to(foreign, target_is_directory=True)
    publication = records.publish_observation(storage, observation)
    assert publication.status == "not_published"
    assert not list(foreign.iterdir())


def test_preexisting_shared_directory_is_never_chmodded(storage, observation):
    directory = storage.source_root / ".code-forge"
    directory.mkdir()
    directory.chmod(0o777)
    publication = records.publish_observation(storage, observation)
    assert publication.status == "not_published"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o777


@pytest.mark.parametrize("acl", [b"extended-acl", errno.EPERM, errno.EIO])
def test_detected_or_uninspectable_acl_fails_closed(storage, observation, monkeypatch, acl):
    def inspect(*args, **kwargs):
        if isinstance(acl, bytes):
            return acl
        raise OSError(acl, "private error must not escape")

    monkeypatch.setattr(records.os, "getxattr", inspect)
    publication = records.publish_observation(storage, observation)
    assert publication.status == "not_published"
    assert "private" not in repr(publication)


@pytest.mark.parametrize("error", [errno.ENODATA, errno.ENOTSUP])
def test_absent_or_unsupported_acl_namespace_on_qualified_mount(
    storage, observation, monkeypatch, error
):
    def inspect(*args, **kwargs):
        raise OSError(error, "namespace absence")

    monkeypatch.setattr(records.os, "getxattr", inspect)
    assert records.publish_observation(storage, observation).status == "published"


def test_every_directory_and_leaf_mount_is_requalified(storage, observation, monkeypatch):
    original = records._filesystem
    checked = []

    def inspect(fd, info):
        checked.append(Path(os.readlink(f"/proc/self/fd/{fd}")).name)
        original(fd, info)

    monkeypatch.setattr(records, "_filesystem", inspect)
    publication = records.publish_observation(storage, observation)
    assert publication.status == "published"
    for name in (
        "primary",
        ".code-forge",
        "advisory-diagnostics",
        observation.context.execution_id,
        observation.context.repository_id,
        records._AXIS,
        "1.json",
    ):
        assert name in checked
    assert any(name.startswith(".pending-") for name in checked)


@pytest.mark.parametrize("kind", ["unknown", "missing", "duplicate", "device", "fdinfo", "unreadable"])
def test_filesystem_qualification_fails_closed(storage, observation, monkeypatch, kind):
    original = records._bounded_proc
    selected_mount = None
    mutated_mount = False

    def inspect(path, cap):
        nonlocal selected_mount, mutated_mount
        raw = original(path, cap)
        if kind == "unreadable":
            raise OSError(errno.EACCES, "denied")
        if "/fdinfo/" in path:
            mount_ids = [line.split()[1] for line in raw.splitlines() if line.startswith(b"mnt_id:")]
            assert len(mount_ids) == 1
            selected_mount = mount_ids[0]
        if kind == "fdinfo" and "fdinfo" in path:
            return b"mnt_id: invalid\n"
        if path.endswith("mountinfo"):
            if kind == "unknown":
                # Mutate the held fd's actual mount, independently of the
                # runner filesystem and without copying the production list.
                assert selected_mount is not None
                lines = raw.splitlines()
                matches = [
                    index for index, line in enumerate(lines) if line.split()[0] == selected_mount
                ]
                assert len(matches) == 1
                index = matches[0]
                before, separator, after = lines[index].partition(b" - ")
                filesystem, space, rest = after.partition(b" ")
                assert separator and space and filesystem != b"unsupported-test-filesystem"
                lines[index] = before + separator + b"unsupported-test-filesystem" + space + rest
                mutated = b"\n".join(lines) + b"\n"
                assert mutated != raw
                mutated_mount = True
                return mutated
            if kind == "missing":
                return b""
            if kind == "duplicate":
                return raw + raw
            if kind == "device":
                return b"\n".join(
                    b" ".join([*line.split()[:2], b"999:999", *line.split()[3:]])
                    for line in raw.splitlines()
                )
        return raw

    monkeypatch.setattr(records, "_bounded_proc", inspect)
    assert records.publish_observation(storage, observation).status == "not_published"
    if kind == "unknown":
        assert mutated_mount


def test_proc_upper_bound_plus_one_read(tmp_path):
    file = tmp_path / "proc-fixture"
    file.write_bytes(b"x" * 17)
    with pytest.raises(records.DiagnosticError):
        records._bounded_proc(str(file), 16)


@pytest.mark.parametrize("kind", ["replace", "modify", "missing", "symlink", "fifo", "directory"])
def test_reader_rejects_leaf_identity_content_and_nonregular_files(
    storage, observation, context, tmp_path, kind
):
    publication = records.publish_observation(storage, observation)
    path = final_path(publication)
    raw = path.read_bytes()
    if kind == "modify":
        path.write_bytes(raw.replace(b"source", b"SOURCE"))
    elif kind == "replace":
        original = path.stat()
        # Keep the old inode allocated. Unlink/recreate may recycle its number,
        # which is a distinct post-close ABA limitation, not this precondition.
        path.rename(path.with_name("retained-original"))
        path.write_bytes(raw)
        replacement = path.stat()
        assert (replacement.st_dev, replacement.st_ino) != (original.st_dev, original.st_ino)
        assert path.read_bytes() == raw
    else:
        path.unlink()
        if kind == "symlink":
            other = tmp_path / "foreign"
            other.write_bytes(raw)
            path.symlink_to(other)
        elif kind == "fifo":
            os.mkfifo(path)
        elif kind == "directory":
            path.mkdir()
    with pytest.raises(records.DiagnosticError):
        records.read_observation(publication, context)


@pytest.mark.parametrize("position", range(5))
def test_reader_rejects_replaced_directory_even_with_same_leaf_bytes(
    storage, observation, context, position
):
    publication = records.publish_observation(storage, observation)
    old_path = final_path(publication)
    raw = old_path.read_bytes()
    directory = storage.source_root.joinpath(
        *(name for name, _ in publication.locator.directories[: position + 1])
    )
    directory.rename(directory.with_name(directory.name + ".old"))
    new_path = final_path(publication)
    new_path.parent.mkdir(parents=True, mode=0o700)
    new_path.write_bytes(raw)
    with pytest.raises(records.DiagnosticError):
        records.read_observation(publication, context)


def test_reader_calls_actual_strict_codec_not_merely_digest(storage, observation, context, monkeypatch):
    publication = records.publish_observation(storage, observation)
    path = final_path(publication)
    raw = b'{"schema_version":1}'
    path.write_bytes(raw)
    locator = replace(publication.locator, byte_count=len(raw), digest=hashlib.sha256(raw).hexdigest())
    with pytest.raises(records.DiagnosticError, match="invalid_schema"):
        records.read_observation(replace(publication, locator=locator), context)


@pytest.mark.parametrize("operation", ["mkdir", "open", "write", "fstat", "getxattr"])
def test_precommit_operation_failure_is_not_published(storage, observation, monkeypatch, operation):
    def fail(*args, **kwargs):
        raise OSError(errno.EIO, "sensitive filesystem detail")

    with monkeypatch.context() as patch:
        patch.setattr(records.os, operation, fail)
        publication = records.publish_observation(storage, observation)
    assert publication.status == "not_published"
    assert publication.locator is None
    assert publication.failure == "publication_failed"
    assert "sensitive" not in repr(publication)


def test_short_write_is_completed_without_republishing(storage, observation, context, monkeypatch):
    original = os.write
    calls = []

    def short(fd, data):
        calls.append(len(data))
        return original(fd, data[:7])

    monkeypatch.setattr(records.os, "write", short)
    publication = records.publish_observation(storage, observation)
    assert len(calls) > 1
    assert records.read_observation(publication, context) == observation


@pytest.mark.parametrize("kind", ["zero", "partial-error"])
def test_incomplete_write_never_publishes_and_removes_owned_temp(
    storage, observation, monkeypatch, kind
):
    original = os.write
    calls = 0

    def fail(fd, data):
        nonlocal calls
        calls += 1
        if kind == "zero":
            return 0
        if calls == 1:
            return original(fd, data[:5])
        raise OSError(errno.ENOSPC, "full")

    monkeypatch.setattr(records.os, "write", fail)
    publication = records.publish_observation(storage, observation)
    assert publication.status == "not_published"
    assert publication.cleanup == "complete"
    assert not list(storage.source_root.rglob("*.json"))
    assert not list(storage.source_root.rglob(".pending-*"))


@pytest.mark.parametrize("committed", [False, True])
def test_link_error_distinguishes_verified_commit_from_unknown(
    storage, observation, context, monkeypatch, committed
):
    original = os.link
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        if committed:
            original(*args, **kwargs)
        raise OSError(errno.EIO, "commit outcome uncertain")

    monkeypatch.setattr(records.os, "link", fail)
    publication = records.publish_observation(storage, observation)
    assert len(calls) == 1
    assert publication.cleanup == "complete"
    if committed:
        assert publication.status == "published"
        assert records.read_observation(publication, context) == observation
    else:
        assert publication.status == "unknown"
        assert publication.locator is None


def test_commit_then_unreadable_remains_unknown_without_retry(storage, observation, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError(errno.EIO, "cannot verify")

    monkeypatch.setattr(records, "_read_leaf", fail)
    publication = records.publish_observation(storage, observation)
    assert publication.status == "unknown"
    assert publication.locator is None
    assert len(list(storage.source_root.rglob("*.json"))) == 1
    assert not list(storage.source_root.rglob(".pending-*"))


def test_committed_file_survives_cleanup_failure(storage, observation, context, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError(errno.EACCES, "cleanup failed")

    monkeypatch.setattr(records.os, "unlink", fail)
    publication = records.publish_observation(storage, observation)
    assert publication.status == "published"
    assert publication.cleanup == "failed"
    assert records.read_observation(publication, context) == observation
    assert len(list(storage.source_root.rglob(".pending-*"))) == 1


def test_cleanup_does_not_remove_foreign_temp_replacement(storage, observation, context, monkeypatch):
    original = os.link
    foreign = []

    def replace_temp(source, destination, **kwargs):
        original(source, destination, **kwargs)
        parent = kwargs["src_dir_fd"]
        os.unlink(source, dir_fd=parent)
        fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
        os.write(fd, b"foreign")
        os.close(fd)
        foreign.append(source)

    monkeypatch.setattr(records.os, "link", replace_temp)
    publication = records.publish_observation(storage, observation)
    assert publication.status == "published"
    assert publication.cleanup == "failed"
    assert (final_path(publication).parent / foreign[0]).read_bytes() == b"foreign"
    assert records.read_observation(publication, context) == observation


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("boundary", ["write", "link", "cleanup"])
def test_process_control_propagates_and_preserves_committed_record(
    storage, observation, monkeypatch, signal, boundary
):
    original_link = os.link

    def fail(*args, **kwargs):
        if boundary == "link":
            original_link(*args, **kwargs)
        raise signal("cancel")

    monkeypatch.setattr(records.os, "unlink" if boundary == "cleanup" else boundary, fail)
    with pytest.raises(signal, match="cancel"):
        records.publish_observation(storage, observation)
    assert bool(list(storage.source_root.rglob("*.json"))) == (boundary != "write")
    if boundary != "cleanup":
        assert not list(storage.source_root.rglob(".pending-*"))


def test_cleanup_signal_does_not_replace_primary_signal(storage, observation, monkeypatch):
    primary = KeyboardInterrupt("primary")

    def write(*args, **kwargs):
        raise primary

    def cleanup(*args, **kwargs):
        raise SystemExit("secondary")

    monkeypatch.setattr(records.os, "write", write)
    monkeypatch.setattr(records.os, "unlink", cleanup)
    with pytest.raises(KeyboardInterrupt) as caught:
        records.publish_observation(storage, observation)
    assert caught.value is primary


@pytest.mark.parametrize("operation", ["open", "read", "stat", "getxattr"])
def test_reader_io_failures_are_bounded(storage, observation, context, monkeypatch, operation):
    publication = records.publish_observation(storage, observation)

    def fail(*args, **kwargs):
        raise OSError(errno.EIO, "private path detail")

    monkeypatch.setattr(records.os, operation, fail)
    with pytest.raises(records.DiagnosticError) as caught:
        records.read_observation(publication, context)
    assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        "published",
        records.PublicationResult("unknown"),
        records.PublicationResult("published"),
    ],
)
def test_reader_never_accepts_malformed_or_unsuccessful_publication(context, value):
    with pytest.raises(records.DiagnosticError):
        records.read_observation(value, context)


def test_invalid_observation_cannot_trigger_path_creation(storage, context):
    bad = records.Observation(context, "no_named_targets", (records.Diagnostic("external", "unsafe"),))
    result = records.publish_observation(storage, bad)
    assert result.status == "not_published"
    assert not list(storage.source_root.iterdir())


def test_reader_rejects_replaced_source_root(storage, observation, context):
    publication = records.publish_observation(storage, observation)
    storage.source_root.rename(storage.source_root.with_name("old"))
    storage.source_root.mkdir(mode=0o700)
    with pytest.raises(records.DiagnosticError, match="identity_mismatch"):
        records.read_observation(publication, context)


@pytest.mark.parametrize("boundary", ["bind", "read"])
def test_arbitrary_diagnostic_exception_text_cannot_escape(
    storage, context, observation, monkeypatch, boundary
):
    publication = records.publish_observation(storage, observation)

    def fail(*args, **kwargs):
        raise records.DiagnosticError("private" * 1000)

    monkeypatch.setattr(records, "_open_source", fail)
    with pytest.raises(records.DiagnosticError) as caught:
        if boundary == "bind":
            records.bind_storage(storage.source_root)
        else:
            records.read_observation(publication, context)
    assert str(caught.value) in {"storage_unavailable", "read_failed"}


def test_commit_then_fileexists_error_is_verified_not_assumed_nonpublication(
    storage, observation, context, monkeypatch
):
    original = os.link

    def commit_then_error(*args, **kwargs):
        original(*args, **kwargs)
        raise FileExistsError("late failure")

    monkeypatch.setattr(records.os, "link", commit_then_error)
    publication = records.publish_observation(storage, observation)
    assert publication.status == "published"
    assert records.read_observation(publication, context) == observation


def test_ambiguous_foreign_final_after_link_error_is_never_adopted(storage, observation, monkeypatch):
    raw = records._payload(observation)

    def foreign_final(source, destination, **kwargs):
        parent = kwargs["dst_dir_fd"]
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
        os.write(fd, raw)
        os.close(fd)
        raise FileExistsError("outcome requires exact owned identity")

    monkeypatch.setattr(records.os, "link", foreign_final)
    publication = records.publish_observation(storage, observation)
    assert publication.status == "unknown"
    assert publication.locator is None
    assert next(storage.source_root.rglob("*.json")).read_bytes() == raw


def test_final_regular_file_with_shared_write_mode_is_rejected(storage, observation, context):
    publication = records.publish_observation(storage, observation)
    path = final_path(publication)
    path.chmod(0o660)
    with pytest.raises(records.DiagnosticError, match="unsafe_storage"):
        records.read_observation(publication, context)
    assert stat.S_IMODE(path.stat().st_mode) == 0o660


def test_leaf_mutation_during_read_is_detected(storage, observation, context, monkeypatch):
    publication = records.publish_observation(storage, observation)
    path = final_path(publication)
    original = os.read
    mutated = False

    def read(fd, count):
        nonlocal mutated
        result = original(fd, count)
        if result and not mutated:
            mutated = True
            path.write_bytes(path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(records.os, "read", read)
    with pytest.raises(records.DiagnosticError, match="content_mismatch"):
        records.read_observation(publication, context)


def test_failed_temp_identity_inspection_reports_incomplete_cleanup(storage, observation, monkeypatch):
    original = os.fstat

    def fstat(fd):
        if Path(os.readlink(f"/proc/self/fd/{fd}")).name.startswith(".pending-"):
            raise OSError(errno.EIO, "unknown temp identity")
        return original(fd)

    with monkeypatch.context() as patch:
        patch.setattr(records.os, "fstat", fstat)
        publication = records.publish_observation(storage, observation)
    assert publication.status == "not_published"
    assert publication.cleanup == "failed"
    assert len(list(storage.source_root.rglob(".pending-*"))) == 1


def test_close_process_control_does_not_replace_contained_primary_error(
    storage, observation, monkeypatch
):
    original_close = os.close

    def close(fd):
        original_close(fd)
        raise SystemExit("cleanup")

    def write(*args, **kwargs):
        raise OSError(errno.ENOSPC, "primary")

    with monkeypatch.context() as patch:
        patch.setattr(records.os, "close", close)
        patch.setattr(records.os, "write", write)
        publication = records.publish_observation(storage, observation)
    assert publication.status == "not_published"
    assert publication.cleanup == "complete"


def test_close_process_control_is_not_hidden_by_an_earlier_ordinary_close_failure(
    storage, observation, monkeypatch
):
    original_close = os.close
    calls = 0

    def close(fd):
        nonlocal calls
        original_close(fd)
        calls += 1
        if calls == 1:
            raise OSError(errno.EIO, "ordinary")
        if calls == 2:
            raise KeyboardInterrupt("cancel")

    with monkeypatch.context() as patch:
        patch.setattr(records.os, "close", close)
        with pytest.raises(KeyboardInterrupt, match="cancel"):
            records.publish_observation(storage, observation)
    assert calls >= 2
    assert len(list(storage.source_root.rglob("*.json"))) == 1


def test_reader_rejects_same_size_semantically_equivalent_rewrite(storage, observation, context):
    publication = records.publish_observation(storage, observation)
    path = final_path(publication)
    original = path.read_bytes()
    data = json.loads(original)
    rewritten = json.dumps(
        dict(reversed(list(data.items()))), ensure_ascii=False, separators=(",", ":")
    ).encode()
    assert len(rewritten) == len(original)
    assert records.decode_observation(rewritten, context) == observation
    path.write_bytes(rewritten)
    with pytest.raises(records.DiagnosticError, match="content_mismatch"):
        records.read_observation(publication, context)


@pytest.mark.parametrize("status", ["not_published", "unknown"])
@pytest.mark.parametrize("cleanup", ["not_needed", "complete", "failed"])
@pytest.mark.parametrize("failure", sorted(records._PUBLICATION_FAILURES))
def test_closed_nonpublished_results_validate_without_locator(context, status, cleanup, failure):
    result = records.PublicationResult(status, failure=failure, cleanup=cleanup)
    assert records.validate_publication(result, context) is None
    with pytest.raises(records.DiagnosticError, match="not_published"):
        records.read_observation(result, context)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", None),
        ("status", False),
        ("status", "success"),
        ("status", "x" * 10000),
        ("cleanup", None),
        ("cleanup", False),
        ("cleanup", "unknown"),
        ("cleanup", "x" * 10000),
        ("failure", "publication_failed"),
        ("failure", "x" * 10000),
        ("locator", None),
        ("locator", {}),
        ("locator", "x" * 10000),
    ],
)
def test_malformed_published_callback_fields_rejected(storage, observation, context, field, value):
    publication = records.publish_observation(storage, observation)
    with pytest.raises(records.DiagnosticError):
        records.validate_publication(replace(publication, **{field: value}), context)


@pytest.mark.parametrize("status", ["not_published", "unknown"])
@pytest.mark.parametrize("failure", [None, 1, False, "foreign reason", "x" * 10000])
def test_failed_result_requires_closed_failure_code(context, status, failure):
    with pytest.raises(records.DiagnosticError, match="invalid_publication"):
        records.validate_publication(records.PublicationResult(status, failure=failure), context)


@pytest.mark.parametrize("status", ["not_published", "unknown"])
def test_failed_result_must_not_carry_locator(storage, observation, context, status):
    publication = records.publish_observation(storage, observation)
    bad = replace(publication, status=status, failure="publication_failed")
    with pytest.raises(records.DiagnosticError, match="invalid_publication"):
        records.validate_publication(bad, context)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("directories", []),
        ("directories", ()),
        ("directories", (None,) * 10000),
        ("filename", "../1.json"),
        ("filename", "2.json"),
        ("filename", 1),
        ("filename", "x" * 10000),
        ("identity", None),
        ("identity", (1, 2)),
        ("byte_count", True),
        ("byte_count", 1.0),
        ("byte_count", 0),
        ("byte_count", 4097),
        ("digest", None),
        ("digest", "A" * 64),
        ("digest", "x" * 10000),
        ("binding", None),
        ("binding", {}),
        ("binding", "x" * 10000),
    ],
)
def test_malformed_locator_fields_rejected_before_retention(storage, observation, context, field, value):
    publication = records.publish_observation(storage, observation)
    locator = replace(publication.locator, **{field: value})
    with pytest.raises(records.DiagnosticError, match="invalid_locator"):
        records.validate_publication(replace(publication, locator=locator), context)


@pytest.mark.parametrize("position", range(5))
@pytest.mark.parametrize("value", [None, "../escape", "x" * 10000, 1])
def test_every_namespace_component_is_closed(storage, observation, context, position, value):
    publication = records.publish_observation(storage, observation)
    directories = list(publication.locator.directories)
    directories[position] = (value, directories[position][1])
    locator = replace(publication.locator, directories=tuple(directories))
    with pytest.raises(records.DiagnosticError, match="invalid_locator"):
        records.validate_publication(replace(publication, locator=locator), context)


@pytest.mark.parametrize("value", [True, -1, 2**64, 1.0, "1", None])
@pytest.mark.parametrize("location", ["source", "directory", "leaf"])
def test_locator_identities_are_exact_bounded_integers(storage, observation, context, location, value):
    publication = records.publish_observation(storage, observation)
    identity = records._Identity(value, value)
    locator = publication.locator
    if location == "source":
        locator = replace(locator, binding=replace(locator.binding, identity=identity))
    elif location == "directory":
        first, *remaining = locator.directories
        locator = replace(locator, directories=((first[0], identity), *remaining))
    else:
        locator = replace(locator, identity=identity)
    with pytest.raises(records.DiagnosticError, match="invalid_locator"):
        records.validate_publication(replace(publication, locator=locator), context)


@pytest.mark.parametrize(
    "path",
    [
        Path("relative"),
        Path("//alias/root"),
        Path("/tmp/../escape"),
        Path("/tmp/\0bad"),
        Path("/tmp/" + "a" * 256),
        Path("/tmp/" + "é" * 128),
        Path("/tmp/\ud800"),
        Path("/" + "/".join(["a" * 200] * 21)),
        Path("/" + "/".join(["x"] * 3000)),
        "/tmp/not-a-path-object",
    ],
)
def test_storage_binding_path_shape_is_bounded_without_resolving(storage, observation, context, path):
    publication = records.publish_observation(storage, observation)
    binding = replace(publication.locator.binding, source_root=path)
    locator = replace(publication.locator, binding=binding)
    with pytest.raises(records.DiagnosticError, match="invalid_locator"):
        records.validate_publication(replace(publication, locator=locator), context)


def test_publication_shape_validation_performs_no_io_or_ownership_claim(
    storage, observation, context, monkeypatch
):
    publication = records.publish_observation(storage, observation)
    # A well-shaped absent path validates; only the subsequent reader establishes
    # the real source root, mount domain, ownership, identities, and bytes.
    absent = storage.source_root / "does-not-exist"
    locator = replace(
        publication.locator, binding=replace(publication.locator.binding, source_root=absent)
    )
    no_disk = replace(publication, locator=locator)

    def forbidden(*args, **kwargs):
        pytest.fail("pure publication validation must not access filesystem")

    with monkeypatch.context() as patch:
        for name in ("open", "stat", "fstat", "getxattr"):
            patch.setattr(records.os, name, forbidden)
        patch.setattr(Path, "resolve", forbidden)
        assert records.validate_publication(publication, context) is None
        assert records.validate_publication(no_disk, context) is None
    with pytest.raises(records.DiagnosticError):
        records.read_observation(no_disk, context)


def test_reader_uses_shared_publication_validator(storage, observation, context, monkeypatch):
    publication = records.publish_observation(storage, observation)
    original = records.validate_publication
    calls = []

    def validate(result, expected):
        calls.append((result, expected))
        return original(result, expected)

    monkeypatch.setattr(records, "validate_publication", validate)
    assert records.read_observation(publication, context) == observation
    assert calls == [(publication, context)]
    with pytest.raises(records.DiagnosticError, match="invalid_publication"):
        records.read_observation(replace(publication, cleanup="invented"), context)


@pytest.mark.parametrize(
    "root",
    [
        Path("/" + "/".join(["x"] * 64)),
        Path("/" + "a" * 256),
        Path("/" + "/".join(["a" * 200] * 21)),
        Path("relative"),
        Path("/tmp/../outside"),
    ],
)
def test_unsupported_source_shape_is_refused_before_resolve_or_open(root, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unsupported source shape must not touch filesystem")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", forbidden)
        patch.setattr(records.os, "open", forbidden)
        with pytest.raises(records.DiagnosticError):
            records.bind_storage(root)
        with records._Descriptors() as descriptors:
            with pytest.raises(records.DiagnosticError):
                records._open_source(root, descriptors)


@pytest.mark.parametrize(
    "excluded",
    [
        [],
        [Path("/tmp")],
        (Path("/tmp"),) * 257,
        (Path("relative"),),
        (Path("/" + "/".join(["x"] * 64)),),
    ],
)
def test_invalid_exclusion_collection_is_refused_before_any_filesystem_read(
    storage, monkeypatch, excluded
):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid exclusion collection must not touch filesystem")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", forbidden)
        patch.setattr(records.os, "open", forbidden)
        with pytest.raises(records.DiagnosticError):
            records.bind_storage(storage.source_root, excluded)


def test_root_component_budget_counts_anchor_and_rejects_one_more():
    supported = Path("/" + "/".join(["x"] * 63))
    assert len(supported.parts) == 64
    records._validate_root(supported)
    with pytest.raises(records.DiagnosticError):
        records._validate_root(supported / "overflow")


def test_exact_full_path_byte_limit_is_supported_without_io():
    components = ["a" * 255] * 15 + ["a" * 254]
    supported = Path("/" + "/".join(components))
    assert len(str(supported).encode("utf-8")) == 4095
    records._validate_root(supported)
    components[-1] += "a"
    with pytest.raises(records.DiagnosticError):
        records._validate_root(Path("/" + "/".join(components)))


@pytest.mark.parametrize("case", ["identical-bytes", "changed-bytes", "stale-execution"])
def test_simulated_reused_leaf_identity_preserves_content_and_execution_checks(
    storage, observation, context, monkeypatch, case
):
    publication = records.publish_observation(storage, observation)
    path = final_path(publication)
    original_bytes = path.read_bytes()
    original_stat = path.stat()
    replacement_bytes = original_bytes
    if case == "changed-bytes":
        changed = records.make_observation(context, "unavailable")
        replacement_bytes = records._payload(changed)
        replacement_bytes += b" " * (len(original_bytes) - len(replacement_bytes))
        assert records.decode_observation(replacement_bytes, context) == changed
    elif case == "stale-execution":
        stale_context = replace(context, execution_id=str(uuid4()))
        stale = records.make_observation(stale_context, "no_named_targets")
        replacement_bytes = records._payload(stale)
        # Explicitly supply the stale bytes' digest to reach the independent
        # context check. This is a controlled reader input, not a claim that
        # the real publisher committed this altered payload for the host.
        publication = replace(
            publication,
            locator=replace(publication.locator, digest=hashlib.sha256(replacement_bytes).hexdigest()),
        )
        assert records.decode_observation(replacement_bytes, stale_context) == stale
    assert len(replacement_bytes) == len(original_bytes)
    if case != "identical-bytes":
        assert replacement_bytes != original_bytes

    # Deliberately keep the original allocated, so this fixture never relies on
    # a particular kernel allocator. Only numeric identity is simulated below.
    path.rename(path.with_name("retained-original"))
    path.write_bytes(replacement_bytes)
    replacement_stat = path.stat()
    replacement_identity = (replacement_stat.st_dev, replacement_stat.st_ino)
    assert replacement_identity != (original_stat.st_dev, original_stat.st_ino)
    identity = records._identity

    def reused_number(info):
        if (info.st_dev, info.st_ino) == replacement_identity:
            return publication.locator.identity
        return identity(info)

    monkeypatch.setattr(records, "_identity", reused_number)
    if case == "identical-bytes":
        # Post-close dev/ino identity is not an unbroken file-generation proof.
        assert records.read_observation(publication, context) == observation
    else:
        reason = "content_mismatch" if case == "changed-bytes" else "context_mismatch"
        with pytest.raises(records.DiagnosticError, match=reason):
            records.read_observation(publication, context)
