"""Project a private static receipt to fixed public labels; collect nothing.

This is not the collector's validator or a boundary against malicious workflow
code. Problem codes describe missing necessary observations, not root causes or
qualification. No host collection, collector imports, subprocesses or raw values
escape.
"""

import json
import os
import stat
import sys

MAX_INPUT = 16384
# Keep this independent of the collector: importing it is unnecessary here.
# New collector codes require an explicit public-disclosure review.
PUBLIC_PROBLEM_CODES = frozenset(
    {
        "apparmor_observed",
        "apparmor_profiles_observed",
        "apparmor_restriction_observed",
        "bwrap_identity",
        "cgroup2_mount",
        "controllers",
        "current_common_ancestor_access",
        "delegated_directory_access",
        "destination_procs_access",
        "existing_manager",
        "kernel_identity",
        "ordinary_user",
        "source_membership",
        "subtree_control",
    }
)


def summary(value, diagnostic_code):
    result = {
        "schema": "forge-public-static-summary-v2",
        "mode": "static",
        "status": "UNKNOWN",
        "boundary": "NOT_RUN",
        "qualification": "NOT_RUN",
        "raw_evidence": "NOT_PUBLISHED",
        "reason": "unavailable",
        "problems": [],
    }
    if (
        type(diagnostic_code) is not int
        or not 0 <= diagnostic_code <= 255
        or type(value) is not dict
        or value.get("schema") != "forge-linux-admission-observation-v2"
        or value.get("boundary") != "NOT_RUN"
        or value.get("qualification") != "NOT_RUN"
        or value.get("status") not in ("OBSERVED", "STOP")
    ):
        return result
    problems = value.get("problems")
    if (
        type(problems) is not list
        or len(problems) > len(PUBLIC_PROBLEM_CODES)
        or any(type(code) is not str or code not in PUBLIC_PROBLEM_CODES for code in problems)
        or (value["status"] == "STOP") != bool(problems)
    ):
        return result
    if value["status"] == "STOP":
        result.update(
            status="STOP",
            reason="collector_stop",
            # Emit code-owned literals only, not receipt-controlled values.
            problems=sorted(code for code in PUBLIC_PROBLEM_CODES if code in problems),
        )
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
