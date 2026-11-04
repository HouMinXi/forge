"""Explicit two-stat diagnostic worker. Never admission or service execution.

Only the dedicated reviewed dispatch runner may start this worker. Its source and
imports must be bound to the exact dispatched tree before process creation.
"""

import hashlib
import types
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    path = ROOT / "scripts" / (name + ".py")
    hashes = json.loads(os.environ["BOUND_SOURCE_SHA256"])
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not __import__("stat").S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError("source")
        data = b""
        while len(data) <= 65536:
            chunk = os.read(fd, 65537 - len(data))
            if not chunk:
                break
            data += chunk
        if (
            len(data) != info.st_size
            or hashlib.sha256(data).hexdigest() != hashes["scripts/" + name + ".py"]
        ):
            raise ValueError("source")
    finally:
        os.close(fd)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)  # noqa: S102 -- reviewed bytes bound by runner.
    return module


def argv(manager_pid, collector_pid):
    for pid in (manager_pid, collector_pid):
        if type(pid) is not int or not 0 < pid < 2**31:
            raise ValueError("identity")
    return [
        "/usr/bin/sudo",
        "-n",
        "--",
        "/usr/bin/timeout",
        "--signal=TERM",
        "--kill-after=1s",
        "--",
        "2s",
        "/usr/bin/stat",
        "--dereference",
        "--printf=%d:%i\\n",
        "--",
        f"/proc/{manager_pid}/ns/mnt",
        f"/proc/{manager_pid}/root",
        f"/proc/{collector_pid}/ns/mnt",
        f"/proc/{collector_pid}/root",
    ]


def stat_sample(base, manager_pid, collector_pid, deadline, receipt=None):
    end = min(deadline, time.monotonic() + 4)
    result = base.capture(argv(manager_pid, collector_pid), base.ENV, end, limit=256, privileged=True)
    if receipt is not None:
        receipt.append(
            {
                "sequence": len(receipt) + 1,
                "exit_code": result.get("exit_code") if type(result.get("exit_code")) is int else None,
                "reason": result.get("reason")
                if result.get("reason") in ("timeout", "output", "io")
                else None,
                "errno": result.get("errno") if type(result.get("errno")) is int else None,
                "stderr_bytes": result.get("stderr_bytes")
                if type(result.get("stderr_bytes")) is int
                else None,
                "direct_sudo_reaped": result.get("direct_sudo_reaped") is True,
                "privileged_descendants": "NOT_INDEPENDENTLY_OBSERVED",
            }
        )
    raw = result["stdout"]
    if (
        result["reason"]
        or result["exit_code"] != 0
        or result["stderr_bytes"]
        or not result["direct_sudo_reaped"]
        or type(raw) is not bytes
        or re.fullmatch(rb"(?:[0-9]{1,20}:[0-9]{1,20}\n){4}", raw) is None
    ):
        raise ValueError("stat")
    values = [[int(n) for n in row.split(b":")] for row in raw.splitlines()]
    if any(n >= 2**64 for row in values for n in row):
        raise ValueError("stat")
    return values


def recheck_snapshot(bus, probe, before, candidates):
    manager = bus.properties(probe.OBJECT, probe.MANAGER, probe.MANAGER_FIELDS)
    roster = bus.request(probe.OBJECT, probe.MANAGER, "ListUnits", (), "a(ssssssouso)")
    if len(roster) > probe.MAX_UNITS:
        raise ValueError("limit")
    roster = sorted((r[0], r[6], r[2], r[3], r[4]) for r in roster)
    jobs = bus.request(probe.OBJECT, probe.MANAGER, "ListJobs", (), "a(usssoo)")
    if len(jobs) > probe.MAX_ITEMS or jobs or before["jobs"]:
        raise ValueError("jobs")
    if manager != before["manager"] or roster != before["roster"]:
        raise ValueError("changed")
    for unit in before["units"]:
        if unit["properties"]["Id"] in candidates:
            if bus.properties(unit["path"], probe.UNIT, probe.UNIT_FIELDS) != unit["properties"]:
                raise ValueError("changed")


def fragment_inputs(snapshot, selected):
    selected_units = [u["properties"] for u in snapshot["units"] if u["properties"]["Id"] in selected]
    paths, file_units, no_fragment = [], [], []
    for props in selected_units:
        if props["FragmentPath"]:
            paths.append(props["FragmentPath"])
            file_units.append(props["Id"])
        elif (
            props["Id"] == "-.slice"
            and props["Names"] == ["-.slice"]
            and props["LoadState"] == "loaded"
            and props["SourcePath"] == ""
            and props["DropInPaths"] == []
            and props["Following"] == ""
            and not props["Transient"]
            and not props["NeedDaemonReload"]
        ):
            # Record only what the manager reported; this does not
            # assert absence of all configuration or other effects.
            no_fragment.append({"unit_id": "-.slice", "state": "FRAGMENT_PATH_EMPTY_REPORTED"})
        else:
            raise ValueError("empty_fragment")
    return paths, file_units, no_fragment


def observe(probe, candidates_module, fragments):
    deadline = time.monotonic() + 25
    result = {
        "schema": "forge-dependency-view-v1",
        "status": "UNKNOWN",
        "snapshot_consistency": "NONATOMIC",
        "stability": "NOT_ESTABLISHED",
        "manager_disk_equivalence": "NOT_ESTABLISHED",
        "package_provenance": "NOT_ESTABLISHED",
        "transaction_closure": "NOT_ESTABLISHED",
        "qualification": "NOT_RUN",
        "service_execution": "NOT_REQUESTED",
        "library_reply_allocation": "NOT_BOUNDED",
        "privileged_descendants": "NOT_INDEPENDENTLY_OBSERVED",
        "phase": "preflight",
        "stat_attempts": [],
    }
    try:
        probe.ordinary_identity()
        base = probe.metadata()
        tools_before = base.tools_snapshot(deadline)
        version = base.ordinary(
            ["/usr/bin/timeout", "--version"], operation="timeout_version", deadline=deadline
        )
        if not version.splitlines() or version.splitlines()[0] != b"timeout (GNU coreutils) 9.4":
            raise ValueError("timeout_version")
        collector_pid = os.getpid()
        collector_start = base.starttime(collector_pid)
        collector_fd = os.pidfd_open(collector_pid)
        collector = {"pid": collector_pid, "starttime": collector_start, "fd": collector_fd}
        try:
            # This path and library are already subject to the ordinary probe's checks.
            path = Path("/usr/lib/x86_64-linux-gnu/libsystemd.so.0").resolve(strict=True)
            data, library_id = base.file_bytes(path, 0, 16777216)
            probe.need(data.startswith(b"\x7fELF"), "library")
            library = probe.C.CDLL(str(path))
            with probe.verified_channel(base, deadline) as (channel, recheck, manager):
                bus = probe.Bus(library, channel, deadline)
                try:

                    def identities():
                        probe.remaining(deadline)
                        base.live(collector, os.getuid())
                        recheck()

                    identities()
                    window_start = time.monotonic_ns()
                    result["phase"] = "first_stat"
                    first = stat_sample(
                        base, manager["pid"], collector_pid, deadline, result["stat_attempts"]
                    )
                    if first[0] != first[2] or first[1] != first[3]:
                        raise ValueError("view")
                    identities()
                    result["phase"] = "snapshot"
                    snapshot = probe.collect(bus, recheck)
                    derived = candidates_module.derive(snapshot)
                    if (
                        derived["status"] != "DERIVED_OFFLINE_ONLY"
                        or derived["inbound_coverage"] == "UNKNOWN"
                    ):
                        raise ValueError("candidates")
                    selected = set(derived["candidates"])
                    if any(
                        u["properties"]["NeedDaemonReload"] or u["properties"]["Transient"]
                        for u in snapshot["units"]
                        if u["properties"]["Id"] in selected
                    ):
                        raise ValueError("unavailable")
                    # The collector does not subscribe to all lifecycle events and
                    # does not resample every noncandidate property.
                    derived["inbound_coverage"] = "NOT_ESTABLISHED"
                    paths, file_units, no_fragment = fragment_inputs(snapshot, selected)
                    result["phase"] = "fragments"
                    files = fragments.collect_fragment_hashes(
                        paths, snapshot["manager"]["UnitPath"], deadline=deadline
                    )
                    if files["status"] != "HASHED":
                        raise ValueError("files")
                    files["unit_ids"] = file_units
                    files["no_fragment"] = no_fragment
                    result["phase"] = "recheck"
                    recheck_snapshot(bus, probe, snapshot, selected)
                    identities()
                    result["phase"] = "second_stat"
                    second = stat_sample(
                        base, manager["pid"], collector_pid, deadline, result["stat_attempts"]
                    )
                    identities()
                    result["phase"] = "final_recheck"
                    window_end = time.monotonic_ns()
                    if base.tools_snapshot(deadline) != tools_before:
                        raise ValueError("tools")
                    if first != second or first[0] != first[2] or first[1] != first[3]:
                        raise ValueError("view")
                    if base.file_bytes(path, 0, 16777216)[1] != library_id:
                        raise ValueError("library")
                    probe.remaining(deadline)
                    # The ordinary probe samples these properties for its own
                    # comparison. They are not retained in this receipt.
                    snapshot["manager"].pop("ControlGroup", None)
                    for unit in snapshot["units"]:
                        unit["properties"].pop("RequiresMountsFor", None)
                    result.update(
                        status="OBSERVED",
                        phase="complete",
                        manager=manager,
                        collector={"pid": collector_pid, "starttime": collector_start},
                        view_ids=first,
                        monotonic_window_ns=[window_start, window_end],
                        snapshot=snapshot,
                        candidates=derived,
                        fragments=files,
                    )
                finally:
                    bus.close()
        finally:
            os.close(collector_fd)
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        # Avoid echoing errors, paths or input values from failures.
        result.update(status="UNKNOWN", reason="incomplete_or_changed")
    return result


def bounded_output(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    if len(raw) + 1 <= 262144:
        return raw, False
    retained = {
        key: value.get(key)
        for key in (
            "schema",
            "phase",
            "stat_attempts",
            "snapshot_consistency",
            "stability",
            "manager_disk_equivalence",
            "package_provenance",
            "transaction_closure",
            "qualification",
            "service_execution",
            "library_reply_allocation",
            "privileged_descendants",
        )
    }
    retained.update(status="UNKNOWN", reason="output")
    return json.dumps(retained, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(), True


def main():
    if sys.argv[1:]:
        return 2
    value = observe(
        load("dependency_snapshot"), load("dependency_candidates"), load("dependency_fragment_evidence")
    )
    raw, overflow = bounded_output(value)
    sys.stdout.buffer.write(raw + b"\n")
    return 0 if value["status"] == "OBSERVED" and not overflow else 2


if __name__ == "__main__":
    sys.exit(main())
