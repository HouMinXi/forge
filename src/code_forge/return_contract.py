# SPDX-License-Identifier: Apache-2.0
"""Explicit, source-bound Python return contracts, independent of model prose.

This opt-in interface adjudicates only its caller-supplied input/output question.
Its results cannot be replayed as normal-review proof or clean-credit authority.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

from ._return_contract_eval import evaluate

RULE = "python-return-contract-v1"
MAX_SOURCE = 32768


@dataclass(frozen=True)
class ContractResult:
    status: str
    reason: str
    repository: str
    revision: str
    path: str
    function: str
    arguments_json: str
    expected_json: str
    actual_json: str | None = None
    source_sha256: str | None = None
    blob: str | None = None
    rule: str = RULE
    parser: str = (
        f"{sys.implementation.name}-{sys.version_info.major}."
        f"{sys.version_info.minor}.{sys.version_info.micro}"
    )


def _primitive(value):
    return (
        value is None
        or type(value) is bool
        or (type(value) is int and value.bit_length() <= 256)
        or (type(value) is str and len(value) <= 1024)
    )


def _git(root, *args):
    return subprocess.run(
        [
            "git",
            "--no-pager",
            "--no-replace-objects",
            "--literal-pathspecs",
            "-c",
            "protocol.allow=never",
            "-C",
            str(root),
            *args,
        ],
        env={
            **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=True,
    ).stdout


def check_return_contract(repository, revision, path, function, arguments, expected):
    """Read an immutable committed blob and decide one complete return obligation.

    The caller, not a model response, supplies the contract. Only the selected
    commit is covered, never dirty worktree bytes. Every invocation reacquires
    source; serialized results and finding fingerprints confer no authority.
    """
    if type(arguments) is dict:
        arguments = dict(arguments)
    if not (type(repository) is str or isinstance(repository, Path)):
        raise ValueError("repository must be a filesystem path")
    if not (
        type(revision) is str
        and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision)
        and type(path) is str
        and 0 < len(path) <= 1024
        and not path.startswith("/")
        and all(part not in ("", ".", "..") for part in path.split("/"))
        and "\\" not in path
        and not any(ord(c) < 32 for c in path)
        and str(PurePosixPath(path)) == path
        and type(function) is str
        and function.isidentifier()
        and len(function) <= 128
        and type(arguments) is dict
        and len(arguments) <= 32
        and all(
            type(k) is str and k.isidentifier() and len(k) <= 128 and _primitive(v)
            for k, v in arguments.items()
        )
        and _primitive(expected)
    ):
        raise ValueError("invalid return contract")
    root = Path(repository).resolve()
    base = dict(
        repository=str(root),
        revision=revision,
        path=path,
        function=function,
        arguments_json=json.dumps(arguments, sort_keys=True),
        expected_json=json.dumps(expected),
    )
    try:
        reported_root = os.fsdecode(_git(root, "rev-parse", "--show-toplevel").rstrip(b"\n"))
        if Path(reported_root).resolve() != root:
            return ContractResult("UNKNOWN", "repository-root-mismatch", **base)
        if _git(root, "cat-file", "-t", revision) != b"commit\n":
            return ContractResult("UNKNOWN", "revision-not-commit", **base)
        entries = _git(root, "ls-tree", "-z", "--full-tree", revision, "--", path).split(b"\0")
        entries = [entry for entry in entries if entry]
        if len(entries) != 1:
            return ContractResult("UNKNOWN", "source-not-unique", **base)
        header, actual_path = entries[0].split(b"\t", 1)
        mode, kind, blob_bytes = header.split()
        if mode not in (b"100644", b"100755") or kind != b"blob" or actual_path != path.encode("utf-8"):
            return ContractResult("UNKNOWN", "source-not-regular-file", **base)
        blob = blob_bytes.decode("ascii")
        size = int(_git(root, "cat-file", "-s", blob))
        if size > MAX_SOURCE:
            return ContractResult("UNKNOWN", "source-budget", **base)
        source = _git(root, "cat-file", "blob", blob)
        if len(source) != size:
            return ContractResult("UNKNOWN", "source-size-mismatch", **base)
    except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
        return ContractResult("CHECK_FAILED", "snapshot-unavailable", **base)
    supported, actual, reason = evaluate(source, function, arguments)
    evidence = dict(source_sha256=hashlib.sha256(source).hexdigest(), blob=blob)
    if not supported:
        return ContractResult("UNKNOWN", reason, **base, **evidence)
    matches = type(actual) is type(expected) and actual == expected
    return ContractResult(
        "DISMISSED" if matches else "CONFIRMED",
        "return-matches-contract" if matches else "return-violates-contract",
        actual_json=json.dumps(actual),
        **base,
        **evidence,
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("repository", "revision", "path", "function", "arguments", "expected"):
        parser.add_argument("--" + flag, required=True)
    args = parser.parse_args(argv)
    try:
        if len(args.arguments) > 65536 or len(args.expected) > 4096:
            raise ValueError("contract JSON budget exceeded")
        arguments = json.loads(args.arguments, object_pairs_hook=_unique_object)
        expected = json.loads(args.expected)
        result = check_return_contract(
            args.repository, args.revision, args.path, args.function, arguments, expected
        )
    except (ValueError, TypeError, RecursionError) as exc:
        parser.error(str(exc))
    print(json.dumps(asdict(result), sort_keys=True))
    return {"DISMISSED": 0, "CONFIRMED": 1, "UNKNOWN": 2, "CHECK_FAILED": 3}[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
