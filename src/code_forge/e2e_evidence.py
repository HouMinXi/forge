# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Judge an execution record against the commit it claims to have tested.

This is the reader side of the R3 execution evidence slice. It answers one
question: did a test in this file run, at the commit under review, in the run
the caller asked about. It reports, and changes no verdict.

Standalone on purpose. The first version keeps its result out of the review
findings, because a finding carries a fingerprint that later rounds compare,
so putting this in that stream would change what rounds are matched against.

Three inputs come from the caller and one is read here:

- the record path, allocated by whoever ran the tests;
- the execution id, allocated by the same caller at run time;
- the commit under review;
- the repository, whose HEAD and working tree are read here. A parameter alone
  cannot stamp a commit onto an unrelated diff: HEAD must equal the named
  commit and the tree must be clean, or nothing is proven.

States, in the order they are decided:

``absent``         no record was configured, or the file is not there
``in_tree``        the record sits inside the reviewed tree, so the reviewed
                   change supplied it
``unusable``       unreadable, not JSON, wrong shape, a bad path, or too large
``unbound_review`` no 40-hex commit was named, or the named commit is not the
                   clean HEAD of this repository
``foreign``        the record names another schema
``incomplete``     the run did not finish, so the record proves nothing
``wrong_execution`` the record belongs to a different run
``dirty_run``      the tests ran on a tree that was not clean
``stale``          the tests ran at a different commit
``bound``          the record describes this run of this commit

A record inside the tree disqualifies itself before the tree is judged, because
the question "is this commit clean" cannot change that answer; reporting it
would bury that answer behind a dirty-tree message on the common case.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from .exit_codes import EXIT_CLI_ERROR, EXIT_FAIL, EXIT_PASS

SCHEMA = "forge-e2e-execution-v1"

# Execution ids are shortened to this many characters in messages.
_ID_WIDTH = 12

# A record is a list of paths. Anything past this is not a test report.
MAX_RECORD_BYTES = 2 * 1024 * 1024

_HEX = "0123456789abcdef"


@dataclass(frozen=True)
class Evidence:
    """What could be established, with the reason when nothing could."""

    state: str
    detail: str
    executed_files: tuple[str, ...] = ()
    review_commit: str | None = None
    record: str | None = None

    @property
    def bound(self) -> bool:
        return self.state == "bound"

    def as_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "state": self.state,
            "detail": self.detail,
            "bound": self.bound,
            "executed_files": list(self.executed_files),
            "review_commit": self.review_commit,
            "record": self.record,
        }


def _git(root: Path, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            # encoding puts the pipe in text mode on its own, and check
            # already defaults to False, so neither is repeated here.
            # A different encoding only changes a byte under a non-UTF-8
            # locale, which the suite does not build.
            encoding="utf-8",  # pragma: no mutate
            errors="replace",
        )
    except OSError as exc:
        # The reader only asks whether the code is zero.
        return 1, str(exc)  # pragma: no mutate
    # A failing git writes its reason to stderr and nothing to stdout, so the
    # first non-empty stream is what the caller can act on.
    return result.returncode, (result.stdout or result.stderr).strip()


def _read_repo_state(root: Path) -> tuple[str | None, bool | None, str | None]:
    """(HEAD commit, tracked files match it, problem). None means unreadable.

    Untracked files are not counted. Running a test suite leaves caches and
    reports behind, and none of that is part of the commit under review. A
    tracked file that differs from the commit is a different content, and that
    is what this refuses.
    """
    code, head = _git(root, "rev-parse", "HEAD")
    if code != 0:
        return None, None, "not a git worktree: %s" % head
    code, status = _git(root, "status", "--porcelain", "--untracked-files=no")
    if code != 0:
        # The caller reports unbound_review on this path and drops the head.
        return head or None, None, "cannot read the working tree state: %s" % status  # pragma: no mutate
    return head, status == "", None


def _is_commit(value: object) -> TypeGuard[str]:
    if not isinstance(value, str) or len(value) != 40:
        return False
    return all(char in _HEX for char in value.lower())


def _valid_paths(value: object) -> tuple[str, ...] | None:
    """The record's file list, or None when any entry is unusable.

    The producer writes repo-relative POSIX paths with no empty, "." or ".."
    segment, so a record carrying one of those spellings was not written by a
    run of this tool. The reader refuses the whole list rather than normalising
    it: accepting two spellings of one path would let a record describe a file
    the run never reported.
    """
    if not isinstance(value, list):
        return None
    cleaned: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry:
            return None
        # An absolute entry also has an empty first segment, which the next
        # guard refuses, so this clause and the one after it overlap.
        if "\\" in entry or "\x00" in entry or entry.startswith("/"):  # pragma: no mutate
            return None
        parts = entry.split("/")
        if any(part in ("", ".", "..") for part in parts):
            return None
        cleaned.append("/".join(parts))
    return tuple(sorted(set(cleaned)))


def _same(left: str, right: str) -> bool:
    """Plain comparison: both sides are run identifiers, not secrets."""
    return isinstance(left, str) and isinstance(right, str) and left == right


def _judge(document: object, *, expected_execution_id: str, review_commit: str) -> Evidence:
    """Decide the state of a parsed record. Split out so each branch is testable."""
    if not isinstance(document, dict):
        return Evidence("unusable", "the record is not a JSON object")
    if document.get("schema") != SCHEMA:
        return Evidence(
            "foreign",
            "the record names schema %r, not %r" % (document.get("schema"), SCHEMA),
        )
    if document.get("complete") is not True:
        return Evidence("incomplete", "the run did not finish writing this record")

    recorded_id = document.get("execution_id")
    if not isinstance(recorded_id, str) or not recorded_id:
        return Evidence("unusable", "the record carries no execution id")
    if not _same(recorded_id, expected_execution_id):
        return Evidence(
            "wrong_execution",
            "the record belongs to execution %s, not %s"
            % (recorded_id[:_ID_WIDTH], expected_execution_id[:_ID_WIDTH]),
        )

    snapshot = document.get("snapshot")
    if not isinstance(snapshot, dict):
        return Evidence("unusable", "the record carries no snapshot")
    commit = snapshot.get("commit")
    dirty = snapshot.get("dirty")
    if not _is_commit(commit) or not isinstance(dirty, bool):
        return Evidence("unusable", "the snapshot has no commit or no clean flag")
    if dirty:
        return Evidence("dirty_run", "the tests ran on a working tree that was not clean")
    if commit.lower() != review_commit.lower():
        return Evidence(
            "stale",
            "the tests ran at %s, and the review is of %s"
            % (commit[:_ID_WIDTH], review_commit[:_ID_WIDTH]),
        )

    files = _valid_paths(document.get("executed_files"))
    if files is None:
        return Evidence("unusable", "executed_files is not a list of repository paths")
    return Evidence("bound", "the record describes this run of this commit", files)


def verify(
    *,
    record_path: str | Path | None,
    execution_id: str,
    reviewed_commit: str,
    review_root: str | Path,
) -> Evidence:
    """Judge one record against one clean commit. Never raises on bad input."""
    root = Path(review_root)
    # A fallback of "XXXX" fails _is_commit exactly as "" does.
    named = (
        reviewed_commit or ""  # pragma: no mutate
    ).strip()
    if not _is_commit(named):
        return Evidence("unbound_review", "no 40-hex commit was named for the review")

    if record_path is None or not str(record_path).strip():
        return Evidence("absent", "no execution record was configured", review_commit=named)
    record = Path(record_path)
    # A relative path belongs to the repository under review, not to whatever
    # directory the caller happened to run from.
    if not record.is_absolute():
        record = root / record
    resolved = record.expanduser().resolve(
        strict=False  # pragma: no mutate - None is falsy, same path
    )
    if resolved.is_relative_to(root.resolve()):
        return Evidence(
            "in_tree",
            "the record is inside the reviewed tree, so the reviewed change supplied it",
            review_commit=named,
            record=str(record),
        )
    if not resolved.is_file():
        return Evidence("absent", "no record at %s" % record, review_commit=named, record=str(record))
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        return Evidence(
            "unusable", "cannot stat the record: %s" % exc, review_commit=named, record=str(record)
        )
    if size > MAX_RECORD_BYTES:
        return Evidence(
            "unusable",
            "the record is %d bytes, over the %d byte limit" % (size, MAX_RECORD_BYTES),
            review_commit=named,
            record=str(record),
        )
    try:
        text = resolved.read_text(encoding="utf-8")  # pragma: no mutate - see _git
    except (OSError, UnicodeError) as exc:
        return Evidence(
            "unusable", "cannot read the record: %s" % exc, review_commit=named, record=str(record)
        )
    try:
        document = json.loads(text)
    except ValueError as exc:
        return Evidence(
            "unusable", "the record is not JSON: %s" % exc, review_commit=named, record=str(record)
        )

    head, clean, problem = _read_repo_state(root)
    if problem is not None:
        return Evidence("unbound_review", problem, review_commit=named)
    if not clean:
        return Evidence(
            "unbound_review",
            "tracked files differ from the commit, so the reviewed content is not one commit",
            review_commit=named,
            record=str(record),
        )
    if head is None or head.lower() != named.lower():
        return Evidence(
            "unbound_review",
            # head is non-empty here: _read_repo_state reports a problem
            # instead whenever it cannot read HEAD.
            "HEAD is %s, and the review names %s" % (head[:_ID_WIDTH], named[:_ID_WIDTH]),
            review_commit=named,
            record=str(record),
        )

    verdict = _judge(document, expected_execution_id=execution_id, review_commit=named)
    return Evidence(
        verdict.state,
        verdict.detail,
        verdict.executed_files,
        review_commit=named,
        record=str(record),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m code_forge.e2e_evidence",  # pragma: no mutate - prose
        description="Judge a test execution record against the commit under review.",  # pragma: no mutate - prose
    )
    # The flag names and required-ness below are asserted by the command-line
    # tests; what these pragmas leave out is the help prose.
    parser.add_argument(
        "--record",
        help="path to the record written by the test run",  # pragma: no mutate - prose
    )
    parser.add_argument(
        "--execution-id",
        required=True,
        help="the id the caller allocated for that run; any other run is refused",  # pragma: no mutate
    )
    parser.add_argument(
        "--reviewed-commit",
        required=True,
        help="40-hex commit under review; must be the clean HEAD of --root",  # pragma: no mutate
    )
    parser.add_argument(
        "--root",
        default=".",
        help="repository under review (default: cwd)",  # pragma: no mutate - prose
    )
    args = parser.parse_args(argv)

    if not (args.execution_id or "").strip():
        print("code-forge: e2e-evidence: --execution-id is empty", file=sys.stderr)
        return EXIT_CLI_ERROR

    evidence = verify(
        record_path=args.record,
        execution_id=args.execution_id.strip(),
        reviewed_commit=args.reviewed_commit,
        review_root=args.root,
    )
    print(json.dumps(evidence.as_dict(), indent=2, sort_keys=True))
    return EXIT_PASS if evidence.bound else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
