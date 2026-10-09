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

SPEC_SHA256 = "52f595d6c254b4e89c7e08329e3a9b16663c4e4a72565693d03ca72717663177"
MAX_CAPSULE = 128 * 1024
MAX_METADATA = 64 * 1024
SO_PEERPIDFD = 77
PROVIDER = "/opt/hostedtoolcache/Python/3.12.14/x64/bin/python"
HELPER = ".github/scripts/forge_ci/user_service.py"
BOOT_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C", "LANG": "C"}
PROFILE_PATH = "/opt/hostedtoolcache/Python/3.12.14/x64/bin:/usr/bin:/bin"
FIXED_ENV = {"NODE_DISABLE_COMPILE_CACHE": "1", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONPATH": ".github/scripts:src", "PYTHONDONTWRITEBYTECODE": "1",
             "SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0", "OTEL_SDK_DISABLED": "true"}
OPTIONAL_ENV = frozenset()
REQUIRED_ENV = frozenset("""PATH HOME XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_HOME TMPDIR RUNNER_TEMP RUNNER_OS RUNNER_ARCH GITHUB_ACTIONS CI
GITHUB_WORKSPACE GITHUB_EVENT_PATH GITHUB_EVENT_NAME GITHUB_REF_TYPE GITHUB_REF GITHUB_REPOSITORY
GITHUB_REPOSITORY_OWNER GITHUB_REPOSITORY_ID GITHUB_REPOSITORY_OWNER_ID GITHUB_ACTOR GITHUB_ACTOR_ID
GITHUB_TRIGGERING_ACTOR GITHUB_WORKFLOW_REF GITHUB_RUN_NUMBER GITHUB_SERVER_URL GITHUB_API_URL
GITHUB_SHA GITHUB_WORKFLOW_SHA GITHUB_JOB GITHUB_RUN_ID GITHUB_RUN_ATTEMPT RUNNER_ENVIRONMENT ImageOS EVIDENCE""".split())
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
BINDING_KEYS = {"schema_version", "repository_id", "owner_id", "actor_id", "triggering_actor_id",
                "event_name", "full_ref", "before_sha", "candidate_sha", "workflow_sha", "workflow_path",
                "run_id", "run_number", "run_attempt", "job_key", "boot_id", "workflow_id", "job_id",
                "job_started_at", "job_started_ns"}
SOURCE_KEYS = {"candidate_sha", "tree_oid", "source_sha256", "workflow_sha256", "helper_sha256"}
HELPERS = {".github/scripts/forge_ci/" + name + ".py" for name in (
    "__init__", "facts", "launch", "admission", "setup_policy", "controller", "outcomes", "payload",
    "probes", "pytest_observer", "user_service", "baseline_measurement")}
OWNER_KEYS = {"pid", "uid", "gid", "start_ticks", "pidns", "userns", "mntns", "cgroupns", "boot_id"}
CAPSULE_KEYS = {"schema_version", "kind", "binding", "owner", "repo", "evidence", "cwd", "entrypoint", "clock", "environment", "receipt", "source"}
STARTUP_SECONDS, CANCEL_SECONDS, RUNTIME_SECONDS, CLIENT_SECONDS, STEP_SECONDS = 30, 6990, 7080, 7170, 7200
GRACE_SECONDS, REAP_SECONDS, STOP_SECONDS = 45, 10, 30
JOB_SECONDS, CLEANUP_SECONDS, ARTIFACT_SECONDS = 9000, 60, 300
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
    'invalid source fields': "US110",
    'insufficient authenticated job headroom': "US111",
    'PATH Python differs from provider': "US112",
    'credential configuration present': "US113",
    'declared diagnostic tool missing': "US114",
    'diagnostic HOME or XDG mismatch': "US115",
    'installed runtime changed': "US116",
    'invalid diagnostic runtime identity': "US117",
    'invalid diagnostic runtime root': "US118",
    'invalid runtime admission fields': "US119",
    'invalid runtime interpreter fields': "US120",
    'invalid runtime protected file': "US121",
    'invalid runtime record hashes fields': "US122",
    'noncanonical runtime record': "US123",
    'retained runtime record changed': "US124",
    'runnable claude present': "US125",
    'runtime admission binding mismatch': "US126",
    'runtime admission changed': "US127",
    'runtime ancestor alias': "US128",
    'runtime checkout source changed': "US129",
    'runtime directory alias': "US130",
    'runtime file bound exceeded': "US131",
    'runtime file identity changed': "US132",
    'runtime fixed paths changed': "US133",
    'runtime install selection changed': "US134",
    'runtime installer changed': "US135",
    'runtime inventory encoded bound': "US136",
    'runtime package inventory byte bound': "US137",
    'runtime package inventory entry bound': "US138",
    'runtime package root alias': "US139",
    'runtime profile deadline exceeded': "US140",
    'runtime profile requires ordinary owner': "US141",
    'runtime protected file changed while reading': "US142",
    'runtime protected path alias': "US143",
    'runtime record encoded bound': "US144",
    'runtime record hash invalid': "US145",
    'runtime root is not private': "US146",
    'runtime summary bound': "US147",
    'runtime tool is not executable': "US148",
    'system pytest imported outside diagnostic HOME': "US149",
    'system pytest site outside diagnostic HOME': "US150",
    'unexpected private HOME configuration': "US151",
    'unknown runtime record': "US152",
    'unreviewed PATH alias': "US153",
    'untrusted runtime ancestor': "US154",
    'untrusted runtime directory': "US155",
    'wrong diagnostic Python or packages': "US156",
    'wrong provider patch version': "US157",
    'foreign installed metadata directory': "US158",
    'installed metadata aggregate bound': "US159",
    'installed metadata drift': "US160",
    'installed metadata members changed': "US161",
    'invalid installed metadata member': "US162",
    'installed executable drift before import': "US163",
    'installed package drift before import': "US164",
    'runtime package directory changed during inventory': "US165",
    'runtime package traversal unreadable': "US166",
    'active import root exceeds package boundary': "US167",
    'invalid active import roots': "US168",
    'invalid runtime distribution locations': "US169",
    'noncanonical active import root': "US170",
    'overlapping runtime import boundaries': "US171",
    'previously absent import root appeared': "US172",
    'runtime checkout root alias': "US173",
    'unreviewed checkout import root': "US174",
    'unsupported active import archive or file': "US175",
    'runtime PATH selection changed': "US176",
    'private Node provision binding changed': "US177",
    'private Node exposure changed': "US178",
    'private Node vendor inventory changed': "US179",
    'private Node provision incomplete': "US180",
    'private Node archive rejected': "US181",
    'private Node archive identity changed': "US182",
    'private Node materialization changed': "US183",
    'private Node download rejected': "US184",
    'private Node component deadline': "US185",
    'private Node component output bound': "US186",
    'private Node worker failed': "US187",
    'private Node worker settlement incomplete': "US188",
    'private Node compatibility failed': "US189",
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
    need(source.get("PATH") == profile_path(source), "changed fixed payload environment")
    validate_environment(result)
    return result


def validate_environment(value):
    need(type(value) is dict and REQUIRED_ENV | FIXED_ENV.keys() <= value.keys()
         and value.keys() <= REQUIRED_ENV | OPTIONAL_ENV | FIXED_ENV.keys(), "invalid payload environment keys")
    need(all(text(item) for item in value.values()), "invalid payload environment value")
    need(all(value[key] == item for key, item in FIXED_ENV.items()), "changed fixed payload environment")
    need(value["RUNNER_OS"] == "Linux" and value["RUNNER_ARCH"] == "X64"
         and value["GITHUB_ACTIONS"] == "true" and value["CI"] == "true", "wrong runner environment")
    runner = Path(value["RUNNER_TEMP"])
    need(runner.is_absolute() and str(runner) == value["RUNNER_TEMP"] and ".." not in runner.parts,
         "invalid diagnostic runtime root")
    need(re.fullmatch(r"[1-9][0-9]{0,18}", value["GITHUB_RUN_ID"]) is not None
         and value["GITHUB_RUN_ATTEMPT"] == "1", "invalid diagnostic runtime identity")
    need(value["PATH"] == profile_path(value), "changed fixed payload environment")
    home = runner / ("forge-b-home-" + value["GITHUB_RUN_ID"] + "-" + value["GITHUB_RUN_ATTEMPT"])
    need(value["HOME"] == str(home) and value["TMPDIR"] == str(runner / "forge-tests")
         and value["XDG_CONFIG_HOME"] == str(home / ".config")
         and value["XDG_CACHE_HOME"] == str(home / ".cache")
         and value["XDG_DATA_HOME"] == str(home / ".local/share"), "diagnostic HOME or XDG mismatch")


def unit_name(binding):
    return f"forge-ci-{binding['run_id']}-{binding['run_attempt']}-{binding['job_id']}.service"


def validate_binding(value):
    # This standalone structural check is not admission authority. The verified
    # shared setup identity implementation is re-run before controller spawn.
    keys(value, BINDING_KEYS, "binding")
    need(type(value["schema_version"]) is int and value["schema_version"] == 1,
         "invalid binding fields")
    for key in ("before_sha", "candidate_sha", "workflow_sha"):
        need(type(value[key]) is str and re.fullmatch(r"[0-9a-f]{40}", value[key])
             and value[key] != "0" * 40, "invalid source binding")
    need(value["candidate_sha"] == value["workflow_sha"] and value["before_sha"] != value["candidate_sha"],
         "invalid source binding")
    need(all(positive(value[key]) for key in ("repository_id", "owner_id", "actor_id", "triggering_actor_id",
             "run_id", "run_number", "run_attempt", "workflow_id", "job_id", "job_started_ns"))
         and value["run_attempt"] == 1, "invalid run binding")
    need(value["job_key"] == "linux-tests" and value["event_name"] == "push"
         and value["full_ref"] == "refs/heads/ci/baseline-b-4dd7214cf1a24483a42c7e19cfc8ec28"
         and value["workflow_path"] == ".github/workflows/linux-tests.yml", "invalid job binding")
    need(type(value["job_started_at"]) is str
         and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value["job_started_at"]),
         "invalid run binding")
    need(type(value["boot_id"]) is str and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["boot_id"]), "invalid boot binding")


def validate_source(source, binding):
    keys(source, SOURCE_KEYS, "source")
    need(source["candidate_sha"] == binding["candidate_sha"] and type(source["tree_oid"]) is str
         and re.fullmatch(r"[0-9a-f]{40}", source["tree_oid"]), "invalid source binding")
    for key in ("source_sha256", "workflow_sha256"):
        need(type(source[key]) is str and re.fullmatch(r"[0-9a-f]{64}", source[key])
             and source[key] != "0" * 64, "invalid source binding")
    helpers = source["helper_sha256"]
    need(type(helpers) is dict and helpers.keys() == HELPERS
         and all(type(item) is str and re.fullmatch(r"[0-9a-f]{64}", item) and item != "0" * 64
                 for item in helpers.values()), "invalid authenticated helper set")


def validate_capsule(value):
    keys(value, CAPSULE_KEYS, "capsule")
    need(type(value["schema_version"]) is int and value["schema_version"] == 1 and value["kind"] == "qualification", "unsupported capsule profile")
    validate_binding(value["binding"])
    validate_source(value["source"], value["binding"])
    owner = value["owner"]
    keys(owner, OWNER_KEYS, "owner")
    need(all(positive(owner[key]) for key in ("pid", "uid", "gid", "start_ticks")), "invalid owner identity")
    for key, prefix in (("pidns", "pid"), ("userns", "user"), ("mntns", "mnt"), ("cgroupns", "cgroup")):
        need(type(owner[key]) is str and re.fullmatch(prefix + r":\[[1-9][0-9]*\]", owner[key]), "invalid owner namespace")
    need(owner["boot_id"] == value["binding"]["boot_id"], "owner boot mismatch")
    for key in ("repo", "cwd", "evidence", "receipt"):
        item = value[key]
        need(text(item, 4096) and item.startswith("/") and str(Path(item)) == item
             and ".." not in Path(item).parts, "noncanonical capsule path")
    need(value["repo"] == value["cwd"] and Path(value["evidence"]).name == "qualification"
         and Path(value["receipt"]) == Path(value["evidence"]).parent / "launch-bootstrap.json", "wrong fixed working paths")
    keys(value["entrypoint"], {"python", "helper_sha256", "controller_sha256", "receipt_sha256", "runtime_sha256"}, "entrypoint")
    need(value["entrypoint"]["python"] == PROVIDER, "wrong fixed interpreter")
    need(all(type(value["entrypoint"][key]) is str and re.fullmatch(r"[0-9a-f]{64}", value["entrypoint"][key])
             for key in ("helper_sha256", "controller_sha256", "receipt_sha256", "runtime_sha256")), "invalid entrypoint identity")
    keys(value["clock"], {"started_utc_ns", "started_monotonic_ns", "deadline_utc_ns", "deadline_monotonic_ns",
                          "job_deadline_utc_ns", "artifact_deadline_utc_ns"}, "clock")
    clock = value["clock"]
    need(all(positive(item) for item in clock.values()), "invalid clock")
    artifact_deadline_utc_ns = clock["artifact_deadline_utc_ns"]  # A: J + 8700
    work_deadline_utc_ns = artifact_deadline_utc_ns - CLEANUP_SECONDS * NS  # C
    need(clock["deadline_utc_ns"] - clock["started_utc_ns"] == STEP_SECONDS * NS
         and clock["deadline_monotonic_ns"] - clock["started_monotonic_ns"] == STEP_SECONDS * NS
         and clock["job_deadline_utc_ns"] == value["binding"]["job_started_ns"] + JOB_SECONDS * NS
         and clock["artifact_deadline_utc_ns"] == clock["job_deadline_utc_ns"] - ARTIFACT_SECONDS * NS
         and value["binding"]["job_started_ns"] <= clock["started_utc_ns"]
         and clock["deadline_utc_ns"] <= work_deadline_utc_ns, "changed action budget")
    validate_environment(value["environment"])
    env = value["environment"]
    need(env["GITHUB_WORKSPACE"] == value["repo"] and env["EVIDENCE"] == str(Path(value["evidence"]).parent), "payload path mismatch")
    for key, field in (("GITHUB_SHA", "candidate_sha"), ("GITHUB_WORKFLOW_SHA", "workflow_sha"), ("GITHUB_RUN_ID", "run_id"),
                       ("GITHUB_RUN_ATTEMPT", "run_attempt"), ("GITHUB_JOB", "job_key"), ("GITHUB_RUN_NUMBER", "run_number")):
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
    return [PROVIDER, "-m", "forge_ci.controller", "--receipt", capsule["receipt"],
            "--repo", capsule["repo"], "--evidence", capsule["evidence"],
            "--service-started-monotonic-ns", str(capsule["clock"]["started_monotonic_ns"]),
            "--service-started-utc-ns", str(capsule["clock"]["started_utc_ns"]),
            "--service-artifact-deadline-utc-ns", str(capsule["clock"]["artifact_deadline_utc_ns"]),
            "--runtime-sha256", capsule["entrypoint"]["runtime_sha256"]]


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
               (min(clock["started_utc_ns"] + seconds * NS, clock["deadline_utc_ns"],
                    (clock["artifact_deadline_utc_ns"] - CLEANUP_SECONDS * NS
                     if "artifact_deadline_utc_ns" in clock else clock["deadline_utc_ns"])) - time.time_ns()) / NS)


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


def local_receipt(repo, receipt_path, environment):
    need(repo.resolve(strict=True) == repo and Path.cwd() == repo
         and receipt_path == Path(environment["EVIDENCE"]) / "launch-bootstrap.json", "wrong canonical checkout")
    sys.path.insert(0, str(repo / ".github/scripts"))
    launch = importlib.import_module("forge_ci.launch")
    document = launch.load_receipt(receipt_path)
    checkout = launch.inspect_checkout(repo, document["binding"]["candidate_sha"])
    event = launch.parse_json(launch.read_regular(Path(environment["GITHUB_EVENT_PATH"]), limit=launch.MAX_API), limit=launch.MAX_API)
    current = launch.validate_local_launch(environment, event, checkout, document)
    need(current["binding"] == document["binding"] and current["source"] == document["source"],
         "bootstrap source binding mismatch")
    return document


def verify_checkout_imports(capsule):
    """Authenticate every helper and receipt before the first checkout import."""
    repo = Path(capsule["repo"])
    need(repo.resolve(strict=True) == repo and Path.cwd() == repo, "wrong bootstrap checkout")
    need(entrypoint_identity(repo, Path(capsule["receipt"])) == capsule["entrypoint"], "changed authenticated entrypoint")
    raw = read_regular(capsule["receipt"], 256 * 1024)
    document = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_no_constant)
    need(canonical(document.get("binding")) == canonical(capsule["binding"])
         and canonical(document.get("source")) == canonical(capsule["source"]), "bootstrap source binding mismatch")
    validate_source(document["source"], capsule["binding"])
    for relative, digest in document["source"]["helper_sha256"].items():
        path = repo / relative
        need(path.resolve(strict=True) == path and hashlib.sha256(read_regular(path, 256 * 1024)).hexdigest() == digest,
             "changed authenticated helper bytes")
    need(not any(name == "forge_ci" or name.startswith("forge_ci.") for name in sys.modules), "checkout package imported before authentication")


def entrypoint_identity(repo, receipt_path):
    return {"python": PROVIDER, "helper_sha256": hashlib.sha256(read_regular(repo / HELPER, 256 * 1024)).hexdigest(),
            "controller_sha256": hashlib.sha256(read_regular(repo / ".github/scripts/forge_ci/controller.py", 256 * 1024)).hexdigest(),
            "receipt_sha256": hashlib.sha256(read_regular(receipt_path, 256 * 1024)).hexdigest(),
            "runtime_sha256": hashlib.sha256(read_regular(receipt_path.parent / "runtime-admission.json", MAX_METADATA)).hexdigest()}


def require_job_headroom(binding, now=None):
    now = time.time_ns() if now is None else now
    need(positive(now) and binding["job_started_ns"] <= now
         and now + (STEP_SECONDS + CLEANUP_SECONDS + ARTIFACT_SECONDS) * NS <= binding["job_started_ns"] + JOB_SECONDS * NS,
         "insufficient authenticated job headroom")


def bind_clock(clock, binding):
    require_job_headroom(binding)
    return {**clock, "job_deadline_utc_ns": binding["job_started_ns"] + JOB_SECONDS * NS,
            "artifact_deadline_utc_ns": binding["job_started_ns"] + (JOB_SECONDS - ARTIFACT_SECONDS) * NS}


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
    raw = canonical({"schema_version": 1, "spec_sha256": SPEC_SHA256, "binding": capsule["binding"], "source": capsule["source"],
                     "receipt_sha256": capsule["entrypoint"]["receipt_sha256"],
                     "unit": unit_name(capsule["binding"]), **value})
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
                    document = local_receipt(repo, Path(capsule["receipt"]), capsule["environment"])
                    need(document["binding"] == capsule["binding"] and document["source"] == capsule["source"], "bootstrap source binding mismatch")
                    need(entrypoint_identity(repo, Path(capsule["receipt"])) == capsule["entrypoint"] and Path(__file__).resolve(strict=True) == repo / HELPER, "bootstrap entrypoint mismatch")
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
    validate_binding(result.get("binding"))
    validate_source(result.get("source"), result["binding"])
    need(type(result.get("schema_version")) is int and result["schema_version"] == 1
         and canonical(result["binding"]) == canonical(capsule["binding"])
         and result["unit"] == unit_name(capsule["binding"])
         and result["spec_sha256"] == SPEC_SHA256 and canonical(result["source"]) == canonical(capsule["source"])
         and result["receipt_sha256"] == capsule["entrypoint"]["receipt_sha256"], "service receipt binding mismatch")
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


def launcher(receipt_path, repo, evidence):
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
                document = local_receipt(repo, receipt_path, env)
                binding = document["binding"]
                need(evidence.parent.resolve(strict=True) == evidence.parent and not evidence.exists() and not evidence.is_symlink(), "controller evidence is not fresh")
                capsule = {"schema_version": 1, "kind": "qualification", "binding": binding, "owner": owner,
                           "repo": str(repo), "cwd": str(repo), "evidence": str(evidence),
                           "receipt": str(receipt_path), "source": document["source"],
                           "entrypoint": entrypoint_identity(repo, receipt_path),
                           "clock": clock, "environment": env}
                clock = bind_clock(clock, binding)
                capsule["clock"] = clock
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
                require_job_headroom(binding)
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


# First-B runtime admission is produced only after the fixed reviewed installer.
# None of these recorded runtime hashes is a pre-install or self-authorizing pin.
RUNTIME_PROFILE = "first-B-auth-v2-private-node"
RUNTIME_INVENTORY_LIMIT = 8 * 1024 * 1024
RUNTIME_FILE_LIMIT = 512 * 1024 * 1024
RUNTIME_TOTAL_LIMIT = 2 * 1024 * 1024 * 1024
RUNTIME_ENTRIES = 50000
REQUIRED_TOOLS = ("python", "python3", "git", "bash", "sh", "bwrap", "node", "npm", "semgrep", "ruff")
OLD_PROFILE_PATH = "/opt/hostedtoolcache/Python/3.12.14/x64/bin:/usr/local/bin:/usr/bin:/bin"
PATH_SELECTION_TOOLS = (*REQUIRED_TOOLS, "shellcheck", "eslint", "mutmut", "gremlins", "python3.9", "python3.12",
                        "python3.14", "code-forge", "code-forge-mcp", "pytest", "tee", "df", "cp", "perl", "grep", "pgrep", "true")
PATH_SELECTION_LABELS = ("provider_bin", "usr_bin", "bin_alias")
OLD_PATH_SELECTION_LABELS = ("provider_bin", "usr_local_bin", "usr_bin", "bin_alias")
PATH_STOP_LIMIT = 4096
PATH_STOP_KEYS = {"schema_version", "kind", "status", "gate", "run_id", "run_attempt", "candidate_sha",
                  "workflow_sha", "workflow_job", "boot_id", "observations"}
INSTALL_ARGV = [
    ["python", "-m", "pip", "install", "-e", ".[dev,mcp,semgrep,vertex]", "pytest==9.1.1"],
    ["python", "-m", "pip", "install", "--target", "SYSTEM_USER_SITE", "pytest==9.1.1"],
]
INSTALL_RECORDS = ("node-provision.json", "install.log", "system-pytest-install.log", "interpreters.log", "pip-check.log", "requirements.freeze.txt")
CREDENTIAL_PATHS = (".aws", ".azure", ".ssh", ".claude", ".claude.json", ".netrc", ".git-credentials", ".npmrc", ".pypirc",
                    ".config/gcloud", ".config/gh", ".config/claude", ".config/openai", ".config/pip", ".local/share/keyrings")
RUNTIME_KEYS = {"schema_version", "profile", "spec_sha256", "source", "environment_sha256", "installer_sha256",
                "records_sha256", "profile_metadata", "install_argv", "generated_install_metadata"}
RUNTIME_PROBE = r'''import hashlib, importlib.metadata, json, pathlib, site, sys, sysconfig
import pytest, _pytest.cacheprovider
assert sys.implementation.name == 'cpython' and sys.version_info[:2] == (3, 12)
assert pytest.__version__ == '9.1.1'
assert set(_pytest.cacheprovider.CACHEDIR_FILES) == {'.gitignore', 'README.md', 'CACHEDIR.TAG'}
packages = sorted([[d.metadata['Name'], d.version, str(pathlib.Path(d.locate_file('')).resolve())] for d in importlib.metadata.distributions()])
assert all(type(n) is str and n and type(v) is str and v and type(p) is str for n, v, p in packages)
assert len(packages) <= 4096 and len({(n.lower().replace('_', '-'), p) for n,v,p in packages}) == len(packages)
print(json.dumps({'executable':sys.executable, 'version':list(sys.version_info[:3]),
 'user_site':site.getusersitepackages(), 'import_roots':list(sys.path), 'package_roots':sorted(set([sysconfig.get_path('purelib'), sysconfig.get_path('platlib'), site.getusersitepackages()])),
 'pytest_path':str(pathlib.Path(pytest.__file__).resolve()), 'cache_source':str(pathlib.Path(_pytest.cacheprovider.__file__).resolve()),
 'cache_support':{n:hashlib.sha256(b).hexdigest() for n,b in _pytest.cacheprovider.CACHEDIR_FILES.items()}, 'packages':packages},
 sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False))
'''


# Source pins derive from the independently signature-verified official release.
# A local completion record is evidence, never authority for vendor bytes.
NODE_ARCHIVE_URL = "https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-x64.tar.xz"
NODE_ARCHIVE_BYTES = 31890184
NODE_ARCHIVE_SHA256 = "fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6"
NODE_DECODED_BYTES = 206673920
NODE_DECODED_SHA256 = "ae7b5f0310ed1df3b07f969e34e40104e5605d9e99c50b76998458fef060fdec"
NODE_MANIFEST_BYTES = 446005
NODE_MANIFEST_SHA256 = "1a32412b8434368fed1ea865a5e1d8f072756e2462165e5ec806d09a01fb3f35"
NODE_SELECTED_BYTES = 138935660
NODE_ARCHIVE_ROOT = "node-v24.21.0-linux-x64"
NODE_ALIASES = {"node": "../runtime/bin/node", "npm": "../runtime/lib/node_modules/npm/bin/npm-cli.js"}
NODE_VENDOR_LINKS = {"bin/npm": "../lib/node_modules/npm/bin/npm-cli.js", "bin/npx": "../lib/node_modules/npm/bin/npx-cli.js",
                     "bin/corepack": "../lib/node_modules/corepack/dist/corepack.js"}
NODE_RECORD_LIMIT = 16 * 1024
NODE_RECORD_KEYS = {"schema_version", "kind", "binding", "vendor", "root", "expose", "aliases"}
NODE_CACHE_PROBE = ("const m=require('node:module');const s=m.enableCompileCache();"
                    "if(s.status!==m.constants.compileCacheStatus.DISABLED)process.exit(1);"
                    "process.stdout.write(JSON.stringify({arch:process.arch,compile_cache:'DISABLED',"
                    "executable:process.execPath,platform:process.platform,version:process.version})+'\\n')")


def node_root(environment):
    runner = environment.get("RUNNER_TEMP")
    run, attempt = environment.get("GITHUB_RUN_ID"), environment.get("GITHUB_RUN_ATTEMPT")
    need(type(runner) is str and 0 < len(runner) <= 4096 and runner.isprintable() and ":" not in runner,
         "invalid diagnostic runtime root")
    path = Path(runner)
    need(path.is_absolute() and str(path) == runner and ".." not in path.parts, "invalid diagnostic runtime root")
    need(type(run) is str and re.fullmatch(r"[1-9][0-9]{0,18}", run) is not None and int(run) < 2**63 and attempt == "1",
         "invalid diagnostic runtime identity")
    result = path / ("forge-b-node-" + run + "-1")
    need(len(str(result / "expose").encode("utf-8")) <= 4096, "invalid diagnostic runtime root")
    return result


def profile_path(environment):
    return str(Path(PROVIDER).parent) + ":" + str(node_root(environment) / "expose") + ":/usr/bin:/bin"


def node_pin():
    return {"archive_sha256": NODE_ARCHIVE_SHA256, "archive_bytes": NODE_ARCHIVE_BYTES,
            "manifest_sha256": NODE_MANIFEST_SHA256, "manifest_bytes": NODE_MANIFEST_BYTES,
            "regular_bytes": NODE_SELECTED_BYTES, "entries": 2387, "files": 1928, "directories": 459,
            "node_version": "v24.21.0", "npm_version": "11.19.0"}


def node_binding(environment, deadline_ns):
    document, _ = runtime_json(Path(environment["EVIDENCE"]) / "launch-bootstrap.json", 256 * 1024, deadline_ns)
    binding, source = document["binding"], document["source"]
    validate_binding(binding)
    validate_source(source, binding)
    need(str(binding["run_id"]) == environment["GITHUB_RUN_ID"] and binding["run_attempt"] == 1
         and source["candidate_sha"] == environment["GITHUB_SHA"] == environment["GITHUB_WORKFLOW_SHA"]
         and binding["boot_id"] == read_regular("/proc/sys/kernel/random/boot_id", 64).decode("ascii").strip(),
         "private Node provision binding changed")
    need(runtime_file(Path(environment["GITHUB_WORKSPACE"]) / HELPER, deadline_ns,
                      diagnostic_role="node_binding_helper", diagnostic_member="user_service.py")["sha256"] == source["helper_sha256"][HELPER],
         "private Node provision binding changed")
    return {"run_id": binding["run_id"], "run_attempt": 1, "boot_id": binding["boot_id"],
            "candidate_sha": source["candidate_sha"], "source_sha256": source["source_sha256"],
            "source_record_sha256": runtime_digest(source), "job_started_ns": binding["job_started_ns"]}


def node_aliases(root, deadline_ns):
    """The two explicitly generated symlinks are outside the regular tree walker."""
    runtime_remaining(deadline_ns)
    root, expose = Path(root), Path(root) / "expose"
    runtime_directory(root, private=True)
    before = expose.lstat()
    runtime_directory(expose, private=True)
    need(sorted(p.name for p in expose.iterdir()) == sorted(NODE_ALIASES), "private Node exposure changed")
    result = {}
    for name, target in NODE_ALIASES.items():
        runtime_remaining(deadline_ns)
        path = expose / name
        info = path.lstat()
        need(stat.S_ISLNK(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid() and info.st_gid == os.getgid()
             and os.readlink(path) == target, "private Node exposure changed")
        expected = root / target.removeprefix("../")
        need(path.resolve(strict=True) == expected and expected.resolve(strict=True) == expected,
             "private Node exposure changed")
        identity = expected.lstat()
        need(stat.S_ISREG(identity.st_mode) and identity.st_nlink == 1 and stat.S_IMODE(identity.st_mode) == 0o700
             and identity.st_uid == os.getuid() and identity.st_gid == os.getgid()
             and runtime_stat(expected.lstat()) == runtime_stat(identity)
             and runtime_stat(path.lstat()) == runtime_stat(info) and os.readlink(path) == target,
             "private Node exposure changed")
        result[name] = {"path": str(path), "target": target, "realpath": str(expected), "device": info.st_dev,
                        "inode": info.st_ino, "uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode)}
    need(runtime_stat(expose.lstat()) == runtime_stat(before)
         and sorted(p.name for p in expose.iterdir()) == sorted(NODE_ALIASES), "private Node exposure changed")
    return result


def node_vendor_inventory(root, deadline_ns):
    """Hash the actual entire prefix into the fixed normalized vendor manifest."""
    root, base = Path(root), Path(root) / "runtime"
    runtime_directory(root, private=True)
    runtime_directory(base, private=True)
    rows, files, directories, seen_directories = {}, [], [], {}
    count = total = 0
    for current, dirs, names in os.walk(base, followlinks=False, onerror=runtime_walk_error):
        runtime_remaining(deadline_ns)
        current = Path(current)
        for path in [current, *(current / n for n in dirs), *(current / n for n in names)]:
            name = path.relative_to(root).as_posix()
            if name in rows:
                continue
            count += 1
            need(count <= 2387, "private Node vendor inventory changed")
            info = path.lstat()
            need(info.st_uid == os.getuid() and info.st_gid == os.getgid(), "private Node vendor inventory changed")
            if stat.S_ISDIR(info.st_mode):
                directories.append(runtime_directory(path, private=True))
                seen_directories[path] = runtime_stat(info)
                row = {"path": name, "type": "directory", "mode": "0700", "bytes": 0, "sha256": None}
            else:
                item = runtime_file(path, deadline_ns, diagnostic_role="node_vendor_leaf", diagnostic_root=root)
                need(item["mode"] in {0o600, 0o700} and item["uid"] == os.getuid() and item["gid"] == os.getgid(),
                     "private Node vendor inventory changed")
                total += item["bytes"]
                need(total <= NODE_SELECTED_BYTES, "private Node vendor inventory changed")
                files.append(item)
                row = {"path": name, "type": "regular", "mode": format(item["mode"], "04o"),
                       "bytes": item["bytes"], "sha256": item["sha256"]}
            rows[name] = row
    for path, identity in seen_directories.items():
        runtime_remaining(deadline_ns)
        need(runtime_stat(path.lstat()) == identity and path.resolve(strict=True) == path,
             "private Node vendor inventory changed")
    raw = canonical([rows[name] for name in sorted(rows)])
    need(len(files) == 1928 and len(directories) == 459 and total == NODE_SELECTED_BYTES
         and len(raw) == NODE_MANIFEST_BYTES and hashlib.sha256(raw).hexdigest() == NODE_MANIFEST_SHA256,
         "private Node vendor inventory changed")
    runtime_remaining(deadline_ns)
    return {"manifest_sha256": NODE_MANIFEST_SHA256, "manifest_bytes": len(raw), "regular_bytes": total,
            "files": files, "directories": directories}


def node_provision(environment, deadline_ns):
    root = node_root(environment)
    runtime_directory(root, private=True)
    need(sorted(p.name for p in root.iterdir()) == ["expose", "runtime"], "private Node provision incomplete")
    value, checksum = runtime_json(Path(environment["EVIDENCE"]) / "node-provision.json", NODE_RECORD_LIMIT, deadline_ns)
    need(type(value) is dict and value.keys() == NODE_RECORD_KEYS and type(value["schema_version"]) is int
         and value["schema_version"] == 1 and value["kind"] == "private-pinned-node"
         and value["vendor"] == node_pin() and value["binding"] == node_binding(environment, deadline_ns),
         "private Node provision binding changed")
    need(value["root"] == runtime_directory(root, private=True)
         and value["expose"] == runtime_directory(root / "expose", private=True), "private Node provision binding changed")
    node_vendor_inventory(root, deadline_ns)
    need(value["aliases"] == node_aliases(root, deadline_ns), "private Node exposure changed")
    return {"record_sha256": checksum, **value}


class NodeXZReader:
    """Public file interface only; limit each decoder output and its private memory."""
    def __init__(self, stream, deadline_ns):
        import lzma
        self.stream, self.deadline_ns = stream, deadline_ns
        self.decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=128 * 1024 * 1024)
        self.total, self.sha, self.ended = 0, hashlib.sha256(), False

    def read(self, size):
        need(type(size) is int and size >= 0, "private Node archive rejected")
        if size == 0:
            return b""
        while not self.ended:
            runtime_remaining(self.deadline_ns)
            incoming = self.stream.read(65536) if self.decoder.needs_input else b""
            need(incoming or not self.decoder.needs_input, "private Node archive rejected")
            chunk = self.decoder.decompress(incoming, max_length=min(size, 65536))
            self.total += len(chunk)
            need(self.total <= 256 * 1024 * 1024, "private Node archive rejected")
            self.sha.update(chunk)
            if self.decoder.eof:
                need(not self.decoder.unused_data and not self.stream.read(1), "private Node archive rejected")
                self.ended = True
            if chunk:
                return chunk
        return b""


def node_member_path(member):
    name = member.name.rstrip("/")
    parts = name.split("/")
    try:
        encoded = name.encode("utf-8", "strict")
        lengths = [len(part.encode("utf-8", "strict")) for part in parts]
    except UnicodeError as exc:
        raise ServiceError("private Node archive rejected") from exc
    need(len(encoded) <= 256 and len(parts) <= 16 and all(0 < n <= 128 for n in lengths)
         and all(part not in {".", ".."} for part in parts) and parts[0] == NODE_ARCHIVE_ROOT
         and not name.startswith("/") and "\\" not in name and "\0" not in name
         and not member.pax_headers and not member.issparse(), "private Node archive rejected")
    return name, "/".join(parts[1:])


def node_extract(archive_path, root, deadline_ns):
    """Authenticate before decode; exclusively create the exact selected regular tree."""
    import tarfile
    archive_path, root = Path(archive_path), Path(root)
    runtime_directory(root, private=True)
    need(archive_path.lstat().st_size == NODE_ARCHIVE_BYTES, "private Node archive identity changed")
    # This label names the expected pinned artifact, not the staging basename
    # or authenticated archive content: its hash is checked only after this call.
    archive = runtime_file(archive_path, deadline_ns, diagnostic_role="node_archive", diagnostic_member="node-v24.21.0-linux-x64.tar.xz")
    need(archive["bytes"] == NODE_ARCHIVE_BYTES and archive["sha256"] == NODE_ARCHIVE_SHA256,
         "private Node archive identity changed")
    need(not os.path.lexists(root / "runtime"), "private Node provision incomplete")
    created, rows, seen = set(), {}, set()

    def add_directory(relative):
        if relative in created:
            return
        target = root / relative
        if target.parent != root:
            add_directory(target.parent.relative_to(root).as_posix())
        runtime_remaining(deadline_ns)
        parent = runtime_directory(target.parent, private=True)
        fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            need((os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (parent["device"], parent["inode"]),
                 "private Node materialization changed")
            os.mkdir(target.name, 0o700, dir_fd=fd)
            runtime_directory(target, private=True)
            need(runtime_directory(target.parent, private=True) == parent, "private Node materialization changed")
        finally:
            os.close(fd)
        created.add(relative)
        rows[relative] = {"path": relative, "type": "directory", "mode": "0700", "bytes": 0, "sha256": None}

    add_directory("runtime")
    count = files = directories = links = regular = 0
    fd = os.open(archive_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = runtime_stat(os.fstat(stream.fileno()))
        # Recheck the retained opened archive so replacing it between hash/open
        # cannot substitute a different compressed stream.
        digest, received = hashlib.sha256(), 0
        while True:
            runtime_remaining(deadline_ns)
            chunk = stream.read(65536)
            if not chunk:
                break
            received += len(chunk)
            need(received <= NODE_ARCHIVE_BYTES, "private Node archive identity changed")
            digest.update(chunk)
        need(received == NODE_ARCHIVE_BYTES and digest.hexdigest() == NODE_ARCHIVE_SHA256,
             "private Node archive identity changed")
        stream.seek(0)
        decoded = NodeXZReader(stream, deadline_ns)
        with tarfile.open(fileobj=decoded, mode="r|", bufsize=10240) as tar:
            for member in tar:
                runtime_remaining(deadline_ns)
                count += 1
                need(count <= 6000, "private Node archive rejected")
                name, relative = node_member_path(member)
                need(name not in seen and (relative or member.isdir()), "private Node archive rejected")
                seen.add(name)
                take = relative in {"bin/node", "LICENSE", "lib/node_modules/npm"} or relative.startswith("lib/node_modules/npm/")
                destination = "runtime/" + relative
                if member.isdir():
                    directories += 1
                    need(member.mode == 0o755 and member.size == 0, "private Node archive rejected")
                    if take:
                        add_directory(destination)
                elif member.issym():
                    links += 1
                    need(relative in NODE_VENDOR_LINKS and member.linkname == NODE_VENDOR_LINKS[relative]
                         and member.mode == 0o777 and member.size == 0, "private Node archive rejected")
                else:
                    need(member.type == tarfile.REGTYPE and member.mode in {0o644, 0o755} and 0 <= member.size <= 128 * 1024 * 1024,
                         "private Node archive rejected")
                    files += 1
                    regular += member.size
                    need(regular <= 256 * 1024 * 1024, "private Node archive rejected")
                    output = parent_fd = None
                    checksum, total = hashlib.sha256(), 0
                    mode = 0o700 if member.mode & 0o111 else 0o600
                    if take:
                        target = root / destination
                        add_directory(target.parent.relative_to(root).as_posix())
                        parent = runtime_directory(target.parent, private=True)
                        parent_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                        need((os.fstat(parent_fd).st_dev, os.fstat(parent_fd).st_ino) == (parent["device"], parent["inode"]),
                             "private Node materialization changed")
                    try:
                        if take:
                            output = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                             mode, dir_fd=parent_fd)
                        with tar.extractfile(member) as content:
                            while True:
                                runtime_remaining(deadline_ns)
                                chunk = content.read(65536)
                                if not chunk:
                                    break
                                total += len(chunk)
                                need(total <= member.size, "private Node archive rejected")
                                if take:
                                    checksum.update(chunk)
                                    view = memoryview(chunk)
                                    while view:
                                        runtime_remaining(deadline_ns)
                                        written = os.write(output, view)
                                        need(written > 0, "private Node materialization changed")
                                        view = view[written:]
                        need(total == member.size, "private Node archive rejected")
                        if take:
                            info = os.fstat(output)
                            need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                                 and info.st_gid == os.getgid() and stat.S_IMODE(info.st_mode) == mode and info.st_size == total,
                                 "private Node materialization changed")
                            need(runtime_stat(target.lstat()) == runtime_stat(info)
                                 and runtime_directory(target.parent, private=True) == parent,
                                 "private Node materialization changed")
                            rows[destination] = {"path": destination, "type": "regular", "mode": format(mode, "04o"),
                                                 "bytes": total, "sha256": checksum.hexdigest()}
                    finally:
                        if output is not None:
                            os.close(output)
                        if parent_fd is not None:
                            os.close(parent_fd)
        while decoded.read(65536):
            runtime_remaining(deadline_ns)
        need(decoded.total == NODE_DECODED_BYTES and decoded.sha.hexdigest() == NODE_DECODED_SHA256
             and (count, files, directories, links, regular) == (5888, 4797, 1088, 3, 201362264),
             "private Node archive rejected")
        need(before == runtime_stat(os.fstat(stream.fileno())) == runtime_stat(archive_path.lstat()),
             "private Node archive identity changed")
    raw = canonical([rows[name] for name in sorted(rows)])
    need(len(raw) == NODE_MANIFEST_BYTES and hashlib.sha256(raw).hexdigest() == NODE_MANIFEST_SHA256,
         "private Node vendor inventory changed")
    return node_vendor_inventory(root, deadline_ns)


@contextmanager
def node_wall_limit(deadline_ns):
    import http.client
    # Worker-only alarm also bounds libc DNS and TLS/header processing, which a
    # socket timeout alone does not bound. Never overwrite an earlier timer.
    need(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "private Node component deadline")
    old_handler = signal.getsignal(signal.SIGALRM)
    failure = None

    def retain(exc):
        nonlocal failure
        expected = (ServiceError, OSError, http.client.HTTPException)
        old_control = failure is not None and (getattr(failure, "_forge_control", False) or not isinstance(failure, expected))
        new_control = getattr(exc, "_forge_control", False) or not isinstance(exc, expected)
        if failure is None or (not old_control and new_control):
            failure = exc

    def expired(signum, frame):
        error = ServiceError("private Node component deadline")
        error._forge_control = True
        raise error

    try:
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, runtime_remaining(deadline_ns))
        yield
        runtime_remaining(deadline_ns)
    except BaseException as exc:  # noqa: BLE001 - preserve first control through both restorations
        retain(exc)
    finally:
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
        except BaseException as exc:  # noqa: BLE001 - still attempt the independent handler restore
            retain(exc)
        try:
            signal.signal(signal.SIGALRM, old_handler)
        except BaseException as exc:  # noqa: BLE001
            retain(exc)
    if failure is not None:
        raise failure


def node_download(root, deadline_ns):
    """One fixed TLS GET; http.client has no ambient proxy/auth/redirect handlers."""
    import http.client
    import ssl
    runtime_remaining(deadline_ns)
    transfer_deadline = min(deadline_ns, time.monotonic_ns() + 60 * NS)
    connection = http.client.HTTPSConnection("nodejs.org", timeout=min(10, runtime_remaining(transfer_deadline)),
                                            context=ssl.create_default_context())
    path = Path(root) / "archive.tar.xz"
    fd = response = failure = None

    def retain(exc):
        nonlocal failure
        expected = (ServiceError, OSError, http.client.HTTPException)
        old_control = failure is not None and (getattr(failure, "_forge_control", False) or not isinstance(failure, expected))
        new_control = getattr(exc, "_forge_control", False) or not isinstance(exc, expected)
        if failure is None or (not old_control and new_control):
            failure = exc

    try:
        with node_wall_limit(min(transfer_deadline, time.monotonic_ns() + 10 * NS)):
            connection.connect()
        with node_wall_limit(transfer_deadline):
            connection.sock.settimeout(min(10, runtime_remaining(transfer_deadline)))
            connection.request("GET", "/dist/v24.21.0/node-v24.21.0-linux-x64.tar.xz",
                               headers={"Accept-Encoding": "identity", "Connection": "close"})
            response = connection.getresponse()
            need(response.status == 200 and response.getheader("Content-Encoding") in {None, "identity"}
                 and response.getheader("Content-Length") == str(NODE_ARCHIVE_BYTES), "private Node download rejected")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            digest, total = hashlib.sha256(), 0
            while True:
                runtime_remaining(transfer_deadline)
                # read1 performs at most one underlying socket read. The outer
                # original transfer alarm also covers a peer trickling headers.
                if connection.sock is not None:
                    connection.sock.settimeout(min(10, runtime_remaining(transfer_deadline)))
                chunk = response.read1(65536)
                runtime_remaining(transfer_deadline)
                if not chunk:
                    break
                total += len(chunk)
                need(total <= 32 * 1024 * 1024 and total <= NODE_ARCHIVE_BYTES, "private Node download rejected")
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    runtime_remaining(transfer_deadline)
                    written = os.write(fd, view)
                    need(written > 0, "private Node download rejected")
                    view = view[written:]
            need(total == NODE_ARCHIVE_BYTES and digest.hexdigest() == NODE_ARCHIVE_SHA256,
                 "private Node archive identity changed")
    except BaseException as exc:  # noqa: BLE001 - retain the first control through closure
        retain(exc)
    finally:
        closers = ([lambda: os.close(fd)] if fd is not None else [])
        closers += ([response.close] if response is not None else [])
        closers.append(connection.close)
        for close in closers:
            try:
                close()
            except BaseException as exc:  # noqa: BLE001
                retain(exc)
    if failure is not None:
        raise failure
    runtime_remaining(transfer_deadline)
    return path


def node_owned_command(argv, environment, cwd, deadline_ns, *, settlement_ns=5 * NS):
    """One retained child/session, one original cutoff, one bounded settlement."""
    runtime_remaining(deadline_ns)
    need(type(settlement_ns) is int and 0 < settlement_ns <= 5 * NS, "private Node component deadline")
    cutoff = deadline_ns - settlement_ns
    need(time.monotonic_ns() < cutoff, "private Node component deadline")
    process = selector = failure = result = None
    wait_owned = False
    chunks = {"stdout": bytearray(), "stderr": bytearray()}

    def retain(exc):
        nonlocal failure
        expected = (ServiceError, OSError, subprocess.TimeoutExpired)
        old_control = failure is not None and (getattr(failure, "_forge_control", False) or not isinstance(failure, expected))
        new_control = getattr(exc, "_forge_control", False) or not isinstance(exc, expected)
        if failure is None or (not old_control and new_control):
            failure = exc

    def observe():
        nonlocal wait_owned
        need(wait_owned, "private Node worker settlement incomplete")
        try:
            terminal = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            need(terminal is None or (terminal.si_pid == process.pid
                 and terminal.si_code in {os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED}),
                 "private Node worker settlement incomplete")
            return terminal
        except BaseException:  # noqa: BLE001 - never signal after uncertain wait ownership
            wait_owned = False
            raise

    try:
        process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, start_new_session=True)
        wait_owned = True
        selector = selectors.DefaultSelector()
        for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while True:
            left = (cutoff - time.monotonic_ns()) / NS
            need(left > 0, "private Node component deadline")
            if not selector.get_map():
                terminal = observe()
                if terminal is not None:
                    # Retain the original leader PID while signaling its group,
                    # including members that closed both inherited pipes. This
                    # proves signaling and direct-child reap, not whole-tree
                    # quiescence or supervision of an escaped session.
                    need(time.monotonic_ns() < cutoff, "private Node component deadline")
                    need(os.getpgid(process.pid) == process.pid and os.getsid(process.pid) == process.pid,
                         "private Node worker settlement incomplete")
                    need(time.monotonic_ns() < cutoff, "private Node component deadline")
                    os.killpg(process.pid, signal.SIGKILL)
                    need(time.monotonic_ns() < cutoff, "private Node component deadline")
                    code = process.wait(timeout=0)
                    wait_owned = False
                    expected_code = terminal.si_status if terminal.si_code == os.CLD_EXITED else -terminal.si_status
                    need(code == expected_code, "private Node worker settlement incomplete")
                    break
            for key, _ in selector.select(min(left, 0.1)):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                chunks[key.data].extend(chunk)
                need(sum(map(len, chunks.values())) <= MAX_METADATA, "private Node component output bound")
        need(process.returncode == 0 and not chunks["stderr"], "private Node worker failed")
        result = bytes(chunks["stdout"])
    except BaseException as exc:  # noqa: BLE001 - first control/unexpected error keeps identity
        retain(exc)
    finally:
        if selector is not None:
            try:
                selector.close()
            except BaseException as exc:  # noqa: BLE001
                retain(exc)
        if process is not None:
            try:
                # No poll/reap precedes this path. An exited leader still owns
                # its PID, so its original group can be signaled safely.
                if process.returncode is None and wait_owned and time.monotonic_ns() < deadline_ns:
                    observe()
                    need(os.getpgid(process.pid) == process.pid and os.getsid(process.pid) == process.pid,
                         "private Node worker settlement incomplete")
                    need(time.monotonic_ns() < deadline_ns, "private Node component deadline")
                    os.killpg(process.pid, signal.SIGKILL)
                    left = (deadline_ns - time.monotonic_ns()) / NS
                    if left > 0:
                        process.wait(timeout=min(5, left))
                        wait_owned = False
                need(process.returncode is not None, "private Node worker settlement incomplete")
            except BaseException as exc:  # noqa: BLE001 - no second wait or renewed budget
                retain(exc)
            finally:
                for pipe in (process.stdout, process.stderr):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except BaseException as exc:  # noqa: BLE001
                            retain(exc)
    if failure is not None:
        raise failure
    need(time.monotonic_ns() < cutoff, "private Node component deadline")
    return result


def node_install_worker(environment, deadline_ns):
    check_private_profile(environment, deadline_ns=deadline_ns)
    root = node_root(environment)
    need(sorted(p.name for p in root.iterdir()) == ["expose"] and not any((root / "expose").iterdir()),
         "private Node provision incomplete")
    binding = node_binding(environment, deadline_ns)
    archive = node_download(root, deadline_ns)
    node_extract(archive, root, deadline_ns)
    runtime_remaining(deadline_ns)
    # No failure cleanup: a partial tree stays unadmitted. Normal reproducible
    # staging removal happens before the original success cutoff only.
    os.unlink(archive)
    runtime_remaining(deadline_ns)
    for name, target in NODE_ALIASES.items():
        os.symlink(target, root / "expose" / name)
    aliases = node_aliases(root, deadline_ns)
    need(binding == node_binding(environment, deadline_ns), "private Node provision binding changed")
    value = {"schema_version": 1, "kind": "private-pinned-node", "binding": binding, "vendor": node_pin(),
             "root": runtime_directory(root, private=True), "expose": runtime_directory(root / "expose", private=True),
             "aliases": aliases}
    runtime_write(Path(environment["EVIDENCE"]) / "node-provision.json", value, NODE_RECORD_LIMIT, deadline_ns)
    runtime_remaining(deadline_ns)


def node_install(environment, deadline_ns):
    started = time.monotonic_ns()
    need(type(deadline_ns) is int and started < deadline_ns <= started + 600 * NS, "private Node component deadline")
    provisional_cutoff = min(started + 175 * NS, deadline_ns - 5 * NS)
    runtime_remaining(provisional_cutoff)
    # Read the existing authenticated clock before scans or network work. This
    # does not add a fresh window to the remaining original prelude budget.
    document, _ = runtime_json(Path(environment["EVIDENCE"]) / "launch-bootstrap.json", 256 * 1024, provisional_cutoff)
    validate_binding(document["binding"])
    validate_source(document["source"], document["binding"])
    clock_monotonic, clock_utc = time.monotonic_ns(), time.time_ns()
    need(document["binding"]["job_started_ns"] <= clock_utc, "private Node component deadline")
    prelude_left = document["binding"]["job_started_ns"] + (JOB_SECONDS - STEP_SECONDS - CLEANUP_SECONDS - ARTIFACT_SECONDS) * NS - clock_utc
    total_deadline = min(started + 180 * NS, deadline_ns, clock_monotonic + prelude_left)
    need(total_deadline > time.monotonic_ns() + 5 * NS, "private Node component deadline")
    work_cutoff = total_deadline - 5 * NS
    binding = node_binding(environment, work_cutoff)
    need(binding["job_started_ns"] == document["binding"]["job_started_ns"], "private Node provision binding changed")
    check_private_profile(environment, deadline_ns=work_cutoff)
    argv = [PROVIDER, "-B", "-I", "-S", str(Path(environment["GITHUB_WORKSPACE"]) / HELPER),
            "node-install-worker", "--deadline-ns", str(work_cutoff)]
    raw = node_owned_command(argv, environment, environment["GITHUB_WORKSPACE"], total_deadline)
    need(raw == b"", "private Node worker failed")
    node_provision(environment, work_cutoff)
    runtime_remaining(work_cutoff)


def node_probe(environment, deadline_ns):
    # Each executable probe starts only after full source/vendor/alias revalidation.
    root, expose = node_root(environment), node_root(environment) / "expose"
    identity = {"arch": "x64", "compile_cache": "DISABLED", "executable": str(root / "runtime/bin/node"),
                "platform": "linux", "version": "v24.21.0"}
    commands = [([str(expose / "node"), "--version"], b"v24.21.0\n"),
                ([str(expose / "npm"), "--prefix=" + str(expose), "--userconfig=" + str(expose / ".npm-user-probe"),
                  "--globalconfig=" + str(expose / ".npm-global-probe"), "--version"], b"11.19.0\n"),
                ([str(expose / "node"), "-e", NODE_CACHE_PROBE], identity)]
    for argv, expected in commands:
        node_provision(environment, deadline_ns)
        need(environment.get("NODE_DISABLE_COMPILE_CACHE") == "1" and environment.get("PATH") == profile_path(environment),
             "changed fixed payload environment")
        timeout = min(5, runtime_remaining(deadline_ns))
        probe_deadline = min(deadline_ns, time.monotonic_ns() + int(timeout * NS))
        settlement_ns = int(min(0.25, timeout / 2) * NS)
        work_cutoff = probe_deadline - settlement_ns
        raw = node_owned_command(argv, environment, expose, probe_deadline, settlement_ns=settlement_ns)
        runtime_remaining(work_cutoff)
        if type(expected) is dict:
            observed = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_no_constant)
            need(type(observed) is dict and observed == expected, "private Node compatibility failed")
        else:
            need(raw == expected, "private Node compatibility failed")
        runtime_remaining(work_cutoff)
        node_aliases(root, work_cutoff)
        runtime_remaining(work_cutoff)
    return {"node_version": "v24.21.0", "npm_version": "11.19.0", "compile_cache": "DISABLED", "node_identity": identity}


def runtime_remaining(deadline_ns):
    need(type(deadline_ns) is int and time.monotonic_ns() < deadline_ns, "runtime profile deadline exceeded")
    return (deadline_ns - time.monotonic_ns()) / NS


def runtime_digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def runtime_json(path, limit, deadline_ns):
    runtime_remaining(deadline_ns)
    raw = read_regular(path, limit)
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_no_constant)
    need(raw == canonical(value), "noncanonical runtime record")
    runtime_remaining(deadline_ns)
    return value, hashlib.sha256(raw).hexdigest()


def runtime_stat(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


FILE_STOP_LIMIT = 2048
FILE_OBSERVATION_KEYS = {"role", "member", "root_class", "relative_name_sha256", "file_type", "uid", "gid", "mode", "nlink", "bytes",
                         "ordinary_uid", "ordinary_gid", "is_regular", "single_link", "write_bits_clear", "uid_allowed", "gid_allowed", "size_allowed"}
FILE_STOP_KEYS = {"schema_version", "kind", "status", "gate", "run_id", "run_attempt", "candidate_sha",
                  "workflow_sha", "workflow_job", "boot_id", "observation"}
FILE_DYNAMIC_CONTEXTS = {"node_vendor_leaf": "private_node_prefix", "runtime_inventory_native": "private_node_prefix",
                         "runtime_inventory_package": "python_package_root"}


def file_root_class(role, member):
    need(type(role) is str, "invalid runtime protected file")
    if role in FILE_DYNAMIC_CONTEXTS:
        need(member is None, "invalid runtime protected file")
        return FILE_DYNAMIC_CONTEXTS[role]
    need(role in FILE_FIXED_CONTEXTS and type(member) is str and member in FILE_FIXED_CONTEXTS[role],
         "invalid runtime protected file")
    return FILE_FIXED_CONTEXTS[role][member]


def validate_file_observation(value):
    # Data-only rejection evidence never authorizes a file or runtime root.
    need(type(value) is dict and value.keys() == FILE_OBSERVATION_KEYS, "invalid runtime protected file")
    root_class = file_root_class(value["role"], value["member"])
    need(type(value["root_class"]) is str and value["root_class"] == root_class, "invalid runtime protected file")
    digest = value["relative_name_sha256"]
    need((type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest) is not None)
         if value["role"] in FILE_DYNAMIC_CONTEXTS else digest is None, "invalid runtime protected file")
    need(type(value["file_type"]) is str and value["file_type"] in {*DIRECTORY_TYPES.values(), "unknown"},
         "invalid runtime protected file")
    need(all(type(value[key]) is int and 0 <= value[key] < 2**32 for key in ("uid", "gid", "ordinary_uid", "ordinary_gid"))
         and value["ordinary_uid"] > 0 and value["ordinary_gid"] > 0, "invalid runtime protected file")
    need(type(value["nlink"]) is int and 0 <= value["nlink"] < 2**64
         and type(value["bytes"]) is int and 0 <= value["bytes"] < 2**63, "invalid runtime protected file")
    need(type(value["mode"]) is str and re.fullmatch(r"[0-7]{4}", value["mode"]) is not None, "invalid runtime protected file")
    predicates = {"is_regular": value["file_type"] == "regular", "single_link": value["nlink"] == 1,
                  "write_bits_clear": not int(value["mode"], 8) & 0o022,
                  "uid_allowed": value["uid"] in {0, value["ordinary_uid"]},
                  "gid_allowed": value["gid"] in {0, value["ordinary_gid"]},
                  "size_allowed": value["bytes"] <= RUNTIME_FILE_LIMIT}
    need(all(type(value[key]) is bool and value[key] == expected for key, expected in predicates.items())
         and not all(predicates.values()), "invalid runtime protected file")


def runtime_file_observation(path, info, uid, gid, role, member, root):
    root_class = file_root_class(role, member)
    digest = None
    if role in FILE_DYNAMIC_CONTEXTS:
        need(isinstance(root, Path) and root.is_absolute(), "invalid runtime protected file")
        relative = path.relative_to(root).as_posix()
        need(relative and not relative.startswith("/") and all(part not in {"", ".", ".."} for part in relative.split("/")),
             "invalid runtime protected file")
        encoded = os.fsencode(relative)
        need(len(encoded) <= 4096, "invalid runtime protected file")
        digest = hashlib.sha256(b"runtime-file-relative-v1\0" + encoded).hexdigest()
    else:
        need(root is None, "invalid runtime protected file")
    observation = {"role": role, "member": member, "root_class": root_class, "relative_name_sha256": digest,
                   "file_type": DIRECTORY_TYPES.get(stat.S_IFMT(info.st_mode), "unknown"),
                   "uid": info.st_uid, "gid": info.st_gid, "mode": format(stat.S_IMODE(info.st_mode), "04o"),
                   "nlink": info.st_nlink, "bytes": info.st_size, "ordinary_uid": uid, "ordinary_gid": gid,
                   "is_regular": stat.S_ISREG(info.st_mode), "single_link": info.st_nlink == 1,
                   "write_bits_clear": not info.st_mode & 0o022,
                   "uid_allowed": info.st_uid in {0, uid}, "gid_allowed": info.st_gid in {0, gid},
                   "size_allowed": info.st_size <= RUNTIME_FILE_LIMIT}
    validate_file_observation(observation)
    return observation


def runtime_file(path, deadline_ns, *, diagnostic_role=None, diagnostic_member=None, diagnostic_root=None):
    runtime_remaining(deadline_ns)
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=True) == path, "runtime protected path alias")
    before = path.lstat()
    uid, gid = os.getuid(), os.getgid()
    try:
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and not before.st_mode & 0o022
             and before.st_uid in {0, uid} and before.st_gid in {0, gid}
             and before.st_size <= RUNTIME_FILE_LIMIT, "invalid runtime protected file")
    except ServiceError as error:
        # Only the same rejecting lstat is observed, before opening the file.
        if getattr(error, "_forge_control", False):
            raise
        try:
            error.file_observation = runtime_file_observation(path, before, uid, gid, diagnostic_role, diagnostic_member, diagnostic_root)
        except Exception as diagnostic_error:  # noqa: BLE001 - optional metadata cannot expose exception values
            if getattr(diagnostic_error, "_forge_control", False):
                raise
        raise
    checksum = hashlib.sha256()
    total = 0
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        need(runtime_stat(os.fstat(stream.fileno())) == runtime_stat(before), "runtime file identity changed")
        while True:
            runtime_remaining(deadline_ns)
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            need(total <= RUNTIME_FILE_LIMIT, "runtime file bound exceeded")
            checksum.update(chunk)
        need(runtime_stat(os.fstat(stream.fileno())) == runtime_stat(before) and runtime_stat(path.lstat()) == runtime_stat(before) and total == before.st_size
             and path.resolve(strict=True) == path, "runtime protected file changed while reading")
    runtime_remaining(deadline_ns)
    return {"path": str(path), "device": before.st_dev, "inode": before.st_ino, "uid": before.st_uid,
            "gid": before.st_gid, "mode": stat.S_IMODE(before.st_mode), "bytes": total, "sha256": checksum.hexdigest()}


DIRECTORY_STOP_LIMIT = 2048
DIRECTORY_LABELS = frozenset({"private_home", "private_config", "private_cache", "private_data", "private_tmp",
                              "private_local", "private_node", "provider_bin", "usr_local_bin", "usr_bin", "bin_alias"})
DIRECTORY_TYPES = {stat.S_IFDIR: "directory", stat.S_IFREG: "regular", stat.S_IFLNK: "symlink",
                   stat.S_IFIFO: "fifo", stat.S_IFSOCK: "socket", stat.S_IFCHR: "character", stat.S_IFBLK: "block"}
DIRECTORY_OBSERVATION_KEYS = {"label", "file_type", "uid", "gid", "mode", "ordinary_uid", "ordinary_gid",
                              "is_directory", "write_bits_clear", "uid_allowed", "gid_allowed"}
DIRECTORY_STOP_KEYS = {"schema_version", "kind", "status", "gate", "run_id", "run_attempt", "candidate_sha",
                       "workflow_sha", "workflow_job", "boot_id", "observation"}


def validate_directory_observation(value):
    # This closed, data-only diagnostic cannot authorize a directory or runtime.
    need(type(value) is dict and value.keys() == DIRECTORY_OBSERVATION_KEYS, "untrusted runtime directory")
    need(type(value["label"]) is str and value["label"] in DIRECTORY_LABELS
         and type(value["file_type"]) is str and value["file_type"] in {*DIRECTORY_TYPES.values(), "unknown"},
         "untrusted runtime directory")
    need(all(type(value[key]) is int and 0 <= value[key] < 2**32 for key in ("uid", "gid", "ordinary_uid", "ordinary_gid"))
         and value["ordinary_uid"] > 0 and value["ordinary_gid"] > 0, "untrusted runtime directory")
    need(type(value["mode"]) is str and re.fullmatch(r"[0-7]{4}", value["mode"]) is not None,
         "untrusted runtime directory")
    predicates = {"is_directory": value["file_type"] == "directory",
                  "write_bits_clear": not int(value["mode"], 8) & 0o022,
                  "uid_allowed": value["uid"] in {0, value["ordinary_uid"]},
                  "gid_allowed": value["gid"] in {0, value["ordinary_gid"]}}
    need(all(type(value[key]) is bool and value[key] == expected for key, expected in predicates.items())
         and not all(predicates.values()), "untrusted runtime directory")


def runtime_directory(path, *, private=False, diagnostic_label=None):
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=True) == path, "runtime directory alias")
    info = path.lstat()
    uid, gid = os.getuid(), os.getgid()
    try:
        need(stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o022
             and info.st_uid in {0, uid} and info.st_gid in {0, gid}, "untrusted runtime directory")
    except ServiceError as error:
        # Capture only this rejecting lstat, never a second observation of path.
        # Diagnostic errors must not replace the original fixed rejection.
        try:
            if type(diagnostic_label) is str and diagnostic_label in DIRECTORY_LABELS:
                observation = {"label": diagnostic_label, "file_type": DIRECTORY_TYPES.get(stat.S_IFMT(info.st_mode), "unknown"),
                               "uid": info.st_uid, "gid": info.st_gid, "mode": format(stat.S_IMODE(info.st_mode), "04o"),
                               "ordinary_uid": uid, "ordinary_gid": gid, "is_directory": stat.S_ISDIR(info.st_mode),
                               "write_bits_clear": not info.st_mode & 0o022,
                               "uid_allowed": info.st_uid in {0, uid}, "gid_allowed": info.st_gid in {0, gid}}
                validate_directory_observation(observation)
                error.directory_observation = observation
        except Exception as diagnostic_error:  # noqa: BLE001 - optional metadata cannot expose exception values
            if getattr(diagnostic_error, "_forge_control", False):
                raise
        raise
    if private:
        need(info.st_uid == os.getuid() and info.st_gid == os.getgid() and stat.S_IMODE(info.st_mode) == 0o700,
             "runtime root is not private")
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino,
            "uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode)}


def check_private_profile(environment, *, deadline_ns):
    validate_environment(environment)
    runtime_remaining(deadline_ns)
    need(os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0, "runtime profile requires ordinary owner")
    runner = Path(environment["RUNNER_TEMP"])
    need(runner.resolve(strict=True) == runner, "runtime ancestor alias")
    for ancestor in (runner, *runner.parents):
        info = ancestor.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.getuid()}
             and (not info.st_mode & 0o022 or (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)),
             "untrusted runtime ancestor")
    roots = [runtime_directory(environment[key], private=True, diagnostic_label=label)
             for key, label in (("HOME", "private_home"), ("XDG_CONFIG_HOME", "private_config"),
                                ("XDG_CACHE_HOME", "private_cache"), ("XDG_DATA_HOME", "private_data"), ("TMPDIR", "private_tmp"))]
    home = Path(environment["HOME"])
    runtime_directory(home / ".local", private=True, diagnostic_label="private_local")
    for relative in CREDENTIAL_PATHS:
        candidate = home / relative
        need(not candidate.exists() and not candidate.is_symlink(), "credential configuration present")
    # Config starts empty, and the fixed dependency installer has no reason to
    # populate it. Reject unknown configuration rather than interpreting secrets.
    need(not any((home / ".config").iterdir()), "unexpected private HOME configuration")
    node = node_root(environment)
    roots.append(runtime_directory(node, private=True, diagnostic_label="private_node"))
    runtime_directory(node / "expose", private=True, diagnostic_label="private_node")
    members = sorted(p.name for p in (node / "expose").iterdir())
    if not members:
        need(sorted(p.name for p in node.iterdir()) == ["expose"], "private Node provision incomplete")
    else:
        need(members == sorted(NODE_ALIASES), "private Node exposure changed")
        node_provision(environment, deadline_ns)
    directories = []
    for component, label in zip(profile_path(environment).split(":"), ("provider_bin", "private_node", "usr_bin", "bin_alias"), strict=True):
        path = Path(component)
        real = path.resolve(strict=True)
        need(str(real) == component or (component == "/bin" and str(real) == "/usr/bin"), "unreviewed PATH alias")
        identity = runtime_directory(real, diagnostic_label=label)
        directories.append({"path": component, "realpath": str(real), **{k: v for k, v in identity.items() if k != "path"}})
        cli = path / "claude"
        need(not (cli.exists() and os.access(cli, os.X_OK)), "runnable claude present")
    runtime_remaining(deadline_ns)
    return {"roots": roots, "path_directories": directories}


def runtime_executable(name, deadline_ns, environment=None):
    import shutil
    environment = os.environ if environment is None else environment
    if name in NODE_ALIASES:
        node_provision(environment, deadline_ns)
    resolved = shutil.which(name, path=profile_path(environment))
    need(resolved is not None, "declared diagnostic tool missing")
    path = Path(resolved)
    real = path.resolve(strict=True)
    if name in NODE_ALIASES:
        need(path == node_root(environment) / "expose" / name
             and real == node_root(environment) / NODE_ALIASES[name].removeprefix("../"), "private Node exposure changed")
    result = runtime_file(real, deadline_ns, diagnostic_role="required_executable", diagnostic_member=name)
    need(result["mode"] & 0o111, "runtime tool is not executable")
    return {"requested": name, "path": str(path), "realpath": str(real), "identity": result}


def runtime_probe(interpreter, environment, deadline_ns):
    raw = metadata([interpreter, "-B", "-c", RUNTIME_PROBE], environment,
                   timeout=min(5, runtime_remaining(deadline_ns)))
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_no_constant)
    keys(value, {"executable", "version", "user_site", "package_roots", "import_roots", "pytest_path", "cache_source", "cache_support", "packages"}, "runtime interpreter")
    need(value["version"][:2] == [3, 12] and type(value["packages"]) is list, "wrong diagnostic Python or packages")
    site = Path(value["user_site"])
    home = Path(environment["HOME"])
    need(site.is_absolute() and site.is_relative_to(home) and site.resolve(strict=True) == site,
         "system pytest site outside diagnostic HOME")
    need(Path(value["pytest_path"]).is_relative_to(site) if interpreter == "/usr/bin/python3" else True,
         "system pytest imported outside diagnostic HOME")
    return runtime_import_plan(value, environment, interpreter)


def runtime_import_plan(value, environment, interpreter):
    """Classify only the active import search roots, never the whole host.

    Checkout roots retain full launch/source proof. The two literal stdlib
    directories and their lib-dynload children retain the existing interpreter
    trust boundary. Every other active directory is package content to hash.
    """
    repo = Path(environment["GITHUB_WORKSPACE"])
    home = Path(environment["HOME"])
    need(repo.is_absolute() and repo.resolve(strict=True) == repo, "runtime checkout root alias")
    checkout = {str(repo), str(repo / "src"), str(repo / ".github/scripts")}
    stdlib = Path("/usr/lib/python3.12" if interpreter == "/usr/bin/python3" else
                  "/opt/hostedtoolcache/Python/3.12.14/x64/lib/python3.12")
    stdlib_paths = {str(stdlib), str(stdlib / "lib-dynload")}
    need(type(value["import_roots"]) is list and len(value["import_roots"]) <= 256
         and type(value["package_roots"]) is list and len(value["package_roots"]) <= 256
         and all(type(p) is str and len(p) <= 4096 for p in value["import_roots"] + value["package_roots"]),
         "invalid active import roots")
    need(type(value["packages"]) is list and len(value["packages"]) <= 4096
         and all(type(p) is list and len(p) == 3 and all(text(x, 4096) for x in p) for p in value["packages"]),
         "invalid runtime distribution locations")
    distribution_roots = {p[2] for p in value["packages"]}
    active = {str(repo) if p == "" else p for p in value["import_roots"]}
    candidates = active | set(value["package_roots"]) | distribution_roots
    package_roots, missing, source_roots, standard_roots = set(), set(), set(), set()
    for name in sorted(candidates):
        path = Path(name)
        need(path.is_absolute() and str(path) == name and path.resolve(strict=False) == path,
             "noncanonical active import root")
        if name in checkout:
            source_roots.add(name)
            continue
        if name in stdlib_paths and name not in distribution_roots and name not in value["package_roots"]:
            runtime_directory(path)
            standard_roots.add(name)
            continue
        need(not path.is_relative_to(repo), "unreviewed checkout import root")
        need(not any(anchor == path or anchor.is_relative_to(path) for anchor in (repo, home, stdlib)),
             "active import root exceeds package boundary")
        if not path.exists():
            need(not path.is_symlink(), "noncanonical active import root")
            missing.add(name)
            continue
        need(path.is_dir(), "unsupported active import archive or file")
        runtime_directory(path)
        package_roots.add(name)
    value.update(package_roots=sorted(package_roots), import_roots=sorted(active),
                 checkout_roots=sorted(source_roots), stdlib_roots=sorted(standard_roots), missing_roots=sorted(missing))
    return value


def inventory_import_roots(probes):
    roots, missing = set(), set()
    for probe in probes.values():
        # Include distribution and sys.path roots independently of sysconfig;
        # otherwise Debian and .pth-added package bytes would be omitted.
        declared = set(probe["package_roots"])
        locations = {p[2] for p in probe.get("packages", [])}
        active = set(probe.get("import_roots", []))
        exempt = set(probe.get("checkout_roots", [])) | set(probe.get("stdlib_roots", []))
        absent = set(probe.get("missing_roots", []))
        need(not exempt & declared and not exempt & absent and not absent & declared,
             "overlapping runtime import boundaries")
        roots.update((declared | locations | active) - exempt - absent)
        missing.update(absent)
    for name in sorted(missing):
        path = Path(name)
        need(path.is_absolute() and path.resolve(strict=False) == path and not path.exists() and not path.is_symlink(),
             "previously absent import root appeared")
    for name in sorted(roots):
        path = Path(name)
        need(path.is_absolute() and str(path) == name and path.resolve(strict=True) == path,
             "runtime package root alias")
        need(path.is_dir(), "unsupported active import archive or file")
    return sorted(roots), sorted(missing)


def runtime_walk_error(_):
    raise ServiceError("runtime package traversal unreadable")


def runtime_inventory(probes, executables, deadline_ns, environment=None):
    environment = os.environ if environment is None else environment
    roots, missing = inventory_import_roots(probes)
    node = node_root(environment)
    provision = node_provision(environment, deadline_ns)
    native_root = str(node / "runtime")
    need(not any(Path(native_root).is_relative_to(Path(root)) or Path(root).is_relative_to(node) for root in roots),
         "overlapping runtime import boundaries")
    roots = sorted([*roots, native_root])
    files, directories, directory_stats, count, total = {}, {}, {}, 0, 0
    for root in roots:
        base = Path(root)
        need(base.resolve(strict=True) == base, "runtime package root alias")
        for current, dirs, names in os.walk(base, followlinks=False, onerror=runtime_walk_error):
            runtime_remaining(deadline_ns)
            current = Path(current)
            for path in [current, *(current / n for n in dirs), *(current / n for n in names)]:
                name = str(path)
                if name in files or name in directories:
                    continue
                count += 1
                need(count <= RUNTIME_ENTRIES, "runtime package inventory entry bound")
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    directories[name] = runtime_directory(path)
                    directory_stats[name] = runtime_stat(info)
                else:
                    item = runtime_file(path, deadline_ns,
                                        diagnostic_role="runtime_inventory_native" if root == native_root else "runtime_inventory_package",
                                        diagnostic_root=node if root == native_root else base)
                    total += item["bytes"]
                    need(total <= RUNTIME_TOTAL_LIMIT, "runtime package inventory byte bound")
                    files[name] = item
    for name, identity in directory_stats.items():
        runtime_remaining(deadline_ns)
        need(runtime_stat(Path(name).lstat()) == identity and Path(name).resolve(strict=True) == Path(name),
             "runtime package directory changed during inventory")
    # Charge all leaves, including executables outside import roots, to the
    # same aggregate and global deduplication. Native bytes were walked above.
    for item in executables.values():
        if item["realpath"] not in files:
            count += 1
            total += item["identity"]["bytes"]
            need(count <= RUNTIME_ENTRIES, "runtime package inventory entry bound")
            need(total <= RUNTIME_TOTAL_LIMIT, "runtime package inventory byte bound")
            files[item["realpath"]] = item["identity"]
    for path in (node, node / "expose"):
        name = str(path)
        need(name not in directories and name not in files, "overlapping runtime import boundaries")
        count += 1
        directories[name] = runtime_directory(path, private=True)
    aliases = node_aliases(node, deadline_ns)
    count += len(aliases)
    total += sum(len(item["target"].encode("utf-8")) for item in aliases.values())
    need(count <= RUNTIME_ENTRIES, "runtime package inventory entry bound")
    need(total <= RUNTIME_TOTAL_LIMIT, "runtime package inventory byte bound")
    need(provision == node_provision(environment, deadline_ns), "private Node provision binding changed")
    result = {"schema_version": 1, "roots": roots, "missing_roots": missing, "directories": [directories[n] for n in sorted(directories)],
              "files": [files[n] for n in sorted(files)], "native_aliases": aliases}
    need(len(canonical(result)) <= RUNTIME_INVENTORY_LIMIT, "runtime inventory encoded bound")
    runtime_remaining(deadline_ns)
    return result


def current_executables(deadline_ns, environment=None):
    environment = os.environ if environment is None else environment
    node_provision(environment, deadline_ns)
    executables = {name: runtime_executable(name, deadline_ns, environment) for name in REQUIRED_TOOLS}
    provider = Path(PROVIDER).resolve(strict=True)
    need(executables["python"]["realpath"] == executables["python3"]["realpath"] == str(provider),
         "PATH Python differs from provider")
    system = Path("/usr/bin/python3").resolve(strict=True)
    executables["/usr/bin/python3"] = {"requested": "/usr/bin/python3", "path": "/usr/bin/python3",
                                          "realpath": str(system), "identity": runtime_file(system, deadline_ns,
                                              diagnostic_role="system_interpreter", diagnostic_member="system_python3")}
    return executables


def validate_path_selection(value):
    need(type(value) is dict and value.keys() == set(PATH_SELECTION_TOOLS), "runtime PATH selection changed")
    for name, item in value.items():
        need(item is None or (type(item) is str and item in (*PATH_SELECTION_LABELS, "private_node")),
             "runtime PATH selection changed")
        need((name in NODE_ALIASES and item == "private_node") or (name not in NODE_ALIASES and item != "private_node"),
             "runtime PATH selection changed")
    need(len(canonical(value)) <= PATH_STOP_LIMIT, "runtime record encoded bound")


def approved_node_transition(name, observation):
    return (name in NODE_ALIASES and observation["old"] in {None, "usr_local_bin"}
            and observation["base"] is None and observation["new"] == "private_node")


def validate_path_observations(value, *, rejected=True):
    # Closed diagnostic labels do not authorize execution or an exception.
    need(type(value) is dict and value.keys() == set(PATH_SELECTION_TOOLS), "runtime PATH selection changed")
    for name, observation in value.items():
        need(type(observation) is dict and observation.keys() == {"old", "base", "new"}, "runtime PATH selection changed")
        for side, labels in (("old", OLD_PATH_SELECTION_LABELS), ("base", PATH_SELECTION_LABELS),
                             ("new", (*PATH_SELECTION_LABELS, "private_node"))):
            item = observation[side]
            need(item is None or (type(item) is str and item in labels), "runtime PATH selection changed")
            need(item != "private_node" or name in NODE_ALIASES, "runtime PATH selection changed")
    if rejected:
        need(any(not approved_node_transition(name, item) and
                 (item["old"] != item["new"] or item["base"] != item["new"])
                 for name, item in value.items()), "runtime PATH selection changed")
    need(len(canonical(value)) <= PATH_STOP_LIMIT, "runtime record encoded bound")


def current_path_selection(deadline_ns, environment=None, *, retain_observations=None):
    import shutil
    environment = os.environ if environment is None else environment
    node_provision(environment, deadline_ns)
    final_path = profile_path(environment)
    snapshots, finite_snapshots = [], []
    for _ in range(2):
        snapshot, finite_snapshot = {}, {}
        for name in PATH_SELECTION_TOOLS:
            observation, finite_observation = {}, {}
            for side, path, labels in (("old", OLD_PROFILE_PATH, OLD_PATH_SELECTION_LABELS),
                                       ("base", PROFILE_PATH, PATH_SELECTION_LABELS),
                                       ("new", final_path, ("provider_bin", "private_node", "usr_bin", "bin_alias"))):
                runtime_remaining(deadline_ns)
                selected = shutil.which(name, path=path)
                runtime_remaining(deadline_ns)
                allowed = {directory + "/" + name: label for directory, label in zip(path.split(":"), labels, strict=True)}
                need(selected is None or (type(selected) is str and selected in allowed), "runtime PATH selection changed")
                observation[side] = selected
                finite_observation[side] = None if selected is None else allowed[selected]
            snapshot[name] = observation
            finite_snapshot[name] = finite_observation
        snapshots.append(snapshot)
        finite_snapshots.append(finite_snapshot)
    try:
        need(snapshots[0] == snapshots[1], "runtime PATH selection changed")
        for name, item in snapshots[0].items():
            if name in NODE_ALIASES:
                need(approved_node_transition(name, finite_snapshots[0][name])
                     and item["new"] == str(node_root(environment) / "expose" / name), "runtime PATH selection changed")
            else:
                need(item["old"] == item["base"] == item["new"], "runtime PATH selection changed")
    except ServiceError as error:
        try:
            for observations in finite_snapshots:
                if any(not approved_node_transition(name, item) and
                       (item["old"] != item["new"] or item["base"] != item["new"])
                       for name, item in observations.items()):
                    validate_path_observations(observations)
                    error.path_observations = observations
                    break
        except Exception as diagnostic_error:  # noqa: BLE001 - preserve fixed rejection and first controls
            if getattr(diagnostic_error, "_forge_control", False):
                raise
        raise
    selection = {name: item["new"] for name, item in finite_snapshots[0].items()}
    validate_path_selection(selection)
    validate_path_observations(finite_snapshots[0], rejected=False)
    if retain_observations is not None:
        need(type(retain_observations) is dict and not retain_observations, "runtime PATH selection changed")
        retain_observations.update(finite_snapshots[0])
    runtime_remaining(deadline_ns)
    return selection


def current_runtime(environment, *, deadline_ns):
    profile = check_private_profile(environment, deadline_ns=deadline_ns)
    profile["private_node"] = node_provision(environment, deadline_ns)
    profile["path_observations"] = {}
    profile["path_selection"] = current_path_selection(deadline_ns, environment, retain_observations=profile["path_observations"])
    executables = current_executables(deadline_ns, environment)
    profile["node_probe"] = node_probe(environment, deadline_ns)
    probes = {"provider": runtime_probe(PROVIDER, environment, deadline_ns),
              "system": runtime_probe("/usr/bin/python3", environment, deadline_ns)}
    need(probes["provider"]["version"] == [3, 12, 14], "wrong provider patch version")
    profile.update(executables=executables, provider={k: v for k, v in probes["provider"].items() if k != "packages"},
                   system={k: v for k, v in probes["system"].items() if k != "packages"})
    return profile, probes, runtime_inventory(probes, executables, deadline_ns, environment)


def runtime_write(path, value, limit, deadline_ns):
    runtime_remaining(deadline_ns)
    raw = canonical(value)
    need(len(raw) <= limit, "runtime record encoded bound")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        runtime_remaining(deadline_ns)
        stream.flush()
        runtime_remaining(deadline_ns)
        os.fsync(stream.fileno())
    runtime_remaining(deadline_ns)
    return hashlib.sha256(raw).hexdigest()


def validate_file_stop(value):
    need(type(value) is dict and value.keys() == FILE_STOP_KEYS, "invalid runtime protected file")
    need(type(value["schema_version"]) is int and value["schema_version"] == 1
         and type(value["kind"]) is str and value["kind"] == "runtime-file-rejection"
         and type(value["status"]) is str and value["status"] == "STOP"
         and type(value["gate"]) is str and value["gate"] == "US121", "invalid runtime protected file")
    need(positive(value["run_id"]) and type(value["run_attempt"]) is int and value["run_attempt"] == 1,
         "invalid runtime protected file")
    need(all(type(value[key]) is str and re.fullmatch(r"[0-9a-f]{40}", value[key]) is not None
             and value[key] != "0" * 40 for key in ("candidate_sha", "workflow_sha"))
         and value["candidate_sha"] == value["workflow_sha"], "invalid runtime protected file")
    need(type(value["workflow_job"]) is str and value["workflow_job"] == "linux-tests"
         and type(value["boot_id"]) is str
         and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["boot_id"]) is not None,
         "invalid runtime protected file")
    validate_file_observation(value["observation"])
    need(len(canonical(value)) <= FILE_STOP_LIMIT, "runtime record encoded bound")


def persist_file_stop(error, evidence, environment, deadline_ns):
    if public_gate(error) != "US121" or not hasattr(error, "file_observation"):
        return
    runtime_remaining(deadline_ns)
    validate_environment(environment)
    need(os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0, "runtime profile requires ordinary owner")
    fixed_evidence = Path(environment["RUNNER_TEMP"]) / "forge-evidence"
    need(str(evidence) == str(fixed_evidence) and environment["EVIDENCE"] == str(fixed_evidence),
         "runtime fixed paths changed")
    runtime_directory(fixed_evidence, private=True)
    value = {"schema_version": 1, "kind": "runtime-file-rejection", "status": "STOP", "gate": "US121",
             "run_id": int(environment["GITHUB_RUN_ID"]), "run_attempt": 1, "candidate_sha": environment["GITHUB_SHA"],
             "workflow_sha": environment["GITHUB_WORKFLOW_SHA"], "workflow_job": environment["GITHUB_JOB"],
             "boot_id": read_regular("/proc/sys/kernel/random/boot_id", 64).decode("ascii").strip(),
             "observation": error.file_observation}
    validate_file_stop(value)
    runtime_write(fixed_evidence / "runtime-file-stop.json", value, FILE_STOP_LIMIT, deadline_ns)


def validate_directory_stop(value):
    need(type(value) is dict and value.keys() == DIRECTORY_STOP_KEYS, "untrusted runtime directory")
    need(type(value["schema_version"]) is int and value["schema_version"] == 1
         and type(value["kind"]) is str and value["kind"] == "runtime-directory-rejection"
         and type(value["status"]) is str and value["status"] == "STOP"
         and type(value["gate"]) is str and value["gate"] == "US155", "untrusted runtime directory")
    need(positive(value["run_id"]) and type(value["run_attempt"]) is int and value["run_attempt"] == 1,
         "untrusted runtime directory")
    need(all(type(value[key]) is str and re.fullmatch(r"[0-9a-f]{40}", value[key]) is not None
             and value[key] != "0" * 40 for key in ("candidate_sha", "workflow_sha"))
         and value["candidate_sha"] == value["workflow_sha"], "untrusted runtime directory")
    need(type(value["workflow_job"]) is str and value["workflow_job"] == "linux-tests"
         and type(value["boot_id"]) is str
         and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["boot_id"]) is not None,
         "untrusted runtime directory")
    validate_directory_observation(value["observation"])
    need(len(canonical(value)) <= DIRECTORY_STOP_LIMIT, "runtime record encoded bound")


def persist_directory_stop(error, evidence, environment, deadline_ns):
    if public_gate(error) != "US155" or not hasattr(error, "directory_observation"):
        return
    runtime_remaining(deadline_ns)
    validate_environment(environment)
    need(os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0, "runtime profile requires ordinary owner")
    fixed_evidence = Path(environment["RUNNER_TEMP"]) / "forge-evidence"
    need(str(evidence) == str(fixed_evidence) and environment["EVIDENCE"] == str(fixed_evidence),
         "runtime fixed paths changed")
    runtime_directory(fixed_evidence, private=True)
    value = {"schema_version": 1, "kind": "runtime-directory-rejection", "status": "STOP", "gate": "US155",
             "run_id": int(environment["GITHUB_RUN_ID"]), "run_attempt": 1, "candidate_sha": environment["GITHUB_SHA"],
             "workflow_sha": environment["GITHUB_WORKFLOW_SHA"], "workflow_job": environment["GITHUB_JOB"],
             "boot_id": read_regular("/proc/sys/kernel/random/boot_id", 64).decode("ascii").strip(),
             "observation": error.directory_observation}
    validate_directory_stop(value)
    runtime_write(fixed_evidence / "runtime-directory-stop.json", value, DIRECTORY_STOP_LIMIT, deadline_ns)


def validate_path_stop(value):
    need(type(value) is dict and value.keys() == PATH_STOP_KEYS, "runtime PATH selection changed")
    need(type(value["schema_version"]) is int and value["schema_version"] == 1
         and type(value["kind"]) is str and value["kind"] == "runtime-path-selection-change"
         and type(value["status"]) is str and value["status"] == "STOP"
         and type(value["gate"]) is str and value["gate"] == "US176", "runtime PATH selection changed")
    need(positive(value["run_id"]) and type(value["run_attempt"]) is int and value["run_attempt"] == 1,
         "runtime PATH selection changed")
    need(all(type(value[key]) is str and re.fullmatch(r"[0-9a-f]{40}", value[key]) is not None
             and value[key] != "0" * 40 for key in ("candidate_sha", "workflow_sha"))
         and value["candidate_sha"] == value["workflow_sha"], "runtime PATH selection changed")
    need(type(value["workflow_job"]) is str and value["workflow_job"] == "linux-tests"
         and type(value["boot_id"]) is str
         and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["boot_id"]) is not None,
         "runtime PATH selection changed")
    validate_path_observations(value["observations"])
    need(len(canonical(value)) <= PATH_STOP_LIMIT, "runtime record encoded bound")


def persist_path_stop(error, evidence, environment, deadline_ns):
    if public_gate(error) != "US176" or not hasattr(error, "path_observations"):
        return
    runtime_remaining(deadline_ns)
    validate_environment(environment)
    need(os.getuid() == os.geteuid() > 0 and os.getgid() == os.getegid() > 0, "runtime profile requires ordinary owner")
    fixed_evidence = Path(environment["RUNNER_TEMP"]) / "forge-evidence"
    need(str(evidence) == str(fixed_evidence) and environment["EVIDENCE"] == str(fixed_evidence),
         "runtime fixed paths changed")
    runtime_directory(fixed_evidence, private=True)
    value = {"schema_version": 1, "kind": "runtime-path-selection-change", "status": "STOP", "gate": "US176",
             "run_id": int(environment["GITHUB_RUN_ID"]), "run_attempt": 1, "candidate_sha": environment["GITHUB_SHA"],
             "workflow_sha": environment["GITHUB_WORKFLOW_SHA"], "workflow_job": environment["GITHUB_JOB"],
             "boot_id": read_regular("/proc/sys/kernel/random/boot_id", 64).decode("ascii").strip(),
             "observations": error.path_observations}
    validate_path_stop(value)
    runtime_write(fixed_evidence / "runtime-path-stop.json", value, PATH_STOP_LIMIT, deadline_ns)


EDITABLE_DIRECTORY = "src/code_review_forge.egg-info"
EDITABLE_FILES = ("PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt", "requires.txt", "top_level.txt")
FILE_FIXED_CONTEXTS = {
    "node_binding_helper": {"user_service.py": "checkout"},
    "node_archive": {"node-v24.21.0-linux-x64.tar.xz": "node_download_staging"},
    "required_executable": dict.fromkeys(REQUIRED_TOOLS, "required_tool"),
    "system_interpreter": {"system_python3": "system_tool"},
    "retained_runtime_record": {**dict.fromkeys(INSTALL_RECORDS, "evidence_record"),
                                "runtime-packages.json": "private_runtime_record", "runtime-inventory.json": "private_runtime_record"},
    "runtime_installer": {"render_linux_workflow.py": "checkout"},
    "editable_metadata": dict.fromkeys(EDITABLE_FILES, "editable_metadata"),
}


def installed_metadata(environment, deadline_ns):
    runtime_remaining(deadline_ns)
    repo = Path(environment["GITHUB_WORKSPACE"])
    directory = repo / EDITABLE_DIRECTORY
    identity = runtime_directory(directory)
    need(identity["uid"] == os.getuid() and identity["gid"] == os.getgid(), "foreign installed metadata directory")
    need({path.name for path in directory.iterdir()} == set(EDITABLE_FILES), "installed metadata members changed")
    entries, total = [], 0
    for name in EDITABLE_FILES:
        item = runtime_file(directory / name, deadline_ns, diagnostic_role="editable_metadata", diagnostic_member=name)
        need(item["uid"] == os.getuid() and item["gid"] == os.getgid() and not item["mode"] & 0o111
             and item["bytes"] <= 65536, "invalid installed metadata member")
        total += item["bytes"]
        need(total <= 262144, "installed metadata aggregate bound")
        entries.append({"path": EDITABLE_DIRECTORY + "/" + name, "mode": item["mode"], "uid": item["uid"],
                        "gid": item["gid"], "size": item["bytes"], "sha256": item["sha256"]})
    runtime_remaining(deadline_ns)
    need({path.name for path in directory.iterdir()} == set(EDITABLE_FILES), "installed metadata members changed")
    return {"directory": EDITABLE_DIRECTORY, "entries": entries}


def runtime_record_path(environment, name):
    need(name in set(INSTALL_RECORDS) | {"runtime-packages.json", "runtime-inventory.json"}, "unknown runtime record")
    return (Path(environment["EVIDENCE"]) if name in INSTALL_RECORDS else
            Path(environment["XDG_DATA_HOME"]) / "forge-b-runtime") / name


def produce_runtime_admission(repo, evidence, environment, *, deadline_ns):
    from forge_ci import launch
    need(repo == Path(environment["GITHUB_WORKSPACE"]) and evidence == Path(environment["EVIDENCE"]), "runtime fixed paths changed")
    receipt = launch.load_receipt(evidence / "launch-bootstrap.json")
    need(launch.inspect_checkout(repo, receipt["binding"]["candidate_sha"], deadline=deadline_ns / NS) == receipt["source"],
         "runtime checkout source changed")
    profile, probes, inventory = current_runtime(environment, deadline_ns=deadline_ns)
    records = {name: runtime_file((evidence / name).resolve(strict=True), deadline_ns,
                                 diagnostic_role="retained_runtime_record", diagnostic_member=name)["sha256"] for name in INSTALL_RECORDS}
    private_records = Path(environment["XDG_DATA_HOME"]) / "forge-b-runtime"
    private_records.mkdir(mode=0o700)
    runtime_directory(private_records, private=True)
    records["runtime-packages.json"] = runtime_write(private_records / "runtime-packages.json", probes, MAX_METADATA, deadline_ns)
    records["runtime-inventory.json"] = runtime_write(private_records / "runtime-inventory.json", inventory, RUNTIME_INVENTORY_LIMIT, deadline_ns)
    argv = [INSTALL_ARGV[0], [*INSTALL_ARGV[1][:5], probes["system"]["user_site"], *INSTALL_ARGV[1][6:]]]
    record = {"schema_version": 1, "profile": RUNTIME_PROFILE, "spec_sha256": SPEC_SHA256, "source": receipt["source"],
              "environment_sha256": runtime_digest(environment),
              "installer_sha256": runtime_file(repo / ".github/scripts/render_linux_workflow.py", deadline_ns,
                                                diagnostic_role="runtime_installer", diagnostic_member="render_linux_workflow.py")["sha256"],
              "records_sha256": records, "profile_metadata": profile, "install_argv": argv,
              "generated_install_metadata": installed_metadata(environment, deadline_ns)}
    runtime_write(evidence / "runtime-admission.json", record, MAX_METADATA, deadline_ns)
    return record


def load_runtime_admission(environment, source, *, deadline_ns):
    validate_environment(environment)
    record, _ = runtime_json(Path(environment["EVIDENCE"]) / "runtime-admission.json", MAX_METADATA, deadline_ns)
    keys(record, RUNTIME_KEYS, "runtime admission")
    need(record["schema_version"] == 1 and record["profile"] == RUNTIME_PROFILE and record["spec_sha256"] == SPEC_SHA256
         and record["source"] == source and record["environment_sha256"] == runtime_digest(environment), "runtime admission binding mismatch")
    expected = set(INSTALL_RECORDS) | {"runtime-packages.json", "runtime-inventory.json"}
    keys(record["records_sha256"], expected, "runtime record hashes")
    need(all(type(x) is str and re.fullmatch(r"[0-9a-f]{64}", x) for x in record["records_sha256"].values()), "runtime record hash invalid")
    revalidate_runtime_admission(record, environment, source, deadline_ns=deadline_ns)
    return record


def revalidate_runtime_admission(record, environment, source, *, deadline_ns):
    runtime_remaining(deadline_ns)
    keys(record, RUNTIME_KEYS, "runtime admission")
    need(record["source"] == source and record["environment_sha256"] == runtime_digest(environment), "runtime admission binding mismatch")
    need(type(record["profile_metadata"]) is dict and "path_selection" in record["profile_metadata"],
         "runtime PATH selection changed")
    validate_path_selection(record["profile_metadata"]["path_selection"])
    need("path_observations" in record["profile_metadata"], "runtime PATH selection changed")
    validate_path_observations(record["profile_metadata"]["path_observations"], rejected=False)
    evidence, repo = Path(environment["EVIDENCE"]), Path(environment["GITHUB_WORKSPACE"])
    retained, _ = runtime_json(evidence / "runtime-admission.json", MAX_METADATA, deadline_ns)
    need(retained == record, "runtime admission changed")
    need(runtime_file(repo / ".github/scripts/render_linux_workflow.py", deadline_ns,
                      diagnostic_role="runtime_installer", diagnostic_member="render_linux_workflow.py")["sha256"] == record["installer_sha256"], "runtime installer changed")
    for name, checksum in record["records_sha256"].items():
        need(runtime_file(runtime_record_path(environment, name), deadline_ns,
                          diagnostic_role="retained_runtime_record", diagnostic_member=name)["sha256"] == checksum, "retained runtime record changed")
    need(installed_metadata(environment, deadline_ns) == record["generated_install_metadata"], "installed metadata drift")
    # Rehash authenticated executable and package bytes BEFORE importing them.
    # A changed package may not execute merely to report that it has changed.
    check_private_profile(environment, deadline_ns=deadline_ns)
    executables = current_executables(deadline_ns, environment)
    need(executables == record["profile_metadata"]["executables"], "installed executable drift before import")
    saved_probes, _ = runtime_json(runtime_record_path(environment, "runtime-packages.json"), MAX_METADATA, deadline_ns)
    before = runtime_inventory(saved_probes, executables, deadline_ns, environment)
    need(runtime_digest(before) == record["records_sha256"]["runtime-inventory.json"], "installed package drift before import")
    from forge_ci import launch
    need(launch.inspect_checkout(repo, source["candidate_sha"], deadline=deadline_ns / NS) == source,
         "runtime checkout source changed")
    profile, probes, inventory = current_runtime(environment, deadline_ns=deadline_ns)
    need(profile == record["profile_metadata"] and runtime_digest(probes) == record["records_sha256"]["runtime-packages.json"]
         and runtime_digest(inventory) == record["records_sha256"]["runtime-inventory.json"], "installed runtime changed")
    need(record["install_argv"] == [INSTALL_ARGV[0], [*INSTALL_ARGV[1][:5], probes["system"]["user_site"], *INSTALL_ARGV[1][6:]]],
         "runtime install selection changed")
    runtime_remaining(deadline_ns)


def runtime_summary(record):
    keys(record, RUNTIME_KEYS, "runtime admission")
    result = {"schema_version": 1, "profile": RUNTIME_PROFILE, "admission_sha256": runtime_digest(record),
              "installer_sha256": record["installer_sha256"], "records_sha256": record["records_sha256"],
              "profile_metadata": record["profile_metadata"],
              "generated_install_metadata_sha256": runtime_digest(record["generated_install_metadata"])}
    need(len(canonical(result)) <= MAX_METADATA, "runtime summary bound")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="role", required=True)
    commands.add_parser("bootstrap")
    launch = commands.add_parser("launch")
    for name in ("receipt", "repo", "evidence"):
        launch.add_argument("--" + name, required=True, type=Path)
    for role in ("profile-check", "preflight"):
        command = commands.add_parser(role)
        command.add_argument("--repo", required=True, type=Path)
        command.add_argument("--evidence", required=True, type=Path)
    install = commands.add_parser("node-install")
    for name in ("repo", "evidence"):
        install.add_argument("--" + name, required=True, type=Path)
    install.add_argument("--deadline-ns", required=True, type=int)
    worker = commands.add_parser("node-install-worker")
    worker.add_argument("--deadline-ns", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if args.role in {"node-install", "node-install-worker"}:
            os.umask(0o077)
            environment = payload_environment(os.environ)
            if args.role == "node-install":
                need(args.repo == Path(environment["GITHUB_WORKSPACE"]) and args.evidence == Path(environment["EVIDENCE"]),
                     "runtime fixed paths changed")
                node_install(environment, args.deadline_ns)
            else:
                need(time.monotonic_ns() < args.deadline_ns <= time.monotonic_ns() + 175 * NS,
                     "private Node component deadline")
                node_install_worker(environment, args.deadline_ns)
            return 0
        if args.role in {"profile-check", "preflight"}:
            os.umask(0o077)
            environment = payload_environment(os.environ)
            deadline_ns = time.monotonic_ns() + 120 * NS
            if args.role == "profile-check":
                try:
                    check_private_profile(environment, deadline_ns=deadline_ns)
                except ServiceError as error:
                    if getattr(error, "_forge_control", False):
                        raise
                    try:
                        persist_directory_stop(error, args.evidence, environment, deadline_ns)
                    except Exception as diagnostic_error:  # noqa: BLE001 - keep STOP; controls reach the outer handler
                        if getattr(diagnostic_error, "_forge_control", False):
                            raise
                    raise
                provider = str(Path(PROVIDER).resolve(strict=True))
                need(runtime_executable("python", deadline_ns, environment)["realpath"] == provider
                     and runtime_executable("python3", deadline_ns, environment)["realpath"] == provider,
                     "PATH Python differs from provider")
            else:
                try:
                    produce_runtime_admission(args.repo, args.evidence, environment, deadline_ns=deadline_ns)
                except ServiceError as error:
                    if getattr(error, "_forge_control", False):
                        raise
                    try:
                        persist_path_stop(error, args.evidence, environment, deadline_ns)
                        persist_file_stop(error, args.evidence, environment, deadline_ns)
                    except Exception as diagnostic_error:  # noqa: BLE001 - keep STOP; controls reach the outer handler
                        if getattr(diagnostic_error, "_forge_control", False):
                            raise
                    raise
            return 0
        code = bootstrap() if args.role == "bootstrap" else launcher(args.receipt, args.repo, args.evidence)
        # Preserve actual signal identity in terminal evidence and conventional
        # shell exit semantics. Never reinterpret systemd client success as PASS.
        return code if code >= 0 else 128 - code
    except BaseException as exc:  # noqa: BLE001 - never expose private exception values
        print("STOP: fixed user-service admission, lifecycle or evidence failed; gate=" + public_gate(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
