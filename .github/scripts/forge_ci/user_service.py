"""Fixed existing-manager job placement; no policy/delegation/permission changes.

The standalone bootstrap intentionally imports only stdlib. All capsule failures
are value-free. Unit cleanup does not certify migrated sibling Forge cgroups.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time

SPEC_SHA256 = "ff530ded01cd0b2b26b9b74f830643a6c6127ac2c1b848d266055fa3b8183ba4"
MAX_CAPSULE = 128 * 1024
MAX_METADATA = 64 * 1024
SO_PEERPIDFD = 77
PROVIDER = "/opt/hostedtoolcache/Python/3.12.14/x64/bin/python"
HELPER = ".github/scripts/forge_ci/user_service.py"
BOOT_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C", "LANG": "C"}
FIXED_ENV = {"PYTHONPATH": ".github/scripts:src", "PYTHONDONTWRITEBYTECODE": "1",
             "SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0", "OTEL_SDK_DISABLED": "true"}
OPTIONAL_ENV = frozenset("USER LOGNAME SHELL LANG LC_ALL LC_CTYPE TZ".split())
REQUIRED_ENV = frozenset("""PATH HOME TMPDIR RUNNER_TEMP RUNNER_OS RUNNER_ARCH GITHUB_ACTIONS CI
GITHUB_WORKSPACE GITHUB_EVENT_PATH GITHUB_EVENT_NAME GITHUB_REF_TYPE GITHUB_REF GITHUB_REPOSITORY
GITHUB_REPOSITORY_OWNER GITHUB_REPOSITORY_ID GITHUB_REPOSITORY_OWNER_ID GITHUB_ACTOR GITHUB_ACTOR_ID
GITHUB_TRIGGERING_ACTOR GITHUB_WORKFLOW_REF GITHUB_RUN_NUMBER GITHUB_SERVER_URL GITHUB_API_URL
GITHUB_SHA GITHUB_WORKFLOW_SHA GITHUB_JOB GITHUB_RUN_ID GITHUB_RUN_ATTEMPT EVIDENCE""".split())
# Remove startup hooks and service-manager-generated fields before the loader.
FIXED_UNSET = frozenset("""LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT LD_DEBUG LD_DEBUG_OUTPUT LD_PROFILE
LD_ORIGIN_PATH LD_ASSUME_KERNEL LD_BIND_NOW LD_DYNAMIC_WEAK LD_SHOW_AUXV LD_USE_LOAD_BIAS LD_HWCAP_MASK
GLIBC_TUNABLES PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT PYTHONUSERBASE PYTHONWARNINGS
PYTHONBREAKPOINT PYTHONPYCACHEPREFIX PYTHONPLATLIBDIR PYTHONSAFEPATH PYTHONINTMAXSTRDIGITS
PYTHONHASHSEED PYTHONIOENCODING PYTHONUTF8 PYTHONCOERCECLOCALE PYTHONDONTWRITEBYTECODE
NODE_OPTIONS NODE_PATH NODE_EXTRA_CA_CERTS NODE_REPL_EXTERNAL_MODULE NODE_REPL_HISTORY
USER LOGNAME SHELL XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS INVOCATION_ID JOURNAL_STREAM
SYSTEMD_EXEC_PID MANAGERPID MAINPID EXEC_PID SYSTEMD_SCOPE MEMORY_PRESSURE_WATCH MEMORY_PRESSURE_WRITE
CREDENTIALS_DIRECTORY RUNTIME_DIRECTORY STATE_DIRECTORY CACHE_DIRECTORY LOGS_DIRECTORY
CONFIGURATION_DIRECTORY TMPDIR LISTEN_PID LISTEN_FDS LISTEN_FDNAMES NOTIFY_SOCKET WATCHDOG_PID
WATCHDOG_USEC TERM COLORTERM DEBUG_INVOCATION""".split())
BINDING_KEYS = {"nonce", "control_sha", "source_sha256", "run_id", "run_attempt", "job", "boot_id"}
OWNER_KEYS = {"pid", "uid", "gid", "start_ticks", "pidns", "userns", "mntns", "cgroupns", "boot_id"}
CAPSULE_KEYS = {"schema_version", "kind", "binding", "owner", "repo", "evidence", "cwd", "entrypoint", "clock", "environment"}
STARTUP_SECONDS, CANCEL_SECONDS, RUNTIME_SECONDS, CLIENT_SECONDS, STEP_SECONDS = 30, 4290, 4380, 4470, 4500
GRACE_SECONDS, REAP_SECONDS, STOP_SECONDS = 45, 10, 30
NS = 1_000_000_000


class ServiceError(RuntimeError):
    """A finite missing or mismatched service fact requires STOP."""


# Public diagnostic IDs are fixed source constants, never derived from exception
# values. Append new IDs explicitly; do not renumber existing gate identities.
# US000 deliberately collapses unknown exceptions/messages without stringifying.
PUBLIC_GATES = {
    'duplicate capsule field': "US001",
    'nonfinite capsule number': "US002",
    'nonregular required input': "US003",
    'required input byte bound': "US004",
    'capsule framing or byte bound': "US005",
    'noncanonical capsule': "US006",
    'malformed capsule': "US007",
    'missing payload environment': "US008",
    'changed fixed payload environment': "US009",
    'invalid payload environment keys': "US010",
    'invalid payload environment value': "US011",
    'wrong runner environment': "US012",
    'invalid binding fields': "US013",
    'invalid source binding': "US014",
    'invalid run binding': "US015",
    'invalid job binding': "US016",
    'invalid boot binding': "US017",
    'invalid capsule fields': "US018",
    'unsupported capsule profile': "US019",
    'invalid owner fields': "US020",
    'invalid owner identity': "US021",
    'invalid owner namespace': "US022",
    'owner boot mismatch': "US023",
    'noncanonical capsule path': "US024",
    'wrong fixed working paths': "US025",
    'invalid entrypoint fields': "US026",
    'wrong fixed interpreter': "US027",
    'invalid entrypoint identity': "US028",
    'invalid clock fields': "US029",
    'invalid clock': "US030",
    'changed action budget': "US031",
    'payload path mismatch': "US032",
    'payload binding mismatch': "US033",
    'manager environment byte bound': "US034",
    'invalid manager environment name': "US035",
    'manager environment key bound': "US036",
    'invalid unset key set': "US037",
    'invalid process stat': "US038",
    'dead or malformed process': "US039",
    'duplicate status field': "US040",
    'nonordinary process identity': "US041",
    'ambiguous PID namespace view': "US042",
    'requires Linux': "US043",
    'runner identity mismatch': "US044",
    'runner inherited restriction mismatch': "US045",
    'runner capability mismatch': "US046",
    'runner label mismatch': "US047",
    'unsupported capsule transport': "US048",
    'malformed peer credentials': "US049",
    'foreign capsule peer': "US050",
    'missing original-peer pidfd': "US051",
    'dead or changed original peer': "US052",
    'peer namespace or boot mismatch': "US053",
    'capsule read deadline': "US054",
    'capsule byte bound': "US055",
    'exhausted original deadline': "US056",
    'startup deadline': "US057",
    'exhausted startup reserve': "US058",
    'invalid metadata time budget': "US059",
    'metadata deadline': "US060",
    'metadata output byte bound': "US061",
    'metadata command failed': "US062",
    'noncanonical manager path': "US063",
    'foreign or missing manager object': "US064",
    'unexpected runtime directory mode': "US065",
    'invalid unit metadata': "US066",
    'missing unit metadata': "US067",
    'wrong queried unit identity': "US068",
    'ambiguous absent unit': "US069",
    'wrong canonical checkout': "US070",
    'wrong bootstrap checkout': "US071",
    'changed authenticated entrypoint': "US072",
    'invalid authenticated helper set': "US073",
    'changed authenticated helper bytes': "US074",
    'checkout package imported before authentication': "US075",
    'different process and start-thread cgroup views': "US076",
    'wrong service or delegated ancestry': "US077",
    'unreadable ancestry metadata': "US078",
    'delegated parent is not already writable': "US079",
    'required delegated controllers unavailable': "US080",
    'invalid service evidence parent': "US081",
    'service receipt byte bound': "US082",
    'cancelled before child spawn': "US083",
    'owned child identity mismatch': "US084",
    'owned child ancestry mismatch': "US085",
    'unclean bootstrap environment': "US086",
    'wrong isolated bootstrap interpreter': "US087",
    'bootstrap identity mismatch': "US088",
    'bootstrap source binding mismatch': "US089",
    'bootstrap entrypoint mismatch': "US090",
    'cancelled during bootstrap': "US091",
    'unit ownership unavailable': "US092",
    'foreign unit main identity': "US093",
    'unit predates launcher': "US094",
    'foreign unit main command': "US095",
    'foreign unit main ancestry': "US096",
    'unit main changed during ownership check': "US097",
    'service receipt binding mismatch': "US098",
    'outer client deadline': "US099",
    'owned unit invocation changed': "US100",
    'missing owned-child terminal proof': "US101",
    'unit has not terminated': "US102",
    'client and child terminal disagree': "US103",
    'wrong launcher interpreter': "US104",
    'controller evidence is not fresh': "US105",
    'unsupported or inactive user manager': "US106",
    'required systemd-run interface unavailable': "US107",
    'unit already exists': "US108",
    'cancelled before service creation': "US109",
}


def public_gate(error):
    if type(error) is ServiceError and len(error.args) == 1 and type(error.args[0]) is str:
        return PUBLIC_GATES.get(error.args[0], "US000")
    return "US000"


def need(condition, message):
    if not condition:
        raise ServiceError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode() + b"\n"


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "duplicate capsule field")
        result[key] = value
    return result


def _no_constant(_):
    raise ServiceError("nonfinite capsule number")


def read_regular(path, limit=MAX_METADATA):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        need(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "nonregular required input")
        result = stream.read(limit + 1)
    need(len(result) <= limit, "required input byte bound")
    return result


def keys(value, expected, label):
    need(type(value) is dict and set(value) == expected, "invalid " + label + " fields")


def positive(value):
    return type(value) is int and 0 < value < 2**63


def text(value, limit=16384):
    return type(value) is str and 0 < len(value) <= limit and "\0" not in value


def parse_capsule(raw):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_CAPSULE and raw.endswith(b"\n"), "capsule framing or byte bound")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_no_constant)
        need(canonical(value) == raw, "noncanonical capsule")
        validate_capsule(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ServiceError("malformed capsule") from exc


def payload_environment(source):
    need(REQUIRED_ENV <= source.keys(), "missing payload environment")
    result = {key: source[key] for key in REQUIRED_ENV | OPTIONAL_ENV if key in source}
    for key, value in FIXED_ENV.items():
        need(source.get(key) == value, "changed fixed payload environment")
        result[key] = value
    validate_environment(result)
    return result


def validate_environment(value):
    need(type(value) is dict and REQUIRED_ENV | FIXED_ENV.keys() <= value.keys()
         and value.keys() <= REQUIRED_ENV | OPTIONAL_ENV | FIXED_ENV.keys(), "invalid payload environment keys")
    need(all(text(item) for item in value.values()), "invalid payload environment value")
    need(all(value[key] == item for key, item in FIXED_ENV.items()), "changed fixed payload environment")
    need(value["RUNNER_OS"] == "Linux" and value["RUNNER_ARCH"] == "X64"
         and value["GITHUB_ACTIONS"] == "true" and value["CI"] == "true", "wrong runner environment")


def unit_name(binding):
    return f"forge-qual-{binding['nonce']}-{binding['run_id']}-{binding['run_attempt']}.service"


def validate_binding(value):
    keys(value, BINDING_KEYS, "binding")
    for key, count in (("nonce", 32), ("control_sha", 40), ("source_sha256", 64)):
        need(type(value[key]) is str and re.fullmatch(r"[0-9a-f]{" + str(count) + "}", value[key])
             and value[key] != "0" * count, "invalid source binding")
    need(positive(value["run_id"]) and positive(value["run_attempt"]), "invalid run binding")
    need(type(value["job"]) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,99}", value["job"]), "invalid job binding")
    need(type(value["boot_id"]) is str and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["boot_id"]), "invalid boot binding")


def validate_capsule(value):
    keys(value, CAPSULE_KEYS, "capsule")
    need(type(value["schema_version"]) is int and value["schema_version"] == 1 and value["kind"] == "qualification", "unsupported capsule profile")
    validate_binding(value["binding"])
    owner = value["owner"]
    keys(owner, OWNER_KEYS, "owner")
    need(all(positive(owner[key]) for key in ("pid", "uid", "gid", "start_ticks")), "invalid owner identity")
    for key, prefix in (("pidns", "pid"), ("userns", "user"), ("mntns", "mnt"), ("cgroupns", "cgroup")):
        need(type(owner[key]) is str and re.fullmatch(prefix + r":\[[1-9][0-9]*\]", owner[key]), "invalid owner namespace")
    need(owner["boot_id"] == value["binding"]["boot_id"], "owner boot mismatch")
    for key in ("repo", "cwd", "evidence"):
        item = value[key]
        need(text(item, 4096) and item.startswith("/") and str(Path(item)) == item
             and ".." not in Path(item).parts, "noncanonical capsule path")
    need(value["repo"] == value["cwd"] and Path(value["evidence"]).name == "qualification", "wrong fixed working paths")
    keys(value["entrypoint"], {"python", "helper_sha256", "controller_sha256", "manifest_sha256"}, "entrypoint")
    need(value["entrypoint"]["python"] == PROVIDER, "wrong fixed interpreter")
    need(all(type(value["entrypoint"][key]) is str and re.fullmatch(r"[0-9a-f]{64}", value["entrypoint"][key])
             for key in ("helper_sha256", "controller_sha256", "manifest_sha256")), "invalid entrypoint identity")
    keys(value["clock"], {"started_utc_ns", "started_monotonic_ns", "deadline_utc_ns", "deadline_monotonic_ns"}, "clock")
    clock = value["clock"]
    need(all(positive(item) for item in clock.values()), "invalid clock")
    need(clock["deadline_utc_ns"] - clock["started_utc_ns"] == STEP_SECONDS * NS
         and clock["deadline_monotonic_ns"] - clock["started_monotonic_ns"] == STEP_SECONDS * NS, "changed action budget")
    validate_environment(value["environment"])
    env = value["environment"]
    need(env["GITHUB_WORKSPACE"] == value["repo"] and env["EVIDENCE"] == str(Path(value["evidence"]).parent), "payload path mismatch")
    for key, field in (("GITHUB_SHA", "control_sha"), ("GITHUB_WORKFLOW_SHA", "control_sha"), ("GITHUB_RUN_ID", "run_id"),
                       ("GITHUB_RUN_ATTEMPT", "run_attempt"), ("GITHUB_JOB", "job")):
        need(env[key] == str(value["binding"][field]), "payload binding mismatch")


def manager_keys(raw):
    """Discard private values. No query output is ever included in an error."""
    need(type(raw) is bytes and len(raw) <= MAX_METADATA and b"\0" not in raw, "manager environment byte bound")
    names = []
    for line in raw.splitlines():
        name, separator, _ = line.partition(b"=")
        need(separator == b"=" and re.fullmatch(rb"[A-Za-z_][A-Za-z0-9_]{0,127}", name), "invalid manager environment name")
        names.append(name.decode("ascii"))
    need(len(names) <= 256 and len(set(names)) == len(names), "manager environment key bound")
    return sorted((set(names) | FIXED_UNSET) - BOOT_ENV.keys())


def service_argv(capsule, unset):
    need(unset == sorted(set(unset)) and all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) for name in unset)
         and FIXED_UNSET <= set(unset) and not BOOT_ENV.keys() & set(unset), "invalid unset key set")
    return ["/usr/bin/systemd-run", "--user", "--no-ask-password", "--service-type=exec", "--wait", "--pipe",
            "--expand-environment=no", "--unit=" + unit_name(capsule["binding"]), "--working-directory=" + capsule["cwd"],
            "--property=Environment=" + " ".join(key + "=" + value for key, value in BOOT_ENV.items()),
            "--property=UnsetEnvironment=" + " ".join(unset), f"--property=RuntimeMaxSec={RUNTIME_SECONDS}",
            f"--property=TimeoutStopSec={STOP_SECONDS}", "--", "/usr/bin/python3", "-B", "-I", "-S",
            str(Path(capsule["repo"]) / HELPER), "bootstrap"]


def controller_argv(capsule):
    return [PROVIDER, "-m", "forge_ci.controller", "--manifest", str(Path(capsule["repo"]) / ".github/qualification-manifest.json"),
            "--repo", capsule["repo"], "--evidence", capsule["evidence"]]


def process_identity(pid):
    raw = read_regular(f"/proc/{pid}/stat", 4096).decode("ascii")
    # comm can contain spaces and closing parentheses; fields after its LAST ).
    left, separator, right = raw.rpartition(") ")
    need(separator and left.startswith(str(pid) + " ("), "invalid process stat")
    fields = right.split()
    need(len(fields) >= 20 and fields[0] not in {"Z", "X", "x"} and fields[19].isdigit(), "dead or malformed process")
    status = {}
    for line in read_regular(f"/proc/{pid}/status", 32768).decode("ascii").splitlines():
        key, separator, item = line.partition(":")
        if separator:
            need(key not in status, "duplicate status field")
            status[key] = item.strip()
    uid, gid = [int(x) for x in status["Uid"].split()], [int(x) for x in status["Gid"].split()]
    need(len(uid) == len(gid) == 4 and len(set(uid)) == len(set(gid)) == 1 and uid[0] > 0 and gid[0] > 0, "nonordinary process identity")
    need(status.get("NSpid", "").split() == [str(pid)], "ambiguous PID namespace view")
    result = {"pid": pid, "uid": uid[0], "gid": gid[0], "start_ticks": int(fields[19]),
              "boot_id": read_regular("/proc/sys/kernel/random/boot_id", 64).decode("ascii").strip()}
    for name, field in (("pid", "pidns"), ("user", "userns"), ("mnt", "mntns"), ("cgroup", "cgroupns")):
        result[field] = os.readlink(f"/proc/{pid}/ns/{name}")
    return result


def require_runner():
    need(sys.platform == "linux", "requires Linux")
    result = process_identity(os.getpid())
    need(result["uid"] == os.getuid() == os.geteuid() and result["gid"] == os.getgid() == os.getegid(), "runner identity mismatch")
    raw = read_regular("/proc/self/status", 32768).decode("ascii")
    need(re.findall(r"^NoNewPrivs:\s+([01])$", raw, re.M) == ["0"], "runner inherited restriction mismatch")
    for key in ("CapEff", "CapPrm", "CapInh", "CapAmb"):
        need(re.findall(r"^" + key + r":\s+([0-9a-f]{16})$", raw, re.M) == ["0" * 16], "runner capability mismatch")
    need(read_regular("/proc/self/attr/current", 4096).strip() == b"unconfined", "runner label mismatch")
    return result


def ready(fd):
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        return bool(selector.select(0))


def peer_owner(transport, owner):
    need(transport.family == socket.AF_UNIX and transport.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) == socket.SOCK_STREAM,
         "unsupported capsule transport")
    peer = transport.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    need(len(peer) == struct.calcsize("3i"), "malformed peer credentials")
    pid, uid, gid = struct.unpack("3i", peer)
    need((pid, uid, gid) == (owner["pid"], os.getuid(), os.getgid()) == (owner["pid"], owner["uid"], owner["gid"]), "foreign capsule peer")
    pidfd = transport.getsockopt(socket.SOL_SOCKET, SO_PEERPIDFD)
    need(type(pidfd) is int and pidfd >= 0, "missing original-peer pidfd")
    try:
        os.set_inheritable(pidfd, False)
        need(not ready(pidfd) and process_identity(pid) == owner and not ready(pidfd), "dead or changed original peer")
        current = process_identity(os.getpid())
        need(all(current[key] == owner[key] for key in ("pidns", "userns", "mntns", "cgroupns", "boot_id")), "peer namespace or boot mismatch")
        return pidfd
    except BaseException:
        os.close(pidfd)
        raise


def read_capsule(transport):
    deadline = time.monotonic() + 5
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        need(remaining > 0, "capsule read deadline")
        transport.settimeout(remaining)
        chunk = transport.recv(min(65536, MAX_CAPSULE + 1 - len(data)))
        if not chunk:
            return parse_capsule(bytes(data))
        data.extend(chunk)
        need(len(data) <= MAX_CAPSULE, "capsule byte bound")


def remaining(clock, seconds):
    return min((clock["started_monotonic_ns"] + seconds * NS - time.monotonic_ns()) / NS,
               (clock["started_utc_ns"] + seconds * NS - time.time_ns()) / NS)


def budget(clock, seconds=CLIENT_SECONDS, cap=5):
    left = remaining(clock, seconds)
    need(left > 0, "exhausted original deadline")
    return min(cap, left)


@contextmanager
def startup_limit(clock):
    def expired(signum, frame):
        raise ServiceError("startup deadline")
    old = signal.signal(signal.SIGALRM, expired)
    try:
        seconds = remaining(clock, STARTUP_SECONDS)
        need(0 < seconds <= STARTUP_SECONDS, "exhausted startup reserve")
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
        need(remaining(clock, STARTUP_SECONDS) > 0, "startup deadline")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


@contextmanager
def cancellation():
    state = {"signal": None}
    def cancel(signum, frame):
        state["signal"] = signum
    old = {number: signal.signal(number, cancel) for number in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield state
    finally:
        for number, handler in old.items():
            signal.signal(number, handler)


def metadata(argv, env, timeout=5):
    """Bound both output streams, discard stderr values, kill/reap owned child."""
    need(0 < timeout <= 5, "invalid metadata time budget")
    deadline = time.monotonic() + timeout
    work_deadline = deadline - min(0.25, timeout / 2)
    process = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
    chunks = {"out": bytearray(), "err": bytearray()}
    try:
        with selectors.DefaultSelector() as selector:
            for stream, name in ((process.stdout, "out"), (process.stderr, "err")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                need(time.monotonic() < work_deadline, "metadata deadline")
                for key, _ in selector.select(max(0, min(0.1, work_deadline - time.monotonic()))):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        chunks[key.data].extend(chunk)
                        need(sum(map(len, chunks.values())) <= MAX_METADATA, "metadata output byte bound")
        code = process.wait(timeout=max(0, work_deadline - time.monotonic()))
        need(code == 0 and not chunks["err"], "metadata command failed")
        return bytes(chunks["out"])
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=max(0, deadline - time.monotonic()))
        process.stdout.close()
        process.stderr.close()


def manager_environment(owner):
    runtime = Path(f"/run/user/{owner['uid']}")
    for path, socket_required in ((runtime, False), (runtime / "bus", True), (runtime / "systemd/private", True)):
        need(path.resolve(strict=True) == path, "noncanonical manager path")
        info = path.lstat()
        need(info.st_uid == owner["uid"] and info.st_gid == owner["gid"]
             and (stat.S_ISSOCK(info.st_mode) if socket_required else stat.S_ISDIR(info.st_mode)), "foreign or missing manager object")
        if not socket_required:
            need(stat.S_IMODE(info.st_mode) == 0o700, "unexpected runtime directory mode")
    return {**BOOT_ENV, "XDG_RUNTIME_DIR": str(runtime), "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(runtime / "bus")}


def systemctl(*args):
    return ["/usr/bin/systemctl", "--user", "--no-ask-password", *args]


def parse_properties(raw, expected):
    result = {}
    for line in raw.decode("ascii").splitlines():
        key, separator, value = line.partition("=")
        need(separator and key in expected and key not in result, "invalid unit metadata")
        result[key] = value
    need(result.keys() == expected, "missing unit metadata")
    return result


UNIT_FIELDS = {"Id", "LoadState", "ActiveState", "SubState", "MainPID", "InvocationID", "ControlGroup", "Result", "ExecMainCode", "ExecMainStatus"}


def unit_facts(unit, environment, timeout=5):
    # v255 show_one() returns zero for the explicit not-found/inactive unit;
    # --all preserves empty InvocationID/ControlGroup (not an all-unit query).
    facts = parse_properties(metadata(systemctl("show", "--all", "--property=" + ",".join(sorted(UNIT_FIELDS)), "--", unit),
                                      environment, timeout=timeout), UNIT_FIELDS)
    need(facts["Id"] == unit, "wrong queried unit identity")
    if facts["LoadState"] == "not-found":
        need(facts["ActiveState"] == "inactive" and facts["MainPID"] == "0"
             and facts["InvocationID"] == facts["ControlGroup"] == "", "ambiguous absent unit")
    return facts


def local_binding(repo, manifest, environment):
    need(repo.resolve(strict=True) == repo and Path.cwd() == repo and manifest == repo / ".github/qualification-manifest.json", "wrong canonical checkout")
    sys.path.insert(0, str(repo / ".github/scripts"))
    launch = importlib.import_module("forge_ci.launch")
    document = launch.load_manifest(manifest)
    checkout = launch.inspect_checkout(repo, document, manifest_path=manifest)
    event = launch.parse_json(launch.read_regular(Path(environment["GITHUB_EVENT_PATH"]), limit=launch.MAX_API), limit=launch.MAX_API)
    native = launch.validate_event(document["launch"], environment, event, checkout)
    return {"nonce": document["launch"]["nonce"], "control_sha": native["sha"], "source_sha256": document["launch"]["source_sha256"],
            "run_id": native["run_id"], "run_attempt": native["run_attempt"], "job": native["job"],
            "boot_id": read_regular("/proc/sys/kernel/random/boot_id", 64).decode("ascii").strip()}


def verify_checkout_imports(capsule):
    """Authenticate every helper before the first checkout import in -I -S."""
    repo = Path(capsule["repo"])
    need(repo.resolve(strict=True) == repo and Path.cwd() == repo, "wrong bootstrap checkout")
    need(entrypoint_identity(repo) == capsule["entrypoint"], "changed authenticated entrypoint")
    raw = read_regular(repo / ".github/qualification-manifest.json", 256 * 1024)
    document = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_no_constant)
    helpers = document["launch"]["helper_sha256"]
    expected = {".github/scripts/forge_ci/" + name + ".py" for name in (
        "__init__", "facts", "launch", "admission", "setup_policy", "controller", "outcomes", "payload", "probes", "pytest_observer", "user_service")}
    need(type(helpers) is dict and helpers.keys() == expected, "invalid authenticated helper set")
    for relative, digest in helpers.items():
        path = repo / relative
        need(path.resolve(strict=True) == path and hashlib.sha256(read_regular(path, 256 * 1024)).hexdigest() == digest,
             "changed authenticated helper bytes")
    need(not any(name == "forge_ci" or name.startswith("forge_ci.") for name in sys.modules), "checkout package imported before authentication")


def entrypoint_identity(repo):
    return {"python": PROVIDER, "helper_sha256": hashlib.sha256(read_regular(repo / HELPER, 256 * 1024)).hexdigest(),
            "controller_sha256": hashlib.sha256(read_regular(repo / ".github/scripts/forge_ci/controller.py", 256 * 1024)).hexdigest(),
            "manifest_sha256": hashlib.sha256(read_regular(repo / ".github/qualification-manifest.json", 256 * 1024)).hexdigest()}


def ancestry(capsule):
    # The unchanged finite diagnostic parser maps only this process and its start
    # thread; the placeholder names the production destination parent, not a leaf
    # to create, scan, normalize or remove.
    probes = importlib.import_module("forge_ci.probes")
    root = f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service"
    pid, tid = os.getpid(), threading.get_native_id()
    process = read_regular(f"/proc/{pid}/cgroup", 4096).decode("ascii")
    thread = read_regular(f"/proc/{pid}/task/{tid}/cgroup", 4096).decode("ascii")
    need(process == thread, "different process and start-thread cgroup views")
    mounts = read_regular(f"/proc/{pid}/task/{tid}/mountinfo", MAX_METADATA).decode("ascii")
    paths = probes._diagnostic_cgroup_paths(thread, mounts, root, root + "/forge-uncreated-destination")
    need(paths["source"].startswith(root + "/") and Path(paths["source"]).name == unit_name(capsule["binding"])
         and paths["common_ancestor"] == root, "wrong service or delegated ancestry")
    result = {"paths": paths, "pid": pid, "native_tid": tid, "locations": {}}
    for role in ("source", "destination_parent", "common_ancestor"):
        path = paths[role]
        details = {"directory": probes._diagnostic_metadata(path), "procs": probes._diagnostic_metadata(path + "/cgroup.procs")}
        need(all("error" not in item for item in details.values()), "unreadable ancestry metadata")
        result["locations"][role] = details
    parent = result["locations"]["destination_parent"]
    need(parent["directory"]["uid"] == os.getuid() and parent["directory"]["effective_write_access"]
         and parent["procs"]["effective_write_access"], "delegated parent is not already writable")
    for filename in ("cgroup.controllers", "cgroup.subtree_control"):
        need({"memory", "pids"} <= set(read_regular(root + "/" + filename, 4096).decode("ascii").split()), "required delegated controllers unavailable")
    return result


def receipt(capsule, name, value):
    parent = Path(capsule["evidence"]).parent
    need(parent.resolve(strict=True) == parent and parent.stat().st_uid == os.getuid(), "invalid service evidence parent")
    raw = canonical({"schema_version": 1, "spec_sha256": SPEC_SHA256, "binding": capsule["binding"], "unit": unit_name(capsule["binding"]), **value})
    need(len(raw) <= MAX_CAPSULE, "service receipt byte bound")
    fd = os.open(parent / ("service-" + name + ".json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def watch_child(capsule, owner_fd, cancelled):
    need(not cancelled["signal"] and not ready(owner_fd) and remaining(capsule["clock"], STARTUP_SECONDS) > 0, "cancelled before child spawn")
    child = subprocess.Popen(controller_argv(capsule), cwd=capsule["cwd"], env=capsule["environment"],
                             stdin=subprocess.DEVNULL, close_fds=True)
    started = time.monotonic_ns()
    reason, grace_end, reaping = None, None, False
    try:
        child_identity = process_identity(child.pid)
        child_cgroup = read_regular(f"/proc/{child.pid}/cgroup", 4096).decode("ascii")
        need(child_identity["uid"] == capsule["owner"]["uid"] and child_identity["gid"] == capsule["owner"]["gid"]
             and child_identity["boot_id"] == capsule["binding"]["boot_id"], "owned child identity mismatch")
        need(child_cgroup == read_regular("/proc/self/cgroup", 4096).decode("ascii"), "owned child ancestry mismatch")
        while child.poll() is None:
            if reason is None:
                reason = ("owner_lost" if ready(owner_fd) else "signal" if cancelled["signal"] else
                          "deadline" if remaining(capsule["clock"], CANCEL_SECONDS) <= 0 else None)
                if reason:
                    child.send_signal(signal.SIGTERM)  # retained unreaped Popen ownership
                    grace_end = time.monotonic() + min(GRACE_SECONDS, max(0, remaining(capsule["clock"], CLIENT_SECONDS - REAP_SECONDS)))
            if reason and time.monotonic() >= grace_end:
                child.kill()
                break
            time.sleep(min(0.05, max(0.001, remaining(capsule["clock"], CLIENT_SECONDS - REAP_SECONDS))))
        reaping = True
        child.wait(timeout=min(REAP_SECONDS, max(0, remaining(capsule["clock"], CLIENT_SECONDS))))
        reason = reason or ("signal" if cancelled["signal"] else "owner_lost" if ready(owner_fd) else None)
        return {"child_pid": child.pid, "child_identity": child_identity, "child_cgroup": child_cgroup, "returncode": child.returncode, "exit_code": child.returncode if child.returncode >= 0 else None,
                "signal": -child.returncode if child.returncode < 0 else None, "cancel_reason": reason,
                "started_monotonic_ns": started, "ended_monotonic_ns": time.monotonic_ns(),
                "owned_child_reaped": True, "sibling_cgroup_cleanup": "not_certified_by_service"}
    finally:
        if child.poll() is None and not reaping:
            child.kill()
            child.wait(timeout=min(REAP_SECONDS, max(0, remaining(capsule["clock"], CLIENT_SECONDS))))


def bootstrap():
    # No imports from the checkout and no secret reads before this comparison.
    need(dict(os.environ) == BOOT_ENV, "unclean bootstrap environment")
    need(sys.executable == "/usr/bin/python3" and sys.flags.isolated == sys.flags.no_site == sys.flags.dont_write_bytecode == 1,
         "wrong isolated bootstrap interpreter")
    capsule, owner_fd, admitted = None, None, False
    with cancellation() as cancelled:
        try:
            transport = socket.socket(fileno=os.dup(0))
            try:
                capsule = read_capsule(transport)
                with startup_limit(capsule["clock"]):
                    runner = require_runner()
                    owner_fd = peer_owner(transport, capsule["owner"])
                    need(runner["uid"] == capsule["owner"]["uid"] and runner["gid"] == capsule["owner"]["gid"], "bootstrap identity mismatch")
                    repo = Path(capsule["repo"])
                    verify_checkout_imports(capsule)
                    need(local_binding(repo, repo / ".github/qualification-manifest.json", capsule["environment"]) == capsule["binding"], "bootstrap source binding mismatch")
                    need(entrypoint_identity(repo) == capsule["entrypoint"] and Path(__file__).resolve(strict=True) == repo / HELPER, "bootstrap entrypoint mismatch")
                    source = ancestry(capsule)
                    need(not cancelled["signal"] and not ready(owner_fd), "cancelled during bootstrap")
                    admitted = True
                    receipt(capsule, "setup", {"status": "ADMITTED", "owner": capsule["owner"], "owner_contract": "SO_PEERCRED+SO_PEERPIDFD",
                                               "owner_pidfd_live": True, "watcher": runner, "ancestry": source})
            finally:
                transport.close()
                os.close(0)
            result = watch_child(capsule, owner_fd, cancelled)
            receipt(capsule, "terminal", {"status": "STOP" if result["cancel_reason"] else "EXITED", **result})
            return result["returncode"] if not result["cancel_reason"] else 1
        except BaseException as exc:
            if admitted:
                try:
                    receipt(capsule, "bootstrap-stop", {"status": "STOP", "error_type": type(exc).__name__, "gate": public_gate(exc), "qualified": False})
                except (OSError, ServiceError):
                    pass
            raise
        finally:
            if owner_fd is not None:
                os.close(owner_fd)


def owned_watcher(capsule, facts):
    """Acquire only a positively verified main process of this fresh exact unit."""
    need(facts["Id"] == unit_name(capsule["binding"]) and re.fullmatch(r"[0-9a-f]{32}", facts["InvocationID"])
         and facts["MainPID"].isdigit() and int(facts["MainPID"]) > 0, "unit ownership unavailable")
    pid = int(facts["MainPID"])
    fd = os.pidfd_open(pid)
    try:
        identity = process_identity(pid)
        need(identity["uid"] == capsule["owner"]["uid"] and identity["gid"] == capsule["owner"]["gid"]
             and identity["boot_id"] == capsule["owner"]["boot_id"] and identity["pidns"] == capsule["owner"]["pidns"], "foreign unit main identity")
        need(identity["start_ticks"] >= capsule["owner"]["start_ticks"], "unit predates launcher")
        need(read_regular(f"/proc/{pid}/cmdline", 16384).split(b"\0")[:-1] == [os.fsencode(x) for x in
             ["/usr/bin/python3", "-B", "-I", "-S", str(Path(capsule["repo"]) / HELPER), "bootstrap"]], "foreign unit main command")
        path = facts["ControlGroup"]
        need(path.startswith(f"/user.slice/user-{identity['uid']}.slice/user@{identity['uid']}.service/")
             and Path(path).name == unit_name(capsule["binding"])
             and read_regular(f"/proc/{pid}/cgroup", 4096) == ("0::" + path + "\n").encode(), "foreign unit main ancestry")
        need(not ready(fd) and process_identity(pid) == identity, "unit main changed during ownership check")
        return fd
    except BaseException:
        os.close(fd)
        raise


def load_receipt(capsule, name):
    raw = read_regular(Path(capsule["evidence"]).parent / ("service-" + name + ".json"), MAX_CAPSULE)
    result = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_no_constant)
    need(result["binding"] == capsule["binding"] and result["unit"] == unit_name(capsule["binding"])
         and result["spec_sha256"] == SPEC_SHA256, "service receipt binding mismatch")
    return result


def reconcile_client(capsule, client, environment, cancelled):
    unit, clock = unit_name(capsule["binding"]), capsule["clock"]
    signalled, stop_at, invocation = False, None, None
    while client.poll() is None:
        elapsed = remaining(clock, CLIENT_SECONDS)
        need(elapsed > REAP_SECONDS, "outer client deadline")
        cancel_now = cancelled["signal"] or remaining(clock, CANCEL_SECONDS) <= 0
        if cancel_now and not signalled:
            facts = unit_facts(unit, environment, timeout=budget(clock, CLIENT_SECONDS - REAP_SECONDS))
            fd = owned_watcher(capsule, facts)
            try:
                signal.pidfd_send_signal(fd, signal.SIGTERM)
            finally:
                os.close(fd)
            signalled, invocation = True, facts["InvocationID"]
            stop_at = time.monotonic() + min(GRACE_SECONDS + REAP_SECONDS, max(0, remaining(clock, CLIENT_SECONDS - STOP_SECONDS - REAP_SECONDS)))
        if signalled and time.monotonic() >= stop_at:
            facts = unit_facts(unit, environment, timeout=budget(clock, CLIENT_SECONDS - REAP_SECONDS))
            need(facts["InvocationID"] == invocation, "owned unit invocation changed")
            metadata(systemctl("stop", "--no-block", "--", unit), environment, timeout=budget(clock, CLIENT_SECONDS - REAP_SECONDS))
            stop_at = float("inf")
        time.sleep(0.1)
    terminal = load_receipt(capsule, "terminal")
    facts = unit_facts(unit, environment, timeout=budget(clock, CLIENT_SECONDS - REAP_SECONDS))
    need(terminal.get("owned_child_reaped") is True and type(terminal.get("returncode")) is int, "missing owned-child terminal proof")
    need(facts["LoadState"] == "not-found" or (facts["MainPID"] == "0" and facts["ActiveState"] in {"inactive", "failed"}), "unit has not terminated")
    code = terminal["returncode"]
    expected = code if code >= 0 else 128 - code
    need(client.returncode == expected or (terminal.get("cancel_reason") and client.returncode != 0), "client and child terminal disagree")
    receipt(capsule, "client", {"status": "STOP" if signalled or cancelled["signal"] or terminal.get("cancel_reason") else "EXITED",
                               "client_returncode": client.returncode, "unit_facts": facts, "child_returncode": code,
                               "sibling_cgroup_cleanup": "not_certified_by_service"})
    return 1 if signalled or cancelled["signal"] or terminal.get("cancel_reason") else code


def launcher(manifest, repo, evidence):
    started = {"started_utc_ns": time.time_ns(), "started_monotonic_ns": time.monotonic_ns()}
    clock = {**started, "deadline_utc_ns": started["started_utc_ns"] + STEP_SECONDS * NS,
             "deadline_monotonic_ns": started["started_monotonic_ns"] + STEP_SECONDS * NS}
    capsule, client, environment = None, None, None
    with cancellation() as cancelled:
        try:
            with startup_limit(clock):
                need(sys.executable == PROVIDER and sys.version_info[:3] == (3, 12, 14), "wrong launcher interpreter")
                owner = require_runner()
                env = payload_environment(os.environ)
                binding = local_binding(repo, manifest, env)
                need(evidence.parent.resolve(strict=True) == evidence.parent and not evidence.exists() and not evidence.is_symlink(), "controller evidence is not fresh")
                capsule = {"schema_version": 1, "kind": "qualification", "binding": binding, "owner": owner,
                           "repo": str(repo), "cwd": str(repo), "evidence": str(evidence), "entrypoint": entrypoint_identity(repo),
                           "clock": clock, "environment": env}
                validate_capsule(capsule)
                raw = canonical(capsule)
                need(len(raw) <= MAX_CAPSULE, "capsule byte bound")
                environment = manager_environment(owner)
                manager = parse_properties(metadata(systemctl("show", "--property=Version,SystemState"), environment), {"Version", "SystemState"})
                need(manager["Version"].split()[0].split(".")[0].isdigit() and int(manager["Version"].split()[0].split(".")[0]) >= 255
                     and manager["SystemState"] in {"running", "degraded"}, "unsupported or inactive user manager")
                help_text = metadata(["/usr/bin/systemd-run", "--help"], BOOT_ENV)
                need(all(flag in help_text for flag in (b"--expand-environment=", b"--service-type=", b"--pipe", b"--wait")), "required systemd-run interface unavailable")
                unset = manager_keys(metadata(systemctl("show-environment"), environment))
                need(unit_facts(unit_name(binding), environment)["LoadState"] == "not-found", "unit already exists")
                need(not cancelled["signal"], "cancelled before service creation")
                left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    client = subprocess.Popen(service_argv(capsule, unset), env=environment, cwd=repo, stdin=right, close_fds=True)
                    right.close()
                    left.settimeout(min(5, max(0.001, remaining(clock, STARTUP_SECONDS))))
                    left.sendall(raw)
                    left.shutdown(socket.SHUT_WR)
                finally:
                    left.close()
                    right.close()
            need(remaining(clock, STARTUP_SECONDS) > 0, "startup deadline")
            return reconcile_client(capsule, client, environment, cancelled)
        except BaseException as exc:
            # First ask only the verified unit main watcher to drain its child.
            # Exact-unit stop is a later backstop and is never a sibling sweep.
            if client is not None and client.poll() is None and capsule is not None:
                cancelled["signal"] = signal.SIGTERM
                try:
                    reconcile_client(capsule, client, environment, cancelled)
                except BaseException:  # noqa: BLE001 - preserve STOP while bounding owned client cleanup
                    if client.poll() is None:
                        client.kill()
                        client.wait(timeout=min(REAP_SECONDS, max(0, remaining(clock, CLIENT_SECONDS))))
            if capsule is not None:
                try:
                    receipt(capsule, "launcher-stop", {"status": "STOP", "error_type": type(exc).__name__, "gate": public_gate(exc), "qualified": False,
                                                       "sibling_cgroup_cleanup": "not_certified_by_service"})
                except (OSError, ServiceError):
                    pass
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="role", required=True)
    commands.add_parser("bootstrap")
    launch = commands.add_parser("launch")
    for name in ("manifest", "repo", "evidence"):
        launch.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        code = bootstrap() if args.role == "bootstrap" else launcher(args.manifest, args.repo, args.evidence)
        # Preserve actual signal identity in terminal evidence and conventional
        # shell exit semantics. Never reinterpret systemd client success as PASS.
        return code if code >= 0 else 128 - code
    except BaseException as exc:  # noqa: BLE001 - never expose private exception values
        print("STOP: fixed user-service admission, lifecycle or evidence failed; gate=" + public_gate(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
