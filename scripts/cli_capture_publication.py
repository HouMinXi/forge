"""Independently validate closed capture summaries before artifact publication.

Never read raw capture data or copy source bytes into the publication directory.
This does not certify the capture or the installed product.
"""

import json
import os
from pathlib import Path
import stat
import sys

NAMES = ("capability.json", "baseline.json", "diagnostic.json")
FACTS = {"linux", "pidfd_bindings", "git", "ip", "unshare", "strace"}
SUMMARY_KEYS = {
    "schema",
    "mode",
    "receipt_status",
    "harness_shell_exit",
    "worker_returncode",
    "cli_returncode",
    "cli_returncode_before_cleanup",
    "cleanup_induced_termination",
    "collector_delivery",
    "cleanup",
    "qualification",
    "raw_evidence",
}

LEGACY_SUMMARY_KEYS = SUMMARY_KEYS.copy()
SUMMARY_KEYS |= {"controller_stage", "controller_error_category", "failure_observation"}
V2_SUMMARY_KEYS = SUMMARY_KEYS.copy()
SUMMARY_KEYS |= {"launch_stderr_observation"}
LAUNCH_OBSERVATIONS = {
    "NOT_ASSESSED",
    "NOT_RECOGNIZED",
    "STDERR_EXACT_UNSHARE_OPERATION_NOT_PERMITTED",
    "STDERR_EXACT_UNSHARE_PERMISSION_DENIED",
}
STAGES = {
    "INITIALIZE",
    "SANITIZE",
    "PREFLIGHT",
    "LIMITS",
    "INSTALLATION",
    "UTILITY_BINDING",
    "WORKER_BINDING",
    "OWNER_LOAD",
    "WORKER_EXECUTION",
    "WORKER_OUTPUT",
    "WORKER_RECEIPT",
    "FINAL_DELIVERY",
    "FINALIZE",
}
CATEGORIES = {
    "NONE",
    "PERMISSION_DENIED",
    "NOT_FOUND",
    "NOT_DIRECTORY",
    "TIMEOUT",
    "INTERRUPTED",
    "INVALID_DATA",
    "OS_ERROR",
    "OTHER_ERROR",
}


def require(value):
    if not value:
        raise ValueError("invalid public envelope")


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def validate(value, name):
    require(type(value) is dict)
    require(value.get("qualification") == "NOT_ASSESSED")
    require(value.get("raw_evidence") == "NOT_PUBLISHED")
    if name == "capability.json":
        require(
            set(value)
            == {"schema", "status", "facts", "live_admission", "qualification", "raw_evidence"}
        )
        require(value["schema"] == "forge-cli-capture-capability-v1")
        require(value["live_admission"] == "NOT_RUN")
        require(type(value["facts"]) is dict and set(value["facts"]) == FACTS)
        require(all(type(v) is bool for v in value["facts"].values()))
        expected = "PRESENT_NOT_QUALIFIED" if all(value["facts"].values()) else "UNSUPPORTED"
        require(value["status"] == expected)
        return dict(
            schema="forge-cli-capture-capability-v1",
            status=expected,
            facts={key: value["facts"][key] for key in sorted(FACTS)},
            live_admission="NOT_RUN",
            qualification="NOT_ASSESSED",
            raw_evidence="NOT_PUBLISHED",
        )
    require(name in ("baseline.json", "diagnostic.json"))
    if value.get("schema") == "forge-public-cli-capture-summary-v1":
        require(set(value) == LEGACY_SUMMARY_KEYS)
        value = value | {
            "schema": "forge-public-cli-capture-summary-v2",
            "controller_stage": "UNKNOWN",
            "controller_error_category": "UNKNOWN",
            "failure_observation": "NOT_OBSERVED",
        }
    if value.get("schema") == "forge-public-cli-capture-summary-v2":
        require(set(value) == V2_SUMMARY_KEYS)
        value = value | {
            "schema": "forge-public-cli-capture-summary-v3",
            "launch_stderr_observation": "NOT_ASSESSED",
        }
    require(set(value) == SUMMARY_KEYS)
    require(value["schema"] == "forge-public-cli-capture-summary-v3")
    launch = value["launch_stderr_observation"]
    require(type(launch) is str and launch in LAUNCH_OBSERVATIONS)
    if launch != "NOT_ASSESSED":
        require(value["receipt_status"] == "DELIVERED")
        require(type(value["worker_returncode"]) is int and value["worker_returncode"] == 1)
        require(value["failure_observation"] == "WORKER_NONZERO_NO_RECEIPT")
        require(value["cli_returncode"] is None and value["cli_returncode_before_cleanup"] is None)
        require(value["cleanup_induced_termination"] is None)
        require(value["collector_delivery"] == "NOT_OBSERVED")
    stage, category = value["controller_stage"], value["controller_error_category"]
    require(type(stage) is str and stage in STAGES | {"UNKNOWN"})
    require(type(category) is str and category in CATEGORIES | {"UNKNOWN"})
    require((stage == "UNKNOWN") == (category == "UNKNOWN"))
    if stage != "UNKNOWN":
        require((stage == "FINALIZE") == (category == "NONE"))
    require(value["failure_observation"] in {"NOT_OBSERVED", "WORKER_NONZERO_NO_RECEIPT"})
    if value["failure_observation"] == "WORKER_NONZERO_NO_RECEIPT":
        require(value["receipt_status"] == "DELIVERED")
        require(integer(value["worker_returncode"], -127, 255) and value["worker_returncode"] != 0)
        require(value["cli_returncode"] is None and value["cli_returncode_before_cleanup"] is None)
        require(value["cleanup_induced_termination"] is None)
        require(value["collector_delivery"] == "NOT_OBSERVED")
    require(value["mode"] == name.removesuffix(".json"))
    require(
        value["receipt_status"]
        in {"DELIVERED", "CONTRADICTORY", "INVALID", "UNAVAILABLE", "INVALID_ARGUMENTS"}
    )
    require(integer(value["harness_shell_exit"], 0, 255))
    for key in ("worker_returncode", "cli_returncode", "cli_returncode_before_cleanup"):
        require(value[key] is None or integer(value[key], -127, 255))
    require(
        value["cleanup_induced_termination"] is None
        or type(value["cleanup_induced_termination"]) is bool
    )
    require(value["collector_delivery"] in {"NOT_OBSERVED", "HEALTHY", "FAILED", "NOT_APPLICABLE"})
    require(value["cleanup"] in {"NOT_OBSERVED", "COMPLETE", "INCOMPLETE"})
    if value["receipt_status"] != "DELIVERED":
        require(stage == category == "UNKNOWN")
        require(value["failure_observation"] == "NOT_OBSERVED")
        require(
            all(
                value[k] is None
                for k in (
                    "worker_returncode",
                    "cli_returncode",
                    "cli_returncode_before_cleanup",
                    "cleanup_induced_termination",
                )
            )
        )
        require(value["collector_delivery"] == value["cleanup"] == "NOT_OBSERVED")
    require(not (value["mode"] == "baseline" and value["collector_delivery"] == "HEALTHY"))
    require(not (value["mode"] == "diagnostic" and value["collector_delivery"] == "NOT_APPLICABLE"))
    # Every copied scalar has a closed domain; no source bytes are published.
    return {key: value[key] for key in sorted(SUMMARY_KEYS)}


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def reject(_):
    raise ValueError("invalid public envelope")


def private_directory(path):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(path))
    for part in (*reversed(path.parents), path):
        require(stat.S_ISDIR(part.lstat().st_mode))
    row = path.lstat()
    require(row.st_uid == os.getuid() and stat.S_IMODE(row.st_mode) == 0o700)
    return row


def fingerprint(row):
    return (
        row.st_dev,
        row.st_ino,
        row.st_size,
        row.st_mtime_ns,
        row.st_ctime_ns,
        row.st_mode,
        row.st_uid,
        row.st_nlink,
    )


def publish(source, destination):
    source, destination = Path(source), Path(destination)
    before = private_directory(source)
    private_directory(destination.parent)
    require(not os.path.lexists(destination))
    directory = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    values = {}
    try:
        require(fingerprint(before) == fingerprint(os.fstat(directory)))
        for name in NAMES:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
            except FileNotFoundError:
                require(name != "capability.json")
                continue
            try:
                row = os.fstat(fd)
                require(stat.S_ISREG(row.st_mode) and row.st_uid == os.getuid())
                require(
                    stat.S_IMODE(row.st_mode) == 0o600 and row.st_nlink == 1 and 0 < row.st_size <= 4096
                )
                raw = os.read(fd, 4097)
                require(len(raw) == row.st_size and raw.endswith(b"\n"))
                require(fingerprint(row) == fingerprint(os.fstat(fd)))
                require(
                    fingerprint(row)
                    == fingerprint(os.stat(name, dir_fd=directory, follow_symlinks=False))
                )
                value = json.loads(
                    raw, object_pairs_hook=unique, parse_float=reject, parse_constant=reject
                )
                values[name] = validate(value, name)
            finally:
                os.close(fd)
        require(fingerprint(before) == fingerprint(source.lstat()))
        require(fingerprint(before) == fingerprint(os.fstat(directory)))
    finally:
        os.close(directory)
    destination.mkdir(mode=0o700)
    for name, value in values.items():
        fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        require(len(args) == 2)
        publish(*args)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print("public diagnostic summary unavailable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
