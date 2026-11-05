"""Project a private static receipt to fixed public labels; collect nothing.

This is not the collector's validator or a boundary against malicious workflow
code. Problem codes describe missing necessary observations, not root causes or
qualification. No host collection, collector imports, subprocesses or raw values
escape.
"""

import errno
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
PUBLIC_BWRAP_REASONS = frozenset(
    {
        "missing",
        "unreadable",
        "io_error",
        "path_outside_allowlist",
        "unsafe_metadata",
        "oversize",
        "size_mismatch",
    }
)
PUBLIC_PROFILE_REASONS = frozenset({"missing", "unreadable", "io_error", "oversize", "invalid_utf8"})
PUBLIC_ACCESS_FIELDS = {
    "delegated_directory_access": "delegated_directory",
    "destination_procs_access": "destination_procs",
    "current_common_ancestor_access": "common_ancestor_procs",
}


def _require_detail(condition):
    if not condition:
        raise ValueError("invalid detail")


def _failure_reason(value, allowed):
    """Validate an existing diagnostic without publishing its numeric errno."""
    _require_detail(type(value) is dict and set(value) == {"reason", "errno"})
    reason, number = value["reason"], value["errno"]
    _require_detail(type(reason) is str and reason in allowed)
    _require_detail(number is None or type(number) is int and 1 <= number <= 4095)
    if reason == "missing":
        _require_detail(number in (errno.ENOENT, errno.ENOTDIR))
    elif reason == "unreadable":
        _require_detail(number in (errno.EACCES, errno.EPERM))
    elif reason == "io_error":
        _require_detail(number not in (errno.ENOENT, errno.ENOTDIR, errno.EACCES, errno.EPERM))
    else:
        _require_detail(number is None)
    return next(code for code in allowed if code == reason)


def failure_details(value, problems):
    """Project only failing categories, without collecting or recomputing facts.

    This checks the selected detail fields, not the whole private receipt. In
    particular, the public output does not authenticate the reported facts.
    """
    selected = set(problems) & {*PUBLIC_ACCESS_FIELDS, "bwrap_identity", "apparmor_profiles_observed"}
    if not selected:
        return {}
    facts = value.get("facts")
    _require_detail(type(facts) is dict)
    details = {}
    for problem, field in PUBLIC_ACCESS_FIELDS.items():
        if problem not in selected:
            continue
        access = facts.get("access")
        _require_detail(type(access) is dict and field in access)
        _require_detail(access[field] is False or access[field] is None)
        # False also covers missing paths; it is not a permission-denied errno.
        details[problem] = ["not_writable" if access[field] is False else "unknown"]
    diagnostic_categories = selected & {"bwrap_identity", "apparmor_profiles_observed"}
    if diagnostic_categories:
        diagnostics = value.get("diagnostics")
        _require_detail(type(diagnostics) is dict)
        if "bwrap_identity" in selected:
            _require_detail("bwrap" in facts and facts["bwrap"] is None)
            candidates = diagnostics.get("bwrap")
            _require_detail(type(candidates) is dict and set(candidates) == {"usr_bin", "bin"})
            # Discard fixed candidate labels, order and duplicate multiplicity.
            details["bwrap_identity"] = sorted(
                {_failure_reason(item, PUBLIC_BWRAP_REASONS) for item in candidates.values()}
            )
        if "apparmor_profiles_observed" in selected:
            apparmor = facts.get("apparmor")
            _require_detail(type(apparmor) is dict and "bwrap_profiles" in apparmor)
            _require_detail(apparmor["bwrap_profiles"] is None)
            _require_detail(
                "enabled" in apparmor and (apparmor["enabled"] is True or apparmor["enabled"] is None)
            )
            details["apparmor_profiles_observed"] = [
                _failure_reason(diagnostics.get("apparmor_profiles"), PUBLIC_PROFILE_REASONS)
            ]
    return dict(sorted(details.items()))


def summary(value, diagnostic_code):
    result = {
        "schema": "forge-public-static-summary-v3",
        "mode": "static",
        "status": "UNKNOWN",
        "boundary": "NOT_RUN",
        "qualification": "NOT_RUN",
        "raw_evidence": "NOT_PUBLISHED",
        "reason": "unavailable",
        "problems": [],
        "details": {},
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
    try:
        details = failure_details(value, problems)
    except ValueError:
        return result
    if value["status"] == "STOP":
        result.update(
            status="STOP",
            reason="collector_stop",
            # Emit code-owned literals only, not receipt-controlled values.
            problems=sorted(code for code in PUBLIC_PROBLEM_CODES if code in problems),
            details=details,
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
