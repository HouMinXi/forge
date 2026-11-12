"""Project a private capture receipt into fixed, non-qualification observations.

This helper never reads raw events, syscalls, responses or console output. Its
exit describes receipt projection only, not CLI success. The caller retains the
independently observed capture command status as its own execution result.
"""

import errno
import json
import math
import os
from pathlib import PurePosixPath
import re
import stat
import sys

MAX_BYTES = 8 * 1024 * 1024
SCHEMA = "forge-public-cli-capture-summary-v1"
MODES = {"baseline", "diagnostic"}


class InvalidReceipt(ValueError):
    """The private input is outside the supported receipt contract."""


class ContradictoryReceipt(InvalidReceipt):
    """Two recorded or independently observed facts disagree."""


def require(condition):
    if not condition:
        raise InvalidReceipt


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def signature(row):
    return (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)


def private_bytes(path):
    """Read one stable, owned 0600 file through no-follow directory handles."""
    require(type(path) is str and path.startswith("/") and not path.startswith("//"))
    parts = PurePosixPath(path).parts
    require(str(PurePosixPath(path)) == path and ".." not in parts and len(parts) >= 3)
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        for part in parts[1:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = following
        parent = os.fstat(directory)
        require(parent.st_uid == os.geteuid() and stat.S_IMODE(parent.st_mode) == 0o700)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == os.geteuid()
            and stat.S_IMODE(before.st_mode) == 0o600
            and before.st_nlink == 1
            and 0 < before.st_size <= MAX_BYTES
        )
        data = bytearray()
        while len(data) <= MAX_BYTES:
            block = os.read(fd, min(65536, MAX_BYTES + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(fd)
        named = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        require(
            len(data) == before.st_size
            and signature(before) == signature(after) == signature(named)
            and data.endswith(b"\n")
        )
        return bytes(data)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def reject_constant(_value):
    raise InvalidReceipt


def finite_float(text):
    value = float(text)
    require(math.isfinite(value))
    return value


def decode(raw):
    return json.loads(
        raw, object_pairs_hook=unique_keys, parse_constant=reject_constant, parse_float=finite_float
    )


def empty(mode, shell_exit, status):
    return {
        "schema": SCHEMA,
        "mode": mode if type(mode) is str and mode in MODES else "UNKNOWN",
        "receipt_status": status,
        "harness_shell_exit": shell_exit if integer(shell_exit, 0, 255) else None,
        "worker_returncode": None,
        "cli_returncode": None,
        "cli_returncode_before_cleanup": None,
        "cleanup_induced_termination": None,
        "collector_delivery": "NOT_OBSERVED",
        "cleanup": "NOT_OBSERVED",
        "qualification": "NOT_ASSESSED",
        "raw_evidence": "NOT_PUBLISHED",
    }


def exception(value):
    require(value is None or type(value) is dict and type(value.get("type")) is str)


def validate(record, mode, shell_exit):
    require(type(record) is dict and type(record.get("schema")) is int and record["schema"] == 1)
    require(record.get("mode") == mode and integer(record.get("shell_exit"), 0, 255))
    if record["shell_exit"] != shell_exit:
        raise ContradictoryReceipt
    require(
        record.get("qualification") == "NOT_ASSESSED"
        and record.get("capture_scope") == "delivery_framing_only"
        and record.get("ack_consistency") == "NOT_ASSESSED"
        and record.get("stream_correlation") == "NOT_ASSESSED"
    )
    require("outer_exception" in record and type(record.get("record_errors")) is list)
    exception(record["outer_exception"])
    require(
        all(
            key in record
            for key in ("worker_returncode", "worker", "ownership", "final_collector_health")
        )
    )
    worker_code = record.get("worker_returncode")
    require(worker_code is None or integer(worker_code, -127, 255))
    ownership = record.get("ownership")
    if ownership is not None:
        require(type(ownership) is dict and type(ownership.get("cleanup_complete")) is bool)
        require(type(ownership.get("owned")) is list)
        require(
            all(type(row) is dict and type(row.get("remaining")) is bool for row in ownership["owned"])
        )
        if ownership["cleanup_complete"] and any(row["remaining"] for row in ownership["owned"]):
            raise ContradictoryReceipt
    worker = record.get("worker")
    if worker is not None:
        require(
            type(worker) is dict
            and type(worker.get("schema")) is int
            and worker["schema"] == 1
            and worker.get("mode") == mode
            and worker.get("transport_status") == "receipt_written"
            and type(worker.get("cleanup_induced_termination")) is bool
            and type(worker.get("cleanup_errors")) is list
            and "primary_exception" in worker
            and "child_returncode" in worker
            and "child_returncode_before_cleanup" in worker
        )
        exception(worker["primary_exception"])
        code, before = worker["child_returncode"], worker["child_returncode_before_cleanup"]
        require(all(value is None or integer(value, -127, 255) for value in (code, before)))
        require(code is not None or worker["primary_exception"] is not None)
        if before is not None and (before != code or worker["cleanup_induced_termination"]):
            raise ContradictoryReceipt
        require(code is None or integer(worker.get("child_pid"), 1, 2**31 - 1))
    health = record.get("final_collector_health")
    if health is not None:
        require(type(health) is dict and health.get("stage") == "after_owner_quiescence")
        require(health.get("scope") == "delivery_framing_only" and type(health.get("errors")) is list)
        allowed = {"healthy", "failed"} if mode == "diagnostic" else {"not_applicable"}
        require(type(health.get("status")) is str and health["status"] in allowed)
        if worker is None or ownership is None or not ownership["cleanup_complete"]:
            raise ContradictoryReceipt
        if health["status"] in {"healthy", "not_applicable"} and health["errors"]:
            raise ContradictoryReceipt
    return worker, ownership, health


def project(record, mode, shell_exit):
    require(type(mode) is str and mode in MODES and integer(shell_exit, 0, 255))
    worker, ownership, health = validate(record, mode, shell_exit)
    result = empty(mode, shell_exit, "DELIVERED")
    result["worker_returncode"] = record["worker_returncode"]
    if worker is not None:
        result.update(
            cli_returncode=worker["child_returncode"],
            cli_returncode_before_cleanup=worker["child_returncode_before_cleanup"],
            cleanup_induced_termination=worker["cleanup_induced_termination"],
        )
    if ownership is not None:
        if not ownership["cleanup_complete"] or worker is not None and worker["cleanup_errors"]:
            result["cleanup"] = "INCOMPLETE"
        elif worker is not None:
            result["cleanup"] = "COMPLETE"
    if health is not None:
        result["collector_delivery"] = {
            "healthy": "HEALTHY",
            "failed": "FAILED",
            "not_applicable": "NOT_APPLICABLE",
        }[health["status"]]
    return result


def summarize(path, mode, shell_exit):
    if type(mode) is not str or mode not in MODES or not integer(shell_exit, 0, 255):
        return empty(mode, shell_exit, "INVALID_ARGUMENTS")
    try:
        return project(decode(private_bytes(path)), mode, shell_exit)
    except ContradictoryReceipt:
        return empty(mode, shell_exit, "CONTRADICTORY")
    except OSError as error:
        status = "INVALID" if error.errno in {errno.ELOOP, errno.ENOTDIR} else "UNAVAILABLE"
        return empty(mode, shell_exit, status)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return empty(mode, shell_exit, "INVALID")


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    mode = status = path = None
    if len(args) == 6 and set(args[::2]) == {"--result", "--mode", "--shell-exit"}:
        options = dict(zip(args[::2], args[1::2], strict=True))
        mode, path = options["--mode"], options["--result"]
        text = options["--shell-exit"]
        if re.fullmatch(r"0|[1-9][0-9]{0,2}", text):
            status = int(text)
    result = summarize(path, mode, status)
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0 if result["receipt_status"] == "DELIVERED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
