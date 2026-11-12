"""Validate the fixed independent stock context, then ordinary capture summaries."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat

CONTEXT = dict(
    schema="forge-stock-cli-context-v1",
    backend="STOCK_BWRAP",
    requested_environment="UBUNTU_26_04",
    required_runtime="SYSTEM_CPYTHON_314",
    qualification="NOT_ASSESSED",
    historical_cause="NOT_ASSESSED",
    ack_consistency="NOT_ASSESSED",
    power_loss_durability="NOT_ASSESSED",
    raw="NOT_PUBLISHED",
)


NO_SITE_CONTEXT = CONTEXT | {"schema": "forge-stock-cli-context-v2", "startup_mode": "ISOLATED_NO_SITE"}

FACTS = {"linux", "pidfd_bindings", "git", "bwrap", "strace"}


def capability(value):
    if type(value) is not dict or set(value) != {
        "schema",
        "status",
        "facts",
        "live_admission",
        "qualification",
        "raw_evidence",
    }:
        raise ValueError("invalid stock capability")
    facts = value["facts"]
    if (
        type(facts) is not dict
        or set(facts) != FACTS
        or any(type(v) is not bool for v in facts.values())
    ):
        raise ValueError("invalid stock capability facts")
    expected = dict(
        schema="forge-stock-cli-capability-v1",
        status="PRESENT_NOT_QUALIFIED" if all(facts.values()) else "UNSUPPORTED",
        facts=facts,
        live_admission="NOT_RUN",
        qualification="NOT_ASSESSED",
        raw_evidence="NOT_PUBLISHED",
    )
    if value != expected:
        raise ValueError("invalid stock capability envelope")
    return expected


def publish(source, destination):
    path = Path(__file__).with_name("cli_capture_publication.py")
    spec = importlib.util.spec_from_file_location("_stock_capture_publication", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = module.private_directory(source)
    directory = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        module.require(module.fingerprint(before) == module.fingerprint(os.fstat(directory)))
        fd = os.open("context.json", os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        try:
            row = os.fstat(fd)
            module.require(
                stat.S_ISREG(row.st_mode)
                and row.st_uid == os.getuid()
                and stat.S_IMODE(row.st_mode) == 0o600
                and row.st_nlink == 1
                and 0 < row.st_size <= 4096
            )
            raw = os.read(fd, 4097)
            module.require(len(raw) == row.st_size and raw.endswith(b"\n"))
            module.require(
                module.fingerprint(row)
                == module.fingerprint(os.fstat(fd))
                == module.fingerprint(os.stat("context.json", dir_fd=directory, follow_symlinks=False))
            )
        finally:
            os.close(fd)
        value = json.loads(raw, object_pairs_hook=module.unique, parse_constant=module.reject)
        module.require(type(value) is dict and (value == CONTEXT or value == NO_SITE_CONTEXT))
        module.require(
            module.fingerprint(before)
            == module.fingerprint(os.fstat(directory))
            == module.fingerprint(source.lstat())
        )
    finally:
        os.close(directory)
    original = module.validate
    module.validate = lambda value, name: (
        capability(value) if name == "capability.json" else original(value, name)
    )
    module.publish(source, destination)
    fd = os.open(
        destination / "context.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
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
