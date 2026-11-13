"""Offline private-file observations; no target execution or cross-stream ordering."""

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 32 * 1024 * 1024
BASE = {"schema", "seq", "pid", "tid", "monotonic_ns", "time_ns", "event"}
PENDING = {
    "open_requests",
    "open_tickets",
    "open_workers",
    "open_waits",
    "open_submits",
    "open_publishes",
    "open_frames",
    "unknown_targets",
}
REQUEST = {"host_id", "operation_id", "phase", "deadline_ns", "attempt"}
PHASES = {
    "open",
    "input_manifest",
    "register_operation",
    "start",
    "entered",
    "begin_stage",
    "append",
    "seal",
    "adopt",
    "observe",
    "finish",
    "finalize",
    "close",
    "unknown",
}


def module(relative):
    # Only reviewed repository paths chosen in this source, never input paths.
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location("_offline_" + path.stem, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


projection = module("scripts/cli_capture_public_summary.py")
publication = module("scripts/cli_capture_observations_publication.py")
trace_parser = module("qualification/installed_cli/trace_parser.py")
controller = module("scripts/capture_installed_cli.py")


def require(value):
    if not value:
        raise ValueError("offline evidence rejected")


def integer(value, low=0, high=2**63 - 1):
    return type(value) is int and low <= value <= high


def read_private(path, limit=LIMIT):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts)
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        for part in path.parts[1:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = following
        parent = os.fstat(directory)
        require(parent.st_uid == os.geteuid() and stat.S_IMODE(parent.st_mode) == 0o700)
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == os.geteuid()
            and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_nlink == 1
            and 0 < before.st_size <= limit
        )
        raw = bytearray()
        while len(raw) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(fd)
        named = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        require(
            len(raw) == before.st_size
            and projection.signature(before)
            == projection.signature(after)
            == projection.signature(named)
        )
        return bytes(raw)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def decode(raw):
    try:
        return projection.decode(raw)
    except (RecursionError, OverflowError) as exc:
        raise ValueError("offline JSON bounds rejected") from exc


class UnsupportedEvent(ValueError):
    """Known probe family whose lifecycle is deliberately not certified here."""


def events(raw, pid):
    """Strict supported request-only lifecycle; other event families stay incomplete.

    This intentionally does not certify arbitrary ticket/worker/publication traces.
    No realtime/monotonic offset or cross-thread ordering is inferred.
    """
    require(raw.endswith(b"\n") and b"\r" not in raw and len(raw) <= LIMIT)
    lines = raw[:-1].split(b"\n")
    require(4 <= len(lines) <= 200000)
    active, seen, clocks = {}, set(), {}
    requests = 0
    for index, line in enumerate(lines, 1):
        require(len(line) <= 4096)
        row = decode(line)
        require(type(row) is dict and type(row.get("event")) is str)
        name = row["event"]
        extra = (
            set()
            if name == "diagnostic_start"
            else PENDING | {"complete"}
            if name == "diagnostic_footer"
            else REQUEST
            if name in {"host_request_begin", "host_request_end", "host_request_error"}
            else None
        )
        if extra is None and name in {
            "ticket_create",
            "ticket_submit_begin",
            "ticket_submit",
            "ticket_submit_error",
            "ticket_unknown",
            "ticket_complete",
            "worker_begin",
            "worker_end",
            "target_end",
            "wait_begin",
            "wait_end",
            "wait_error",
            "publish_begin",
            "publish_end",
            "publish_error",
        }:
            raise UnsupportedEvent()
        require(extra is not None and set(row) == BASE | extra)
        require(
            type(row["schema"]) is int
            and row["schema"] == 1
            and integer(row["seq"], 1)
            and row["seq"] == index
            and integer(row["pid"], 1, 2**31 - 1)
            and row["pid"] == pid
            and integer(row["tid"], 1, 2**31 - 1)
        )
        require(integer(row["monotonic_ns"], 1) and integer(row["time_ns"], 1))
        tid = row["tid"]
        require(row["monotonic_ns"] >= clocks.get(tid, 0))
        clocks[tid] = row["monotonic_ns"]
        if index == 1:
            require(name == "diagnostic_start")
        elif index == len(lines):
            require(
                name == "diagnostic_footer" and row["complete"] is True and not active and requests > 0
            )
            require(all(type(row[key]) is int and row[key] == 0 for key in PENDING))
        else:
            require(name in {"host_request_begin", "host_request_end", "host_request_error"})
            require(
                integer(row["host_id"], 1)
                and integer(row["operation_id"], 1)
                and type(row["attempt"]) is int
                and row["attempt"] == 0
                and type(row["phase"]) is str
                and row["phase"] in PHASES
            )
            require(row["deadline_ns"] is None or integer(row["deadline_ns"]))
            identity = row["operation_id"]
            fields = {key: row[key] for key in REQUEST} | {"tid": tid}
            if name == "host_request_begin":
                require(identity not in seen)
                seen.add(identity)
                active[identity] = fields
                requests += 1
            else:
                require(active.pop(identity, None) == fields)
    return len(lines)


def fixed_response(index):
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"mutations": []} if index == 0 else {"findings": []}),
                    "reasoning_content": "PRIVATE_SYNTHETIC_WIRE_SENTINEL",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3},
    }


def fixture_bound(root, record, binding, evidence, expected_sha):
    # The supplied source identity is an explicit caller expectation, not inferred
    # from CLI7 or from a hash of whichever helper happens to be installed today.
    require(
        type(expected_sha) is str
        and len(expected_sha) == 40
        and all(c in "0123456789abcdef" for c in expected_sha)
    )
    provenance, _, _ = controller.verify_installation(evidence, owner_only=True)
    require(
        provenance["source_commit"] == expected_sha and binding["installed_provenance"] == provenance
    )
    archive_raw = controller.regular_bytes(Path(evidence) / "source.tar")
    source_members = controller.source_members(archive_raw, expected_sha)
    current_names = {"pyproject.toml", "src/code_forge_launcher.py"} | {
        str(path.relative_to(ROOT))
        for path in (ROOT / "src/code_forge").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    require(set(source_members) == current_names)
    require(all(controller.regular_bytes(ROOT / name) == raw for name, raw in source_members.items()))
    with tarfile.open(fileobj=io.BytesIO(archive_raw), mode="r:") as archive:
        names = archive.getnames()
        member = "qualification/installed_cli/capture_worker.py"
        require(names.count(member) == 1)
        info = archive.getmember(member)
        require(info.isfile() and info.size <= 1024 * 1024)
        source = archive.extractfile(info).read(1024 * 1024 + 1)
    reviewed = (ROOT / "qualification/installed_cli/capture_worker.py").read_bytes()
    require(
        source == reviewed
        and hashlib.sha256(source).hexdigest() == binding["capture_hashes"]["capture_worker.py"]
    )
    worker = record["worker"]
    require(
        json.dumps(worker["peer_transport"], sort_keys=True)
        == json.dumps({"requests": 2, "exchanges": 2, "errors": []}, sort_keys=True)
    )
    # Equality alone would admit True for 1; require exact canonical JSON as well.
    for index in (0, 1):
        raw = read_private(root / "worker" / f"response-{index}.json", 65536)
        row = decode(raw)
        require(json.dumps(row, sort_keys=True) == json.dumps(fixed_response(index), sort_keys=True))
    require(binding["mode"] == record["mode"] if "mode" in binding else True)
    return True


def empty(mode, shell_exit):
    return dict(publication.CONSTANTS) | {
        "mode": mode,
        "receipt_status": "UNAVAILABLE",
        "cleanup": "NOT_OBSERVED",
        "shell_exit": shell_exit,
        "worker_exit": None,
        "cli_exit": None,
        "fixture_outcome": "UNASSESSED",
        "event_integrity": "UNAVAILABLE",
        "event_reason": "NOT_OBSERVED",
        "trace_integrity": "UNAVAILABLE",
        "execution_identity": "UNASSESSED",
        "event_count": None,
        "trace_count": None,
        "sync_count": None,
    }


def analyze(root, mode, shell_exit, *, evidence=None, expected_sha=None):
    row = empty(mode, shell_exit)
    publication.validate(row)
    root = Path(root)
    try:
        record = decode(read_private(root / "result.json", 4 * 1024 * 1024))
        summary = projection.project(record, mode, shell_exit)
        row.update(
            receipt_status="DELIVERED",
            cleanup=summary["cleanup"],
            worker_exit=summary["worker_returncode"],
            cli_exit=summary["cli_returncode"],
        )
        worker, ownership, health = projection.validate(record, mode, shell_exit)
        require(
            ownership is not None
            and ownership["cleanup_complete"] is True
            and not any(item["remaining"] for item in ownership["owned"])
        )
        require(
            worker is not None
            and record["worker_returncode"] == 0
            and record["outer_exception"] is None
            and not record["record_errors"]
            and worker["primary_exception"] is None
            and not worker["cleanup_errors"]
            and worker["cleanup_induced_termination"] is False
        )
        require(
            health is not None
            and health["status"] == ("healthy" if mode == "diagnostic" else "not_applicable")
            and type(health.get("cache_health")) is dict
            and health["cache_health"].get("status") == "healthy"
        )
    except (OSError, ValueError, TypeError, KeyError):
        row["receipt_status"] = "INVALID"
        return publication.validate(row)
    if (
        shell_exit == 7
        and worker["child_returncode"] == worker["child_returncode_before_cleanup"] == 7
        and evidence is not None
        and expected_sha is not None
    ):
        try:
            binding = decode(read_private(root / "binding.json", 1024 * 1024))
            fixture_bound(root, record, binding, evidence, expected_sha)
            row["fixture_outcome"] = "BOUND_EXPECTED_UNRELIABLE"
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            tarfile.TarError,
            zipfile.BadZipFile,
            RecursionError,
            OverflowError,
            csv.Error,
        ):
            pass
    if mode == "diagnostic":
        pid = worker["child_pid"]
        try:
            row["event_count"] = events(read_private(root / "worker/diagnostic/events.jsonl"), pid)
            row["event_integrity"] = "COMPLETE"
            row["event_reason"] = "NONE"
        except UnsupportedEvent:
            row.update(event_integrity="INCOMPLETE", event_reason="UNSUPPORTED_EVENT_SHAPE")
        except (OSError, ValueError, TypeError, KeyError):
            row.update(event_integrity="INCOMPLETE", event_reason="MALFORMED_OR_INCOMPLETE")
        try:
            trace = trace_parser.parse_trace(
                read_private(root / "worker/diagnostic/syscalls.log").decode("utf-8")
            )
            require(trace["complete"] is True)
            row.update(
                trace_integrity="COMPLETE",
                trace_count=len(trace["events"]),
                sync_count=sum(e.get("syscall") in {"fsync", "fdatasync"} for e in trace["events"]),
            )
        except (OSError, ValueError, TypeError, KeyError):
            row["trace_integrity"] = "INCOMPLETE"
    return publication.validate(row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("baseline", "diagnostic"))
    parser.add_argument("--shell-exit", required=True, type=int)
    parser.add_argument("--wheel-evidence", type=Path)
    parser.add_argument("--expected-source-sha")
    args = parser.parse_args()
    try:
        row = analyze(
            args.capture_root,
            args.mode,
            args.shell_exit,
            evidence=args.wheel_evidence,
            expected_sha=args.expected_source_sha,
        )
        print(publication.encode(row), end="")
    except (OSError, ValueError, TypeError, KeyError):
        return 74
    # This is publication delivery only, never a replacement for the captured exit.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
