"""Independent closed projection for offline capture observations, never raw evidence."""

import json

SCHEMA = "forge-offline-capture-observations-v1"
ENUMS = {
    "mode": {"baseline", "diagnostic"},
    "receipt_status": {"DELIVERED", "UNAVAILABLE", "INVALID"},
    "cleanup": {"COMPLETE", "INCOMPLETE", "NOT_OBSERVED"},
    "fixture_outcome": {"BOUND_EXPECTED_UNRELIABLE", "UNASSESSED"},
    "event_reason": {"NONE", "NOT_OBSERVED", "UNSUPPORTED_EVENT_SHAPE", "MALFORMED_OR_INCOMPLETE"},
    "event_integrity": {"COMPLETE", "INCOMPLETE", "UNAVAILABLE"},
    "trace_integrity": {"COMPLETE", "INCOMPLETE", "UNAVAILABLE"},
    "execution_identity": {"UNASSESSED"},
}
CONSTANTS = {
    "schema": SCHEMA,
    "event_scope": "SUPPORTED_REQUEST_SUBSET",
    "trace_scope": "SUPPORTED_SYNTAX_AND_PER_TASK_FRAMING",
    "fixture_scope": "CALLER_TRUSTED_LOCAL_COLLECTOR_RECORDS",
    "cross_stream_order": "NOT_ASSESSED",
    "ack_consistency": "NOT_ASSESSED",
    "power_loss_durability": "NOT_ASSESSED",
    "qualification": "NOT_ASSESSED",
    "raw_evidence": "NOT_PUBLISHED",
}
COUNTS = {"event_count", "trace_count", "sync_count"}
CODES = {"shell_exit", "worker_exit", "cli_exit"}


def validate(row):
    if type(row) is not dict or set(row) != set(ENUMS) | set(CONSTANTS) | COUNTS | CODES:
        raise ValueError("closed observation fields required")
    if any(type(row[k]) is not str or row[k] not in values for k, values in ENUMS.items()):
        raise ValueError("closed observation enum required")
    if any(row[k] != value for k, value in CONSTANTS.items()):
        raise ValueError("observation scope changed")
    for key in COUNTS:
        if row[key] is not None and (type(row[key]) is not int or not 0 <= row[key] <= 200000):
            raise ValueError("bounded observation count required")
    for key in CODES:
        if row[key] is not None and (type(row[key]) is not int or not -127 <= row[key] <= 255):
            raise ValueError("bounded original status required")
    if type(row["shell_exit"]) is not int or not 0 <= row["shell_exit"] <= 255:
        raise ValueError("independent shell status required")
    if row["fixture_outcome"] == "BOUND_EXPECTED_UNRELIABLE" and (
        row["receipt_status"] != "DELIVERED"
        or row["cleanup"] != "COMPLETE"
        or row["shell_exit"] != 7
        or row["worker_exit"] != 0
        or row["cli_exit"] != 7
    ):
        raise ValueError("fixture status contradiction")
    if (row["event_integrity"] == "COMPLETE") != (row["event_reason"] == "NONE") or (
        row["event_integrity"] == "UNAVAILABLE"
    ) != (row["event_reason"] == "NOT_OBSERVED"):
        raise ValueError("event reason contradiction")
    for status, count in (("event_integrity", "event_count"), ("trace_integrity", "trace_count")):
        if row[status] == "COMPLETE" and (
            row["receipt_status"] != "DELIVERED"
            or row["cleanup"] != "COMPLETE"
            or row[count] is None
            or row[count] == 0
        ):
            raise ValueError("complete observation contradiction")
        if row[status] != "COMPLETE" and row[count] is not None:
            raise ValueError("unvalidated observation count")
    if (row["trace_integrity"] == "COMPLETE") != (row["sync_count"] is not None):
        raise ValueError("unvalidated sync count")
    if row["sync_count"] is not None and row["sync_count"] > row["trace_count"]:
        raise ValueError("sync count contradiction")
    if row["mode"] == "baseline" and (
        row["event_integrity"] != "UNAVAILABLE"
        or row["trace_integrity"] != "UNAVAILABLE"
        or row["execution_identity"] != "UNASSESSED"
    ):
        raise ValueError("baseline cannot supply diagnostic evidence")
    return dict(row)


def encode(row):
    return json.dumps(validate(row), sort_keys=True) + "\n"


def publish(candidate, destination, shell_exit, helper_exit):
    """Read only a closed candidate, independently bind its exit, and reserialize."""
    import importlib.util
    import os
    from pathlib import Path
    import stat

    path = Path(__file__).with_name("cli_capture_publication.py")
    spec = importlib.util.spec_from_file_location("_observation_boundary", path)
    boundary = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(boundary)
    candidate, destination = Path(candidate), Path(destination)
    boundary.require(type(helper_exit) is int and helper_exit == 0)
    boundary.require(type(shell_exit) is int and 0 <= shell_exit <= 255)
    before = boundary.private_directory(candidate.parent)
    boundary.private_directory(destination.parent)
    directory = os.open(candidate.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        boundary.require(boundary.fingerprint(before) == boundary.fingerprint(os.fstat(directory)))
        fd = os.open(candidate.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        try:
            info = os.fstat(fd)
            boundary.require(
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_nlink == 1
                and 0 < info.st_size <= 4096
            )
            raw = os.read(fd, 4097)
            boundary.require(len(raw) == info.st_size and raw.endswith(b"\n"))
            boundary.require(
                boundary.fingerprint(info)
                == boundary.fingerprint(os.fstat(fd))
                == boundary.fingerprint(os.stat(candidate.name, dir_fd=directory, follow_symlinks=False))
            )
        finally:
            os.close(fd)
        boundary.require(
            boundary.fingerprint(before)
            == boundary.fingerprint(os.fstat(directory))
            == boundary.fingerprint(candidate.parent.lstat())
        )
    finally:
        os.close(directory)
    row = validate(
        json.loads(
            raw,
            object_pairs_hook=boundary.unique,
            parse_float=boundary.reject,
            parse_constant=boundary.reject,
        )
    )
    boundary.require(row["mode"] == "diagnostic" and row["shell_exit"] == shell_exit)
    # A new destination prevents stale successful evidence from being reused.
    destination.mkdir(mode=0o700)
    fd = os.open(
        destination / "observations.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w") as handle:
        handle.write(encode(row))


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--shell-exit", required=True, type=int)
    parser.add_argument("--helper-exit", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        publish(args.candidate, args.destination, args.shell_exit, args.helper_exit)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        return 74
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
