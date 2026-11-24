"""Manual receipt for one ordinary passing pytest node, not an attestation."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

PROFILE = "forge-r3-outer-call-v1"
PYTEST_VERSION = "9.2.0"
MAX_BYTES = 64 * 1024
PASSING_PHASES = [["setup", "passed"], ["call", "passed"], ["teardown", "passed"]]


class ReceiptError(ValueError):
    """An execution or receipt did not meet the profile."""


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or any(c in value for c in ("\\", ":", "\0", "\n", "\r"))
    ):
        raise ReceiptError("expected a canonical repository-relative path")
    return value


def _node_path(node: str) -> str:
    path, sep, selection = node.partition("::")
    if not sep or not selection or any(c in node for c in ("\0", "\n", "\r")):
        raise ReceiptError("expected one exact pytest node ID")
    return _relative_path(path)


def _text(value: str, pattern: str, label: str) -> str:
    if re.fullmatch(pattern, value) is None:
        raise ReceiptError(f"invalid {label}")
    return value


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "--no-replace-objects", "--no-lazy-fetch", "-C", root, *args],
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise ReceiptError(f"git {args[0]} failed")
    return result.stdout


def _worktree_root(path: Path) -> Path:
    root = path.resolve(strict=True)
    if Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel").rstrip(b"\n"))) != root:
        raise ReceiptError("root is not the Git worktree root")
    return root


def _outside_receipt_path(root: Path, path: Path) -> Path:
    lexical = Path(os.path.abspath(path))
    resolved = path.resolve()
    if lexical.is_relative_to(root) or resolved.is_relative_to(root) or path.is_symlink():
        raise ReceiptError("receipt must be outside the worktree, not a symlink")
    return resolved


def _snapshot(root: Path, source: str, test: str) -> dict:
    commit = _text(_git(root, "rev-parse", "HEAD").decode().strip(), "[0-9a-f]{40}", "commit")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"):
        raise ReceiptError("worktree is not clean")

    def capture(name: str) -> dict:
        real_path = root / name
        if real_path.resolve(strict=True) != real_path or not real_path.is_file():
            raise ReceiptError("source and test must be regular files without symlinks")
        committed = _git(root, "show", f"{commit}:{name}")
        if committed != real_path.read_bytes():
            raise ReceiptError("tracked file bytes differ from the recorded commit")
        return {"path": name, "sha256": hashlib.sha256(committed).hexdigest()}

    return {"commit": commit, "source": capture(source), "test": capture(test)}


def _run_one(root: Path, node: str, test: str) -> None:
    # The helper is a dedicated CLI process. Do not load ambient third-party
    # plugins or use environment-selected pytest arguments.
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    for key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        os.environ.pop(key, None)
    sys.dont_write_bytecode = True
    os.chdir(root)

    import pytest

    if pytest.__version__ != PYTEST_VERSION:
        raise ReceiptError(f"producer requires pytest {PYTEST_VERSION}")

    class Observe:
        def __init__(self):
            self.nodes = []
            self.reports = []
            self.had_expected_failure = False

        def pytest_collection_finish(self, session):
            self.nodes = [(item.nodeid, item.path) for item in session.items]

        def pytest_runtest_logreport(self, report):
            # Same final-hook discriminator as collect_outer_calls.py.
            # Checking the intermediate makereport hook admits subtests.
            if type(report) is pytest.TestReport:
                # A non-strict XPASS also has outcome="passed" and exit 0.
                if hasattr(report, "wasxfail"):
                    self.had_expected_failure = True
                self.reports.append((report.nodeid, [report.when, report.outcome]))

    observer = Observe()
    with tempfile.TemporaryDirectory() as settings:
        ini = Path(settings) / "pytest.ini"
        ini.write_text("[pytest]\n", encoding="utf-8")
        status = pytest.main(
            [
                "-c",
                str(ini),
                "--rootdir",
                str(root),
                "--confcutdir",
                str(root),
                "-p",
                "no:cacheprovider",
                node,
            ],
            plugins=[observer],
        )
    if (
        status != pytest.ExitCode.OK
        or observer.had_expected_failure
        or observer.nodes != [(node, root / test)]
        or observer.reports != [(node, phase) for phase in PASSING_PHASES]
    ):
        raise ReceiptError(f"run not complete with one ordinary passing node (pytest exit {status})")


def produce(args: argparse.Namespace) -> None:
    root = _worktree_root(args.root)
    output = _outside_receipt_path(root, args.output)
    if output.exists():
        raise ReceiptError("output already exists; use a fresh run ID and output path")
    run_id = _text(args.run_id, "[0-9a-f]{32}", "run ID")
    source = _relative_path(args.source)
    test = _node_path(args.node)
    if source == test:
        raise ReceiptError("source and test must be distinct")
    before = _snapshot(root, source, test)
    _run_one(root, args.node, test)
    if _snapshot(root, source, test) != before:
        raise ReceiptError("worktree or Git revision changed during pytest")
    receipt = {
        "profile": PROFILE,
        "pytest": PYTEST_VERSION,
        "run_id": run_id,
        "snapshot": before,
        "node": args.node,
        "pytest_exit": 0,
        "phases": PASSING_PHASES,
    }
    data = json.dumps(receipt, ensure_ascii=True, sort_keys=True) + "\n"
    if len(data.encode("utf-8")) > MAX_BYTES:
        raise ReceiptError("receipt exceeds size limit")
    with output.open("x", encoding="utf-8") as stream:
        stream.write(data)


def _unique_object(items: list[tuple]) -> dict:
    result = dict(items)
    if len(items) != len(result):
        raise ReceiptError("duplicate JSON key")
    return result


def _read_receipt(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ReceiptError("receipt exceeds size limit")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except ReceiptError:
        raise
    except (ValueError, RecursionError) as exc:
        raise ReceiptError("unreadable receipt JSON") from exc


def verify(args: argparse.Namespace) -> None:
    path = _outside_receipt_path(_worktree_root(args.root), args.receipt)
    if not path.is_file():
        raise ReceiptError("receipt is not a regular file")
    source = _relative_path(args.source)
    test = _node_path(args.node)
    if source == test:
        raise ReceiptError("source and test must be distinct")
    expected = {
        "profile": PROFILE,
        "pytest": PYTEST_VERSION,
        "run_id": _text(args.run_id, "[0-9a-f]{32}", "expected run ID"),
        "snapshot": {
            "commit": _text(args.commit, "[0-9a-f]{40}", "expected commit"),
            "source": {
                "path": source,
                "sha256": _text(args.source_sha256, "[0-9a-f]{64}", "expected source digest"),
            },
            "test": {
                "path": test,
                "sha256": _text(args.test_sha256, "[0-9a-f]{64}", "expected test digest"),
            },
        },
        "node": args.node,
        "pytest_exit": 0,
        "phases": PASSING_PHASES,
    }
    receipt = _read_receipt(path)
    if type(receipt) is not dict or receipt.keys() != expected.keys():
        raise ReceiptError("receipt shape differs")
    if type(receipt["pytest_exit"]) is not int:
        raise ReceiptError("pytest exit is not an integer")
    for name, value in expected.items():
        if receipt[name] != value:
            raise ReceiptError(f"receipt does not match expected {name}")
    print("verified one ordinary passing pytest call")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    record = subs.add_parser("produce")
    record.add_argument("--output", type=Path, required=True)
    check = subs.add_parser("verify")
    check.add_argument("receipt", type=Path)
    for flag in ("commit", "source-sha256", "test-sha256"):
        check.add_argument(f"--{flag}", required=True)
    for sub in (record, check):
        sub.add_argument("--root", type=Path, required=True)
        for flag in ("source", "node", "run-id"):
            sub.add_argument(f"--{flag}", required=True)
    args = parser.parse_args()
    try:
        (produce if args.action == "produce" else verify)(args)
    except (OSError, ReceiptError, subprocess.TimeoutExpired) as exc:
        print(f"receipt refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
