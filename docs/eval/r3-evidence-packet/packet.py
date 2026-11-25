"""Record one pytest execution as a single evidence packet.

The packet answers four questions about the same run: which source and test
were under the interpreter, which node actually executed, whether the target
line ran, and what the call outcome was. Because one process produces all of
it, a passing receipt from another run cannot be attached to these hits.

It is not an attestation. Nothing here is signed, so a packet can be edited
afterwards; the anchor is the digest printed to stdout at execution time,
which a caller records separately.

Exit status is pytest's. Nothing is written when the run cannot be bound to a
commit, a source digest and a test digest.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCHEMA = "forge-r3-evidence-v1"
MAX_PACKET_BYTES = 256 * 1024
OBSERVER = Path(__file__).resolve().parent.parent / "r3-target-line" / "target_hits.py"


class PacketError(Exception):
    """The packet cannot be produced or does not match what was expected."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_observer():
    """Reuse the merged tracer instead of keeping a second copy of it."""
    spec = importlib.util.spec_from_file_location("r3_target_hits", OBSERVER)
    if spec is None or spec.loader is None:
        raise PacketError("observer module is not loadable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    commit = result.stdout.strip()
    if result.returncode != 0 or len(commit) != 40:
        raise PacketError("root is not a git checkout with a resolvable commit")
    return commit


def _resolve(root: Path, file_arg: str) -> Path:
    candidate = Path(file_arg)
    path = (root / candidate if not candidate.is_absolute() else candidate).resolve()
    root = root.resolve()
    if path != root and root not in path.parents:
        raise PacketError("file must live under the given root")
    if not path.is_file():
        raise PacketError(f"file does not exist: {file_arg}")
    return path


class PacketCollector:
    """Record the call outcome and the target hits of one selection."""

    def __init__(self, hits, node_of_interest: str) -> None:
        self.hits = hits
        self.node = node_of_interest
        self.collected: list[str] = []
        self.phases: dict[str, list[list[str]]] = {}
        self.exit_status: int | None = None

    def pytest_collection_finish(self, session) -> None:
        self.collected = [item.nodeid for item in session.items]

    def pytest_runtest_logreport(self, report) -> None:
        if type(report) is not pytest.TestReport:
            return
        outcome = report.outcome
        if getattr(report, "wasxfail", None) is not None:
            # An xfail-marked result is its own outcome. The marker survives
            # into the report, so recording a bare "passed" here would let an
            # XPASS satisfy a claim about an ordinary passing call.
            outcome = "xpassed" if outcome == "passed" else "xfailed"
        elif outcome == "failed" and str(report.longrepr).startswith("[XPASS(strict)]"):
            # A strict xfail that passes is reported as a failure whose text
            # opens with this prefix and carries no wasxfail. The marker name
            # alone does not say that: a marker whose condition is false, or
            # an xfail(raises=...) that caught a different exception, fails the
            # same way and must stay a plain failure.
            outcome = "xpassed"
        outcomes = self.phases.setdefault(report.nodeid, [])
        outcomes.append([report.when, outcome])

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item):
        token = self.hits.begin(item.nodeid)
        try:
            return (yield)
        finally:
            self.hits.end(token)

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        self.hits.close()
        self.exit_status = int(exitstatus)


def _body(root: Path, collector: PacketCollector, identity: dict) -> dict:
    hits = collector.hits
    return {
        "schema": SCHEMA,
        "identity": identity,
        "collected": collector.collected,
        "phases": collector.phases,
        "hits": {node: hits.counts.get(node, 0) for node in collector.collected},
        "pytest_exit": collector.exit_status,
    }


def canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def produce(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    target = _resolve(root, args.file)
    source = _resolve(root, args.source)
    if source != target:
        # The digest must describe the file the tracer watches. Accepting two
        # paths would let a packet hash one file and report hits for another.
        raise PacketError("source and target must resolve to the same file")
    test = _resolve(root, args.node.split("::", 1)[0])
    observer = _load_observer()
    hits = observer.LineHits(target, args.line)
    collector = PacketCollector(hits, args.node)

    def stamp() -> dict[str, str]:
        return {
            "commit": _commit(root),
            "source": _sha256(source),
            "test": _sha256(test),
            "observer": _sha256(OBSERVER),
        }

    opening = stamp()
    identity = {
        "run_id": args.run_id,
        "commit": opening["commit"],
        "node": args.node,
        "source": {"path": str(source.relative_to(root)), "sha256": opening["source"]},
        "test": {"path": str(test.relative_to(root)), "sha256": opening["test"]},
        "target": {"path": str(target.relative_to(root)), "line": args.line},
        "observer": {
            "path": str(OBSERVER.relative_to(OBSERVER.parent.parent)),
            "sha256": opening["observer"],
        },
        "python": "%d.%d" % sys.version_info[:2],
        "pytest": pytest.__version__,
    }
    cwd = Path.cwd()
    os.chdir(root)
    try:
        status = int(pytest.main([*args.pytest_args, "-p", "no:cacheprovider"], plugins=[collector]))
    finally:
        os.chdir(cwd)
        hits.close()
    closing = stamp()
    drifted = sorted(name for name in opening if opening[name] != closing[name])
    if drifted:
        # The packet describes opening state; a tree that moved during the run
        # would make that description describe nothing in particular.
        raise PacketError("changed during the run, no packet written: " + ", ".join(drifted))
    node_file = identity["test"]["path"]
    if args.node not in collector.collected or any(
        item.split("::", 1)[0] != node_file for item in collector.collected
    ):
        # --node names the file whose digest the packet carries, while
        # pytest_args decides what actually runs. Refuse unless the recorded
        # node ran and every collected node comes from that same file.
        raise PacketError(f"{args.node} does not name a node in {node_file}")
    body = _body(root, collector, identity)
    body["packet_sha256"] = hashlib.sha256(canonical(body)).hexdigest()
    data = json.dumps(body, indent=2, sort_keys=True) + "\n"
    if len(data.encode("utf-8")) > MAX_PACKET_BYTES:
        raise PacketError("packet exceeds size limit")
    output = args.output.resolve()
    if output == root or root in output.parents:
        raise PacketError("packet must be written outside the checkout")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(data, encoding="utf-8")
    print(f"forge-r3-packet run_id={identity['run_id']} sha256={body['packet_sha256']}")
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--file", required=True, help="target file, relative to root")
    parser.add_argument("--line", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.pytest_args and args.pytest_args[0] == "--":
        args.pytest_args = args.pytest_args[1:]
    try:
        return produce(args)
    except (OSError, PacketError) as exc:
        print(f"packet refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
