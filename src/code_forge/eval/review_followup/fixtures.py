"""Parse and verify a frozen, trusted local fixture tree."""

import hashlib
import json
import math
import re
import stat
from pathlib import PurePosixPath

MAX_BYTES = 1024 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,95}")
_REV = re.compile(r"(?:git:[0-9a-f]{40}|synthetic:[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95})")


class InputError(ValueError):
    """A fixture cannot establish a valid replay input."""

    def __init__(self, code, field):
        self.code = code
        self.field = field
        super().__init__(f"{code}: {field}")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw, field):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InputError("duplicate-key", field)
            result[key] = value
        return result

    def finite(_):
        raise InputError("non-finite-number", field)

    def parse_float(raw_number):
        value = float(raw_number)
        if not math.isfinite(value):
            finite(raw_number)
        return value

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=finite,
            parse_float=parse_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError("invalid-json", field) from exc


def require(ok, field):
    if not ok:
        raise InputError("invalid-schema", field)


def fields(value, names, field):
    require(type(value) is dict and value.keys() == set(names.split()), field)


def identifier(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def revision(value):
    return type(value) is str and _REV.fullmatch(value) is not None


def sha(value):
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def enum(value, *choices):
    return type(value) is str and value in choices


def read_file(root, name, field):
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in ("", ".", "..") for part in name.split("/"))
        or "\\" in name
        or "\x00" in name
    ):
        raise InputError("unsafe-path", field)
    current = root
    try:
        for index, part in enumerate(path.parts):
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise InputError("symlink", field)
            if index < len(path.parts) - 1 and not stat.S_ISDIR(info.st_mode):
                raise InputError("not-directory", field)
        if not stat.S_ISREG(info.st_mode):
            raise InputError("not-regular", field)
        if info.st_size > MAX_BYTES:
            raise InputError("file-too-large", field)
        with current.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise InputError("file-too-large", field)
        return raw
    except FileNotFoundError as exc:
        raise InputError("missing-file", field) from exc
    except OSError as exc:
        raise InputError("file-read-failed", field) from exc


def pinned_files(root, entries):
    """Check every file read before reporting any digest mismatch."""
    data = []
    total = 0
    for field, ref in entries:
        raw = read_file(root, ref["path"], field)
        total += len(raw)
        if total > 32 * MAX_BYTES:
            raise InputError("batch-too-large", field)
        data.append((field, raw, ref["sha256"]))
    for field, raw, expected in data:
        if digest(raw) != expected:
            raise InputError("digest-mismatch", field)
    return {field: raw for field, raw, _ in data}


def ref_shape(ref, field):
    fields(ref, "path sha256", field)
    require(type(ref["path"]) is str and sha(ref["sha256"]), field)
