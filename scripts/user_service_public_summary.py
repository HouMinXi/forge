"""Project a private wrapper result to fixed public labels; collect nothing.

The projection is not a validator or a boundary against malicious workflow code.
It never imports the collector or publishes identifiers, paths, PIDs or hashes.
"""

import json
import os
import stat
import sys

MAX_INPUT = 16384
REASONS = (
    "authorization_required",
    "source_mismatch",
    "observation_complete",
    "collector_stop",
)


def summary(value, diagnostic_code):
    result = {
        "schema": "forge-public-user-service-summary-v1",
        "mode": "user_service",
        "status": "UNKNOWN",
        "boundary": "NOT_RUN",
        "qualification": "NOT_RUN",
        "raw_evidence": "NOT_PUBLISHED",
        "reason": "unavailable",
    }
    if (
        type(diagnostic_code) is not int
        or not 0 <= diagnostic_code <= 255
        or type(value) is not dict
        or value.get("schema") != "forge-user-service-wrapper-status-v1"
        or value.get("boundary") != "NOT_RUN"
        or value.get("qualification") != "NOT_RUN"
        or value.get("status") not in ("OBSERVED", "STOP")
        or value.get("cleanup") not in ("OBSERVED", "STOP", "UNKNOWN", "NOT_RUN")
        or value.get("receipt") not in ("VALID", "INVALID", "MISSING")
        or type(value.get("source_rechecked")) is not bool
    ):
        return result
    if value["status"] == "OBSERVED" and diagnostic_code == 0:
        if (
            value["cleanup"] != "OBSERVED"
            or value["receipt"] != "VALID"
            or value["source_rechecked"] is not True
        ):
            return result
        result.update(status="OBSERVED", reason="observation_complete")
    else:
        result["status"] = "STOP"
        reason = value.get("reason")
        result["reason"] = reason if type(reason) is str and reason in REASONS else "unspecified"
        if value["status"] == "OBSERVED":
            # A flushed success receipt cannot turn a later kill/failure green.
            result["reason"] = "execution_failed"
    return result


def unique_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate")
        value[key] = item
    return value


def invalid_constant(_value):
    raise ValueError("constant")


def read_receipt(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_INPUT:
            raise ValueError("input")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_INPUT + 1)
        after = os.fstat(fd)
        if (
            len(raw) != before.st_size
            or len(raw) > MAX_INPUT
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise ValueError("changed")
        # The producer uses no floating-point fields. Reject exponent overflow
        # as well as NaN/Infinity, which alone would reach parse_constant.
        return json.loads(
            raw,
            object_pairs_hook=unique_keys,
            parse_constant=invalid_constant,
            parse_float=invalid_constant,
        )
    finally:
        os.close(fd)


def main():
    try:
        if len(sys.argv) != 3:
            raise ValueError("arguments")
        result = summary(read_receipt(sys.argv[1]), int(sys.argv[2]))
    except (OSError, ValueError, TypeError, RecursionError):
        result = summary(None, None)
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0 if result["status"] == "OBSERVED" else 2


if __name__ == "__main__":
    sys.exit(main())
