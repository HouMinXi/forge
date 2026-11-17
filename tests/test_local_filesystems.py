# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""The qualification list both storage checks share.

The advisory diagnostic store and the invocation audit each used to carry a
private list of accepted filesystems, and they disagreed: one took overlay,
tmpfs and ext4, the other ext4 and btrfs. A host could pass one and fail the
other on the same directory. These tests pin the shared list and, more
importantly, pin that both call sites read it instead of spelling their own.
"""

from __future__ import annotations

import os

import pytest

from code_forge import _advisory_diagnostics as records
from code_forge import _local_filesystems as shared
from code_forge import invocation_audit as audit

# Filesystems a distribution installs on by default, which is where the old
# lists fell short: Fedora and openSUSE use btrfs, RHEL uses xfs, flash storage
# uses f2fs.
DISTRIBUTION_DEFAULTS = ("ext4", "xfs", "btrfs", "f2fs", "zfs")

# Read-only filesystems cannot hold a store; network and synthetic ones do not
# keep device and inode numbers stable, which is the property the callers check
# after this qualification.
FOREIGN = (
    "nfs",
    "nfs4",
    "cifs",
    "smb3",
    "9p",
    "virtiofs",
    "fuse",
    "fuse.sshfs",
    "ceph",
    "glusterfs",
    "erofs",
    "squashfs",
    "ramfs",
)


def test_the_shared_set_covers_distribution_defaults():
    for name in DISTRIBUTION_DEFAULTS:
        assert name in shared.LOCAL_FILESYSTEMS


def test_the_shared_set_excludes_read_only_and_remote_filesystems():
    for name in FOREIGN:
        assert name not in shared.LOCAL_FILESYSTEMS


def test_both_call_sites_read_the_shared_set():
    """Neither module may spell its own list again."""
    assert audit.LOCAL_FILESYSTEMS is shared.LOCAL_FILESYSTEMS
    assert records.LOCAL_FILESYSTEMS_BYTES is shared.LOCAL_FILESYSTEMS_BYTES
    assert records.SUBVOLUME_FILESYSTEMS_BYTES is shared.SUBVOLUME_FILESYSTEMS_BYTES


def _qualify_with_type(monkeypatch, tmp_path, filesystem: bytes, *, device: bytes | None = None) -> None:
    """Run the real advisory check with the mount line rewritten.

    Only the type and the device column change; the descriptor, its mount id and
    the rest of the line stay real. ``device=None`` writes the descriptor's own
    device number, the shape a flat filesystem produces. Passing bytes writes
    that value instead, so a test can name a mismatch without depending on the
    filesystem the test itself runs on.
    """
    real = records._bounded_proc
    probe = tmp_path / "probe"
    probe.write_bytes(b"x")
    fd = os.open(probe, os.O_RDONLY)
    try:
        info = os.fstat(fd)
        mount_id = [
            line.split()[1]
            for line in real(f"/proc/self/fdinfo/{fd}", 4096).splitlines()
            if line.startswith(b"mnt_id:")
        ][0]
        actual = f"{os.major(info.st_dev)}:{os.minor(info.st_dev)}".encode("ascii")
        replacement = actual if device is None else device

        def renamed(path, cap):
            raw = real(path, cap)
            if not path.endswith("mountinfo"):
                return raw
            lines = raw.splitlines()
            hits = [index for index, line in enumerate(lines) if line.split()[0] == mount_id]
            assert len(hits) == 1
            index = hits[0]
            before, separator, after = lines[index].partition(b" - ")
            columns = before.split()
            columns[2] = replacement
            _, space, rest = after.partition(b" ")
            lines[index] = b" ".join(columns) + separator + filesystem + space + rest
            return b"\n".join(lines) + b"\n"

        monkeypatch.setattr(records, "_bounded_proc", renamed)
        records._filesystem(fd, info)
    finally:
        os.close(fd)


@pytest.mark.parametrize("filesystem", DISTRIBUTION_DEFAULTS)
def test_a_local_filesystem_is_accepted(monkeypatch, tmp_path, filesystem):
    _qualify_with_type(monkeypatch, tmp_path, filesystem.encode("ascii"))


@pytest.mark.parametrize("filesystem", FOREIGN + ("unsupported-test-filesystem",))
def test_a_foreign_filesystem_is_refused(monkeypatch, tmp_path, filesystem):
    with pytest.raises(records.DiagnosticError) as raised:
        _qualify_with_type(monkeypatch, tmp_path, filesystem.encode("ascii"))
    assert str(raised.value) == "unsupported_filesystem"


def test_a_btrfs_subvolume_is_accepted_despite_the_device_mismatch(monkeypatch, tmp_path):
    """A subvolume reports its own device; the mount lists the parent's."""
    _qualify_with_type(monkeypatch, tmp_path, b"btrfs", device=b"9999:9999")


# Every name in the shared set except btrfs, which is the deliberate exemption
# and has its own case above.
@pytest.mark.parametrize(
    "filesystem", ["ext2", "ext3", "ext4", "xfs", "f2fs", "zfs", "bcachefs", "overlay", "tmpfs"]
)
def test_a_device_mismatch_still_refuses_a_flat_filesystem(monkeypatch, tmp_path, filesystem):
    with pytest.raises(records.DiagnosticError) as raised:
        _qualify_with_type(monkeypatch, tmp_path, filesystem.encode("ascii"), device=b"9999:9999")
    assert str(raised.value) == "unsupported_filesystem"


def test_the_refusal_comes_from_the_name_not_the_device(monkeypatch, tmp_path):
    """Hold the device fixed and change only the name: the verdict flips.

    Without this pair a refusal could come from the device comparison and the
    name tests would still pass.
    """
    _qualify_with_type(monkeypatch, tmp_path, b"ext4")
    with pytest.raises(records.DiagnosticError):
        _qualify_with_type(monkeypatch, tmp_path, b"nfs")


def test_the_capability_projection_refuses_only_a_foreign_type():
    """A capability that differs only in filesystem_type is refused.

    The schema-corruption tests in test_invocation_audit mutate every field at
    once, so they cannot show that the type alone is rejected.
    """
    good = {
        "profile_id": "linux-local-audit-v1",
        "filesystem_type": "btrfs",
        "root_device": 1,
        "root_inode": 2,
        "root_mount_id": 3,
        "runtime_fingerprint": "0" * 64,
    }
    assert audit._capability_projection(good)["filesystem_type"] == "btrfs"
    for name in ("nfs", "cifs", "erofs", "unsupported-test-filesystem"):
        with pytest.raises(audit._Rejected):
            audit._capability_projection({**good, "filesystem_type": name})


def _audit_profile_with_type(monkeypatch, tmp_path, filesystem: str):
    """Run the real audit profile read with only the mount's type renamed."""
    real = audit._read_bounded
    probe = tmp_path / "probe"
    probe.write_bytes(b"x")
    fd = os.open(probe, os.O_RDONLY)
    try:
        mount_id = [
            line.split(":", 1)[1].strip()
            for line in real("/proc/self/fdinfo/" + str(fd), 16 * 1024).splitlines()
            if line.startswith("mnt_id:")
        ][0]

        def renamed(path, bound):
            raw = real(path, bound)
            if not path.endswith("mountinfo"):
                return raw
            lines = raw.splitlines()
            hits = [index for index, line in enumerate(lines) if line.split()[0] == mount_id]
            assert len(hits) == 1
            index = hits[0]
            before, separator, after = lines[index].partition(" - ")
            _, space, rest = after.partition(" ")
            lines[index] = before + separator + filesystem + space + rest
            return "\n".join(lines) + "\n"

        monkeypatch.setattr(audit, "_read_bounded", renamed)
        return audit._profile(fd)
    finally:
        os.close(fd)


@pytest.mark.parametrize("filesystem", DISTRIBUTION_DEFAULTS)
def test_the_audit_profile_accepts_a_local_filesystem(monkeypatch, tmp_path, filesystem):
    observed, mount_id = _audit_profile_with_type(monkeypatch, tmp_path, filesystem)
    assert observed == filesystem
    assert mount_id > 0


@pytest.mark.parametrize("filesystem", FOREIGN)
def test_the_audit_profile_refuses_a_foreign_filesystem(monkeypatch, tmp_path, filesystem):
    with pytest.raises(audit._Rejected):
        _audit_profile_with_type(monkeypatch, tmp_path, filesystem)
