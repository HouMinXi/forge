"""Bounded source coordinates for failure diagnostics, never runtime values.

The recorder's digest is only a candidate. Publication checks it against an
ordinary blob in the exact requested commit; a coordinate is not proof that the
exception or the test result is authentic.
"""

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import time

MAX_SOURCE = 2 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]


def line_count(data):
    """Count physical Python newlines, not form-feed or Unicode separators."""
    if not data:
        return 0
    lines = re.split(rb"\r\n|\r|\n", data)
    return len(lines) - (not lines[-1])


def valid_path(value):
    return (
        type(value) is str
        and len(value) <= 512
        and re.fullmatch(r"[A-Za-z0-9_./-]+\.py", value) is not None
        and not PurePosixPath(value).is_absolute()
        and all(part not in (".", "..") for part in value.split("/"))
        and "//" not in value
    )


def candidate(report, root):
    """Best-effort metadata: ordinary diagnostic errors must not break pytest."""
    if not report.failed:
        return None
    try:
        crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
        line = getattr(crash, "lineno", None)
        raw = getattr(crash, "path", None)
        if type(raw) is not str or type(line) is not int or line < 1:
            return None
        root = Path(root).resolve()
        path = Path(raw)
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        relative = path.relative_to(root).as_posix()
        if not valid_path(relative):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE:
                return None
            chunks = []
            remaining = MAX_SOURCE + 1
            while remaining:
                chunk = os.read(fd, min(remaining, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
        finally:
            primary = sys.exception()
            try:
                os.close(fd)
            except OSError as close_error:
                if primary is None:
                    raise
                # Preserve interruption identity, and retain cleanup failure
                # evidence without serializing it in the public artifact.
                BaseException.add_note(
                    primary, f"diagnostic source FD close failed: errno={close_error.errno}"
                )
        if len(data) > MAX_SOURCE or line > line_count(data):
            return None
        return {"path": relative, "line": line, "sha256": hashlib.sha256(data).hexdigest()}
    except Exception:  # noqa: BLE001 - Optional metadata must not replace the test's failure.
        return None


class Verifier:
    """Bound all Git queries per artifact; unavailable coordinates stay absent."""

    def __init__(self, sha, root=ROOT):
        self.sha = sha
        self.root = root
        self.deadline = time.monotonic() + 10
        self.cache = {}

    def _git(self, *args):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(self.root), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=min(2, remaining),
            check=True,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        )
        return result.stdout

    def _blob(self, path):
        if path in self.cache:
            return self.cache[path]
        if len(self.cache) >= 16:
            return None
        self.cache[path] = None
        entry = self._git("ls-tree", "-z", self.sha, "--", path)
        match = re.fullmatch(
            rb"100(?:644|755) blob ([0-9a-f]{40,64})\t" + re.escape(path.encode()) + b"\x00", entry
        )
        if match is None:
            return None
        oid = match[1].decode("ascii")
        size = int(self._git("cat-file", "-s", oid))
        if not 0 <= size <= MAX_SOURCE:
            return None
        data = self._git("cat-file", "blob", oid)
        if len(data) != size:
            return None
        result = (hashlib.sha256(data).hexdigest(), line_count(data))
        self.cache[path] = result
        return result

    def verify(self, value):
        try:
            if type(value) is not dict or set(value) != {"path", "line", "sha256"}:
                return None
            path, line, digest = value["path"], value["line"], value["sha256"]
            if not valid_path(path) or type(line) is not int or line < 1:
                return None
            if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                return None
            if re.fullmatch(r"[0-9a-f]{40}", self.sha) is None:
                return None
            blob = self._blob(path)
            if blob is None or blob[0] != digest or line > blob[1]:
                return None
            return {"path": path, "line": line}
        except Exception:  # noqa: BLE001 - A failed source lookup cannot erase the test identity.
            return None
