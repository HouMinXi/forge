"""Closed stock-bwrap admission; read-only checks, never network repair."""

import errno
import fcntl
import hashlib
import os
from pathlib import Path
import re
import socket
import stat
import struct
import sys

NAMES = ("user", "mnt", "pid", "net", "ipc", "uts")
PROFILE = Path("/etc/apparmor.d/bwrap-userns-restrict")
BWRAP = Path("/usr/bin/bwrap")


def require(value):
    if not value:
        raise ValueError("stock capture admission failed")


def read(path, limit=16 * 1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit)
        raw = bytearray()
        while len(raw) <= limit:
            part = os.read(fd, min(65536, limit + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        after = os.fstat(fd)
        named = Path(path).lstat()

        def identity(r):
            return (r.st_dev, r.st_ino, r.st_mode, r.st_uid, r.st_size, r.st_mtime_ns, r.st_ctime_ns)

        require(len(raw) <= limit and identity(before) == identity(after) == identity(named))
        return bytes(raw)
    finally:
        os.close(fd)


def root_program(path):
    row = Path(path).lstat()
    require(stat.S_ISREG(row.st_mode) and row.st_uid == 0 and not row.st_mode & 0o6022)
    try:
        os.getxattr(path, "security.capability", follow_symlinks=False)
    except OSError as error:
        require(error.errno == errno.ENODATA)
    else:
        raise ValueError("file capabilities not permitted")
    return hashlib.sha256(read(path)).hexdigest()


def host_state(*, host_owner=True):
    require(read("/sys/module/apparmor/parameters/enabled", 65536).strip() == b"Y")
    require(read("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", 65536).strip() == b"1")
    row = PROFILE.lstat()
    require(
        stat.S_ISREG(row.st_mode) and (not host_owner or row.st_uid == 0) and not row.st_mode & 0o6022
    )
    return hashlib.sha256(read(PROFILE)).hexdigest()


def namespaces():
    return {name: os.readlink("/proc/self/ns/" + name) for name in NAMES}


def directory(path):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(path))
    for part in (*reversed(path.parents), path):
        require(stat.S_ISDIR(part.lstat().st_mode))
    row = path.stat()
    require(row.st_uid == os.geteuid() and stat.S_IMODE(row.st_mode) == 0o700)
    return {
        "dev": row.st_dev,
        "ino": row.st_ino,
        "owner": "effective_user",
        "mode": stat.S_IMODE(row.st_mode),
    }


def prepare(output, mode):
    require(mode in ("baseline", "diagnostic"))
    require(os.getuid() != 0 and os.getuid() == os.geteuid())
    require(
        sys.implementation.cache_tag == "cpython-314"
        and Path(sys.executable).resolve() == Path("/usr/bin/python3").resolve()
    )
    programs = {
        "bwrap": {"path": str(BWRAP), "sha256": root_program(BWRAP)},
        "python": {
            "path": str(Path("/usr/bin/python3").resolve()),
            "sha256": root_program(Path("/usr/bin/python3").resolve()),
        },
    }
    if mode == "diagnostic":
        programs["strace"] = {"path": "/usr/bin/strace", "sha256": root_program(Path("/usr/bin/strace"))}
    return {
        "mode": mode,
        "profile_sha256": host_state(),
        "parent_namespaces": namespaces(),
        "programs": programs,
        "output": str(output),
        "output_identity": directory(output),
    }


def readonly_loopback():
    require(socket.if_nameindex() == [(1, "lo")])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as handle:
        flags = struct.unpack_from(
            "H", fcntl.ioctl(handle.fileno(), 0x8913, struct.pack("256s", b"lo")), 16
        )[0]
    require(flags & 8 and flags & 1)


def validate(binding):
    spec = binding["stock"]
    require(
        type(spec) is dict
        and set(spec)
        == {"mode", "profile_sha256", "parent_namespaces", "programs", "output", "output_identity"}
    )
    require(spec["mode"] in ("baseline", "diagnostic"))
    require(
        type(spec["profile_sha256"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", spec["profile_sha256"]) is not None
    )
    parent = spec["parent_namespaces"]
    require(type(parent) is dict and set(parent) == set(NAMES))
    require(
        all(
            type(parent[n]) is str and re.fullmatch(n + r":\[\d+\]", parent[n]) is not None
            for n in NAMES
        )
    )
    require(parent["net"] == binding["host_netns"])
    expected = {"bwrap": str(BWRAP), "python": str(Path("/usr/bin/python3").resolve())}
    if spec["mode"] == "diagnostic":
        expected["strace"] = "/usr/bin/strace"
    require(type(spec["programs"]) is dict and set(spec["programs"]) == set(expected))
    for name, path in expected.items():
        row = spec["programs"][name]
        require(type(row) is dict and set(row) == {"path", "sha256"} and row["path"] == path)
        require(type(row["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is not None)
    identity = spec["output_identity"]
    require(type(identity) is dict and set(identity) == {"dev", "ino", "owner", "mode"})
    require(all(type(identity[n]) is int and identity[n] >= 0 for n in ("dev", "ino", "mode")))
    require(identity["owner"] == "effective_user" and identity["mode"] == 0o700)
    require(type(spec["output"]) is str)
    admission = binding["startup_admission"]
    paths = [
        binding["launcher"],
        binding["target_python"],
        admission["site_packages"],
        admission["venv_config_path"],
        *admission["roots"],
    ]
    require(type(admission["roots"]) is list and all(type(p) is str for p in paths))
    for value in paths:
        path = Path(value)
        require(path.is_absolute() and str(path) == os.path.normpath(path))
        require(not path.is_relative_to(spec["output"]) and not path.is_relative_to("/tmp"))  # noqa: S108 - refuse hidden or writable target inputs
    target = binding["target"]
    require(
        target["version_info"] == [3, 14] and target["sha256"] == spec["programs"]["python"]["sha256"]
    )
    require(str(Path(binding["target_python"]).resolve()) == expected["python"])
    if spec["mode"] == "diagnostic":
        require(binding["utility_identities"]["strace"] == spec["programs"]["strace"])
    return spec


def child(binding):
    spec = validate(binding)
    require(sys.implementation.cache_tag == "cpython-314")
    require(host_state(host_owner=False) == spec["profile_sha256"])
    require(
        read("/proc/self/attr/current", 65536).strip()
        in (b"bwrap//&unpriv_bwrap (enforce)", b"unpriv_bwrap//&bwrap (enforce)")
    )
    status = dict(line.split(":", 1) for line in read("/proc/self/status", 65536).decode().splitlines())
    require(
        all(status[key].strip() == "0" * 16 for key in ("CapEff", "CapPrm", "CapAmb"))
        and status["NoNewPrivs"].strip() == "1"
    )
    current = namespaces()
    parent = spec["parent_namespaces"]
    require(type(parent) is dict and set(parent) == set(NAMES))
    require(
        all(
            re.fullmatch(name + r":\[\d+\]", current[name]) is not None and current[name] != parent[name]
            for name in NAMES
        )
    )
    require(current["net"] != binding["host_netns"])
    require(directory(spec["output"]) == spec["output_identity"])
    require(os.statvfs("/").f_flag & os.ST_RDONLY)
    require(not os.statvfs(spec["output"]).f_flag & os.ST_RDONLY)
    require(os.statvfs(str(Path(spec["output"]) / "binding.json")).f_flag & os.ST_RDONLY)
    admission = binding["startup_admission"]
    for value in (
        binding["launcher"],
        binding["target_python"],
        admission["site_packages"],
        admission["venv_config_path"],
        __file__,
        *admission["roots"],
    ):
        path = Path(value)
        while not path.exists():
            require(path.parent != path)
            path = path.parent
        require(os.statvfs(path).f_flag & os.ST_RDONLY)
    for program in spec["programs"].values():
        require(hashlib.sha256(read(Path(program["path"]))).hexdigest() == program["sha256"])
    readonly_loopback()


def command(binding, payload):
    spec = validate(binding)
    output = Path(spec["output"])
    require(directory(output) == spec["output_identity"])
    require(host_state() == spec["profile_sha256"])
    require(root_program(BWRAP) == spec["programs"]["bwrap"]["sha256"])
    return [
        str(BWRAP),
        "--unshare-all",
        "--die-with-parent",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",  # noqa: S108 - private namespace tmpfs, not host writable storage
        "--bind",
        str(output),
        str(output),
        "--ro-bind",
        str(output / "binding.json"),
        str(output / "binding.json"),
        "--",
        *payload,
    ]
