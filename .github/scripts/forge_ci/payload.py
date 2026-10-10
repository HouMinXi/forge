"""Trusted, stdlib-only payload for the bounded AppArmor qualification probe.

This file is copied into the production sandbox's private workspace. It never
loads policy, changes maps, enters namespaces, or changes host settings.
"""

from __future__ import annotations

import argparse
import errno
import ipaddress
import json
import math
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

MAX_RECORD = 131072
MAX_COMMAND_OUTPUT = 32768
EXPECTED_LABEL = "bwrap//&unpriv_bwrap (enforce)"
CAP_SYS_ADMIN = 1 << 21


class ProbeError(RuntimeError):
    """A missing or incompatible fact is not qualification evidence."""


def begin_audit_clock(max_interval_ns: int) -> dict:
    """Sample the Linux audit clock; an absent named Python constant is irrelevant."""
    if sys.platform != "linux" or max_interval_ns not in (5_000_000_000, 30_000_000_000):
        raise ProbeError("unsupported audit clock or interval envelope")
    try:
        # Linux UAPI CLOCK_REALTIME_COARSE is ID 5, not ordinary realtime.
        resolution = time.clock_getres(5) * 1_000_000_000
        if not math.isfinite(resolution) or not resolution.is_integer() or not 0 < resolution <= max_interval_ns:
            raise ProbeError("unsupported audit clock resolution")
        monotonic_before = time.monotonic_ns()
        before = time.clock_gettime_ns(5)
    except (AttributeError, OSError, OverflowError, TypeError, ValueError) as exc:
        raise ProbeError("CLOCK_REALTIME_COARSE unavailable") from exc
    if type(before) is not int or before < 0 or type(monotonic_before) is not int or monotonic_before < 0:
        raise ProbeError("invalid audit clock sample")
    return {"clock_id": 5, "clock_name": "CLOCK_REALTIME_COARSE", "resolution_ns": int(resolution),
            "before_ns": before, "monotonic_before_ns": monotonic_before}


def end_audit_clock(record: dict) -> None:
    try:
        record["after_ns"] = time.clock_gettime_ns(5)
        record["monotonic_after_ns"] = time.monotonic_ns()
    except (AttributeError, OSError, OverflowError, TypeError, ValueError) as exc:
        raise ProbeError("CLOCK_REALTIME_COARSE unavailable") from exc


def validate_audit_clock(record: dict, started: dict, ended: dict, max_interval_ns: int) -> None:
    """Validate separate precise evidence and the exact enclosing coarse bracket."""
    fields = {"clock_id", "clock_name", "resolution_ns", "before_ns", "after_ns",
              "monotonic_before_ns", "monotonic_after_ns"}
    if (type(record) is not dict or set(record) != fields
            or type(max_interval_ns) is not int or max_interval_ns not in (5_000_000_000, 30_000_000_000)
            or record["clock_name"] != "CLOCK_REALTIME_COARSE"
            or any(type(record[key]) is not int for key in fields - {"clock_name"})
            or record["clock_id"] != 5 or not 0 < record["resolution_ns"] <= max_interval_ns):
        raise ProbeError("missing or unsupported audit clock evidence")
    for first, last in (("before_ns", "after_ns"), ("monotonic_before_ns", "monotonic_after_ns")):
        if not 0 <= record[first] <= record[last] or record[last] - record[first] > max_interval_ns:
            raise ProbeError("backward or unbounded audit clock interval")
    if type(started) is not dict or type(ended) is not dict:
        raise ProbeError("missing precise operation timestamp interval")
    for clock in ("utc_ns", "monotonic_ns"):
        start, end = started.get(clock), ended.get(clock)
        if (type(start) is not int or type(end) is not int or not 0 < start <= end
                or end - start > max_interval_ns):
            raise ProbeError("missing or unbounded operation timestamp interval")
    if not record["monotonic_before_ns"] <= started["monotonic_ns"] <= ended["monotonic_ns"] <= record["monotonic_after_ns"]:
        raise ProbeError("audit sampling interval does not enclose the precise operation")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProbeError("duplicate JSON key: " + key)
        result[key] = value
    return result


def decode_json(raw: bytes, limit: int = MAX_RECORD):
    if not raw or len(raw) > limit:
        raise ProbeError("empty or oversized JSON record")
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ProbeError("nonfinite JSON value: " + value)
            ),
        )
    except (UnicodeError, ValueError) as exc:
        raise ProbeError("invalid or incomplete JSON record") from exc


def read_bytes(path: Path, limit: int = MAX_RECORD) -> bytes:
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ProbeError("oversized evidence: " + str(path))
    return raw


def write_json(path: Path, value) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_RECORD:
        raise ProbeError("oversized output record")
    temporary = path.with_name(path.name + ".partial")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    os.replace(temporary, path)


def read_json(path: Path):
    return decode_json(read_bytes(path))


def read_status(raw: str) -> dict[str, str]:
    fields = {}
    for line in raw.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            if key in fields:
                raise ProbeError("duplicate proc status key")
            fields[key] = value.strip()
    return fields


def snapshot() -> dict:
    raw = read_bytes(Path("/proc/self/status"), 32768).decode("utf-8")
    return {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "uid": os.getuid(),
        "euid": os.geteuid(),
        "gid": os.getgid(),
        "egid": os.getegid(),
        "label": read_bytes(Path("/proc/self/attr/current"), 4096).decode("utf-8").strip(),
        "status_raw": raw,
        "status": read_status(raw),
        "userns": os.readlink("/proc/self/ns/user"),
        "netns": os.readlink("/proc/self/ns/net"),
        "pidns": os.readlink("/proc/self/ns/pid"),
        "identity_mapping": {
            kind + "_map_raw": read_bytes(Path("/proc/self/" + kind + "_map"), 4096).decode("ascii")
            for kind in ("uid", "gid")
        } | {
            "overflow" + kind + "_raw": read_bytes(Path("/proc/sys/kernel/overflow" + kind), 64).decode("ascii")
            for kind in ("uid", "gid")
        },
        "utc_ns": time.time_ns(),
        "monotonic_ns": time.monotonic_ns(),
    }


def validate_snapshot(value: dict, *, nonroot: bool = True) -> None:
    if not isinstance(value, dict) or value.get("label") != EXPECTED_LABEL:
        raise ProbeError("unexpected or non-enforcing payload label")
    status = value.get("status")
    if not isinstance(status, dict) or read_status(value.get("status_raw", "")) != status:
        raise ProbeError("missing or inconsistent raw process status")
    if status.get("NoNewPrivs") != "1":
        raise ProbeError("payload NoNewPrivs is not 1")
    for key in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd"):
        try:
            if len(status[key]) != 16 or int(status[key], 16) < 0:
                raise ValueError("invalid mask")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProbeError("missing or invalid capability mask") from exc
    for key in ("userns", "netns", "pidns"):
        prefix = {"userns": "user", "netns": "net", "pidns": "pid"}[key] + ":["
        identity = value.get(key, "")
        if (
            not isinstance(identity, str)
            or not identity.startswith(prefix)
            or not identity.endswith("]")
        ):
            raise ProbeError("missing namespace identity")
        if not identity[len(prefix) : -1].isdigit():
            raise ProbeError("invalid namespace identity")
    if type(value.get("pid")) is not int or value["pid"] <= 0:
        raise ProbeError("missing namespace PID")
    for key in ("uid", "euid", "gid", "egid"):
        if type(value.get(key)) is not int or value[key] < 0 or (nonroot and value[key] == 0):
            raise ProbeError("payload requires a non-root UID/GID")


def bounded_command(
    argv: list[str], timeout: float, *, pass_fds=(), env=None, limit: int = MAX_COMMAND_OUTPUT
) -> dict:
    """Drain both pipes continuously, fail rather than accept truncation."""
    if timeout <= 0 or timeout > 30:
        raise ProbeError("command timeout outside bounded probe envelope")
    started = {"utc_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}
    stdout, stderr = bytearray(), bytearray()
    record = {"argv": list(argv), "started": started, "stdout": "", "stderr": ""}
    with subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=pass_fds,
        env=env,
    ) as process:
        record["wrapper_pid"] = process.pid
        with selectors.DefaultSelector() as selector:
            for pipe, output in ((process.stdout, stdout), (process.stderr, stderr)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, output)
            try:
                deadline = time.monotonic() + timeout
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ProbeError("command deadline exceeded")
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        key.data.extend(chunk)
                        if len(stdout) + len(stderr) > limit:
                            raise ProbeError("command output exceeded bound")
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except (ProbeError, OSError, subprocess.TimeoutExpired) as exc:
                process.kill()
                process.wait(timeout=5)
                record["error"] = str(exc)
        record.update(
            stdout_hex=bytes(stdout[:limit]).hex(),
            stderr_hex=bytes(stderr[:limit]).hex(),
            returncode=process.returncode,
            stdout=bytes(stdout[:limit]).decode("utf-8", errors="replace"),
            stderr=bytes(stderr[:limit]).decode("utf-8", errors="replace"),
            ended={"utc_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()},
        )
    return record


def command_stdout_bytes(result: dict, limit: int = MAX_COMMAND_OUTPUT) -> bytes:
    """Validate the original command bytes, never replacement-decoded text."""
    encoded = result.get("stdout_hex")
    if not isinstance(encoded, str) or len(encoded) > 2 * limit:
        raise ProbeError("missing or oversized raw command output")
    try:
        raw = bytes.fromhex(encoded)
    except ValueError as exc:
        raise ProbeError("invalid raw command output") from exc
    if raw.hex() != encoded:
        raise ProbeError("noncanonical raw command output")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ProbeError("invalid UTF-8 command output") from exc
    if result.get("stdout") != text:
        raise ProbeError("command display differs from raw output")
    return raw


def _command_json(result: dict):
    return decode_json(command_stdout_bytes(result), MAX_COMMAND_OUTPUT)


def _checked_ip(ip: str, options: list[str]) -> dict:
    result = bounded_command([ip, "-j", *options], 3)
    if result.get("error") or result["returncode"] != 0:
        raise ProbeError("ip query failed: " + json.dumps(result, sort_keys=True))
    result["json"] = _command_json(result)
    if not isinstance(result["json"], list):
        raise ProbeError("ip query did not return an array")
    return result


def network_snapshot(ip: str) -> dict:
    before = os.readlink("/proc/self/ns/net")
    observations = {
        "ip": ip,
        "before_netns": before,
        "links": _checked_ip(ip, ["link", "show"]),
        "addresses4": _checked_ip(ip, ["-4", "address", "show"]),
        "routes4": _checked_ip(ip, ["-4", "route", "show", "table", "all"]),
    }
    disabled = {}
    for interface in ("all", "default", "lo"):
        path = Path("/proc/sys/net/ipv6/conf") / interface / "disable_ipv6"
        disabled[str(path)] = read_bytes(path, 16).decode("ascii").strip()
    observations["ipv6_disable"] = disabled
    if all(value == "1" for value in disabled.values()):
        observations["ipv6_disabled"] = True
    else:
        observations["ipv6_disabled"] = False
        observations["addresses6"] = _checked_ip(ip, ["-6", "address", "show"])
        observations["routes6"] = _checked_ip(ip, ["-6", "route", "show", "table", "all"])
    observations["after_netns"] = os.readlink("/proc/self/ns/net")
    return observations


def _ip_values(network: dict, name: str, options: list[str]) -> list:
    result = network.get(name)
    if not isinstance(result, dict) or result.get("returncode") != 0 or result.get("error"):
        raise ProbeError("missing or failed " + name)
    if result.get("argv") != [network["ip"], "-j", *options]:
        raise ProbeError("wrong ip command for " + name)
    values = _command_json(result)
    if not isinstance(values, list) or result.get("json") != values:
        raise ProbeError("inconsistent ip JSON for " + name)
    return values


def validate_network(network: dict, caller_netns: str, payload_netns: str) -> None:
    if caller_netns == payload_netns or not caller_netns or not payload_netns:
        raise ProbeError("payload did not establish a distinct network namespace")
    if network.get("before_netns") != payload_netns or network.get("after_netns") != payload_netns:
        raise ProbeError("ip observations are not from the payload network namespace")
    if not isinstance(network.get("ip"), str) or not network["ip"].startswith("/"):
        raise ProbeError("missing resolved ip executable")
    links = _ip_values(network, "links", ["link", "show"])
    if len(links) != 1 or not isinstance(links[0], dict) or links[0].get("ifname") != "lo":
        raise ProbeError("network must contain exactly the loopback interface")
    if "LOOPBACK" not in links[0].get("flags", []) or links[0].get("link_type") != "loopback":
        raise ProbeError("missing positive loopback interface evidence")
    disabled = network.get("ipv6_disable", {})
    expected = {"/proc/sys/net/ipv6/conf/" + key + "/disable_ipv6" for key in ("all", "default", "lo")}
    if set(disabled) != expected or any(value not in ("0", "1") for value in disabled.values()):
        raise ProbeError("missing affirmative IPv6 configuration evidence")
    is_disabled = all(value == "1" for value in disabled.values())
    if type(network.get("ipv6_disabled")) is not bool or network["ipv6_disabled"] != is_disabled:
        raise ProbeError("inconsistent IPv6 availability")
    for family in (4,) if is_disabled else (4, 6):
        addresses = _ip_values(network, f"addresses{family}", [f"-{family}", "address", "show"])
        if len(addresses) != 1 or addresses[0].get("ifname") != "lo":
            raise ProbeError("missing loopback address interface evidence")
        entries = addresses[0].get("addr_info")
        if not isinstance(entries, list) or not entries:
            raise ProbeError("missing positive loopback address evidence")
        for address in entries:
            try:
                local = ipaddress.ip_address(address["local"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ProbeError("invalid local interface address") from exc
            if local.version != family or not local.is_loopback:
                raise ProbeError("non-loopback interface address")
            if "peer" in address and address["peer"] != address["local"]:
                raise ProbeError("external address peer")
        routes = _ip_values(network, f"routes{family}", [f"-{family}", "route", "show", "table", "all"])
        for route in routes:
            if not isinstance(route, dict) or route.get("dev") != "lo":
                raise ProbeError("route is not local to loopback")
            if any(key in route for key in ("gateway", "via", "multipath", "nexthops", "nhid", "encap")):
                raise ProbeError("route has an external next hop or encapsulation")
            if route.get("type", "unicast") not in ("local", "broadcast", "unicast"):
                raise ProbeError("unexpected loopback route type")
            try:
                destination = ipaddress.ip_network(route["dst"], strict=False)
            except (KeyError, TypeError, ValueError) as exc:
                raise ProbeError("route has no explicit local destination") from exc
            loopback = ipaddress.ip_network("127.0.0.0/8" if family == 4 else "::1/128")
            if destination.version != family or not destination.subnet_of(loopback):
                raise ProbeError("non-loopback route destination")
            for key in ("prefsrc", "src"):
                if key in route and not ipaddress.ip_address(route[key]).is_loopback:
                    raise ProbeError("non-loopback route source")


def validate_witness_ready(value: dict) -> None:
    if not isinstance(value, dict) or value.get("error"):
        raise ProbeError("capability witness failed before completing evidence")
    before, positive = (value.get(name, {}) for name in ("before", "after_user"))
    for observation in (before, positive):
        validate_snapshot(observation)
        if observation["status"].get("Threads") != "1":
            raise ProbeError("capability witness is not single-threaded")
    if not before["pid"] == positive["pid"] == value.get("namespace_pid"):
        raise ProbeError("witness process changed across capability operations")
    if before["pidns"] != positive["pidns"]:
        raise ProbeError("witness changed PID namespace")
    if before["userns"] == positive["userns"] or before["netns"] != positive["netns"]:
        raise ProbeError("NEWUSER positive control did not establish the required namespaces")
    if not int(positive["status"]["CapEff"], 16) & CAP_SYS_ADMIN:
        raise ProbeError("NEWUSER positive control lacks CAP_SYS_ADMIN")


def validate_witness(value: dict) -> None:
    validate_witness_ready(value)
    positive, after = value["after_user"], value.get("after_net", {})
    validate_snapshot(after)
    if after["status"].get("Threads") != "1" or after["pid"] != value["namespace_pid"]:
        raise ProbeError("witness process changed across capability operations")
    if type(value.get("net_errno")) is not int or value["net_errno"] != errno.EPERM:
        raise ProbeError("NEWNET did not fail with EPERM")
    if any(positive[key] != after[key] for key in ("netns", "userns", "pidns")):
        raise ProbeError("namespace changed during refused NEWNET")
    validate_audit_clock(value.get("net_audit_clock"), value.get("net_started"),
                         value.get("net_ended"), 5_000_000_000)


def witness(workspace: Path, token: str, deadline_seconds: float) -> dict:
    before = snapshot()
    validate_snapshot(before)
    if before["status"].get("Threads") != "1" or len(list(Path("/proc/self/task").iterdir())) != 1:
        raise ProbeError("fresh witness must be single-threaded")
    if not hasattr(os, "unshare"):
        raise ProbeError("os.unshare unavailable; no substitute capability witness")
    os.unshare(os.CLONE_NEWUSER)
    positive = snapshot()
    validate_snapshot(positive)
    if before["userns"] == positive["userns"] or before["netns"] != positive["netns"]:
        raise ProbeError("NEWUSER positive control namespace mismatch")
    if not int(positive["status"]["CapEff"], 16) & CAP_SYS_ADMIN:
        raise ProbeError("NEWUSER did not yield namespace-local CAP_SYS_ADMIN")
    record = {"token": token, "namespace_pid": os.getpid(), "before": before, "after_user": positive}
    validate_witness_ready(record)
    write_json(workspace / "witness-ready.json", record)
    deadline = time.monotonic() + deadline_seconds
    while True:
        if time.monotonic() >= deadline:
            raise ProbeError("controller PID handshake deadline exceeded")
        try:
            release = read_json(workspace / "witness-release.json")
        except FileNotFoundError:
            time.sleep(0.01)
            continue
        if release != {"token": token, "namespace_pid": os.getpid()}:
            raise ProbeError("invalid controller PID handshake")
        break
    # Deliberately no exec, UID/GID-map write, setns or privilege elevation
    # between the NEWUSER positive witness and the NEWNET capability use.
    try:
        record["net_audit_clock"] = begin_audit_clock(5_000_000_000)
        record["net_started"] = {"utc_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}
        try:
            os.unshare(os.CLONE_NEWNET)
            record["net_errno"] = 0
        except OSError as exc:
            record["net_errno"] = exc.errno
        record["net_ended"] = {"utc_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}
        end_audit_clock(record["net_audit_clock"])
    except ProbeError as exc:
        record["error"] = str(exc)
        write_json(workspace / "witness-result.json", record)
        raise
    record["after_net"] = snapshot()
    write_json(workspace / "witness-result.json", record)
    validate_witness(record)
    return record


def run_payload(workspace: Path, token: str, ip: str, deadline: float) -> dict:
    initial = snapshot()
    validate_snapshot(initial)
    write_json(workspace / "initial.json", initial)
    reexec = bounded_command(
        ["/usr/bin/python3", str(workspace / "probe.py"), "reexec", "--workspace", str(workspace)], 5
    )
    if reexec.get("error") or reexec["returncode"]:
        raise ProbeError("fresh Python exec failed: " + json.dumps(reexec))
    child = read_json(workspace / "reexec.json")
    validate_snapshot(child)
    network = network_snapshot(ip)
    child_run = bounded_command(
        [
            "/usr/bin/python3",
            str(workspace / "probe.py"),
            "witness",
            "--workspace",
            str(workspace),
            "--token",
            token,
            "--deadline",
            str(deadline),
        ],
        min(30, deadline + 2),
    )
    if child_run.get("error") or child_run["returncode"]:
        raise ProbeError("capability witness command failed: " + json.dumps(child_run))
    return {
        "initial": initial,
        "reexec": child,
        "reexec_command": reexec,
        "network": network,
        "witness": read_json(workspace / "witness-result.json"),
        "witness_command": child_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("payload", "reexec", "witness"))
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--token", default="")
    parser.add_argument("--ip", default="/usr/sbin/ip")
    parser.add_argument("--deadline", type=float, default=15)
    args = parser.parse_args()
    if not 0 < args.deadline <= 20:
        parser.error("deadline must be between 0 and 20 seconds")
    output = {
        "payload": "payload-result.json",
        "reexec": "reexec.json",
        "witness": "witness-result.json",
    }[args.mode]
    try:
        if args.mode == "reexec":
            result = snapshot()
            validate_snapshot(result)
        elif args.mode == "witness":
            result = witness(args.workspace, args.token, args.deadline)
        else:
            result = run_payload(args.workspace, args.token, args.ip, args.deadline)
        write_json(args.workspace / output, result)
        return 0
    except (OSError, ProbeError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        write_json(args.workspace / (args.mode + "-error.json"), {"error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
