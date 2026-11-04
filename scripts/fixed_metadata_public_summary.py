"""Project a private fixed-metadata receipt to fixed public labels; collect nothing.

This is not the collector's validator or a boundary against malicious workflow
code. No host collection, collector imports, subprocesses or raw values escape.
"""

import json
import os
import stat
import sys

MAX_INPUT = 16384


def summary(value, diagnostic_code):
    result = {
        "schema": "forge-public-fixed-metadata-summary-v1",
        "mode": "fixed-metadata",
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
        or value.get("schema") != "forge-fixed-metadata-v3"
        or value.get("boundary") != "NOT_RUN"
        or value.get("qualification") != "NOT_RUN"
        or value.get("status") not in ("OBSERVED", "STOP")
    ):
        return result
    if value["status"] == "STOP":
        result.update(status="STOP", reason="collector_stop")
    elif diagnostic_code != 0:
        # A flushed success receipt cannot turn a later kill/failure green.
        result.update(status="STOP", reason="execution_failed")
    else:
        result.update(status="OBSERVED", reason="observation_complete")
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
