# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Filesystem types whose device and inode identity a local host can trust.

Two places qualify a storage root by reading its filesystem type out of
``/proc/self/mountinfo``: the advisory diagnostic store and the invocation
audit. Each carried its own list. One accepted overlay, tmpfs and ext4; the
other accepted ext4 and btrfs. A host could therefore pass one check and fail
the other on the same directory, and both lists omitted filesystems that are
ordinary defaults: btrfs on Fedora and openSUSE, xfs on RHEL, f2fs on flash
storage.

What follows are the local filesystems that carry a writable store with stable
device and inode numbers. Two families stay out:

- Read-only filesystems such as erofs and squashfs cannot hold the store.
- Network and synthetic filesystems -- nfs, cifs, 9p, virtiofs, fuse, ceph --
  do not keep device and inode numbers stable across mounts, which is the
  property every caller of this module goes on to check.

The check is a qualification, not a security boundary. A filesystem name that
is absent here is refused rather than assumed safe.
"""

from __future__ import annotations

LOCAL_FILESYSTEMS = frozenset(
    {
        "ext2",
        "ext3",
        "ext4",
        "xfs",
        "btrfs",
        "f2fs",
        "zfs",
        "bcachefs",
        "overlay",
        "tmpfs",
    }
)

# A btrfs subvolume reports its own anonymous device number in st_dev while
# /proc/self/mountinfo lists the device of the subvolume the mount was created
# from. The mount id still identifies the mount, so the device column cannot be
# compared for these filesystems.
SUBVOLUME_FILESYSTEMS_BYTES = frozenset({b"btrfs"})

# The advisory store reads /proc/self/mountinfo as bytes and compares tokens
# without decoding, so it needs the same set in that form.
LOCAL_FILESYSTEMS_BYTES = frozenset(name.encode("ascii") for name in LOCAL_FILESYSTEMS)
