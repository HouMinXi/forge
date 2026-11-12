"""Independent closed-schema publication for stock fixed-child observations."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat

KEYS = {
    "schema",
    "environment",
    "required_runtime",
    "status",
    "stage",
    "error",
    "stock_safety",
    "loopback",
    "strace",
    "cleanup",
    "control_returncode",
    "qualification",
    "causal_attribution",
    "raw",
}


def require(value):
    if not value:
        raise ValueError("invalid stock control envelope")


def validate(value):
    require(type(value) is dict and set(value) == KEYS)
    constants = dict(
        schema="forge-stock-capture-control-v1",
        environment="INDEPENDENT_UBUNTU_26_04",
        required_runtime="SYSTEM_CPYTHON_314",
        qualification="NOT_ASSESSED",
        causal_attribution="NOT_ASSESSED",
        raw="NOT_PUBLISHED",
    )
    require(all(value[k] == v for k, v in constants.items()))
    domains = dict(
        status={"OBSERVED", "UNSUPPORTED"},
        stage={
            "NO_DELIVERY",
            "PREREQUISITE",
            "LAUNCH",
            "CLEANUP",
            "HOST_STATE",
            "CHILD",
            "LOOPBACK",
            "STRACE",
            "COMPLETE",
        },
        error={
            "NOT_OBSERVED",
            "NONE",
            "TIMEOUT",
            "UNAVAILABLE",
            "INTERRUPTED",
            "INVALID",
            "EXECUTION_ERROR",
        },
        stock_safety={"VERIFIED", "NOT_VERIFIED"},
        loopback={"UP", "DOWN", "NOT_OBSERVED"},
        strace={"FIXED_CHILD_COMPLETED", "NOT_OBSERVED"},
        cleanup={"COMPLETE", "INCOMPLETE", "UNKNOWN", "NOT_ASSESSED"},
    )
    for key, domain in domains.items():
        require(type(value[key]) is str and value[key] in domain)
    code = value["control_returncode"]
    require(code is None or type(code) is int and -127 <= code <= 255)
    require((value["stage"] == "NO_DELIVERY") == (value["error"] == "NOT_OBSERVED"))
    if value["status"] == "OBSERVED":
        require(
            value["stage"] == "COMPLETE"
            and value["error"] == "NONE"
            and value["stock_safety"] == "VERIFIED"
            and value["loopback"] == "UP"
            and value["strace"] == "FIXED_CHILD_COMPLETED"
            and value["cleanup"] == "COMPLETE"
            and code == 0
        )
    else:
        require(
            value["stage"] != "COMPLETE"
            and value["error"] != "NONE"
            and value["strace"] == "NOT_OBSERVED"
        )
    if value["stage"] in {"LOOPBACK", "STRACE", "COMPLETE"}:
        require(value["stock_safety"] == "VERIFIED")
    if value["stage"] == "CHILD":
        require(code == 0)
    if value["stage"] == "STRACE":
        require(value["loopback"] == "UP")
    if code is not None:
        require(value["cleanup"] != "NOT_ASSESSED")
    if value["stock_safety"] == "VERIFIED":
        require(
            value["stage"] in {"LOOPBACK", "STRACE", "COMPLETE"}
            and code == 0
            and value["cleanup"] == "COMPLETE"
        )
    else:
        require(value["loopback"] == "NOT_OBSERVED" and value["strace"] == "NOT_OBSERVED")
    if value["loopback"] != "NOT_OBSERVED":
        require(value["stock_safety"] == "VERIFIED")
    if value["loopback"] == "DOWN":
        require(value["stage"] == "LOOPBACK" and value["status"] == "UNSUPPORTED")
    if value["stage"] in {"NO_DELIVERY", "PREREQUISITE"}:
        require(code is None and value["cleanup"] == "NOT_ASSESSED")
    if value["stage"] in {"HOST_STATE", "CHILD", "LOOPBACK", "STRACE", "COMPLETE"}:
        require(value["cleanup"] == "COMPLETE" and type(code) is int)
    return {key: value[key] for key in sorted(KEYS)}


def publish(source, destination):
    path = Path(__file__).with_name("cli_capture_publication.py")
    spec = importlib.util.spec_from_file_location("_stock_private_io", path)
    io = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(io)
    source, destination = Path(source), Path(destination)
    before = io.private_directory(source.parent)
    io.private_directory(destination.parent)
    directory = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        require(io.fingerprint(before) == io.fingerprint(os.fstat(directory)))
        fd = os.open(source.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        try:
            row = os.fstat(fd)
            require(
                stat.S_ISREG(row.st_mode)
                and row.st_uid == os.getuid()
                and stat.S_IMODE(row.st_mode) == 0o600
                and row.st_nlink == 1
                and 0 < row.st_size <= 4096
            )
            raw = os.read(fd, 4097)
            require(len(raw) == row.st_size and raw.endswith(b"\n"))
            require(
                io.fingerprint(row)
                == io.fingerprint(os.fstat(fd))
                == io.fingerprint(os.stat(source.name, dir_fd=directory, follow_symlinks=False))
            )
        finally:
            os.close(fd)
        value = validate(json.loads(raw, object_pairs_hook=io.unique, parse_constant=io.reject))
        require(
            io.fingerprint(before)
            == io.fingerprint(os.fstat(directory))
            == io.fingerprint(source.parent.lstat())
        )
    finally:
        os.close(directory)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    publish(args.source, args.destination)


if __name__ == "__main__":
    main()
