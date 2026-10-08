"""Bounded production-sandbox qualification probes; never loads policy.

All entry points require the ordinary non-root runner. Failures preserve bounded
records and stop qualification. Importing this module performs no probes.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import select
import shutil
import stat
import tempfile
import time

from .payload import (
    ProbeError,
    begin_audit_clock,
    bounded_command,
    command_stdout_bytes,
    decode_json,
    end_audit_clock,
    read_bytes,
    read_json,
    read_status,
    snapshot,
    validate_audit_clock,
    validate_network,
    validate_snapshot,
    validate_witness,
    validate_witness_ready,
    write_json,
)

PRODUCTION_PATH = "/usr/bin:/bin"
AUDIT_LIMIT = 65536
INFO_LIMIT = 4096
_AUDIT_FIELD = re.compile(r'(?<!\S)([a-zA-Z_][a-zA-Z_0-9]*)=("(?:[^"\\]|\\.)*"|[^\s]+)')
_AUDIT_EVENT = re.compile(r"\baudit\(([0-9]+)\.([0-9]+):([0-9]+)\)")


def _production_types():
    # Deferred to keep pure parsers and unit tests independent of runtime setup.
    from code_forge.mutation_engines.isolate import SandboxSpec, Supervisor, SupervisorThread

    return SandboxSpec, Supervisor, SupervisorThread


def require_runner() -> dict:
    caller = snapshot()
    if any(caller[name] == 0 for name in ("uid", "euid", "gid", "egid")):
        raise ProbeError("qualification probes must run as the normal non-root runner")
    if caller["uid"] != caller["euid"] or caller["gid"] != caller["egid"]:
        raise ProbeError("real and effective runner identities differ")
    if caller["label"] != "unconfined":
        raise ProbeError("caller must be unconfined")
    for name in ("CapEff", "CapPrm", "CapInh", "CapAmb"):
        if int(caller["status"].get(name, "-1"), 16) != 0:
            raise ProbeError("caller has unexpected capability grants")
    if caller["status"].get("NoNewPrivs") != "0":
        raise ProbeError("caller has an unexpected inherited NoNewPrivs restriction")
    resolved = shutil.which("bwrap", path=PRODUCTION_PATH)
    if resolved != "/usr/bin/bwrap" or Path(resolved).resolve() != Path("/usr/bin/bwrap"):
        raise ProbeError("production PATH must resolve exactly /usr/bin/bwrap")
    details = Path(resolved).stat()
    if not stat.S_ISREG(details.st_mode) or details.st_uid != 0 or details.st_mode & 0o6022:
        raise ProbeError("bwrap must be a root-owned non-setid, non-writable ELF")
    with open(resolved, "rb") as binary:
        if binary.read(4) != b"\x7fELF":
            raise ProbeError("bwrap is not the admitted ELF")
    try:
        capabilities = os.getxattr(resolved, "security.capability")
    except OSError as exc:
        import errno

        if exc.errno != errno.ENODATA:
            raise ProbeError("cannot establish absence of bwrap file capabilities") from exc
    else:
        if capabilities:
            raise ProbeError("bwrap has file capabilities")
    caller["bwrap"] = resolved
    return caller


def production_probe_argv() -> list[str]:
    """Exact original linux-tests.yml preflight; no metadata instrumentation."""
    command = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-uts",
        "--unshare-ipc",
        "--clearenv",
    ]
    for path in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc"):
        command.extend(("--ro-bind", path, path))
    command.extend(
        (
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",  # noqa: S108 - private sandbox tmpfs
            "--tmpfs",
            "/workspace",
            "--chdir",
            "/workspace",
            "--",
            "/usr/bin/true",
        )
    )
    return command


def parse_info_record(raw: bytes) -> dict:
    value = decode_json(raw, INFO_LIMIT)
    if not isinstance(value, dict) or type(value.get("child-pid")) is not int or value["child-pid"] <= 0:
        raise ProbeError("info FD needs one complete object with a positive integer child-pid")
    return value


def _utc_for_journal(value: int) -> str:
    stamp = datetime.datetime.fromtimestamp(value // 1_000_000_000, datetime.timezone.utc)
    return stamp.strftime("%Y-%m-%d %H:%M:%S") + f".{value % 1_000_000_000 // 1000:06d} UTC"


def read_kernel_audit(start_ns: int, end_ns: int, pid: int) -> list[str]:
    """Only filtered read-only kernel audit; no unbounded journal export."""
    if not 0 < start_ns <= end_ns or end_ns - start_ns > 60_000_000_000 or pid <= 0:
        raise ProbeError("invalid bounded audit request")
    # The query is widened by one millisecond only for journal timestamp
    # resolution. Validation below checks the audit's own exact event interval.
    argv = [
        "/usr/bin/sudo",
        "-n",
        "--",
        "/usr/bin/timeout",
        "--signal=KILL",
        "4s",
        "/usr/bin/journalctl",
        "-k",
        "--no-pager",
        "--output=json",
        "--since=" + _utc_for_journal(start_ns - 1_000_000),
        "--until=" + _utc_for_journal(end_ns + 1_000_000),
        '--grep=apparmor="DENIED".*(bwrap|unprivileged_userns|userns_create)',
    ]
    result = bounded_command(argv, 5, limit=AUDIT_LIMIT)
    if result.get("error") or result["returncode"]:
        raise ProbeError("filtered kernel audit unavailable: " + json.dumps(result, sort_keys=True))
    records = []
    for line in command_stdout_bytes(result, AUDIT_LIMIT).splitlines():
        entry = decode_json(line, AUDIT_LIMIT)
        if (
            not isinstance(entry, dict)
            or entry.get("_TRANSPORT") != "kernel"
            or not isinstance(entry.get("MESSAGE"), str)
        ):
            raise ProbeError("audit source is not a complete kernel journal record")
        records.append(entry["MESSAGE"])
    return records


def _audit_fields(raw: str) -> tuple[dict, int, int]:
    fields = {}
    for match in _AUDIT_FIELD.finditer(raw):
        key, value = match.groups()
        if key in fields:
            raise ProbeError("duplicate audit field")
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise ProbeError("invalid quoted audit field") from exc
        fields[key] = value
    events = list(_AUDIT_EVENT.finditer(raw))
    if len(events) != 1:
        raise ProbeError("missing or ambiguous audit timestamp")
    seconds, fraction, serial = events[0].groups()
    if len(fraction) != 3:
        raise ProbeError("invalid audit timestamp precision")
    # Linux audit prints the coarse clock truncated to exactly milliseconds.
    event_ns = int(seconds) * 1_000_000_000 + int(fraction) * 1_000_000
    return fields, event_ns, 1_000_000


def validate_audit(
    records: list[str],
    *,
    pid: int,
    audit_clock: dict,
    started: dict,
    ended: dict,
    capability: int,
    capname: str,
    profiles: tuple[str, ...],
    max_interval_ns: int = 30_000_000_000,
) -> str:
    if type(pid) is not int or pid <= 0:
        raise ProbeError("invalid or unbounded audit interval/PID")
    validate_audit_clock(audit_clock, started, ended, max_interval_ns)
    first_ms, last_ms = (audit_clock[key] // 1_000_000 for key in ("before_ns", "after_ns"))
    if (
        not isinstance(records, list)
        or not records
        or any(not isinstance(item, str) for item in records)
        or sum(len(item.encode()) for item in records) > AUDIT_LIMIT
    ):
        raise ProbeError("missing or oversized filtered audit evidence")
    candidates = []
    for raw in records:
        if not isinstance(raw, str) or 'apparmor="DENIED"' not in raw:
            raise ProbeError("audit input is not filtered AppArmor denial evidence")
        fields, event_ns, _ = _audit_fields(raw)
        if fields.get("pid") != str(pid):
            continue
        if not first_ms <= event_ns // 1_000_000 <= last_ms:
            raise ProbeError("matching PID audit lies outside the operation interval")
        if fields.get("apparmor") != "DENIED" or fields.get("operation") != "capable":
            raise ProbeError("competing audit denial for the witness PID")
        if (
            fields.get("profile") not in profiles
            or fields.get("capability") != str(capability)
            or fields.get("capname") != capname
        ):
            raise ProbeError("competing capability/profile audit for the witness PID")
        candidates.append(raw)
    if len(candidates) != 1:
        raise ProbeError("missing or ambiguous attributable capability denial")
    return candidates[0]


def _evidence_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir() or path.is_symlink() or path.stat().st_uid != os.getuid():
        raise ProbeError("evidence directory is not controller-owned")
    return path.absolute()


def run_positive_probe(evidence_dir: str | Path, *, timeout: float = 10) -> dict:
    evidence = _evidence_dir(Path(evidence_dir))
    caller = require_runner()
    record = bounded_command(production_probe_argv(), timeout, env={"PATH": PRODUCTION_PATH})
    record["caller"] = caller
    write_json(evidence / "positive-probe.json", record)
    if record.get("error") or record["returncode"] != 0:
        raise ProbeError("original uninstrumented production probe did not pass")
    return record


def run_negative_control(evidence_dir: str | Path, *, timeout: float = 10, audit_reader=None) -> dict:
    """Collect only; call validate_negative_control before the refusal gate.

    A zero return code is recorded as already_capable, allowing the controller
    to choose NOT_NEEDED without pretending the policy mechanism qualified.
    """
    evidence = _evidence_dir(Path(evidence_dir))
    caller = require_runner()
    argv = production_probe_argv()
    with tempfile.TemporaryFile(mode="w+b", dir=evidence) as info:
        details = os.fstat(info.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid() or details.st_mode & 0o077:
            raise ProbeError("info FD is not a private controller-owned regular file")
        separator = argv.index("--")
        replica = argv[:separator] + ["--info-fd", str(info.fileno())] + argv[separator:]
        audit_clock = begin_audit_clock(30_000_000_000)
        record = bounded_command(
            replica, timeout, pass_fds=(info.fileno(),), env={"PATH": PRODUCTION_PATH}
        )
        record["audit_clock"] = audit_clock
        try:
            end_audit_clock(audit_clock)
        except ProbeError as exc:
            record["error"] = (record.get("error", "") + "; audit clock: " + str(exc)).lstrip("; ")
        info.seek(0)
        raw = info.read(INFO_LIMIT + 1)
        record.update(
            caller=caller,
            original_argv=argv,
            info_raw_hex=raw.hex(),
            info_bytes=os.fstat(info.fileno()).st_size,
            already_capable=record["returncode"] == 0,
        )
    write_json(evidence / "negative-command.json", record)
    if record.get("error"):
        raise ProbeError("negative-control command failed to produce bounded evidence")
    validate_audit_clock(record["audit_clock"], record.get("started"), record.get("ended"), 30_000_000_000)
    if record["info_bytes"] > INFO_LIMIT or len(raw) != record["info_bytes"]:
        raise ProbeError("oversized or incomplete info FD record")
    record["info"] = parse_info_record(raw)
    if not record["already_capable"]:
        reader = audit_reader or read_kernel_audit
        record["audit"] = reader(
            record["started"]["utc_ns"], record["ended"]["utc_ns"], record["info"]["child-pid"]
        )
    write_json(evidence / "negative-probe.json", record)
    return record


def validate_negative_audit(records: list[str], *, pid: int, audit_clock: dict, started: dict, ended: dict) -> None:
    """One net_admin refusal, with at most the reviewed bwrap setpcap companion.

    This is negative-control-specific. Each raw denial still passes the shared
    attribution validator; the post-load sys_admin witness uses it unchanged.
    """
    if (type(records) is not list or not 1 <= len(records) <= 2
            or any(type(raw) is not str for raw in records)
            or sum(len(raw.encode()) for raw in records) > AUDIT_LIMIT):
        raise ProbeError("missing or oversized negative-control audit evidence")
    seen_caps = set()
    seen_events = set()
    ordered_events = {}
    required = {"type", "apparmor", "operation", "class", "profile", "pid", "comm", "capability", "capname"}
    for raw in records:
        fields, event_ns, _ = _audit_fields(raw)
        # Reject malformed fragments, additional records, and unknown fields;
        # a field-regex match alone is not permission to discard surrounding text.
        remainder = _AUDIT_FIELD.sub("", _AUDIT_EVENT.sub("", raw))
        if (re.sub(r"\s+", " ", remainder).strip() != "audit: :"
                or set(fields) != required
                or fields["type"] != "1400" or fields["class"] != "cap"):
            raise ProbeError("malformed or unclassified negative-control audit record")
        if fields["pid"] != str(pid) or fields["comm"] != "bwrap":
            raise ProbeError("missing or ambiguous negative child audit attribution")
        capability = fields["capability"]
        capname = {"12": "net_admin", "8": "setpcap"}.get(capability)
        if capname is None or fields["capname"] != capname or capability in seen_caps:
            raise ProbeError("unexpected or duplicate negative-control capability denial")
        event = (event_ns, int(_AUDIT_EVENT.search(raw).group(3)))
        if event[1] in seen_events:
            raise ProbeError("ambiguous negative-control audit event identity")
        validate_audit([raw], pid=pid, audit_clock=audit_clock, started=started, ended=ended,
                       capability=int(capability), capname=capname, profiles=("unprivileged_userns",))
        seen_caps.add(capability)
        seen_events.add(event[1])
        ordered_events[capability] = event
    if "12" not in seen_caps:
        raise ProbeError("missing required negative-control net_admin denial")
    if "8" in ordered_events and not ordered_events["8"] < ordered_events["12"]:
        raise ProbeError("setpcap audit event does not precede net_admin refusal")


def validate_negative_control(record: dict, audit_records=None) -> None:
    if record.get("error") or type(record.get("returncode")) is not int or record["returncode"] != 1:
        raise ProbeError("negative control did not demonstrate the expected refusal")
    if record.get("stderr") != "bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted\n":
        raise ProbeError("negative control lacks expected RTM_NEWADDR EPERM refusal")
    try:
        raw = bytes.fromhex(record["info_raw_hex"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProbeError("missing raw info FD record") from exc
    info = parse_info_record(raw)
    if record.get("info_bytes") != len(raw) or record.get("info") != info:
        raise ProbeError("inconsistent info FD evidence")
    original = production_probe_argv()
    replica = record.get("argv", [])
    try:
        index = replica.index("--info-fd")
        if not replica[index + 1].isdigit() or int(replica[index + 1]) < 3:
            raise ProbeError("info FD is not a separate private descriptor")
        without_info = replica[:index] + replica[index + 2 :]
    except (ValueError, IndexError) as exc:
        raise ProbeError("missing info FD instrumentation") from exc
    if without_info != original or record.get("original_argv") != original:
        raise ProbeError("negative replica changed production flags")
    validate_negative_audit(
        record.get("audit") if audit_records is None else audit_records,
        pid=info["child-pid"],
        audit_clock=record.get("audit_clock"),
        started=record.get("started"),
        ended=record.get("ended"),
    )


def map_witness_pid(cgroup_path: Path, namespace_pid: int, *, proc_root: Path = Path("/proc")) -> dict:
    """Read the dedicated cgroup only; require a unique complete NSpid chain."""
    if type(namespace_pid) is not int or namespace_pid <= 0:
        raise ProbeError("invalid witness namespace PID")
    raw = read_bytes(cgroup_path / "cgroup.procs", 32768).decode("ascii")
    pids = raw.split()
    if (
        not pids
        or len(pids) > 64
        or len(set(pids)) != len(pids)
        or any(not pid.isdigit() or int(pid) <= 0 for pid in pids)
    ):
        raise ProbeError("invalid or ambiguous owned cgroup PID inventory")
    matches = []
    for pid in pids:
        try:
            status_raw = read_bytes(proc_root / pid / "status", 32768).decode("utf-8")
        except FileNotFoundError:
            # An exited sibling is not proof of an absent NSpid mapping. Retry
            # the whole bounded handshake rather than infer its identity.
            raise ProbeError("cgroup process vanished during PID mapping") from None
        status = read_status(status_raw)
        chain = status.get("NSpid", "").split()
        if (
            not chain
            or any(not item.isdigit() or int(item) <= 0 for item in chain)
            or int(chain[0]) != int(pid)
        ):
            raise ProbeError("missing or invalid host NSpid chain")
        if len(chain) >= 2 and int(chain[-1]) == namespace_pid:
            matches.append(
                {
                    "host_pid": int(pid),
                    "namespace_pid": namespace_pid,
                    "nspid": [int(item) for item in chain],
                    "status_raw": status_raw,
                }
            )
    if len(matches) != 1:
        raise ProbeError("witness PID mapping is missing or ambiguous in owned cgroup")
    # Second cgroup read binds the selected live PID to the owned cgroup at
    # release; no wrapper-PID arithmetic or unrestricted process-tree polling.
    if (
        str(matches[0]["host_pid"])
        not in read_bytes(cgroup_path / "cgroup.procs", 32768).decode("ascii").split()
    ):
        raise ProbeError("mapped witness left owned cgroup before release")
    return matches[0]


def _observer_pids(raw: str) -> list[int]:
    if type(raw) is not str or len(raw) > 32768:
        raise ProbeError("invalid owned mapping cgroup inventory")
    fields = raw.split()
    if (not 1 <= len(fields) <= 64 or len(set(fields)) != len(fields)
            or any(re.fullmatch(r"[1-9][0-9]{0,9}", item) is None for item in fields)):
        raise ProbeError("invalid owned mapping cgroup inventory")
    result = list(map(int, fields))
    if len(set(result)) != len(result):
        raise ProbeError("ambiguous owned mapping cgroup inventory")
    return result


def _observer_reader() -> dict:
    return {"pid": os.getpid(), "uid": os.getuid(), "euid": os.geteuid(),
            "gid": os.getgid(), "egid": os.getegid(),
            "userns": os.readlink("/proc/thread-self/ns/user")}


def _observer_status(raw: str) -> tuple[dict, list[int]]:
    if type(raw) is not str or len(raw) > 32768:
        raise ProbeError("invalid host mapping process status")
    status = read_status(raw)
    chain = status.get("NSpid", "").split()
    if not 2 <= len(chain) <= 32 or any(re.fullmatch(r"[1-9][0-9]{0,9}", x) is None for x in chain):
        raise ProbeError("invalid host mapping NSpid chain")
    return status, list(map(int, chain))


def _observer_process(pid: int, namespace_pid: int, proc_root: Path) -> dict:
    path = proc_root / str(pid)
    raw = read_bytes(path / "status", 32768).decode("utf-8")
    _, chain = _observer_status(raw)
    return {"host_pid": pid, "namespace_pid": namespace_pid, "nspid": chain,
            "status_raw": raw, "stat_raw": read_bytes(path / "stat", 4096).decode("utf-8"),
            "userns": os.readlink(path / "ns/user"), "pidns": os.readlink(path / "ns/pid")}


def _observer_identity(value: dict) -> tuple[int, int]:
    expected = {"host_pid", "namespace_pid", "nspid", "status_raw", "stat_raw", "userns", "pidns"}
    if type(value) is not dict or set(value) != expected:
        raise ProbeError("missing host mapping process identity")
    for key in ("host_pid", "namespace_pid"):
        if type(value[key]) is not int or value[key] <= 0:
            raise ProbeError("invalid host mapping process PID")
    status, chain = _observer_status(value["status_raw"])
    if (type(value["nspid"]) is not list or any(type(x) is not int for x in value["nspid"])
            or value["nspid"] != chain or chain[0] != value["host_pid"]
            or chain[-1] != value["namespace_pid"]):
        raise ProbeError("host mapping process NSpid attribution changed")
    raw = value["stat_raw"]
    if type(raw) is not str or len(raw) > 4096 or not raw.startswith(str(value["host_pid"]) + " ("):
        raise ProbeError("invalid host mapping process stat")
    tail = raw[raw.rfind(")") + 1:].split()
    if (len(tail) < 20 or tail[0] not in {"R", "S", "D", "T", "t", "K", "W", "P", "I"}
            or any(re.fullmatch(r"[1-9][0-9]*", x) is None for x in (tail[1], tail[19]))
            or status.get("PPid") != tail[1] or status.get("Pid") != str(value["host_pid"])):
        raise ProbeError("invalid host mapping process incarnation/parent")
    for key, prefix in (("userns", "user"), ("pidns", "pid")):
        if type(value[key]) is not str or re.fullmatch(prefix + r":\[[1-9][0-9]*\]", value[key]) is None:
            raise ProbeError("invalid host mapping namespace identity")
    return int(tail[1]), int(tail[19])


def _single_map(raw: str, expected: list[int]) -> None:
    if type(raw) is not str or len(raw) > 4096 or len(raw.splitlines()) != 1:
        raise ProbeError("invalid single identity mapping")
    fields = raw.split()
    if (len(fields) != 3 or any(re.fullmatch(r"[0-9]{1,10}", x) is None for x in fields)
            or list(map(int, fields)) != expected):
        raise ProbeError("boundary requires the exact single ordinary caller mapping")


def validate_original_host_mapping(value: dict, initial: dict, caller: dict, witness: dict,
                                   cgroup_path: str) -> None:
    expected = {"reader_before", "reader_after", "process_before", "process_after", "parent_status_raw",
                "uid_map_raw", "gid_map_raw", "cgroup_path", "cgroup_procs_before_raw",
                "cgroup_procs_after_raw", "pidfd"}
    if type(value) is not dict or set(value) != expected:
        raise ProbeError("missing original payload host-view mapping evidence")
    if any(type(initial.get(key)) is not int or initial[key] <= 0 for key in ("pid", "ppid", "uid", "gid")):
        raise ProbeError("invalid original payload namespace PID or identity")
    reader = {key: caller.get(key) for key in ("pid", "uid", "euid", "gid", "egid", "userns")}
    if (any(type(reader[k]) is not int or reader[k] <= 0 for k in ("pid", "uid", "euid", "gid", "egid"))
            or reader["uid"] != reader["euid"] or reader["gid"] != reader["egid"]):
        raise ProbeError("invalid admitted host mapping reader")
    for phase in ("reader_before", "reader_after"):
        actual = value[phase]
        if (type(actual) is not dict or set(actual) != set(reader) or actual != reader
                or any(type(actual[k]) is not int for k in ("pid", "uid", "euid", "gid", "egid"))):
            raise ProbeError("host mapping reader identity or namespace changed")
    if (type(value["pidfd"]) is not dict or set(value["pidfd"]) != {"supported", "alive_before", "alive_after"}
            or any(x is not True for x in value["pidfd"].values())):
        raise ProbeError("original payload pidfd lifetime was not established")
    if type(cgroup_path) is not str or not cgroup_path.startswith("/") or value["cgroup_path"] != cgroup_path:
        raise ProbeError("original payload cgroup attribution changed")
    before, after = value["process_before"], value["process_after"]
    parent_pid, start = _observer_identity(before)
    if _observer_identity(after) != (parent_pid, start):
        raise ProbeError("original payload incarnation or parent changed")
    for key in ("host_pid", "namespace_pid", "nspid", "userns", "pidns"):
        if before[key] != after[key]:
            raise ProbeError("original payload namespace or PID changed")
    if (before["namespace_pid"] != initial.get("pid") or before["userns"] != initial.get("userns")
            or before["pidns"] != initial.get("pidns") or before["userns"] == reader["userns"]
            or before["pidns"] == caller.get("pidns")):
        raise ProbeError("original payload namespace attribution mismatch")
    for observation in (before, after):
        status = read_status(observation["status_raw"])
        for key, kind in (("Uid", "uid"), ("Gid", "gid")):
            if status.get(key, "").split() != [str(caller[kind])] * 4:
                raise ProbeError("host observed payload identity is not the ordinary caller")
        if status.get("NoNewPrivs") != "1" or any(
                status.get(k) != "0000000000000000" for k in ("CapEff", "CapPrm", "CapInh", "CapAmb")):
            raise ProbeError("host observed original payload confinement changed")
    parent_status, parent_chain = _observer_status(value["parent_status_raw"])
    if (parent_status.get("Pid") != str(parent_pid) or parent_chain[0] != parent_pid
            or len(parent_chain) != len(before["nspid"])
            or parent_chain[-1] != initial.get("ppid")):
        raise ProbeError("original payload namespace parent attribution mismatch")
    witness_status, witness_chain = _observer_status(witness.get("status_raw"))
    if (any(type(witness.get(key)) is not int or witness[key] <= 0 for key in ("host_pid", "namespace_pid"))
            or type(witness.get("nspid")) is not list or any(type(x) is not int for x in witness["nspid"])
            or witness["nspid"] != witness_chain
            or witness_status.get("Pid") != str(witness.get("host_pid"))
            or witness_chain[0] != witness.get("host_pid") or witness_chain[-1] != witness.get("namespace_pid")
            or len(witness_chain) != len(before["nspid"])
            or witness_status.get("PPid") != str(before["host_pid"])):
        raise ProbeError("live witness parent does not identify original payload")
    for phase in ("cgroup_procs_before_raw", "cgroup_procs_after_raw"):
        pids = _observer_pids(value[phase])
        if any(pid not in pids for pid in (before["host_pid"], parent_pid, witness["host_pid"])):
            raise ProbeError("original payload family left its owned cgroup")
    for kind in ("uid", "gid"):
        _single_map(value[kind + "_map_raw"], [initial[kind], caller[kind], 1])


def observe_original_host_mapping(cgroup_path: Path, initial: dict, caller: dict, witness: dict,
                                  *, proc_root: Path = Path("/proc")) -> dict:
    """Only the paused original payload; never the witness's nested user namespace."""
    selected = map_witness_pid(cgroup_path, initial.get("pid"), proc_root=proc_root)
    pid = selected["host_pid"]
    if not hasattr(os, "pidfd_open"):
        raise ProbeError("pidfd unavailable for original payload mapping")
    fd = os.pidfd_open(pid, 0)
    try:
        if os.get_inheritable(fd):
            raise ProbeError("original payload pidfd must remain private")
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        if poller.poll(0):
            raise ProbeError("original payload exited before host mapping read")
        value = {"reader_before": _observer_reader(), "cgroup_path": str(cgroup_path),
                 "cgroup_procs_before_raw": read_bytes(cgroup_path / "cgroup.procs", 32768).decode("ascii"),
                 "process_before": _observer_process(pid, initial["pid"], proc_root)}
        parent_pid, _ = _observer_identity(value["process_before"])
        if parent_pid not in _observer_pids(value["cgroup_procs_before_raw"]):
            raise ProbeError("original payload parent is outside owned cgroup")
        value["parent_status_raw"] = read_bytes(proc_root / str(parent_pid) / "status", 32768).decode("utf-8")
        for kind in ("uid", "gid"):
            value[kind + "_map_raw"] = read_bytes(proc_root / str(pid) / (kind + "_map"), 4096).decode("ascii")
        value["process_after"] = _observer_process(pid, initial["pid"], proc_root)
        value["cgroup_procs_after_raw"] = read_bytes(cgroup_path / "cgroup.procs", 32768).decode("ascii")
        value["reader_after"] = _observer_reader()
        if poller.poll(0):
            raise ProbeError("original payload exited during host mapping read")
        value["pidfd"] = {"supported": True, "alive_before": True, "alive_after": True}
        validate_original_host_mapping(value, initial, caller, witness, str(cgroup_path))
        return value
    finally:
        os.close(fd)


def validate_initial_mapping(process: dict, caller: dict) -> None:
    """Retain only the original ordinary-user mapping proof, not Python inventory."""
    mapping = process.get("identity_mapping", {})
    host_mapping = caller.get("identity_mapping", {})
    expected_keys = {"uid_map_raw", "gid_map_raw", "overflowuid_raw", "overflowgid_raw"}
    if type(mapping) is not dict or set(mapping) != expected_keys or type(host_mapping) is not dict:
        raise ProbeError("missing actual boundary identity mapping")
    for kind in ("uid", "gid"):
        raw = mapping[kind + "_map_raw"]
        if type(raw) is not str or len(raw) > 4096 or len(raw.splitlines()) != 1:
            raise ProbeError("invalid initial/reexec identity map")
        fields = raw.split()
        if len(fields) != 3 or any(not field.isascii() or not field.isdecimal() for field in fields):
            raise ProbeError("invalid initial/reexec identity map fields")
        # A self-reader sees the immediate devpts parent namespace, not host IDs.
        # The separate live host-view proof is mandatory in the whole validator.
        if list(map(int, fields)) != [process[kind], 0, 1]:
            raise ProbeError("boundary requires the single ordinary caller mapping")
        field = "overflow" + kind + "_raw"
        overflow, host_overflow = mapping[field], host_mapping.get(field)
        for value in (overflow, host_overflow):
            if type(value) is not str or re.fullmatch(r"[1-9][0-9]{0,9}\n?", value) is None:
                raise ProbeError("invalid or missing boundary overflow identity")
        if (int(overflow) > 2**32 - 1 or int(overflow) != int(host_overflow)
                or int(overflow) in {process[kind], caller[kind]}):
            raise ProbeError("ambiguous or changed boundary overflow identity")
    for field in ("CapEff", "CapPrm", "CapInh", "CapAmb"):
        if int(process["status"][field], 16) != 0:
            raise ProbeError("initial/reexec payload inherited capability grants")


def validate_boundary_record(record: dict) -> None:
    result = record.get("payload", {})
    if record.get("returncode") != 0 or record.get("error"):
        raise ProbeError("production sandbox payload did not complete successfully")
    for key in ("initial", "reexec"):
        validate_snapshot(result.get(key, {}))
        validate_initial_mapping(result[key], record.get("caller", {}))
        for identity in ("uid", "euid", "gid", "egid"):
            if result[key][identity] != record["caller"][identity]:
                raise ProbeError("payload real/effective identity changed")
    initial, reexec = result["initial"], result["reexec"]
    validate_original_host_mapping(record.get("original_host_mapping"), initial, record["caller"],
                                   record.get("witness_mapping", {}), record.get("cgroup_path"))
    if initial["identity_mapping"] != reexec["identity_mapping"]:
        raise ProbeError("initial/reexec identity mapping changed")
    if initial["pid"] == reexec["pid"] or reexec.get("ppid") != initial["pid"]:
        raise ProbeError("fresh Python exec was not a separate direct child")
    if any(initial[key] != reexec[key] for key in ("userns", "netns", "pidns")):
        raise ProbeError("re-exec child namespace identity changed")
    expected = ["/usr/bin/python3", "/workspace/probe.py", "reexec", "--workspace", "/workspace"]
    if (
        result.get("reexec_command", {}).get("argv") != expected
        or result["reexec_command"].get("returncode") != 0
        or result["reexec_command"].get("error")
    ):
        raise ProbeError("missing successful real Python exec command")
    validate_network(result.get("network", {}), record["caller"]["netns"], initial["netns"])
    witness = result.get("witness", {})
    validate_witness(witness)
    child_run = result.get("witness_command", {})
    expected_child_prefix = [
        "/usr/bin/python3",
        "/workspace/probe.py",
        "witness",
        "--workspace",
        "/workspace",
        "--token",
        record.get("token"),
        "--deadline",
    ]
    child_argv = child_run.get("argv", [])
    if (
        len(child_argv) != 9
        or child_argv[:8] != expected_child_prefix
        or child_run.get("returncode") != 0
        or child_run.get("error")
    ):
        raise ProbeError("missing successful fresh witness Python command")
    try:
        if not 0 < float(child_argv[8]) <= 20:
            raise ValueError("deadline outside bound")
    except (TypeError, ValueError) as exc:
        raise ProbeError("unbounded witness handshake") from exc
    if witness["before"]["ppid"] != initial["pid"] or any(
        witness["before"][key] != initial[key] for key in ("userns", "netns", "pidns")
    ):
        raise ProbeError("witness was not the fresh sandbox child")
    mapping = record.get("witness_mapping", {})
    if mapping.get("namespace_pid") != witness["namespace_pid"] or witness.get("token") != record.get(
        "token"
    ):
        raise ProbeError("witness does not match the controller PID handshake")
    if mapping.get("host_pid", 0) <= 0:
        raise ProbeError("missing witness host PID")
    validate_audit(
        record.get("audit"),
        pid=mapping["host_pid"],
        audit_clock=witness.get("net_audit_clock"),
        started=witness["net_started"],
        ended=witness["net_ended"],
        capability=21,
        capname="sys_admin",
        profiles=("unpriv_bwrap", "bwrap//&unpriv_bwrap"),
        max_interval_ns=5_000_000_000,
    )


def _diagnostic_time():
    return {"utc_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}


def _diagnostic_read(path, limit):
    """Read only a bounded regular kernel file; never follow its final link."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("nonregular diagnostic file")
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("diagnostic byte bound")
    return raw.decode("ascii")


def _diagnostic_error(error):
    return {"type": type(error).__name__, "errno": getattr(error, "errno", None),
            "reason": str(error)[:160] if type(error) is ValueError else None}


def _diagnostic_metadata(path):
    path = Path(path)
    result = {"path": str(path)}
    try:
        if os.path.realpath(path, strict=True) != str(path):
            raise ValueError("noncanonical diagnostic path")
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError("unexpected diagnostic file type")
        result.update(uid=info.st_uid, gid=info.st_gid, mode=info.st_mode,
                      device=info.st_dev, inode=info.st_ino,
                      effective_write_access=os.access(path, os.W_OK, effective_ids=True))
    except (OSError, ValueError) as error:
        result["error"] = _diagnostic_error(error)
    return result


def _diagnostic_cgroup_paths(raw, mountinfo, root, destination):
    """Only map the current view when one root-mounted cgroup2 is unambiguous."""
    lines = raw.splitlines()
    if len(lines) != 1 or not lines[0].startswith("0::/"):
        raise ValueError("ambiguous source cgroup")
    source = lines[0][3:]
    for value in (source, root, destination):
        if type(value) is not str or len(value) > 4096 or not value.startswith("/"):
            raise ValueError("invalid cgroup path")
        if (any(ord(char) < 33 or ord(char) > 126 or char == "\\" for char in value)
                or (value != "/" and any(part in {"", ".", ".."} for part in value.split("/")[1:]))
                or len(Path(value).parts) > 32):
            raise ValueError("noncanonical or overlong cgroup path")
    entries = [line for line in mountinfo.splitlines() if " - cgroup2 " in line]
    if len(entries) != 1:
        raise ValueError("ambiguous cgroup2 mount")
    halves = entries[0].split(" - ")
    if len(halves) != 2:
        raise ValueError("malformed cgroup2 mount separator")
    fields, filesystem = halves[0].split(), halves[1].split()
    if (len(fields) < 6 or len(filesystem) != 3 or filesystem[0] != "cgroup2"
            or re.fullmatch(r"[1-9][0-9]*", fields[0]) is None
            or re.fullmatch(r"[0-9]+", fields[1]) is None
            or re.fullmatch(r"[0-9]+:[0-9]+", fields[2]) is None):
        raise ValueError("malformed cgroup2 mount fields")
    if fields[3:5] != ["/", "/sys/fs/cgroup"]:
        raise ValueError("unresolved cgroup2 mount root")
    mount = "/sys/fs/cgroup"
    if not root.startswith(mount + "/") or str(Path(destination).parent) != root:
        raise ValueError("unexpected destination subtree")
    view_source = mount + source if source != "/" else mount
    common = os.path.commonpath([view_source, destination])
    if common != mount and not common.startswith(mount + "/"):
        raise ValueError("common ancestor outside cgroup2 mount")
    return {"source": view_source, "destination": destination,
            "destination_parent": root, "common_ancestor": common}


def cgroup_placement_diagnostics(root, destination):
    """Bounded read-only observations, never an admission or placement bypass."""
    import threading

    pid, tid = os.getpid(), threading.get_native_id()
    result = {"schema_version": 1, "observational_only": True, "pid": pid, "native_tid": tid,
              "observed": _diagnostic_time(), "namespaces": {}, "mapping": "unknown"}
    try:
        process = _diagnostic_read(f"/proc/{pid}/cgroup", 4096)
        thread = _diagnostic_read(f"/proc/{pid}/task/{tid}/cgroup", 4096)
        mountinfo = _diagnostic_read(f"/proc/{pid}/task/{tid}/mountinfo", 65536)
        result.update(process_cgroup_raw=process, thread_cgroup_raw=thread,
                      cgroup2_mountinfo=[line for line in mountinfo.splitlines() if " - cgroup2 " in line])
        for name in ("cgroup", "pid", "user", "mnt"):
            target = os.readlink(f"/proc/{pid}/task/{tid}/ns/{name}")
            if len(target) > 128:
                raise ValueError("namespace link bound")
            result["namespaces"][name] = target
        if process != thread:
            raise ValueError("different process and thread cgroup views")
        paths = _diagnostic_cgroup_paths(thread, mountinfo, root, destination)
        result["mapping"] = "current-cgroup2-mount-view"
        result["locations"] = {}
        for role, path in paths.items():
            item = {"directory": _diagnostic_metadata(path),
                    "procs": _diagnostic_metadata(path + "/cgroup.procs"), "state": {}}
            # No ancestry walk: just these four identified directories and
            # three fixed files each. A missing fresh leaf is ordinary evidence.
            for name in ("cgroup.type", "cgroup.controllers", "cgroup.subtree_control"):
                try:
                    file = Path(path) / name
                    if os.path.realpath(file, strict=True) != str(file):
                        raise ValueError("noncanonical cgroup state file")
                    item["state"][name] = {"raw": _diagnostic_read(file, 4096)}
                except (OSError, ValueError, UnicodeError) as error:
                    item["state"][name] = {"error": _diagnostic_error(error)}
            result["locations"][role] = item
    except (OSError, ValueError, UnicodeError) as error:
        result["error"] = _diagnostic_error(error)
    return result


def _owned_probe_supervisor(Supervisor):
    """Wrap only startup/cleanup ownership; all production execution is inherited."""
    import threading

    class OwnedProbeSupervisor(Supervisor):
        """Own cleanup across start publication without changing production internals."""

        def __init__(self, spec, root):
            super().__init__(spec, root)
            self.probe_lock = threading.Lock()
            self.probe_cancelled = False
            self.probe_start_complete = False
            self.probe_cleanup_claimed = False
            self.probe_cleanup_complete = False
            self.probe_cleanup_error = None
            self.probe_diagnostics = {}

        def _observe(self, key, callback):
            try:
                value = callback()
            except Exception as error:  # noqa: BLE001 - diagnostic built-in failures only
                # The controller's SIGTERM Cancelled is an application-defined
                # RuntimeError. Never swallow it, or any BaseException signal.
                if type(error).__module__ != "builtins":
                    raise
                value = {"observational_only": True, "error": _diagnostic_error(error)}
            self.probe_diagnostics[key] = value

        def start(self):
            try:
                with self.probe_lock:
                    cancelled = self.probe_cancelled
                if cancelled:
                    raise ProbeError("probe startup cancelled before owner entry")
                self._observe("placement_context", lambda: cgroup_placement_diagnostics(
                    str(Path(self.cgroup_path).parent), self.cgroup_path))
                self._observe("production_start_started", _diagnostic_time)
                super().start()
            finally:
                try:
                    # Read cached fields only: no extra wait/reap or production
                    # monkeypatch. Inherited start may include its own cleanup.
                    self._observe("production_start_ended", _diagnostic_time)
                    self._observe("partial_start", lambda: {
                        "payload_pid": getattr(self, "payload_pid", None),
                        "limits_readback": dict(getattr(self, "limits_readback", {})),
                        "gate_opened_monotonic_ns": getattr(self, "gate_opened_monotonic_ns", None),
                        "cached_child_exit": getattr(self._process, "_status", None),
                    })
                finally:
                    # Diagnostic errors/cancellation cannot skip ownership
                    # publication or transfer of cleanup to this start owner.
                    with self.probe_lock:
                        self.probe_start_complete = True
                        cleanup = self.probe_cancelled and not self.probe_cleanup_claimed
                        if cleanup:
                            self.probe_cleanup_claimed = True
                    if cleanup:
                        self.probe_cleanup()

        def probe_cleanup(self):
            try:
                try:
                    self._observe("cleanup_started", _diagnostic_time)
                finally:
                    super().teardown()
                # Production teardown deliberately suppresses some wait/removal
                # failures. Its return alone is not proof of final cleanup.
                if self._process is not None and type(self._process.poll()) is not int:
                    raise ProbeError("owned probe child is not confirmed terminal")
                try:
                    os.lstat(self.cgroup_path)
                except FileNotFoundError:
                    pass
                else:
                    raise ProbeError("owned probe cgroup still exists after teardown")
            except Exception as exc:  # noqa: BLE001 - report owner-thread cleanup failures
                self.probe_cleanup_error = str(exc)
            else:
                self.probe_cleanup_complete = True
            finally:
                self._observe("cleanup_ended", _diagnostic_time)

        def cancel_probe_start(self):
            with self.probe_lock:
                self.probe_cancelled = True
                cleanup = self.probe_start_complete and not self.probe_cleanup_claimed
                if cleanup:
                    self.probe_cleanup_claimed = True
            if cleanup:
                self.probe_cleanup()

    return OwnedProbeSupervisor


def _finish_probe(supervisor, thread, thread_started: bool, record: dict) -> None:
    # Production teardown sets an idempotence flag before checking _process.
    # A pending start must therefore transfer cleanup to its owner, never call
    # teardown early and let a later published child outlive that flag.
    supervisor.cancel_probe_start()
    if thread_started:
        thread.join(timeout=10)
        if thread.is_alive():
            record["error"] = "probe supervisor thread did not terminate"
        if not supervisor.probe_cleanup_complete:
            record["error"] = "probe cleanup did not complete"
    if supervisor.probe_cleanup_error is not None:
        record["error"] = "probe teardown failed: " + supervisor.probe_cleanup_error
    record["startup_diagnostics"] = dict(supervisor.probe_diagnostics)
    record["cleanup"] = {
        "cancelled": supervisor.probe_cancelled,
        "start_complete": supervisor.probe_start_complete,
        "complete": supervisor.probe_cleanup_complete,
        "error": supervisor.probe_cleanup_error,
    }


def run_boundary_probe(
    cgroup_root: str, evidence_dir: str | Path, *, timeout: float = 30, audit_reader=None
) -> dict:
    if not 5 <= timeout <= 30:
        raise ProbeError("boundary timeout must be between 5 and 30 seconds")
    evidence = _evidence_dir(Path(evidence_dir))
    caller = require_runner()
    ip = shutil.which("ip", path=PRODUCTION_PATH)
    if not ip or not Path(ip).is_file():
        raise ProbeError("ip missing from actual sandbox PATH")
    SandboxSpec, Supervisor, SupervisorThread = _production_types()
    token = secrets.token_hex(16)
    record = {
        "caller": caller,
        "token": token,
        "started": {"utc_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()},
    }
    with tempfile.TemporaryDirectory(prefix="boundary-workspace-", dir=evidence) as temporary:
        workspace = Path(temporary)
        shutil.copyfile(Path(__file__).with_name("payload.py"), workspace / "probe.py")
        command = (
            "/usr/bin/python3",
            "/workspace/probe.py",
            "payload",
            "--token",
            token,
            "--ip",
            ip,
            "--deadline",
            str(min(20, timeout - 3)),
        )
        spec = SandboxSpec(
            run_id="qualification-" + token[:12],
            command=command,
            cwd="/workspace",
            memory_mb=64,
            pids=32,
            workspace_mb=16,
            env=(("PATH", PRODUCTION_PATH),),
            workspace_host=str(workspace),
        )
        supervisor = _owned_probe_supervisor(Supervisor)(spec, cgroup_root)
        thread = SupervisorThread(supervisor)
        record.update(
            argv=supervisor._bwrap_argv(), cgroup_path=supervisor.cgroup_path, command=list(command)
        )
        deadline = time.monotonic() + timeout
        thread_started = False
        try:
            thread.start()
            thread_started = True
            thread.wait_started(min(10, timeout))
            record.update(
                wrapper_pid=supervisor.payload_pid,
                limits=supervisor.limits_readback,
                gate_opened_monotonic_ns=supervisor.gate_opened_monotonic_ns,
            )
            ready_path = workspace / "witness-ready.json"
            while not ready_path.exists():
                if time.monotonic() >= deadline:
                    raise ProbeError("witness-ready handshake deadline exceeded")
                if supervisor._process is None or supervisor._process.poll() is not None:
                    raise ProbeError("sandbox exited before live witness PID handshake")
                time.sleep(0.01)
            ready = read_json(ready_path)
            if ready.get("token") != token:
                raise ProbeError("unexpected witness handshake token")
            validate_witness_ready(ready)
            mapping = map_witness_pid(Path(supervisor.cgroup_path), ready.get("namespace_pid"))
            record["witness_mapping"] = mapping
            record["witness_ready"] = ready
            initial = read_json(workspace / "initial.json")
            validate_snapshot(initial)
            if ready["before"].get("ppid") != initial["pid"] or any(
                    ready["before"][key] != initial[key] for key in ("userns", "netns", "pidns")):
                raise ProbeError("paused witness does not belong to original payload")
            record["original_host_mapping"] = observe_original_host_mapping(
                Path(supervisor.cgroup_path), initial, caller, mapping)
            write_json(evidence / "boundary-handshake.json", record)
            write_json(
                workspace / "witness-release.json",
                {"token": token, "namespace_pid": ready["namespace_pid"]},
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError("payload completion deadline exceeded")
            record["returncode"] = supervisor.wait(timeout=remaining)
            record["payload"] = read_json(workspace / "payload-result.json")
            witness_result = record["payload"]["witness"]
            record["audit"] = (audit_reader or read_kernel_audit)(
                witness_result["net_started"]["utc_ns"],
                witness_result["net_ended"]["utc_ns"],
                mapping["host_pid"],
            )
            validate_boundary_record(record)
        except Exception as exc:  # noqa: BLE001 - evidence and teardown must survive failed facts
            record["error"] = str(exc)
            raise ProbeError("boundary qualification stopped: " + str(exc)) from exc
        finally:
            _finish_probe(supervisor, thread, thread_started, record)
            record["ended"] = {"utc_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}
            archived = evidence / "boundary-workspace"
            archived.mkdir(mode=0o700, exist_ok=True)
            paths = list(workspace.glob("*.json"))
            if len(paths) > 12:
                record["error"] = "unexpected excess workspace evidence"
            else:
                for path in paths:
                    raw = read_bytes(path)
                    target = archived / path.name
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(raw)
                    record.setdefault("workspace_records", {})[path.name] = {
                        "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
            write_json(evidence / "boundary-probe.json", record)
        if record.get("error"):
            raise ProbeError(record["error"])
    return record


CORPUS_SITE_PATHS = (
    "/usr/local/lib/python3.12/dist-packages",
    "/usr/lib/python3/dist-packages",
    "/home/runner/.local/lib/python3.12/site-packages",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("negative", "positive", "boundary"))
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--cgroup-root")
    args = parser.parse_args()
    try:
        if args.mode == "negative":
            result = run_negative_control(args.evidence)
            if result["already_capable"]:
                print("NOT_NEEDED: the uninstrumented positive probe is still required")
                return 3
            validate_negative_control(result)
        elif args.mode == "positive":
            run_positive_probe(args.evidence)
        else:
            if not args.cgroup_root:
                parser.error("boundary requires --cgroup-root")
            run_boundary_probe(args.cgroup_root, args.evidence)
    except (OSError, ProbeError) as exc:
        print("STOP: " + str(exc))
        return 1
    print("PASS: " + args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
