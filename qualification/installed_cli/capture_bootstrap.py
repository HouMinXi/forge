# SPDX-License-Identifier: Apache-2.0
"""Minimal post-unshare observation, not namespace admission or qualification.

The controller passes these exact reviewed bytes via -c. Fixed private markers
are best effort. The verified worker executes with direct-script globals/argv;
its SystemExit, exceptions and signals retain their ordinary process outcome.
"""

import os
import sys


LIMIT = 1024 * 1024


def directory(path, private=False):
    if not path.startswith("/") or os.path.normpath(path) != path or path.startswith("//"):
        raise ValueError("invalid directory")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.split("/")[1:]:
            if not part:
                continue
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        row = os.fstat(fd)
        if private and (row.st_uid != os.geteuid() or row.st_mode & 0o7777 != 0o700):
            raise ValueError("invalid private directory")
        return fd
    except BaseException:  # noqa: BLE001 - close before preserving original failure
        os.close(fd)
        raise


def mark(fd, name, stage, category="NONE"):
    """Only callers' fixed filenames/enums reach this best-effort channel."""
    if fd is None:
        return
    target = None
    try:
        target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        raw = ('{"schema":1,"stage":"' + stage + '","error_category":"' + category + '"}\n').encode()
        while raw:
            written = os.write(target, raw)
            if written <= 0:
                raise OSError("short metadata write")
            raw = raw[written:]
    except Exception:  # noqa: BLE001,S110 - metadata must never replace worker status
        pass
    finally:
        if target is not None:
            try:
                os.close(target)
            except OSError:
                pass


def category(error):
    if isinstance(error, KeyboardInterrupt):
        return "INTERRUPTED"
    if isinstance(error, TimeoutError):
        return "TIMEOUT"
    if isinstance(error, ImportError):
        return "IMPORT_ERROR"
    if isinstance(error, SyntaxError):
        return "SYNTAX_ERROR"
    if isinstance(error, OSError):
        number = error.errno
        return (
            {1: "PERMISSION_DENIED", 13: "PERMISSION_DENIED", 2: "NOT_FOUND", 20: "NOT_DIRECTORY"}.get(
                number, "OS_ERROR"
            )
            if type(number) is int
            else "OS_ERROR"
        )
    if isinstance(error, (ValueError, TypeError, KeyError)):
        return "INVALID_DATA"
    return "OTHER_ERROR"


def worker_bytes(path):
    parent = directory(os.path.dirname(path))
    fd = None
    try:
        fd = os.open(os.path.basename(path), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        before = os.fstat(fd)
        if before.st_mode & 0o170000 != 0o100000 or not 0 < before.st_size <= LIMIT:
            raise ValueError("invalid worker source")
        data = bytearray()
        while len(data) <= LIMIT:
            block = os.read(fd, min(65536, LIMIT + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(fd)
        named = os.stat(os.path.basename(path), dir_fd=parent, follow_symlinks=False)

        def identity(row):
            return row.st_dev, row.st_ino, row.st_mode, row.st_size, row.st_mtime_ns, row.st_ctime_ns

        if (
            identity(before) != identity(after)
            or identity(before) != identity(named)
            or len(data) != before.st_size
        ):
            raise ValueError("worker source changed")
        return bytes(data)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def main():
    # No heavy worker/HTTP/server imports before this first fixed observation.
    args = sys.argv[1:]
    evidence = None
    if len(args) >= 3:
        try:
            evidence = directory(args[2], private=True)
        except (OSError, ValueError):
            pass  # Missing metadata cannot change the worker's eventual status.
    worker_module = None
    stage = "BOOTSTRAP_ENTERED"
    mark(evidence, "entered.json", stage)
    try:
        if len(args) != 9 or args[3::2] != ["--binding", "--mode", "--output"]:
            raise ValueError("invalid bootstrap arguments")
        worker, expected = args[:2]
        if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise ValueError("invalid worker identity")
        import hashlib

        raw = worker_bytes(worker)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("worker identity changed")
        stage = "WORKER_SOURCE_VERIFIED"
        mark(evidence, "verified.json", stage)
        code = compile(raw, worker, "exec", dont_inherit=True)
        sys.argv = [worker, *args[3:]]
        worker_module = type(sys)("__main__")
        worker_module.__dict__.update(
            {
                "__name__": "__main__",
                "__file__": worker,
                "__package__": None,
                "__spec__": None,
                "__cached__": None,
            }
        )
        stage = "WORKER_EXECUTION_ENTERED"
        mark(evidence, "executing.json", stage)
        sys.modules["__main__"] = worker_module
        exec(code, worker_module.__dict__)  # noqa: S102 - exact SHA-bound worker source, no path reopening
    except SystemExit:
        raise
    except BaseException as error:  # noqa: BLE001 - fixed observation then preserve original exception
        mark(evidence, "error.json", stage, category(error))
        raise
    finally:
        if evidence is not None:
            try:
                os.close(evidence)
            except OSError:
                pass


if __name__ == "__main__":
    main()
