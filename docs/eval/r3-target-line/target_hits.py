"""Record whether one exact source line runs during a pytest selection.

The record answers a narrow question: in this process, while running these
nodes, did the named line execute, and how many times per node. It is an
observation, not a receipt and not an attestation.

A recorded hit proves the line ran. No recorded hit does not prove it did
not: another process, another selection, a skipped or interrupted run, or a
line reached only off the traced path all produce the same empty record.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path

SCHEMA = "forge-r3-target-hits-v1"
MAX_RECORD_BYTES = 64 * 1024


class TargetError(Exception):
    """The target or the record location does not meet the profile."""


def _resolve_target(root: Path, file_arg: str, line: int) -> tuple[Path, int]:
    if line < 1:
        raise TargetError("target line must be a positive integer")
    candidate = Path(file_arg)
    target = (root / candidate if not candidate.is_absolute() else candidate).resolve()
    root = root.resolve()
    if target != root and root not in target.parents:
        raise TargetError("target file must live under the given root")
    if not target.is_file():
        raise TargetError("target file does not exist")
    with open(target, "rb") as handle:
        total = sum(1 for _ in handle)
    if line > total:
        raise TargetError("target line is past the end of the file")
    return target, line


def _outside_output(root: Path, output: Path) -> Path:
    root = root.resolve()
    path = output if output.is_absolute() else (Path.cwd() / output)
    path = path.resolve()
    if path == root or root in path.parents:
        raise TargetError("record must be written outside the checkout")
    return path


class LineHits:
    """Count executions of one file line, per test, across threads."""

    def __init__(self, target: Path, line: int) -> None:
        self.target = str(target)
        self.line = line
        self.lock = threading.Lock()
        self.counts: dict[str, int] = {}
        self.current = ""

    def _count(self) -> None:
        with self.lock:
            self.counts[self.current] = self.counts.get(self.current, 0) + 1

    def _line_scope(self, frame, event, arg):
        if event == "line" and frame.f_lineno == self.line:
            self._count()
        return self._line_scope

    def _file_scope(self, frame, event, arg):
        if frame.f_code.co_filename == self.target:
            return self._line_scope
        return None

    def begin(self, nodeid: str) -> None:
        self.current = nodeid
        self.counts.setdefault(nodeid, 0)
        threading.settrace(self._file_scope)
        sys.settrace(self._file_scope)

    def end(self) -> None:
        sys.settrace(None)
        threading.settrace(None)


class Collector:
    """pytest plugin that brackets each node's call phase with the counter."""

    def __init__(self, hits: LineHits) -> None:
        self.hits = hits
        self.collected: list[str] = []
        self.exit_status: int | None = None

    def pytest_collection_finish(self, session) -> None:
        self.collected = [item.nodeid for item in session.items]

    def pytest_runtest_call(self, item) -> None:
        self.hits.begin(item.nodeid)

    def pytest_runtest_teardown(self, item) -> None:
        self.hits.end()

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        self.hits.end()
        self.exit_status = int(exitstatus)


def _record(root: Path, target: Path, line: int, collector: Collector) -> dict:
    hits = collector.hits
    return {
        "schema": SCHEMA,
        "target": {
            "path": str(target.relative_to(root.resolve())),
            "line": line,
        },
        "collected": collector.collected,
        "hits": {node: hits.counts.get(node, 0) for node in collector.collected},
        "pytest_exit": collector.exit_status,
    }


def run(root: Path, target: Path, line: int, output: Path, args: list[str]) -> int:
    import pytest

    if args and args[0] == "--":
        args = args[1:]
    hits = LineHits(target, line)
    collector = Collector(hits)
    cwd = Path.cwd()
    os.chdir(root)
    try:
        status = int(pytest.main([*args, "-p", "no:cacheprovider"], plugins=[collector]))
    finally:
        os.chdir(cwd)
        hits.end()
    data = json.dumps(_record(root, target, line, collector), indent=2, sort_keys=True) + "\n"
    if len(data.encode("utf-8")) > MAX_RECORD_BYTES:
        raise TargetError("record exceeds size limit")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(data, encoding="utf-8")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--line", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        target, line = _resolve_target(args.root, args.file, args.line)
        output = _outside_output(args.root, args.output)
        return run(args.root.resolve(), target, line, output, args.pytest_args)
    except (OSError, TargetError) as exc:
        print(f"target refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
