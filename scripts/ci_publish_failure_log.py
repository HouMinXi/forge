"""Failure-only allowlisted CI transcript replay, not qualification evidence.

The workflow supplies no secrets and checkout does not persist credentials.
Credential filtering/redaction is defense in depth, not a hostile-code sandbox.
"""

import argparse
import errno
import os
from pathlib import Path
import re
import secrets
import stat

PHASES = ("ownership", "cgroup", "local", *(f"shard-{n}" for n in range(4)), "setup-bwrap")
LOG_NAMES = {phase: phase + ".log" for phase in PHASES}
LOG_NAMES["setup-bwrap"] = "step-04.log"
MAX_BYTES = 32 * 1024 * 1024
CREDENTIAL_NAME = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|CREDENTIALS?|API_KEY|PRIVATE_KEY)(?:_|$)", re.I
)
TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
AUTHORIZATION = re.compile(
    r"(?i)(\b(?:proxy-)?authorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic)\s+)[^\s\"',}]+"
)
URL_AUTH = re.compile(r"(https?://)[^\s/@]+@", re.I)
URL_QUERY = re.compile(r"(https?://[^\s<>\"'?#]+)[?#][^\s<>\"']*", re.I)
# Apt/proxy diagnostics may include authentication schemes other than Basic/Bearer.
AUTH_HEADER = re.compile(r"(?im)(^[ \t]*(?:proxy-)?authorization[\"']?\s*[:=]\s*)[^\r\n]+")
READ_FAILURES = frozenset(
    ("NOT_FOUND", "UNREADABLE", "UNSAFE_PATH", "UNSAFE_FILE", "TOO_LARGE", "CHANGED", "READ_ERROR")
)


class _ReadFailure(ValueError):
    """A fixed reason only, never a path, OS message or transcript content."""


def failure_reason(error):
    if type(error) is _ReadFailure:
        arguments = BaseException.args.__get__(error)
        if len(arguments) == 1 and type(arguments[0]) is str and arguments[0] in READ_FAILURES:
            return arguments[0]
    return "PUBLICATION_ERROR"


def test_environment(environment):
    """Do not pass runner or API credentials into pytest/fixture subprocesses."""
    return {key: value for key, value in environment.items() if not CREDENTIAL_NAME.search(key)}


def redact(text, environment):
    for key, value in sorted(environment.items(), key=lambda item: len(item[1]), reverse=True):
        if value and CREDENTIAL_NAME.search(key):
            text = text.replace(value, "[REDACTED]")
    text = TOKEN.sub("[REDACTED]", text)
    text = AUTHORIZATION.sub(r"\1[REDACTED]", text)
    text = URL_AUTH.sub(r"\1[REDACTED]@", text)
    text = URL_QUERY.sub(r"\1?[REDACTED]", text)
    text = AUTH_HEADER.sub(r"\1[REDACTED]", text)
    # Render terminal controls visibly; retain ordinary multiline diagnostics.
    return re.sub(
        r"[\x00-\x08\x0b-\x1f\x7f-\x9f]",
        lambda match: f"[control-{ord(match.group()):02x}]",
        text,
    )


def read_log(directory, phase):
    if phase not in PHASES:
        raise ValueError("invalid phase")
    # Reuse component-by-component no-follow directory traversal.
    if __package__:
        from .ci_private_transport import _directory
    else:
        from ci_private_transport import _directory
    with _directory(Path(directory)) as parent:
        fd = os.open(LOG_NAMES[phase], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise _ReadFailure("UNSAFE_FILE")
        if before.st_size > MAX_BYTES:
            raise _ReadFailure("TOO_LARGE")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(fd)
        if len(raw) > MAX_BYTES:
            raise _ReadFailure("TOO_LARGE")
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise _ReadFailure("CHANGED")
    finally:
        os.close(fd)
    return raw.decode("utf-8", errors="replace")


def publish(directory, phase):
    try:
        raw = read_log(directory, phase)
    except OSError as error:
        # Only the native errno is inspected; never format the error or filename.
        number = OSError.errno.__get__(error)
        if type(number) is not int:
            number = None
        reason = {
            errno.ENOENT: "NOT_FOUND",
            errno.EACCES: "UNREADABLE",
            errno.EPERM: "UNREADABLE",
            errno.ELOOP: "UNSAFE_PATH",
            errno.ENOTDIR: "UNSAFE_PATH",
        }.get(number, "READ_ERROR")
        raise _ReadFailure(reason) from None
    text = redact(raw, os.environ)
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
    except Exception as error:  # noqa: BLE001 - Only fixed codes cross the failure boundary.
        print(
            f"Failure transcript unavailable (reason={failure_reason(error)}); "
            "original test result is unchanged."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
