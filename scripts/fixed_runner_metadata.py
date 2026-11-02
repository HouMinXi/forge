"""One explicitly dispatched, fixed privileged metadata diagnostic; never admission."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import selectors
import stat
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
BRANCH = "forge-fixed-metadata-observer-20261102"
WORKFLOW = ".github/workflows/strict-linux-admission.yml"
SOURCE = "scripts/fixed_runner_metadata.py"
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
TOOLS = ("git", "systemctl", "busctl", "sudo", "timeout", "stat", "grep")
NAMES = ("bwrap", "unpriv_bwrap")
KEYS = (
    "REPOSITORY",
    "RUN_ID",
    "RUN_ATTEMPT",
    "JOB",
    "REF",
    "WORKFLOW_SHA",
    "WORKFLOW_REF",
    "SOURCE_SHA",
    "EXPECTED_SHA",
    "EVENT",
)
REASONS = {
    "context",
    "source",
    "tool",
    "version",
    "identity",
    "manager",
    "format",
    "command",
    "timeout",
    "output",
    "io",
    "changed",
    "unexpected",
}


class Stop(ValueError):
    def __init__(self, reason):
        assert reason in REASONS
        super().__init__(reason)
        self.reason = reason


def need(condition, reason):
    if not condition:
        raise Stop(reason)


def context():
    value = {k: os.environ.get("CONTEXT_" + k, "") for k in KEYS}
    need(value["REPOSITORY"] == "HouMinXi/forge" and value["EVENT"] == "workflow_dispatch", "context")
    need(value["JOB"] == "fixed-metadata" and value["REF"] == "refs/heads/" + BRANCH, "context")
    need(re.fullmatch(r"[1-9][0-9]{0,19}", value["RUN_ID"]) and value["RUN_ATTEMPT"] == "1", "context")
    need(
        all(
            re.fullmatch(r"[0-9a-f]{40}", value[k])
            for k in ("SOURCE_SHA", "EXPECTED_SHA", "WORKFLOW_SHA")
        ),
        "context",
    )
    need(value["SOURCE_SHA"] == value["EXPECTED_SHA"] == value["WORKFLOW_SHA"], "context")
    need(value["WORKFLOW_REF"] == "HouMinXi/forge/" + WORKFLOW + "@refs/heads/" + BRANCH, "context")
    return value


def fingerprint(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def file_bytes(path, owner, limit):
    path = Path(path)
    need(path.resolve(strict=True) == path, "tool" if owner == 0 else "source")
    for directory in path.parents:
        info = directory.stat()
        need(info.st_uid in (0, owner) and not info.st_mode & 0o022, "tool" if owner == 0 else "source")
    before = path.lstat()
    need(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == owner
        and not before.st_mode & 0o022
        and before.st_size <= limit,
        "tool" if owner == 0 else "source",
    )
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        need(fingerprint(os.fstat(fd)) == fingerprint(before), "changed")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        need(len(data) == before.st_size <= limit, "changed")
        need(fingerprint(os.fstat(fd)) == fingerprint(before) == fingerprint(path.lstat()), "changed")
        return bytes(data), fingerprint(before)
    finally:
        os.close(fd)


def tools_snapshot(deadline=None):
    result = {}
    for name in TOOLS:
        if deadline is not None:
            need(time.monotonic() < deadline, "timeout")
        data, identity = file_bytes(Path("/usr/bin") / name, 0, 16777216)
        need(data.startswith(b"\x7fELF") and identity[2] & 0o111, "tool")
        need(identity[2] & 0o6000 == (0o4000 if name == "sudo" else 0), "tool")
        result[name] = {"sha256": hashlib.sha256(data).hexdigest(), "identity": identity}
    if deadline is not None:
        need(time.monotonic() < deadline, "timeout")
    return result


def capture(argv, env, deadline, limit=4096, privileged=False, cwd="/"):
    """Discard stderr; bounded drain; no claim about privileged descendants."""
    need(time.monotonic() < deadline, "timeout")
    proc = None
    result = {
        "stdout": b"",
        "exit_code": None,
        "stderr_bytes": 0,
        "reason": None,
        "errno": None,
        "direct_sudo_reaped": False,
        "privileged_descendants": "NOT_INDEPENDENTLY_OBSERVED" if privileged else "NOT_APPLICABLE",
    }
    out = bytearray()
    try:
        proc = subprocess.Popen(
            argv,
            env=env,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        with selectors.DefaultSelector() as selector:
            for stream in (proc.stdout, proc.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result["reason"] = "timeout"
                    break
                for key, _ in selector.select(min(remaining, 0.05)):
                    data = os.read(key.fileobj.fileno(), 4096)
                    if not data:
                        selector.unregister(key.fileobj)
                    elif key.fileobj is proc.stdout:
                        room = max(0, limit - len(out))
                        out.extend(data[:room])
                        if len(data) > room:
                            result["reason"] = "output"
                    else:
                        result["stderr_bytes"] = min(4097, result["stderr_bytes"] + len(data))
        try:
            proc.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            result["reason"] = "timeout"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result["reason"] = result["reason"] or "io"
        result["errno"] = exc.errno if isinstance(exc, OSError) else None
    finally:
        if proc is not None:
            if proc.returncode is None and not privileged:
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except (OSError, subprocess.SubprocessError):
                    result["reason"] = result["reason"] or "io"
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except OSError as exc:
                    result["reason"] = result["reason"] or "io"
                    result["errno"] = result["errno"] or exc.errno
            result["exit_code"] = proc.returncode
            result["direct_sudo_reaped"] = proc.returncode is not None
        result["stdout"] = bytes(out)
    return result


def ordinary(argv, env=ENV, cwd="/", deadline=None):
    end = time.monotonic() + 2
    result = capture(argv, env, min(end, deadline) if deadline is not None else end, cwd=cwd)
    need(result["reason"] is None and result["exit_code"] == 0 and not result["stderr_bytes"], "command")
    return result["stdout"]


def source_snapshot(expected, deadline=None):
    hashes, blobs, modes = {}, {}, {}
    for name in (SOURCE, WORKFLOW):
        data, info = file_bytes(ROOT / name, os.getuid(), 131072)
        need(not info[2] & 0o6000, "source")
        hashes[name] = hashlib.sha256(data).hexdigest()
        blobs[name] = hashlib.sha1(
            b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False
        ).hexdigest()
        modes[name] = "100755" if info[2] & 0o111 else "100644"
    env = {**ENV, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    git = ["/usr/bin/git", "--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false"]
    need(
        ordinary([*git, "rev-parse", "HEAD"], env, ROOT, deadline=deadline).decode("ascii").strip()
        == expected,
        "source",
    )
    records = ordinary(
        [*git, "ls-tree", "-z", "HEAD", "--", SOURCE, WORKFLOW], env, ROOT, deadline=deadline
    ).split(b"\0")
    need(records[-1] == b"" and len(records) == 3, "source")
    seen = set()
    for record in records[:-1]:
        meta, name = record.decode("ascii").split("\t")
        mode, kind, blob = meta.split(" ")
        need(
            name in blobs
            and name not in seen
            and kind == "blob"
            and blob == blobs[name]
            and mode == modes[name],
            "source",
        )
        seen.add(name)
    return hashes


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "format")
        result[key] = value
    return result


def bus(method, argument, kind, env, deadline=None):
    data = ordinary(
        [
            "/usr/bin/busctl",
            "--user",
            "--auto-start=no",
            "--allow-interactive-authorization=no",
            "--json=short",
            "--timeout=1s",
            "call",
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            method,
            "s",
            argument,
        ],
        env,
        deadline=deadline,
    )
    value = json.loads(data, object_pairs_hook=unique_pairs)
    need(
        type(value) is dict
        and set(value) == {"type", "data"}
        and value["type"] == kind
        and type(value["data"]) is list
        and len(value["data"]) == 1,
        "format",
    )
    return value["data"][0]


def starttime(pid):
    with open(f"/proc/{pid}/stat", "rb") as handle:
        raw = handle.read(4097)
    need(len(raw) <= 4096, "identity")
    value = raw.rsplit(b")", 1)[1].split()[19]
    need(value.isdigit(), "identity")
    return int(value)


def manager(uid):
    runtime = Path(f"/run/user/{uid}")
    info, peer = runtime.lstat(), (runtime / "bus").lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o077, "manager")
    need(stat.S_ISSOCK(peer.st_mode) and peer.st_uid == uid, "manager")
    env = {
        **ENV,
        "XDG_RUNTIME_DIR": str(runtime),
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(runtime / "bus"),
    }
    raw = ordinary(
        [
            "/usr/bin/systemctl",
            "show",
            "--all",
            "--no-pager",
            "--property=Id,ActiveState,MainPID",
            "--",
            f"user@{uid}.service",
        ]
    )
    pairs = [line.split("=", 1) for line in raw.decode("ascii").splitlines()]
    need(len(pairs) == 3 and all(len(pair) == 2 for pair in pairs), "manager")
    fields = dict(pairs)
    need(
        set(fields) == {"Id", "ActiveState", "MainPID"}
        and fields["Id"] == f"user@{uid}.service"
        and fields["ActiveState"] == "active",
        "manager",
    )
    owner = bus("GetNameOwner", "org.freedesktop.systemd1", "s", env)
    need(type(owner) is str and re.fullmatch(r":[0-9]+\.[0-9]+", owner), "identity")
    pid = bus("GetConnectionUnixProcessID", owner, "u", env)
    need(type(pid) is int and 0 < pid < 2**31 and fields["MainPID"] == str(pid), "identity")
    need(os.stat(f"/proc/{pid}").st_uid == uid, "identity")
    started = starttime(pid)
    fd = os.pidfd_open(pid)
    return {"pid": pid, "starttime": started, "owner": owner, "env": env, "fd": fd}


def live(identity, uid):
    poller = select.poll()
    poller.register(identity["fd"], select.POLLIN)
    need(not poller.poll(0), "identity")
    pid = identity["pid"]
    need(os.stat(f"/proc/{pid}").st_uid == uid and starttime(pid) == identity["starttime"], "identity")
    need(not poller.poll(0), "identity")


def root_argv(kind, pid=None):
    prefix = [
        "/usr/bin/sudo",
        "-n",
        "--",
        "/usr/bin/timeout",
        "--signal=TERM",
        "--kill-after=1s",
        "--",
        "2s",
    ]
    if kind == "stat":
        need(type(pid) is int and 0 < pid < 2**31, "identity")
        return [
            *prefix,
            "/usr/bin/stat",
            "--dereference",
            "--printf=%d:%i\\n",
            "--",
            f"/proc/{pid}/ns/mnt",
            f"/proc/{pid}/ns/cgroup",
            f"/proc/{pid}/root",
        ]
    need(kind in NAMES and pid is None, "format")
    return [
        *prefix,
        "/usr/bin/grep",
        "-E",
        "-q",
        "--",
        "^" + kind + " [(][^[:cntrl:]()]+[)]$",
        "/sys/kernel/security/apparmor/profiles",
    ]


def numbers(raw):
    need(
        type(raw) is bytes
        and len(raw) <= 256
        and re.fullmatch(rb"(?:[0-9]{1,20}:[0-9]{1,20}\n){3}", raw),
        "format",
    )
    values = [[int(item) for item in row.split(b":")] for row in raw.splitlines()]
    need(all(0 <= item < 2**64 for pair in values for item in pair), "format")
    return dict(zip(("mnt", "cgroup", "root"), values, strict=True))


def self_numbers():
    return {
        name: [value.st_dev, value.st_ino]
        for name, value in (
            ("mnt", os.stat("/proc/self/ns/mnt")),
            ("cgroup", os.stat("/proc/self/ns/cgroup")),
            ("root", os.stat("/")),
        )
    }


def privileged(kind, identity, uid, deadline, receipt):
    live(identity, uid)
    entry = {
        "operation": kind,
        "direct_sudo_reaped": False,
        "reason": "unexpected",
        "exit_code": None,
        "stderr_bytes": 0,
        "errno": None,
        "privileged_descendants": "NOT_INDEPENDENTLY_OBSERVED",
    }
    receipt["commands"].append(entry)
    result = capture(
        root_argv(kind, identity["pid"] if kind == "stat" else None),
        ENV,
        min(deadline, time.monotonic() + 4),
        limit=256 if kind == "stat" else 0,
        privileged=True,
    )
    entry.update({k: v for k, v in result.items() if k != "stdout"})
    need(
        result["reason"] is None and result["direct_sudo_reaped"] and result["stderr_bytes"] == 0,
        result["reason"] or "command",
    )
    need(result["exit_code"] in ((0,) if kind == "stat" else (0, 1)), "command")
    need(kind == "stat" or result["stdout"] == b"", "format")
    live(identity, uid)
    return numbers(result["stdout"]) if kind == "stat" else result["exit_code"] == 0


def observe():
    started = time.monotonic()
    result = {
        "schema": "forge-fixed-metadata-v1",
        "duration_ms": 0,
        "status": "STOP",
        "phase": "preflight",
        "reason": None,
        "errno": None,
        "context": None,
        "source_sha256": None,
        "tool_sha256": None,
        "timeout_version": None,
        "manager": None,
        "caller": None,
        "observations": None,
        "commands": [],
        "direct_command_cleanup": "NOT_RUN",
        "privileged_descendants": "NOT_INDEPENDENTLY_OBSERVED",
        "authentication": "UNVERIFIED_DECLARED_CONTEXT",
        "ordinary_gate": "NOT_REPLACED",
        "service": "NOT_RUN",
        "boundary": "NOT_RUN",
        "qualification": "NOT_RUN",
    }
    identity = None
    try:
        uid, gid = os.getuid(), os.getgid()
        need(uid == os.geteuid() != 0 and gid == os.getegid() != 0, "identity")
        result["context"] = ctx = context()
        tools = tools_snapshot()
        result["tool_sha256"] = {k: v["sha256"] for k, v in tools.items()}
        result["source_sha256"] = source_snapshot(ctx["SOURCE_SHA"])
        need(
            ordinary(["/usr/bin/timeout", "--version"]).splitlines()[0]
            == b"timeout (GNU coreutils) 9.4",
            "version",
        )
        result["timeout_version"] = "9.4"
        identity = manager(uid)
        live(identity, uid)
        result["manager"] = {
            "pid": identity["pid"],
            "starttime": identity["starttime"],
            "uid": uid,
            "bus_owner": identity["owner"],
        }
        result["caller"] = self_numbers()
        need(
            tools_snapshot() == tools and source_snapshot(ctx["SOURCE_SHA"]) == result["source_sha256"],
            "changed",
        )
        result["phase"] = "privileged_reads"
        deadline = time.monotonic() + 15
        first = privileged("stat", identity, uid, deadline, result)
        profiles = {name: privileged(name, identity, uid, deadline, result) for name in NAMES}
        last = privileged("stat", identity, uid, deadline, result)
        result["phase"] = "recheck"
        need(
            bus("GetNameOwner", "org.freedesktop.systemd1", "s", identity["env"], deadline)
            == identity["owner"],
            "changed",
        )
        need(
            bus("GetConnectionUnixProcessID", identity["owner"], "u", identity["env"], deadline)
            == identity["pid"],
            "changed",
        )
        live(identity, uid)
        need(first == last and self_numbers() == result["caller"], "changed")
        need(
            tools_snapshot(deadline) == tools
            and source_snapshot(ctx["SOURCE_SHA"], deadline) == result["source_sha256"],
            "changed",
        )
        need(time.monotonic() < deadline, "timeout")
        result["observations"] = {
            "observer": "fixed_privileged_stat_and_grep",
            "manager_identity": last,
            "matches_caller": {k: last[k] == result["caller"][k] for k in last},
            "known_profile_matches": profiles,
            "profile_status_basis": "NORMAL_SUDO_TIMEOUT_GREP_CHAIN_WITHOUT_DIAGNOSTICS",
        }
        result["status"] = "OBSERVED"
    except (OSError, ValueError, IndexError, KeyError, TypeError, RecursionError) as exc:
        result["reason"] = (
            exc.reason if isinstance(exc, Stop) else "io" if isinstance(exc, OSError) else "format"
        )
        result["errno"] = exc.errno if isinstance(exc, OSError) else None
    except Exception:  # noqa: BLE001 -- retain finite STOP evidence without private exception text.
        result["reason"] = "unexpected"
    finally:
        if identity is not None:
            try:
                os.close(identity["fd"])
            except OSError:
                result["status"], result["reason"] = "STOP", "io"
        if result["commands"]:
            result["direct_command_cleanup"] = (
                "DIRECT_SUDO_REAPED"
                if all(c["direct_sudo_reaped"] for c in result["commands"])
                else "UNKNOWN"
            )
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
    return result


def evidence_directory(directory):
    directory = Path(directory)
    before = directory.lstat()
    need(
        directory.resolve(strict=True) == directory
        and stat.S_ISDIR(before.st_mode)
        and before.st_uid == os.getuid()
        and before.st_mode & 0o777 == 0o700,
        "source",
    )
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    if fingerprint(os.fstat(fd)) != fingerprint(before):
        os.close(fd)
        raise Stop("changed")
    return fd


def save(fd, value):
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    need(len(data) <= 16384, "output")
    target = os.open(
        "metadata-observation.json",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=fd,
    )
    with os.fdopen(target, "wb") as handle:
        handle.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--allow-fixed-privileged-metadata", action="store_true", required=True)
    args = parser.parse_args()
    fd = None
    try:
        fd = evidence_directory(args.output_dir)
        result = observe()
        save(fd, result)
        return 0 if result["status"] == "OBSERVED" else 2
    except Exception:  # noqa: BLE001 -- finite last-resort boundary; never print private exception data.
        return 2
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                return 2


if __name__ == "__main__":
    raise SystemExit(main())
