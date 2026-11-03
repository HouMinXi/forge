"""Publish only fixed labels and bounded counts, never raw diagnostic values.

This is a projection, not a diagnostic validator or a confidentiality boundary
against malicious workflow code. It performs no host collection or subprocesses.
"""

import json
import os
import stat
import sys

MAX_INPUT = 300000
SCHEMAS = {
    "static": "forge-linux-admission-observation-v2",
    "fixed": "forge-fixed-metadata-v3",
    "ordinary": "forge-dependency-dispatch-v1",
    "view": "forge-dependency-view-dispatch-v1",
}
REASONS = frozenset(
    "context source identity command format io timeout output limit changed library abi version "
    "tool private_endpoint private_connect private_credentials snapshot cleanup unexpected "
    "incomplete_or_changed conflicting_modes".split()
)


def summary(value, mode):
    result = {
        "schema": "forge-public-diagnostic-summary-v1",
        "mode": mode if mode in SCHEMAS else "unknown",
        "status": "UNKNOWN",
        "qualification": "NOT_RUN",
        "raw_evidence": "NOT_PUBLISHED",
        "reason": "unavailable",
    }
    if (
        mode not in SCHEMAS
        or type(value) is not dict
        or value.get("schema") != SCHEMAS[mode]
        or value.get("qualification") != "NOT_RUN"
        or value.get("status") not in ("OBSERVED", "STOP", "UNKNOWN")
    ):
        return result
    result["status"] = value["status"]
    reason = value.get("reason")
    result["reason"] = reason if type(reason) is str and reason in REASONS else "unspecified"
    inner = value.get("probe" if mode == "ordinary" else "observation")
    if mode not in ("ordinary", "view") or type(inner) is not dict:
        return result
    expected = "forge-dependency-snapshot-v1" if mode == "ordinary" else "forge-dependency-view-v1"
    if inner.get("schema") != expected or inner.get("qualification") != "NOT_RUN":
        return result
    # Do not carry through any key or string from the raw receipt. Even errors,
    # hashes, object paths, context, process IDs and dependency edges are omitted.
    snapshot = inner.get("snapshot")
    if type(snapshot) is dict:
        for source, target, maximum in (("units", "unit_count", 256), ("jobs", "job_count", 64)):
            rows = snapshot.get(source)
            if type(rows) is list and len(rows) <= maximum:
                result[target] = len(rows)
    if mode == "view":
        candidates, fragments = inner.get("candidates"), inner.get("fragments")
        for container, source, target in (
            (candidates, "candidates", "candidate_count"),
            (fragments, "fragment_sha256", "fragment_count"),
        ):
            rows = container.get(source) if type(container) is dict else None
            if type(rows) is list and len(rows) <= 64:
                result[target] = len(rows)
        attempts = inner.get("stat_attempts")
        if type(attempts) is list and len(attempts) <= 2:
            result["reported_stat_attempt_count"] = len(attempts)
    return result


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def read_receipt(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT:
            raise ValueError("input")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_INPUT + 1)
        after = os.fstat(fd)
        if (
            len(raw) != info.st_size
            or len(raw) > MAX_INPUT
            or (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise ValueError("changed")
        return json.loads(raw, object_pairs_hook=unique_keys)
    finally:
        os.close(fd)


def main():
    mode = sys.argv[1] if len(sys.argv) == 3 and sys.argv[1] in SCHEMAS else "unknown"
    try:
        value = read_receipt(sys.argv[2]) if mode != "unknown" else None
        result = summary(value, mode)
    except (OSError, ValueError, TypeError, RecursionError):
        result = summary(None, mode)
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 0 if result["status"] == "OBSERVED" else 2


if __name__ == "__main__":
    sys.exit(main())
