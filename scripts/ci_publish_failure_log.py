"""Failure-only pytest transcript replay, not qualification evidence.

The workflow supplies no secrets and checkout does not persist credentials.
Credential filtering/redaction is defense in depth, not a hostile-code sandbox.
"""

import argparse
import os
from pathlib import Path
import re
import secrets
import stat

PHASES = ("ownership", "cgroup", "local", *(f"shard-{n}" for n in range(4)))
MAX_BYTES = 32 * 1024 * 1024
CREDENTIAL_NAME = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|CREDENTIALS?|API_KEY|PRIVATE_KEY)(?:_|$)", re.I
)
TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
AUTHORIZATION = re.compile(
    r"(?i)(\b(?:proxy-)?authorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic)\s+)[^\s\"',}]+"
)
URL_AUTH = re.compile(r"(https?://)[^\s/@]+:[^\s/@]+@", re.I)


def test_environment(environment):
    """Do not pass runner or API credentials into pytest/fixture subprocesses."""
    return {key: value for key, value in environment.items() if not CREDENTIAL_NAME.search(key)}


def redact(text, environment):
    for key, value in sorted(environment.items(), key=lambda item: len(item[1]), reverse=True):
        if value and CREDENTIAL_NAME.search(key):
            text = text.replace(value, "[REDACTED]")
    text = TOKEN.sub("[REDACTED]", text)
    text = AUTHORIZATION.sub(r"\1[REDACTED]", text)
    return URL_AUTH.sub(r"\1[REDACTED]@", text)


def read_log(directory, phase):
    if phase not in PHASES:
        raise ValueError("invalid phase")
    # Reuse component-by-component no-follow directory traversal.
    if __package__:
        from .ci_private_transport import _directory
    else:
        from ci_private_transport import _directory
    with _directory(Path(directory)) as parent:
        fd = os.open(phase + ".log", os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_BYTES:
            raise ValueError("invalid transcript")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(fd)
        if len(raw) > MAX_BYTES or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("transcript changed")
    finally:
        os.close(fd)
    return raw.decode("utf-8", errors="replace")


def publish(directory, phase):
    text = redact(read_log(directory, phase), os.environ)
    token = secrets.token_hex(32)
    while token in text:
        token = secrets.token_hex(32)
    # An untrusted traceback must not be interpreted as a workflow command.
    print(f"::stop-commands::{token}", flush=True)
    try:
        print(f"Failure transcript: {phase} (diagnostic only)")
        print(text, end="" if text.endswith("\n") else "\n", flush=True)
    finally:
        print(f"::{token}::", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--phase", required=True, choices=PHASES)
    args = parser.parse_args()
    try:
        publish(args.directory, args.phase)
    except Exception:  # noqa: BLE001 - Never leak error paths or credential-bearing exception text.
        print("Failure transcript unavailable; original test result is unchanged.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
