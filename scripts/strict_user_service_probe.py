"""Bounded existing-user-manager topology probe; NEVER isolation qualification.

Opt-in creates one temporary ordinary user service and one private Unix socket.
No payload, bwrap, product cgroup, controller/policy write or missing manager start.
The command-line context is declared, not authenticated by a CI provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import select
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time

LIMIT = 16384
START_SECONDS = 5
CHANNEL_SECONDS = 5
STOP_SECONDS = 5
SYSTEMCTL = "/usr/bin/systemctl"
SYSTEMD_RUN = "/usr/bin/systemd-run"
PYTHON = "/usr/bin/python3"
CGROUP = Path("/sys/fs/cgroup")
SCHEMA = "forge-user-service-topology-v1"
REASONS = {
    "invalid_data",
    "unsafe_path",
    "unsafe_tool",
    "ordinary_user",
    "manager_unavailable",
    "unit_collision",
    "command_timeout",
    "command_output",
    "command_failed",
    "channel_timeout",
    "channel_closed",
    "peer_mismatch",
    "identity_mismatch",
    "process_gone",
    "topology_unavailable",
    "topology_prerequisites",
    "cleanup_unverified",
    "io_error",
    "source_changed",
    "config_invalid",
    "config_limit",
    "config_unsafe",
    "config_changed",
    "config_unsupported",
    "config_environment_file",
    "config_hook",
    "config_dependency",
    "config_timeout",
}


class ProbeError(ValueError):
    def __init__(self, reason: str, returncode: int | None = None):
        assert reason in REASONS
        super().__init__(reason)
        self.reason, self.returncode = reason, returncode
        self.child_cleanup = None


def require(ok: bool, reason: str = "invalid_data") -> None:
    if not ok:
        raise ProbeError(reason)


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected))
    return value


def integer(value, low=0, high=2**32 - 1):
    require(type(value) is int and low <= value <= high)
    return value


def pattern(value, regex):
    require(type(value) is str and re.fullmatch(regex, value) is not None)
    return value


def cpath(value):
    require(type(value) is str and 0 < len(value) <= 1024)
    require(value.startswith("/") and not value.startswith("//"))
    require(all(" " <= c <= "~" for c in value) and "\\" not in value)
    require(str(PurePosixPath(value)) == value and ".." not in PurePosixPath(value).parts)
    return value


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode(
        "ascii"
    )


def decode(raw):
    require(type(raw) is bytes and 0 < len(raw) <= LIMIT)

    def pairs(items):
        out = {}
        for k, v in items:
            require(k not in out)
            out[k] = v
        return out

    def bad(_):
        raise ProbeError("invalid_data")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)
        require(encode(value) == raw)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ProbeError("invalid_data") from exc


def read(path, limit=LIMIT):
    with open(path, "rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit)
    return data


def source_hash():
    return hashlib.sha256(read(Path(__file__).resolve(), 131072)).hexdigest()


def starttime(pid):
    # comm may contain whitespace and ')'; the final ')' terminates field 2.
    raw = read(f"/proc/{pid}/stat").decode("ascii")
    end = raw.rfind(")")
    require(end > 0)
    fields = raw[end + 2 :].split()
    require(len(fields) >= 20 and fields[19].isdigit())
    return integer(int(fields[19]), 1, 2**64 - 1)


def boot_id():
    return pattern(
        read("/proc/sys/kernel/random/boot_id").decode("ascii").strip(),
        r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
    )


def namespaces(pid="self"):
    return {name: os.stat(f"/proc/{pid}/ns/{name}").st_ino for name in ("mnt", "cgroup")}


def membership(pid="self"):
    lines = read(f"/proc/{pid}/cgroup").decode("ascii").splitlines()
    require(len(lines) == 1 and lines[0].startswith("0::"), "topology_unavailable")
    return cpath(lines[0][3:])


def manager_root(uid):
    return f"/user.slice/user-{uid}.slice/user@{uid}.service"


def controller_list(path):
    values = read(path).decode("ascii").split()
    require(len(values) <= 32 and len(set(values)) == len(values))
    for value in values:
        pattern(value, r"[a-z_]{1,32}")
    return sorted(values)


def topology():
    # Reject alternate/escaped mount roots instead of claiming their equivalence.
    mounts = read("/proc/self/mountinfo", 65536).decode("ascii").splitlines()
    at_mount = [line for line in mounts if len(line.split()) > 5 and line.split()[4] == str(CGROUP)]
    require(
        len(at_mount) == 1 and " - cgroup2 " in at_mount[0] and at_mount[0].split()[3] == "/",
        "topology_unavailable",
    )
    source, destination = membership(), manager_root(os.getuid())
    require(source.startswith(destination + "/"), "topology_unavailable")
    common = os.path.commonpath((source, destination))
    root = CGROUP / destination.lstrip("/")
    return {
        "source": source,
        "destination": destination,
        "common": common,
        "controllers": controller_list(root / "cgroup.controllers"),
        "subtree_control": controller_list(root / "cgroup.subtree_control"),
        "directory_access": os.access(root, os.W_OK | os.X_OK, effective_ids=True),
        "destination_access": os.access(root / "cgroup.procs", os.W_OK, effective_ids=True),
        "common_access": os.access(
            CGROUP / common.lstrip("/") / "cgroup.procs", os.W_OK, effective_ids=True
        ),
    }


def validate_topology(value, uid):
    keys(
        value,
        {
            "source",
            "destination",
            "common",
            "controllers",
            "subtree_control",
            "directory_access",
            "destination_access",
            "common_access",
        },
    )
    for k in ("source", "destination", "common"):
        cpath(value[k])
    require(value["destination"] == manager_root(uid))
    require(value["source"].startswith(value["destination"] + "/"))
    require(value["common"] == os.path.commonpath((value["source"], value["destination"])))
    for k in ("controllers", "subtree_control"):
        require(type(value[k]) is list and len(value[k]) <= 32)
        for v in value[k]:
            pattern(v, r"[a-z_]{1,32}")
        require(value[k] == sorted(set(value[k])))
    for k in ("directory_access", "destination_access", "common_access"):
        require(type(value[k]) is bool)
    return value


def topology_ready(value):
    return all(value[k] for k in ("directory_access", "destination_access", "common_access")) and all(
        {"memory", "pids"} <= set(value[k]) for k in ("controllers", "subtree_control")
    )


def validate_context(value):
    keys(value, {"source_sha", "collector_sha256", "run_id", "run_attempt"})
    pattern(value["source_sha"], r"[0-9a-f]{40}")
    pattern(value["collector_sha256"], r"[0-9a-f]{64}")
    pattern(value["run_id"], r"[1-9][0-9]{0,19}")
    integer(value["run_attempt"], 1, 1000000)
    return value


def validate_hello(value):
    keys(
        value,
        {
            "kind",
            "nonce",
            "context",
            "uid",
            "gid",
            "pid",
            "starttime",
            "boot_id",
            "namespaces",
            "topology",
        },
    )
    require(value["kind"] == "hello")
    pattern(value["nonce"], r"[0-9a-f]{64}")
    validate_context(value["context"])
    for k in ("uid", "gid", "pid"):
        integer(value[k], 1)
    integer(value["starttime"], 1, 2**64 - 1)
    pattern(value["boot_id"], r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
    keys(value["namespaces"], {"mnt", "cgroup"})
    for inode in value["namespaces"].values():
        integer(inode, 1, 2**64 - 1)
    validate_topology(value["topology"], value["uid"])
    return value


def remaining(deadline, reason="channel_timeout"):
    value = deadline - time.monotonic()
    require(value > 0, reason)
    return value


def send(channel, value, deadline):
    data = encode(value)
    require(len(data) <= LIMIT)
    channel.settimeout(remaining(deadline))
    try:
        channel.sendall(data)
    except TimeoutError as exc:
        raise ProbeError("channel_timeout") from exc


def receive(channel, deadline):
    data = bytearray()
    while b"\n" not in data:
        channel.settimeout(remaining(deadline))
        try:
            block = channel.recv(min(4096, LIMIT + 1 - len(data)))
        except TimeoutError as exc:
            raise ProbeError("channel_timeout") from exc
        require(bool(block), "channel_closed")
        data.extend(block)
        require(len(data) <= LIMIT)
    return decode(bytes(data))


def peer(channel):
    return struct.unpack(
        "3i", channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    )


def verified_tool(path):
    resolved = Path(path).resolve(strict=True)
    require(str(resolved).startswith("/usr/bin/"), "unsafe_tool")
    for item in [resolved, *resolved.parents]:
        info = item.lstat()
        require(info.st_uid == 0 and not info.st_mode & 0o6022, "unsafe_tool")
        require(
            stat.S_ISREG(info.st_mode) if item == resolved else stat.S_ISDIR(info.st_mode), "unsafe_tool"
        )
    require(bool(resolved.stat().st_mode & 0o111), "unsafe_tool")
    return str(resolved)


def verified_source(uid):
    script = Path(__file__).resolve(strict=True)
    pattern(str(script), r"/[A-Za-z0-9_./-]+")
    for item in (script, *script.parents):
        info = item.lstat()
        require(info.st_uid in {0, uid} and not info.st_mode & 0o022, "unsafe_path")
        require(
            stat.S_ISREG(info.st_mode) if item == script else stat.S_ISDIR(info.st_mode), "unsafe_path"
        )
    return str(script)


def command(argv, env, deadline, limit=LIMIT):
    """Bound streams and preserve primary plus direct-child cleanup failures."""
    child = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    output, primary, secondary = bytearray(), None, None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ, True)
            selector.register(child.stderr, selectors.EVENT_READ, False)
            while selector.get_map():
                events = selector.select(remaining(deadline, "command_timeout"))
                require(bool(events), "command_timeout")
                for key, _ in events:
                    block = os.read(key.fileobj.fileno(), 4096)
                    if not block:
                        selector.unregister(key.fileobj)
                    else:
                        require(key.data and len(output) + len(block) <= limit, "command_output")
                        output.extend(block)
            # Keep the leader unreaped until killpg; its PID cannot be reused.
            while True:
                info = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if info is not None:
                    break
                time.sleep(min(0.01, remaining(deadline, "command_timeout")))
            code = info.si_status if info.si_code == os.CLD_EXITED else -info.si_status
            if code != 0:
                raise ProbeError("command_failed", code)
    except (OSError, ValueError) as exc:
        primary = exc
    finally:
        try:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                secondary = {"reason": "cleanup_unverified", "errno": exc.errno}
            try:
                child.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired) as exc:
                secondary = secondary or {
                    "reason": "cleanup_unverified",
                    "errno": getattr(exc, "errno", None),
                }
        finally:
            for stream in (child.stdout, child.stderr):
                try:
                    stream.close()
                except OSError as exc:
                    secondary = secondary or {"reason": "cleanup_unverified", "errno": exc.errno}
    if secondary is not None:
        if not isinstance(primary, ProbeError):
            converted = ProbeError("io_error" if primary else "cleanup_unverified")
            converted.original_errno = getattr(primary, "errno", None)
            converted.__cause__ = primary
            primary = converted
        primary.child_cleanup = secondary
    if primary is not None:
        raise primary
    return output.decode("ascii")


def manager_environment(tool, env, deadline):
    # Values never enter a receipt; digest detects ordinary concurrent changes.
    raw = command([tool, "--user", "show-environment"], env, deadline)
    names = []
    for line in raw.splitlines():
        name, sep, _ = line.partition("=")
        require(sep == "=" and len(names) < 256)
        pattern(name, r"[A-Za-z_][A-Za-z0-9_]{0,127}")
        require(name not in names)
        names.append(name)
    return sorted(names), hashlib.sha256(raw.encode("ascii")).hexdigest()


def show(tool, env, unit, deadline, user=True):
    fields = {
        "Id",
        "LoadState",
        "ActiveState",
        "SubState",
        "MainPID",
        "ControlGroup",
        "Delegate",
        "Description",
        "Transient",
        "InvocationID",
    }
    raw = command(
        [
            tool,
            *(["--user"] if user else []),
            "show",
            "--all",
            "--no-pager",
            "--property=" + ",".join(sorted(fields)),
            "--",
            unit,
        ],
        env,
        deadline,
    )
    result = {}
    for line in raw.splitlines():
        name, sep, value = line.partition("=")
        require(sep == "=" and name in fields and name not in result)
        result[name] = value
    require(set(result) == fields)
    require(result["Id"] == unit)
    require(result["MainPID"].isdigit())
    result["MainPID"] = integer(int(result["MainPID"]))
    require(result["LoadState"] in {"loaded", "not-found"})
    require(result["ActiveState"] in {"active", "inactive", "failed", "activating", "deactivating"})
    pattern(result["SubState"], r"[a-z-]{1,32}")
    require(result["Delegate"] in {"yes", "no"})
    require(result["Transient"] in {"yes", "no"})
    require(len(result["Description"]) <= 256)
    if result["InvocationID"]:
        pattern(result["InvocationID"], r"[0-9a-f]{32}")
    if result["ControlGroup"]:
        cpath(result["ControlGroup"])
    return result


def environment(uid):
    runtime = Path(f"/run/user/{uid}")
    for item in (Path("/run"), Path("/run/user")):
        info = item.lstat()
        require(
            stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, "unsafe_path"
        )
    info = runtime.lstat()
    require(
        stat.S_ISDIR(info.st_mode) and info.st_uid == uid and stat.S_IMODE(info.st_mode) == 0o700,
        "unsafe_path",
    )
    info = (runtime / "bus").lstat()
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == uid, "unsafe_path")
    with socket.socket(socket.AF_UNIX) as bus:
        bus.settimeout(1)
        bus.connect(str(runtime / "bus"))
        require(peer(bus)[1] == uid, "peer_mismatch")
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "XDG_RUNTIME_DIR": str(runtime),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
    }


def live(pidfd, pid, started):
    require(not select.select([pidfd], [], [], 0)[0], "process_gone")
    require(starttime(pid) == started, "identity_mismatch")


def owned_group(group, uid, unit):
    cpath(group)
    require(
        group.startswith(manager_root(uid) + "/") and PurePosixPath(group).name == unit,
        "identity_mismatch",
    )
    return group


def owned_unit(current, marker, invocation=None):
    require(current["Transient"] == "yes" and current["Description"] == marker, "identity_mismatch")
    pattern(current["InvocationID"], r"[0-9a-f]{32}")
    require(invocation is None or current["InvocationID"] == invocation, "identity_mismatch")
    return current["InvocationID"]


def empty_group(group, deadline):
    """Read all descendants of the exact owned service group, bounded to 128 dirs."""
    root = CGROUP / group.lstrip("/")
    try:
        root.lstat()
    except FileNotFoundError:
        return "absent"
    pending, count = [root], 0
    while pending:
        remaining(deadline, "cleanup_unverified")
        directory = pending.pop()
        count += 1
        require(count <= 128, "cleanup_unverified")
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode), "cleanup_unverified")
        require(not read(directory / "cgroup.procs").strip(), "cleanup_unverified")
        events = read(directory / "cgroup.events").decode("ascii").splitlines()
        require("populated 0" in events and "populated 1" not in events, "cleanup_unverified")
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                require(index < 256, "cleanup_unverified")
                require(not entry.is_symlink(), "cleanup_unverified")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
    return "empty"


# Configuration gate: narrow read-only systemd-255 compatibility profile.
BUSCTL = "/usr/bin/busctl"
CONFIG_SECONDS = 5
BUS_LIMIT = 65536
BUS_MANAGER = "org.freedesktop.systemd1.Manager"
BUS_UNIT = "org.freedesktop.systemd1.Unit"
BUS_SERVICE = "org.freedesktop.systemd1.Service"
BUS_OBJECT = "/org/freedesktop/systemd1"
EXEC_SIGNATURE = "a(sasbttttuii)"


def bus_json(raw):
    require(type(raw) is str and len(raw.encode("ascii")) <= BUS_LIMIT, "config_invalid")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "config_invalid")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ProbeError("config_invalid")),
        )
    except (ValueError, RecursionError) as exc:
        raise ProbeError("config_invalid") from exc
    return keys(value, {"type", "data"})


def config_call(tool, env, destination, path, interface, method, signature, args, result_type, deadline):
    allowed = {
        ("org.freedesktop.DBus", "GetNameOwner"),
        ("org.freedesktop.DBus", "GetConnectionUnixProcessID"),
        ("org.freedesktop.DBus.Properties", "Get"),
        (BUS_MANAGER, "ListUnitsByPatterns"),
    }
    require((interface, method) in allowed, "config_invalid")
    raw = command(
        [
            tool,
            "--user",
            "--auto-start=no",
            "--allow-interactive-authorization=no",
            "--json=short",
            "--timeout=2s",
            "call",
            destination,
            path,
            interface,
            method,
            signature,
            *args,
        ],
        env,
        deadline,
        limit=BUS_LIMIT,
    )
    reply = bus_json(raw)
    require(
        reply["type"] == result_type and type(reply["data"]) is list and len(reply["data"]) == 1,
        "config_invalid",
    )
    return reply["data"][0]


def config_strings(value, limit):
    require(type(value) is list and len(value) <= limit, "config_limit")
    for item in value:
        require(
            type(item) is str and 0 < len(item) <= 1024 and all(" " <= c <= "~" for c in item),
            "config_invalid",
        )
    require(len(value) == len(set(value)), "config_invalid")
    return value


def config_meta(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid]


def config_safe(info, uid, directory=True):
    require(
        (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        and info.st_uid in {0, uid}
        and not info.st_mode & 0o022,
        "config_unsafe",
    )


def config_open_dir(path, uid, deadline):
    """Return a held descriptor, plus the complete existing ancestor identities."""
    cpath(path)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    witness = []
    try:
        config_safe(os.fstat(fd), uid)
        witness.append(config_meta(os.fstat(fd)))
        parts = PurePosixPath(path).parts[1:]
        # Only distribution-owned top-level aliases; no arbitrary links followed.
        if parts and parts[0] in {"lib", "lib64", "bin", "sbin"}:
            info = os.stat(parts[0], dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                require(
                    info.st_uid == 0
                    and os.readlink(parts[0], dir_fd=fd) in {"usr/" + parts[0], "/usr/" + parts[0]},
                    "config_unsafe",
                )
                witness.append(config_meta(info))
                parts = ("usr", *parts)
        for part in parts:
            remaining(deadline, "config_timeout")
            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                os.close(fd)
                return None, witness
            try:
                config_safe(os.fstat(next_fd), uid)
            except BaseException:
                os.close(next_fd)
                raise
            os.close(fd)
            fd = next_fd
            witness.append(config_meta(os.fstat(fd)))
        return fd, witness
    except BaseException:
        os.close(fd)
        raise


def config_entries(fd, deadline):
    entries = []
    with os.scandir(fd) as iterator:
        for entry in iterator:
            remaining(deadline, "config_timeout")
            require(len(entries) < 128, "config_limit")
            require(all(" " <= c <= "~" for c in entry.name), "config_invalid")
            entries.append(entry.name)
    return sorted(entries)


def config_read_file(parent_fd, name, uid):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    try:
        before = os.fstat(fd)
        config_safe(before, uid, directory=False)
        require(before.st_size <= LIMIT, "config_limit")
        data = os.read(fd, LIMIT + 1)
        after = os.fstat(fd)
        require(len(data) <= LIMIT and len(data) == before.st_size, "config_limit")
        require(
            config_meta(before) == config_meta(after)
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns,
            "config_changed",
        )
        return data, [*config_meta(after), after.st_size, after.st_mtime_ns]
    finally:
        os.close(fd)


def config_comment_only(data):
    try:
        text = data.decode("ascii")
    except UnicodeError as exc:
        raise ProbeError("config_unsupported") from exc
    require("\x00" not in text and "\\\n" not in text and "\\\r" not in text, "config_unsupported")
    for line in text.split("\n"):
        line = line.removesuffix("\r")
        require(all(c in "\t" or " " <= c <= "~" for c in line), "config_unsupported")
        stripped = line.lstrip(" \t")
        if stripped and not stripped.startswith(("#", ";")):
            if re.match(r"EnvironmentFile\s*=", stripped):
                raise ProbeError("config_environment_file")
            if re.match(
                r"Exec(?:StartPre|StartPost|Start|Stop|StopPost|Condition|Reload)\s*=", stripped
            ):
                raise ProbeError("config_hook")
            raise ProbeError("config_unsupported")


def config_generated(data, argv, unset_names, description):
    """Narrow v255 transient serializer grammar, not a general unit parser."""
    for arg in argv:
        pattern(arg, r"[A-Za-z0-9_./-]+")
    for name in unset_names:
        pattern(name, r"[A-Za-z_][A-Za-z0-9_]{0,127}")
    pattern(description, r"forge-topology-[0-9a-f]{64}")
    quoted_argv = " ".join('"' + arg + '"' for arg in argv)
    quoted_names = " ".join('"' + name + '"' for name in unset_names)
    expected = {
        ("Unit", "Description"): [description],
        ("Service", "Type"): ["exec"],
        ("Service", "ExecStart"): ["", quoted_argv],
        ("Service", "StandardOutput"): ["null"],
        ("Service", "StandardError"): ["null"],
        ("Service", "UnsetEnvironment"): [quoted_names],
        ("Service", "RuntimeMaxSec"): ["15s"],
        ("Service", "TimeoutStopSec"): ["2s"],
        ("Service", "KillMode"): ["control-group"],
    }
    text = data.decode("ascii")
    require("\x00" not in text and "\r" not in text and text.endswith("\n"), "config_invalid")
    observed, section = {}, None
    for line in text.split("\n"):
        if not line or line.startswith("#"):
            continue
        if line in {"[Unit]", "[Service]"}:
            section = line[1:-1]
            continue
        name, sep, value = line.partition("=")
        key = (section, name)
        require(sep == "=" and key in expected, "config_unsupported")
        values = observed.setdefault(key, [])
        values.append(value)
        require(
            len(values) <= len(expected[key]) and values == expected[key][: len(values)],
            "config_changed",
        )
    require(observed == expected, "config_changed")


def config_scan(paths, unit, uid, deadline, generated=None, generated_content=None):
    stems = ("service", "forge-.service", "forge-topology-.service", unit)
    result, accepted, total, generated_count = {}, [], 0, 0
    for base in paths:
        fd, witness = config_open_dir(base, uid, deadline)
        records = {}
        try:
            for name in (
                unit,
                *(
                    stem + suffix
                    for stem in stems
                    for suffix in (".d", ".wants", ".requires", ".upholds")
                ),
            ):
                remaining(deadline, "config_timeout")
                if fd is None:
                    records[name] = None
                    continue
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    records[name] = None
                    continue
                full = str(PurePosixPath(base) / name)
                if name == unit:
                    require(generated == full, "unit_collision")
                    data, metadata = config_read_file(fd, name, uid)
                    require(generated_content is not None, "config_invalid")
                    config_generated(data, *generated_content)
                    generated_count += 1
                    records[name] = None  # Sole expected generated-fragment delta.
                    continue
                config_safe(info, uid)
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    require(config_meta(info) == config_meta(os.fstat(child)), "config_changed")
                    names = config_entries(child, deadline)
                    require(name.endswith(".d") or not names, "config_dependency")
                    files = {}
                    for filename in names:
                        if not filename.endswith(".conf"):
                            continue
                        require(len(accepted) < 64, "config_limit")
                        data, metadata = config_read_file(child, filename, uid)
                        total += len(data)
                        require(total <= 262144, "config_limit")
                        config_comment_only(data)
                        file_path = str(PurePosixPath(full) / filename)
                        accepted.append(file_path)
                        files[filename] = [metadata, hashlib.sha256(data).hexdigest()]
                    require(names == config_entries(child, deadline), "config_changed")
                    records[name] = {"identity": config_meta(info), "entries": names, "files": files}
                finally:
                    os.close(child)
            result[base] = {"witness": witness, "present": fd is not None, "records": records}
        finally:
            if fd is not None:
                os.close(fd)
    require(generated is None or generated_count == 1, "config_changed")
    return result, sorted(accepted)


def config_same(before, after, generated=None):
    require(before.keys() == after.keys(), "config_changed")
    for path, old in before.items():
        new = after[path]
        require(old["records"] == new["records"], "config_changed")
        if generated and str(PurePosixPath(generated).parent) == path and not old["present"]:
            # systemd may create its transient directory for this first service.
            require(
                new["present"] and new["witness"][: len(old["witness"])] == old["witness"],
                "config_changed",
            )
        else:
            require(
                old["witness"] == new["witness"] and old["present"] == new["present"], "config_changed"
            )


class ConfigGate:
    """No start/load/set/reload method is available through this read-only gate."""

    def __init__(self, tool, env, unit, uid, manager_pid):
        self.tool, self.env, self.unit, self.uid = tool, env, unit, uid
        self.deadline = time.monotonic() + CONFIG_SECONDS
        self.prechecked = False
        self.owner = self.call(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetNameOwner",
            "s",
            ["org.freedesktop.systemd1"],
            "s",
        )
        pattern(self.owner, r":[0-9]+\.[0-9]+")
        self.pid = self.call(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetConnectionUnixProcessID",
            "s",
            [self.owner],
            "u",
        )
        require(type(self.pid) is int and self.pid == manager_pid > 0, "config_changed")
        require(os.stat(f"/proc/{self.pid}").st_uid == uid, "config_unsafe")
        self.started = starttime(self.pid)
        self.pidfd = os.pidfd_open(self.pid)
        try:
            self.namespace = namespaces(self.pid)
            require(self.namespace == namespaces(), "config_unsafe")
            root, own_root = os.stat(f"/proc/{self.pid}/root"), os.stat("/")
            self.root_identity = (root.st_dev, root.st_ino)
            require((root.st_dev, root.st_ino) == (own_root.st_dev, own_root.st_ino), "config_unsafe")
            self.version = self.prop(BUS_OBJECT, BUS_MANAGER, "Version", "s")
            pattern(self.version, r"255(?:\.[0-9]+)?(?:[-+~ ][A-Za-z0-9.+~_-]+)*")
            self.paths = config_strings(self.prop(BUS_OBJECT, BUS_MANAGER, "UnitPath", "as"), 32)
            require(bool(self.paths), "config_invalid")
            for path in self.paths:
                cpath(path)
            self.absent()
            self.snapshot, self.comments = config_scan(self.paths, unit, uid, self.deadline)
            self.fingerprint = hashlib.sha256(encode(self.snapshot)).hexdigest()
        except BaseException as exc:
            try:
                os.close(self.pidfd)
            except OSError as cleanup:
                exc.child_cleanup = {"reason": "cleanup_unverified", "errno": cleanup.errno}
            raise

    def call(self, destination, path, interface, method, signature, args, result_type):
        return config_call(
            self.tool,
            self.env,
            destination,
            path,
            interface,
            method,
            signature,
            args,
            result_type,
            self.deadline,
        )

    def prop(self, path, interface, name, kind):
        value = self.call(
            self.owner, path, "org.freedesktop.DBus.Properties", "Get", "ss", [interface, name], "v"
        )
        keys(value, {"type", "data"})
        require(value["type"] == kind, "config_invalid")
        return value["data"]

    def roster(self):
        rows = self.call(
            self.owner,
            BUS_OBJECT,
            BUS_MANAGER,
            "ListUnitsByPatterns",
            "asas",
            ["0", "1", "*.service"],
            "a(ssssssouso)",
        )
        require(type(rows) is list and len(rows) <= 64, "config_limit")
        result = {}
        for row in rows:
            require(type(row) is list and len(row) == 10, "config_invalid")
            for index in (0, 1, 2, 3, 4, 5, 6, 8, 9):
                require(type(row[index]) is str and len(row[index]) <= 1024, "config_invalid")
            integer(row[7])
            pattern(row[0], r"[A-Za-z0-9:_.@\\-]+\.service")
            pattern(row[6], r"/org/freedesktop/systemd1/unit/[A-Za-z0-9_]+")
            require(row[0] not in result, "config_invalid")
            names = config_strings(self.prop(row[6], BUS_UNIT, "Names", "as"), 64)
            require(
                row[0] in names and all(name.endswith(".service") for name in names), "config_invalid"
            )
            result[row[0]] = (row[6], names)
        return result

    def absent(self):
        require(all(self.unit not in names for _, names in self.roster().values()), "unit_collision")

    def unchanged_manager(self):
        live(self.pidfd, self.pid, self.started)
        root = os.stat(f"/proc/{self.pid}/root")
        require(
            namespaces(self.pid) == self.namespace == namespaces()
            and (root.st_dev, root.st_ino) == self.root_identity,
            "config_changed",
        )
        owner = self.call(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetNameOwner",
            "s",
            ["org.freedesktop.systemd1"],
            "s",
        )
        require(
            owner == self.owner
            and self.prop(BUS_OBJECT, BUS_MANAGER, "UnitPath", "as") == self.paths
            and self.prop(BUS_OBJECT, BUS_MANAGER, "Version", "s") == self.version,
            "config_changed",
        )

    def prelaunch(self):
        self.unchanged_manager()
        self.absent()
        snapshot, comments = config_scan(self.paths, self.unit, self.uid, self.deadline)
        config_same(self.snapshot, snapshot)
        require(comments == self.comments, "config_changed")
        self.prechecked = True

    def postlaunch(self, deadline, argv, invocation, unset_names=(), description=""):
        require(self.prechecked, "config_invalid")
        self.deadline = deadline
        self.unchanged_manager()
        roster = self.roster()
        require(self.unit in roster and roster[self.unit][1] == [self.unit], "config_changed")
        path = roster[self.unit][0]
        require(
            self.prop(path, BUS_UNIT, "InvocationID", "ay") == list(bytes.fromhex(invocation)),
            "config_changed",
        )
        require(self.prop(path, BUS_UNIT, "Transient", "b") is True, "config_changed")
        require(
            set(config_strings(self.prop(path, BUS_UNIT, "DropInPaths", "as"), 64))
            <= set(self.comments),
            "config_changed",
        )
        for name in (
            "ExecCondition",
            "ExecStartPre",
            "ExecStartPost",
            "ExecStop",
            "ExecStopPost",
            "ExecReload",
        ):
            require(self.prop(path, BUS_SERVICE, name, EXEC_SIGNATURE) == [], "config_hook")
        require(
            self.prop(path, BUS_SERVICE, "EnvironmentFiles", "a(sb)") == [], "config_environment_file"
        )
        for name, kind, expected in (
            ("Type", "s", "exec"),
            ("KillMode", "s", "control-group"),
            ("RuntimeMaxUSec", "t", 15000000),
            ("TimeoutStopUSec", "t", 2000000),
            ("StandardOutput", "s", "null"),
            ("StandardError", "s", "null"),
            ("Delegate", "b", False),
            ("Environment", "as", []),
            ("PassEnvironment", "as", []),
        ):
            actual = self.prop(path, BUS_SERVICE, name, kind)
            require(type(actual) is type(expected) and actual == expected, "config_changed")
        require(
            sorted(config_strings(self.prop(path, BUS_SERVICE, "UnsetEnvironment", "as"), 256))
            == sorted(unset_names),
            "config_changed",
        )
        commands = self.prop(path, BUS_SERVICE, "ExecStart", EXEC_SIGNATURE)
        require(
            type(commands) is list
            and len(commands) == 1
            and type(commands[0]) is list
            and len(commands[0]) == 10,
            "config_invalid",
        )
        require(
            commands[0][0] == argv[0] and commands[0][1] == argv and commands[0][2] is False,
            "config_changed",
        )
        for value in commands[0][3:]:
            integer(value, 0, 2**64 - 1)
        for name in ("Wants", "Upholds", "OnFailure", "OnSuccess"):
            require(self.prop(path, BUS_UNIT, name, "as") == [], "config_dependency")
        requires = config_strings(self.prop(path, BUS_UNIT, "Requires", "as"), 8)
        require(set(requires) <= {"app.slice", "basic.target"}, "config_dependency")
        fragment = cpath(self.prop(path, BUS_UNIT, "FragmentPath", "s"))
        require(
            PurePosixPath(fragment).name == self.unit
            and str(PurePosixPath(fragment).parent) in self.paths
            and str(PurePosixPath(fragment).parent)
            == self.env["XDG_RUNTIME_DIR"] + "/systemd/transient",
            "config_changed",
        )
        snapshot, comments = config_scan(
            self.paths,
            self.unit,
            self.uid,
            deadline,
            generated=fragment,
            generated_content=(argv, unset_names, description),
        )
        config_same(self.snapshot, snapshot, generated=fragment)
        require(comments == self.comments, "config_changed")

    def summary(self, postchecked=False):
        return {
            "status": "OBSERVED_CLEAN",
            "manager_pid": self.pid,
            "manager_starttime": self.started,
            "version": self.version,
            "unit_path_count": len(self.paths),
            "comment_file_count": len(self.comments),
            "fingerprint": self.fingerprint,
            "prelaunch_rechecked": self.prechecked,
            "postlaunch_rechecked": postchecked,
        }

    def close(self):
        os.close(self.pidfd)


def service(socket_path, nonce, context, parent_pid):
    validate_context(context)
    pattern(nonce, r"[0-9a-f]{64}")
    require(os.getuid() == os.geteuid() != 0 and os.getgid() == os.getegid() != 0, "ordinary_user")
    require(source_hash() == context["collector_sha256"], "source_changed")
    deadline = time.monotonic() + CHANNEL_SECONDS
    with socket.socket(socket.AF_UNIX) as channel:
        channel.settimeout(remaining(deadline))
        channel.connect(socket_path)
        require(peer(channel) == (parent_pid, os.getuid(), os.getgid()), "peer_mismatch")
        send(
            channel,
            {
                "kind": "hello",
                "nonce": nonce,
                "context": context,
                "uid": os.getuid(),
                "gid": os.getgid(),
                "pid": os.getpid(),
                "starttime": starttime(os.getpid()),
                "boot_id": boot_id(),
                "namespaces": namespaces(),
                "topology": topology(),
            },
            deadline,
        )
        challenge = receive(channel, deadline)
        keys(challenge, {"kind", "challenge", "nonce"})
        require(challenge["kind"] == "challenge" and challenge["nonce"] == nonce, "identity_mismatch")
        pattern(challenge["challenge"], r"[0-9a-f]{64}")
        require(source_hash() == context["collector_sha256"], "source_changed")
        send(channel, {"kind": "ack", "nonce": nonce, "challenge": challenge["challenge"]}, deadline)
        # Stay alive for independent post-ACK liveness and MainPID reconciliation.
        finish = receive(channel, deadline)
        require(finish == {"kind": "finish", "nonce": nonce}, "identity_mismatch")


def probe(context):
    """Only CLI --allow-transient-service invokes this state-changing entrypoint."""
    validate_context(context)
    uid, gid = os.getuid(), os.getgid()
    nonce = secrets.token_hex(32)
    unit = "forge-topology-" + secrets.token_hex(16) + ".service"
    receipt = {
        "schema": SCHEMA,
        "context": context,
        "context_authentication": "UNVERIFIED_DECLARED_CONTEXT",
        "unit": unit,
        "owner_uid": uid,
        "owner_gid": gid,
        "status": "STOP",
        "probe_stage": "preflight",
        "observation_status": "NOT_RUN",
        "observation": None,
        "binding": None,
        "cleanup_evidence": None,
        "channel_cleanup_errors": [],
        "configuration_gate": None,
        "primary": None,
        "cleanup": {
            "status": "NOT_RUN",
            "reason": None,
            "errno": None,
            "exit_code": None,
            "signal": None,
            "child_cleanup": None,
        },
        "boundary": "NOT_RUN",
        "qualification": "NOT_RUN",
    }
    env = tool = directory = listener = channel = group = invocation = gate = None
    directory_identity = socket_identity = None
    marker = "forge-topology-" + hashlib.sha256(nonce.encode("ascii")).hexdigest()
    attempted, stopped = False, False
    started = time.monotonic()
    try:
        require(uid == os.geteuid() != 0 and gid == os.getegid() != 0, "ordinary_user")
        require(context["collector_sha256"] == source_hash(), "source_changed")
        verified_source(uid)
        tool, launcher, python = map(verified_tool, (SYSTEMCTL, SYSTEMD_RUN, PYTHON))
        env = environment(uid)
        deadline = time.monotonic() + START_SECONDS
        receipt["probe_stage"] = "startup"
        manager = show(tool, env, f"user@{uid}.service", deadline, user=False)
        require(
            manager["ActiveState"] == "active"
            and manager["Delegate"] == "yes"
            and manager["ControlGroup"] == manager_root(uid),
            "manager_unavailable",
        )
        environment_names, environment_digest = manager_environment(tool, env, deadline)
        receipt["probe_stage"] = "configuration"
        try:
            gate = ConfigGate(verified_tool(BUSCTL), env, unit, uid, manager["MainPID"])
            gate.prelaunch()
            receipt["configuration_gate"] = gate.summary()
        except (OSError, ValueError) as exc:
            receipt["configuration_gate"] = {"status": "STOP", **error_record(exc)}
            raise
        deadline = time.monotonic() + START_SECONDS
        receipt["probe_stage"] = "startup"
        require(manager_environment(tool, env, deadline)[1] == environment_digest, "identity_mismatch")
        directory = Path(tempfile.mkdtemp(prefix="forge-topology-", dir=env["XDG_RUNTIME_DIR"]))
        directory_identity = (directory.stat().st_dev, directory.stat().st_ino)
        socket_path = directory / "channel"
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(socket_path))
        socket_identity = (socket_path.lstat().st_dev, socket_path.lstat().st_ino)
        listener.listen(1)
        attempted = True  # Even an ambiguous launch failure requires exact-unit cleanup.
        launch_argv = [
            launcher,
            "--user",
            "--quiet",
            "--unit=" + unit,
            "--service-type=exec",
            "--description=" + marker,
            "--property=StandardOutput=null",
            "--property=StandardError=null",
            "--property=UnsetEnvironment=" + " ".join(environment_names),
            "--property=RuntimeMaxSec=15s",
            "--property=TimeoutStopSec=2s",
            "--property=KillMode=control-group",
            "--",
            python,
            "-I",
            "-S",
            "-B",
            str(Path(__file__).resolve()),
            "--service",
            str(socket_path),
            "--nonce",
            nonce,
            "--parent-pid",
            str(os.getpid()),
            "--source-sha",
            context["source_sha"],
            "--collector-sha256",
            context["collector_sha256"],
            "--run-id",
            context["run_id"],
            "--run-attempt",
            str(context["run_attempt"]),
        ]
        command(launch_argv, env, deadline)
        current = show(tool, env, unit, deadline)
        invocation = owned_unit(current, marker)
        group = owned_group(current["ControlGroup"], uid, unit)
        listener.settimeout(remaining(deadline))
        channel, _ = listener.accept()
        deadline = time.monotonic() + CHANNEL_SECONDS
        receipt["probe_stage"] = "exchange"
        require(manager_environment(tool, env, deadline)[1] == environment_digest, "identity_mismatch")
        pid, peer_uid, peer_gid = peer(channel)
        require(peer_uid == uid and peer_gid == gid and pid > 0, "peer_mismatch")
        with os.fdopen(os.pidfd_open(pid), "rb", closefd=True) as handle:
            pidfd = handle.fileno()
            before = starttime(pid)
            hello = validate_hello(receive(channel, deadline))
            require(
                (hello["pid"], hello["uid"], hello["gid"], hello["starttime"])
                == (pid, uid, gid, before),
                "identity_mismatch",
            )
            require(
                hello["nonce"] == nonce
                and hello["context"] == context
                and hello["boot_id"] == boot_id()
                and hello["namespaces"] == namespaces(pid) == namespaces(),
                "identity_mismatch",
            )
            current = show(tool, env, unit, deadline)
            owned_unit(current, marker, invocation)
            group = owned_group(current["ControlGroup"], uid, unit)
            require(
                current["ActiveState"] == "active"
                and current["MainPID"] == pid
                and membership(pid) == hello["topology"]["source"] == group,
                "identity_mismatch",
            )
            live(pidfd, pid, before)
            challenge = secrets.token_hex(32)
            send(channel, {"kind": "challenge", "nonce": nonce, "challenge": challenge}, deadline)
            require(
                receive(channel, deadline) == {"kind": "ack", "nonce": nonce, "challenge": challenge},
                "identity_mismatch",
            )
            live(pidfd, pid, before)
            current = show(tool, env, unit, deadline)
            owned_unit(current, marker, invocation)
            require(
                current["MainPID"] == pid
                and current["ActiveState"] == "active"
                and current["ControlGroup"] == group
                and membership(pid) == group,
                "identity_mismatch",
            )
            require(source_hash() == context["collector_sha256"], "source_changed")
            require(
                manager_environment(tool, env, deadline)[1] == environment_digest, "identity_mismatch"
            )
            live(pidfd, pid, before)
            try:
                gate.postlaunch(
                    deadline,
                    launch_argv[launch_argv.index("--") + 1 :],
                    invocation,
                    environment_names,
                    marker,
                )
                receipt["configuration_gate"] = gate.summary(postchecked=True)
            except (OSError, ValueError) as exc:
                receipt["configuration_gate"] = {"status": "STOP", **error_record(exc)}
                raise
            live(pidfd, pid, before)
            receipt["binding"] = {
                "peer_pid": pid,
                "peer_uid": peer_uid,
                "peer_gid": peer_gid,
                "main_pid": current["MainPID"],
                "starttime": before,
                "boot_id": boot_id(),
                "namespaces": namespaces(pid),
                "control_group": group,
                "invocation": invocation,
                "pidfd_before_ack": True,
                "pidfd_after_ack": True,
                "challenge_ack": True,
                "collector_rechecked": True,
            }
            receipt["observation"] = {k: v for k, v in hello.items() if k not in {"kind", "nonce"}}
            send(channel, {"kind": "finish", "nonce": nonce}, deadline)
            require(topology_ready(hello["topology"]), "topology_prerequisites")
            receipt["probe_stage"] = "observed"
    except (OSError, ValueError, UnicodeError) as exc:
        receipt["primary"] = error_record(exc)
    finally:
        for label, endpoint in (("peer", channel), ("listener", listener), ("manager_pidfd", gate)):
            if endpoint is not None:
                try:
                    endpoint.close()
                except OSError as exc:
                    receipt["channel_cleanup_errors"].append({"channel": label, "errno": exc.errno})
        if attempted:
            try:
                deadline = time.monotonic() + STOP_SECONDS
                current = show(tool, env, unit, deadline)
                if current["LoadState"] != "not-found":
                    invocation = owned_unit(current, marker, invocation)
                    if current["ControlGroup"]:
                        group = owned_group(current["ControlGroup"], uid, unit)
                    command([tool, "--user", "stop", "--", unit], env, deadline)
                current = show(tool, env, unit, deadline)
                require(
                    current["ActiveState"] == "inactive"
                    and current["SubState"] == "dead"
                    and current["MainPID"] == 0,
                    "cleanup_unverified",
                )
                require(group is not None and invocation is not None, "cleanup_unverified")
                group_status = empty_group(group, deadline)
                receipt["cleanup_evidence"] = {
                    "unit": unit,
                    "load_state": current["LoadState"],
                    "active_state": current["ActiveState"],
                    "sub_state": current["SubState"],
                    "main_pid": current["MainPID"],
                    "control_group": group,
                    "invocation": invocation,
                    "group_status": group_status,
                }
                stopped = True
                receipt["cleanup"]["status"] = "OBSERVED"
            except (OSError, ValueError, UnicodeError) as exc:
                receipt["cleanup"] = {"status": "STOP", **error_record(exc)}
        else:
            stopped = True
        if stopped and directory is not None:
            try:
                info = directory.lstat()
                require(
                    stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == directory_identity,
                    "cleanup_unverified",
                )
                if socket_identity is not None:
                    info = (directory / "channel").lstat()
                    require(
                        stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == socket_identity,
                        "cleanup_unverified",
                    )
                    (directory / "channel").unlink()
                directory.rmdir()
            except (OSError, ValueError) as exc:
                receipt["cleanup"] = {"status": "STOP", **error_record(exc)}
        if receipt["channel_cleanup_errors"] and receipt["cleanup"]["reason"] is None:
            receipt["cleanup"] = {"status": "STOP", **error_record(ProbeError("cleanup_unverified"))}
        if receipt["primary"] is None and receipt["cleanup"]["status"] == "OBSERVED":
            receipt["status"] = "OBSERVED"
        receipt["observation_status"] = (
            "OBSERVED" if receipt["observation"] is not None else "UNKNOWN" if attempted else "NOT_RUN"
        )
        receipt["duration_ms"] = int((time.monotonic() - started) * 1000)
    return validate_receipt(receipt)


def error_record(exc):
    return {
        "reason": exc.reason if isinstance(exc, ProbeError) else "io_error",
        "errno": exc.errno if isinstance(exc, OSError) else getattr(exc, "original_errno", None),
        "child_cleanup": getattr(exc, "child_cleanup", None),
        "exit_code": exc.returncode
        if isinstance(exc, ProbeError) and exc.returncode is not None and exc.returncode >= 0
        else None,
        "signal": -exc.returncode
        if isinstance(exc, ProbeError) and exc.returncode is not None and exc.returncode < 0
        else None,
    }


def validate_error(value, nullable=False):
    keys(value, {"reason", "errno", "exit_code", "signal", "child_cleanup"})
    require(
        (type(value["reason"]) is str and value["reason"] in REASONS)
        or (nullable and value["reason"] is None)
    )
    for key, maximum in (("errno", 4095), ("exit_code", 255), ("signal", 64)):
        if value[key] is not None:
            integer(value[key], 0, maximum)
    require(value["exit_code"] is None or value["signal"] is None)
    if value["child_cleanup"] is not None:
        child = keys(value["child_cleanup"], {"reason", "errno"})
        require(child["reason"] == "cleanup_unverified")
        if child["errno"] is not None:
            integer(child["errno"], 0, 4095)
    if value["reason"] is None:
        require(all(value[k] is None for k in ("errno", "exit_code", "signal", "child_cleanup")))


def validate_receipt(value):
    keys(
        value,
        {
            "schema",
            "context",
            "context_authentication",
            "unit",
            "owner_uid",
            "owner_gid",
            "status",
            "observation",
            "binding",
            "cleanup_evidence",
            "channel_cleanup_errors",
            "configuration_gate",
            "primary",
            "cleanup",
            "boundary",
            "qualification",
            "duration_ms",
            "probe_stage",
            "observation_status",
        },
    )
    require(value["schema"] == SCHEMA)
    require(
        type(value["probe_stage"]) is str
        and value["probe_stage"] in {"preflight", "configuration", "startup", "exchange", "observed"}
    )
    require(
        type(value["observation_status"]) is str
        and value["observation_status"] in {"NOT_RUN", "UNKNOWN", "OBSERVED"}
    )
    require((value["observation_status"] == "OBSERVED") == (value["observation"] is not None))
    require(value["context_authentication"] == "UNVERIFIED_DECLARED_CONTEXT")
    validate_context(value["context"])
    pattern(value["unit"], r"forge-topology-[0-9a-f]{32}\.service")
    integer(value["owner_uid"])
    integer(value["owner_gid"])
    require(value["boundary"] == value["qualification"] == "NOT_RUN")
    integer(value["duration_ms"], 0, 2**53 - 1)
    if value["observation"] is not None:
        keys(
            value["observation"],
            {"context", "uid", "gid", "pid", "starttime", "boot_id", "namespaces", "topology"},
        )
        validate_hello({"kind": "hello", "nonce": "0" * 64, **value["observation"]})
        require(value["observation"]["context"] == value["context"])
        require(
            value["observation"]["uid"] == value["owner_uid"]
            and value["observation"]["gid"] == value["owner_gid"]
        )
        owned_group(
            value["observation"]["topology"]["source"], value["observation"]["uid"], value["unit"]
        )
    if value["binding"] is not None:
        binding = keys(
            value["binding"],
            {
                "peer_pid",
                "peer_uid",
                "peer_gid",
                "main_pid",
                "starttime",
                "boot_id",
                "namespaces",
                "control_group",
                "invocation",
                "pidfd_before_ack",
                "pidfd_after_ack",
                "challenge_ack",
                "collector_rechecked",
            },
        )
        require(value["observation"] is not None)
        actor = value["observation"]
        for key, actor_key in (
            ("peer_pid", "pid"),
            ("peer_uid", "uid"),
            ("peer_gid", "gid"),
            ("main_pid", "pid"),
            ("starttime", "starttime"),
        ):
            integer(binding[key], 1, 2**64 - 1)
            require(binding[key] == actor[actor_key])
        require(
            binding["boot_id"] == actor["boot_id"]
            and binding["namespaces"] == actor["namespaces"]
            and binding["control_group"] == actor["topology"]["source"]
        )
        pattern(binding["invocation"], r"[0-9a-f]{32}")
        for key in ("pidfd_before_ack", "pidfd_after_ack", "challenge_ack", "collector_rechecked"):
            require(binding[key] is True)
    require((value["binding"] is None) == (value["observation"] is None))
    if value["cleanup_evidence"] is not None:
        terminal = keys(
            value["cleanup_evidence"],
            {
                "unit",
                "load_state",
                "active_state",
                "sub_state",
                "main_pid",
                "control_group",
                "invocation",
                "group_status",
            },
        )
        require(terminal["unit"] == value["unit"])
        require(
            terminal["load_state"] in ("loaded", "not-found")
            and terminal["active_state"] == "inactive"
            and terminal["sub_state"] == "dead"
        )
        require(type(terminal["main_pid"]) is int and terminal["main_pid"] == 0)
        pattern(terminal["invocation"], r"[0-9a-f]{32}")
        cpath(terminal["control_group"])
        owned_group(terminal["control_group"], value["owner_uid"], value["unit"])
        require(terminal["group_status"] in ("empty", "absent"))
        if value["binding"] is not None:
            require(
                terminal["control_group"] == value["binding"]["control_group"]
                and terminal["invocation"] == value["binding"]["invocation"]
            )
    require(type(value["channel_cleanup_errors"]) is list and len(value["channel_cleanup_errors"]) <= 3)
    for channel_error in value["channel_cleanup_errors"]:
        keys(channel_error, {"channel", "errno"})
        require(channel_error["channel"] in ("peer", "listener", "manager_pidfd"))
        if channel_error["errno"] is not None:
            integer(channel_error["errno"], 0, 4095)
    gate_record = value["configuration_gate"]
    if gate_record is not None:
        require(type(gate_record) is dict and "status" in gate_record)
        if gate_record["status"] == "STOP":
            validate_error({k: v for k, v in gate_record.items() if k != "status"})
        else:
            keys(
                gate_record,
                {
                    "status",
                    "manager_pid",
                    "manager_starttime",
                    "version",
                    "unit_path_count",
                    "comment_file_count",
                    "fingerprint",
                    "prelaunch_rechecked",
                    "postlaunch_rechecked",
                },
            )
            require(gate_record["status"] == "OBSERVED_CLEAN")
            integer(gate_record["manager_pid"], 1)
            integer(gate_record["manager_starttime"], 1, 2**64 - 1)
            pattern(gate_record["version"], r"255(?:\.[0-9]+)?(?:[-+~ ][A-Za-z0-9.+~_-]+)*")
            integer(gate_record["unit_path_count"], 1, 32)
            integer(gate_record["comment_file_count"], 0, 64)
            pattern(gate_record["fingerprint"], r"[0-9a-f]{64}")
            require(
                gate_record["prelaunch_rechecked"] is True
                and type(gate_record["postlaunch_rechecked"]) is bool
            )
    if value["primary"] is not None:
        validate_error(value["primary"])
    cleanup = keys(
        value["cleanup"], {"status", "reason", "errno", "exit_code", "signal", "child_cleanup"}
    )
    require(type(cleanup["status"]) is str and cleanup["status"] in {"NOT_RUN", "OBSERVED", "STOP"})
    validate_error({k: v for k, v in cleanup.items() if k != "status"}, nullable=True)
    require((cleanup["status"] == "STOP") == (cleanup["reason"] is not None))
    require(cleanup["status"] != "OBSERVED" or value["cleanup_evidence"] is not None)
    require(not value["channel_cleanup_errors"] or cleanup["status"] == "STOP")
    observed = (
        value["observation"] is not None
        and value["primary"] is None
        and cleanup["status"] == "OBSERVED"
        and topology_ready(value["observation"]["topology"])
    )
    require(value["status"] == ("OBSERVED" if observed else "STOP"))
    require(
        not observed
        or (
            value["probe_stage"] == "observed"
            and gate_record is not None
            and gate_record["status"] == "OBSERVED_CLEAN"
            and gate_record["postlaunch_rechecked"] is True
        )
    )
    require(len(encode(value)) <= LIMIT)
    return value


def parse_receipt(raw):
    return validate_receipt(decode(raw))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-transient-service", action="store_true")
    parser.add_argument("--service")
    parser.add_argument("--nonce")
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--collector-sha256", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True, type=int)
    args = parser.parse_args()
    context = {
        "source_sha": args.source_sha,
        "collector_sha256": args.collector_sha256,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
    }
    if args.service:
        require(
            not args.allow_transient_service and args.nonce is not None and args.parent_pid is not None
        )
        service(args.service, args.nonce, context, args.parent_pid)
        return 0
    require(args.allow_transient_service)
    result = probe(context)
    sys.stdout.buffer.write(encode(result))
    return 0 if result["status"] == "OBSERVED" else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        sys.stderr.write(encode(error_record(error)).decode("ascii"))
        sys.exit(2)
