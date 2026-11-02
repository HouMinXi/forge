"""Read-only Linux admission observations, never isolation qualification.

Only a verified, fixed systemctl metadata query is executed; no service,
namespace or payload is launched. Git identity is declared, not authenticated,
and the bwrap version is not executed. Even OBSERVED is not an admission proof:
TOCTOU, private manager peer binding, its actual child topology and host policy
enforcement require a separately reviewed live boundary run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

SCHEMA = "forge-linux-admission-observation-v1"
MAX_RECEIPT = 16384
MAX_READ = 65536
COMMAND_BYTES = 4096
COMMAND_SECONDS = 3
SYSTEMCTL = "/usr/bin/systemctl"
COMMAND_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
CGROUP_MOUNT = "/sys/fs/cgroup"
BWRAP_PATHS = ("/usr/bin/bwrap", "/bin/bwrap")
PROFILE_NAMES = ("bwrap", "/usr/bin/bwrap", "usr.bin.bwrap")
IDENTITY_KEYS = {
    "repository",
    "run_id",
    "run_attempt",
    "job_name",
    "workflow_ref",
    "workflow_sha",
    "expected_source_sha",
    "collector_sha256",
    "authentication",
}
FACT_KEYS = {
    "uid",
    "gid",
    "kernel",
    "boot_id",
    "cgroup2_mount",
    "source_cgroup",
    "delegated_root",
    "controllers",
    "subtree_control",
    "manager",
    "common_ancestor",
    "access",
    "bwrap",
    "apparmor",
}
MANAGER_KEYS = {"active_state", "control_group", "delegate"}
ACCESS_KEYS = {"delegated_directory", "destination_procs", "common_ancestor_procs"}
BWRAP_KEYS = {"path", "realpath", "uid", "gid", "mode", "size", "sha256", "version"}
APPARMOR_KEYS = {"enabled", "restrict_userns", "process_profile", "bwrap_profiles"}
SHA = re.compile(r"[0-9a-f]{40}")
HEX256 = re.compile(r"[0-9a-f]{64}")


class ObservationError(ValueError):
    """A receipt or an observation does not satisfy the closed data contract."""


def _require(condition: bool, label: str) -> None:
    if not condition:
        raise ObservationError(label)


def _keys(value: object, expected: set[str], label: str) -> dict:
    _require(type(value) is dict and set(value) == expected, label)
    return value


def _text(value: object, maximum: int, label: str) -> str:
    _require(type(value) is str and 0 < len(value) <= maximum, label)
    _require(all(" " <= char <= "~" for char in value), label)
    return value


def _integer(value: object, maximum: int, label: str) -> int:
    _require(type(value) is int and 0 <= value <= maximum, label)
    return value


def _path(value: object) -> str:
    value = _text(value, 1024, "cgroup path")
    _require(
        value.startswith("/") and not value.startswith("//") and str(PurePosixPath(value)) == value,
        "cgroup path",
    )
    _require(".." not in PurePosixPath(value).parts and "\\" not in value, "cgroup path")
    return value


def _nullable(value, check):
    if value is not None:
        check(value)


def _boolean(value):
    _require(type(value) is bool, "boolean")


def _controllers(value):
    _require(type(value) is list and len(value) <= 32, "controllers")
    _require(value == sorted(set(value)), "controller order")
    _require(all(type(v) is str and re.fullmatch(r"[a-z_]{1,32}", v) for v in value), "controller")


def _hash(value, pattern=SHA):
    _require(type(value) is str and pattern.fullmatch(value) is not None, "hash")


def validate(receipt: object) -> dict:
    """Validate shape, finite types, derived status and the non-qualification claim."""
    receipt = _keys(
        receipt,
        {"schema", "identity", "facts", "status", "problems", "boundary", "qualification"},
        "receipt keys",
    )
    _require(receipt["schema"] == SCHEMA, "schema")
    _require(receipt["boundary"] == receipt["qualification"] == "NOT_RUN", "qualification claim")
    identity = _keys(receipt["identity"], IDENTITY_KEYS, "identity keys")
    _require(identity["authentication"] == "UNVERIFIED_DECLARED_CONTEXT", "identity authentication")
    _require(
        re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", _text(identity["repository"], 200, "repository")
        )
        is not None,
        "repository",
    )
    _require(
        re.fullmatch(r"[1-9][0-9]{0,19}", _text(identity["run_id"], 20, "run id")) is not None, "run id"
    )
    _require(1 <= _integer(identity["run_attempt"], 1000000, "attempt"), "attempt")
    _require(
        re.fullmatch(r"[A-Za-z0-9_-]{1,64}", _text(identity["job_name"], 64, "job name")) is not None,
        "job name",
    )
    workflow = _text(identity["workflow_ref"], 512, "workflow ref")
    _require(
        workflow.startswith(identity["repository"] + "/.github/workflows/") and "@" in workflow,
        "workflow ref",
    )
    for key in ("workflow_sha", "expected_source_sha"):
        _hash(identity[key])
    _hash(identity["collector_sha256"], HEX256)
    facts = _keys(receipt["facts"], FACT_KEYS, "fact keys")
    for key in ("uid", "gid"):
        _integer(facts[key], 2**32 - 1, key)
    _nullable(facts["kernel"], lambda v: _text(v, 128, "kernel"))
    _nullable(
        facts["boot_id"],
        lambda v: _require(
            type(v) is str
            and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", v) is not None,
            "boot id",
        ),
    )
    _nullable(facts["cgroup2_mount"], _boolean)
    _require(facts["delegated_root"] == delegated_root(facts["uid"]), "delegated root")
    for key in ("source_cgroup", "common_ancestor"):
        _nullable(facts[key], _path)
    expected_common = (
        common_ancestor(facts["source_cgroup"], facts["delegated_root"])
        if facts["source_cgroup"] is not None
        else None
    )
    _require(facts["common_ancestor"] == expected_common, "common ancestor")
    for key in ("controllers", "subtree_control"):
        _nullable(facts[key], _controllers)
    manager = _keys(facts["manager"], MANAGER_KEYS, "manager keys")
    _require(
        manager["active_state"]
        in (
            None,
            "active",
            "inactive",
            "failed",
            "activating",
            "deactivating",
            "reloading",
            "maintenance",
        ),
        "manager state",
    )
    _nullable(manager["control_group"], _path)
    _nullable(manager["delegate"], _boolean)
    for value in _keys(facts["access"], ACCESS_KEYS, "access keys").values():
        _nullable(value, _boolean)
    if facts["bwrap"] is not None:
        tool = _keys(facts["bwrap"], BWRAP_KEYS, "bwrap keys")
        _require(tool["path"] in BWRAP_PATHS and tool["realpath"] in BWRAP_PATHS, "bwrap path")
        for key in ("uid", "gid"):
            _integer(tool[key], 2**32 - 1, key)
        _integer(tool["mode"], 0o177777, "mode")
        _integer(tool["size"], 16 * 1024 * 1024, "bwrap size")
        _hash(tool["sha256"], HEX256)
        _require(tool["version"] is None, "version was not executed")
    apparmor = _keys(facts["apparmor"], APPARMOR_KEYS, "AppArmor keys")
    _nullable(apparmor["enabled"], _boolean)
    _require(
        apparmor["restrict_userns"] is None
        or type(apparmor["restrict_userns"]) is int
        and apparmor["restrict_userns"] in (0, 1),
        "userns restriction",
    )
    _require(apparmor["process_profile"] in (None, "unconfined", "confined"), "process profile")
    profiles = apparmor["bwrap_profiles"]
    _require(
        profiles is None
        or type(profiles) is list
        and profiles == sorted(set(profiles))
        and all(v in PROFILE_NAMES for v in profiles),
        "bwrap profiles",
    )
    problems = derive_problems(identity, facts)
    _require(receipt["problems"] == problems, "problems")
    _require(receipt["status"] == ("STOP" if problems else "OBSERVED"), "status")
    return receipt


def canonical(receipt: dict) -> bytes:
    return (
        json.dumps(validate(receipt), sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("ascii")


def parse(raw: bytes) -> dict:
    _require(type(raw) is bytes and len(raw) <= MAX_RECEIPT, "receipt size")

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate key")
            result[key] = value
        return result

    def nonfinite(value):
        raise ObservationError("nonfinite number")

    try:
        receipt = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
        _require(canonical(receipt) == raw, "noncanonical receipt")
        return receipt
    except (UnicodeError, RecursionError, ValueError, TypeError) as exc:
        raise ObservationError("invalid observation receipt") from exc


def delegated_root(uid: int) -> str:
    return f"/user.slice/user-{uid}.slice/user@{uid}.service"


def common_ancestor(source: str, destination: str) -> str:
    _path(source)
    _path(destination)
    return os.path.commonpath((source, destination))


def derive_problems(identity: dict, facts: dict) -> list[str]:
    """These are necessary observations, not sufficient admission predicates."""
    checks = {
        "ordinary_user": facts["uid"] != 0 and facts["gid"] != 0,
        "kernel_identity": facts["kernel"] is not None and facts["boot_id"] is not None,
        "cgroup2_mount": facts["cgroup2_mount"] is True,
        "source_membership": facts["source_cgroup"] is not None,
        "controllers": facts["controllers"] is not None
        and {"memory", "pids"} <= set(facts["controllers"]),
        "subtree_control": facts["subtree_control"] is not None
        and {"memory", "pids"} <= set(facts["subtree_control"]),
        "existing_manager": facts["manager"]
        == {"active_state": "active", "control_group": facts["delegated_root"], "delegate": True},
        "delegated_directory_access": facts["access"]["delegated_directory"] is True,
        "destination_procs_access": facts["access"]["destination_procs"] is True,
        "current_common_ancestor_access": facts["access"]["common_ancestor_procs"] is True,
        "bwrap_identity": facts["bwrap"] is not None
        and facts["bwrap"]["uid"] == 0
        and stat.S_ISREG(facts["bwrap"]["mode"])
        and facts["bwrap"]["mode"] & 0o6022 == 0
        and bool(facts["bwrap"]["mode"] & 0o111)
        and facts["bwrap"]["size"] > 0,
        "apparmor_observed": facts["apparmor"]["enabled"] is not None
        and facts["apparmor"]["process_profile"] is not None,
        "apparmor_restriction_observed": facts["apparmor"]["enabled"] is False
        or facts["apparmor"]["restrict_userns"] is not None,
        "apparmor_profiles_observed": facts["apparmor"]["enabled"] is False
        or facts["apparmor"]["bwrap_profiles"] is not None,
    }
    return sorted(key for key, present in checks.items() if not present)


def read_text(path: str, limit: int = MAX_READ) -> str | None:
    try:
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
        if len(data) <= limit:
            return data.decode("utf-8").strip()
    except (OSError, UnicodeError):
        pass
    return None


def _verified_systemctl() -> bool:
    """Do not execute a discovered/PATH-selected or writable metadata tool.

    This conservative Ubuntu path rejects symlinks rather than inferring their
    ancestor safety. These observations are not an execution/TOCTOU certificate.
    """
    try:
        for path in ("/", "/usr", "/usr/bin"):
            info = os.lstat(path)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                return False
        info = os.lstat(SYSTEMCTL)
        return (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and not info.st_mode & 0o6022
            and bool(info.st_mode & 0o111)
        )
    except OSError:
        return False


def _bounded_query(argv: tuple[str, ...]) -> str | None:
    """Bound reads and the isolated process group, including an exited leader.

    The only production caller supplies the fixed verified systemctl command.
    Group signalling plus direct-child reap is not descendant-quiescence proof.
    Stderr is refused on its first byte, never retained or printed.
    """
    child = None
    output = bytearray()
    try:
        child = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=COMMAND_ENV,
            start_new_session=True,
        )
        deadline = time.monotonic() + COMMAND_SECONDS
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ, "stdout")
            selector.register(child.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                events = selector.select(remaining)
                if not events:
                    return None
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), min(1024, COMMAND_BYTES + 1 - len(output)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.data == "stderr":
                        return None
                    else:
                        output.extend(chunk)
                        if len(output) > COMMAND_BYTES:
                            return None
            remaining = deadline - time.monotonic()
            if remaining <= 0 or child.wait(timeout=remaining) != 0:
                return None
            return bytes(output).decode("utf-8").strip()
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return None
    finally:
        if child is not None:
            try:
                # Always target the group: a reaped leader can leave a child
                # holding either pipe open. Never skip this on poll()!=None.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=COMMAND_SECONDS)
            finally:
                if child.stdout is not None:
                    child.stdout.close()
                if child.stderr is not None:
                    child.stderr.close()


def query_manager(uid: int) -> dict:
    _integer(uid, 2**32 - 1, "uid")
    if not _verified_systemctl():
        return dict.fromkeys(sorted(MANAGER_KEYS))
    return manager_properties(
        _bounded_query(
            (
                SYSTEMCTL,
                "--system",
                "--no-pager",
                "show",
                f"user@{uid}.service",
                "--property=ActiveState,ControlGroup,Delegate",
            )
        )
    )


def source_membership(raw: str | None) -> str | None:
    if raw is None:
        return None
    lines = [line[3:] for line in raw.splitlines() if line.startswith("0::")]
    if len(lines) != 1:
        return None
    try:
        return _path(lines[0])
    except ObservationError:
        return None


def mounted_cgroup2(raw: str | None) -> bool | None:
    if raw is None:
        return None
    matches = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 10 and parts[4] == CGROUP_MOUNT and "-" in parts:
            at = parts.index("-")
            matches.append(at + 1 < len(parts) and parts[at + 1] == "cgroup2" and parts[3] == "/")
    # Mountinfo can expose stacked mounts. The first row is not proof of the
    # topmost filesystem, so ambiguous topology is unknown, never True.
    return matches[0] if len(matches) == 1 else False if not matches else None


def manager_properties(raw: str | None) -> dict:
    unknown = dict.fromkeys(sorted(MANAGER_KEYS))
    if raw is None:
        return unknown
    lines = raw.splitlines()
    pairs = [line.split("=", 1) for line in lines]
    if len(pairs) != 3 or any(len(pair) != 2 for pair in pairs):
        return unknown
    props = dict(pairs)
    if set(props) != {"ActiveState", "ControlGroup", "Delegate"}:
        return unknown
    try:
        _require(
            props["ActiveState"]
            in (
                "active",
                "inactive",
                "failed",
                "activating",
                "deactivating",
                "reloading",
                "maintenance",
            ),
            "manager state",
        )
        _require(props["Delegate"] in ("yes", "no"), "delegate")
        control = _path(props["ControlGroup"]) if props["ControlGroup"] else None
        return {
            "active_state": props["ActiveState"],
            "control_group": control,
            "delegate": props["Delegate"] == "yes",
        }
    except ObservationError:
        return unknown


def controller_names(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    names = sorted(raw.split())
    try:
        _controllers(names)
        return names
    except (ObservationError, TypeError):
        return None


def writable(path: str, directory: bool = False) -> bool | None:
    try:
        return os.access(path, os.W_OK | (os.X_OK if directory else 0), effective_ids=True)
    except (OSError, NotImplementedError):
        return None


def bwrap_identity() -> dict | None:
    for path in BWRAP_PATHS:
        real = os.path.realpath(path)
        if real not in BWRAP_PATHS:
            continue
        try:
            with open(real, "rb") as handle:
                info = os.fstat(handle.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != 0
                    or info.st_mode & 0o6022
                    or not info.st_mode & 0o111
                    or not 0 < info.st_size <= 16 * 1024 * 1024
                ):
                    continue
                content = handle.read(16 * 1024 * 1024 + 1)
            if len(content) != info.st_size:
                continue
            return {
                "path": path,
                "realpath": real,
                "uid": info.st_uid,
                "gid": info.st_gid,
                "mode": info.st_mode,
                "size": info.st_size,
                "sha256": hashlib.sha256(content).hexdigest(),
                "version": None,
            }
        except OSError:
            continue
    return None


def collect(identity: dict) -> dict:
    identity = dict(identity)
    identity.update(
        {
            # Context SHA is a declaration, not an authenticated source binding.
            "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "authentication": "UNVERIFIED_DECLARED_CONTEXT",
        }
    )
    uid = os.geteuid()
    destination = delegated_root(uid)
    source = source_membership(read_text("/proc/self/cgroup"))
    common = common_ancestor(source, destination) if source is not None else None
    manager = query_manager(uid)
    enabled = read_text("/sys/module/apparmor/parameters/enabled", 16)
    restricted = read_text("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", 16)
    profile = read_text("/proc/self/attr/current", 1024)
    profiles = read_text("/sys/kernel/security/apparmor/profiles")
    facts = {
        "uid": uid,
        "gid": os.getegid(),
        "kernel": os.uname().release,
        "boot_id": read_text("/proc/sys/kernel/random/boot_id", 64),
        "cgroup2_mount": mounted_cgroup2(read_text("/proc/self/mountinfo")),
        "source_cgroup": source,
        "delegated_root": destination,
        "controllers": controller_names(
            read_text(CGROUP_MOUNT + destination + "/cgroup.controllers", 1024)
        ),
        "subtree_control": controller_names(
            read_text(CGROUP_MOUNT + destination + "/cgroup.subtree_control", 1024)
        ),
        "manager": manager,
        "common_ancestor": common,
        "access": {
            "delegated_directory": writable(CGROUP_MOUNT + destination, True),
            "destination_procs": writable(CGROUP_MOUNT + destination + "/cgroup.procs"),
            "common_ancestor_procs": writable(CGROUP_MOUNT + common.rstrip("/") + "/cgroup.procs")
            if common is not None
            else None,
        },
        "bwrap": bwrap_identity(),
        "apparmor": {
            "enabled": {"Y": True, "N": False}.get(enabled),
            "restrict_userns": {"0": 0, "1": 1}.get(restricted),
            "process_profile": None
            if profile is None
            else "unconfined"
            if profile == "unconfined"
            else "confined",
            "bwrap_profiles": None
            if profiles is None
            else sorted(
                name
                for name in PROFILE_NAMES
                if any(
                    line == name + " (enforce)" or line == name + " (complain)"
                    for line in profiles.splitlines()
                )
            ),
        },
    }
    problems = derive_problems(identity, facts)
    receipt = {
        "schema": SCHEMA,
        "identity": identity,
        "facts": facts,
        "status": "STOP" if problems else "OBSERVED",
        "problems": problems,
        "boundary": "NOT_RUN",
        "qualification": "NOT_RUN",
    }
    return validate(receipt)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ObservationError("invalid arguments")


def _main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = vars(parser.parse_args(argv))
    output = args.pop("output")
    receipt = collect(args)
    raw = canonical(receipt)
    _require(len(raw) <= MAX_RECEIPT, "receipt size")
    # Exclusive creation: do not follow an existing output link or overwrite an
    # earlier attempt's observation. Its parent is created by the workflow.
    with output.open("xb") as handle:
        handle.write(raw)
    parse(output.read_bytes())
    print(f"{receipt['status']}: static observations only; boundary and qualification NOT_RUN")
    return 2 if receipt["status"] == "STOP" else 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (OSError, ObservationError, subprocess.SubprocessError):
        # No raw path, contents, stderr or traceback on collection/persistence
        # failure. Missing artifact remains a failed workflow, never a pass.
        print("STOP: observation_unavailable; qualification NOT_RUN", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
