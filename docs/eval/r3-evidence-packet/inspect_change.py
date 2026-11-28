"""Report what one pinned change's pytest records establish. Never run a review."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from code_forge.e2e_evidence import MAX_RECORD_BYTES
from code_forge.e2e_evidence import verify as verify_record

from verify import Refused, check_digest, check_execution, check_identity, load

_HUNK = re.compile(rb"^@@ -[0-9]+(?:,[0-9]+)? \+([0-9]+)(?:,[0-9]+)? @@")
_MAX_FILE_BYTES = 16 * 1024 * 1024


def _oid(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{40}", value):
        raise argparse.ArgumentTypeError("expected a full Git commit ID")
    return value


def _digest(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise argparse.ArgumentTypeError("expected a SHA-256 digest")
    return value


def _number(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("expected a non-negative integer")
    return number


def _git(root: Path, *args: str) -> bytes:
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    env.pop("GIT_DIFF_OPTS", None)
    env.pop("GIT_EXTERNAL_DIFF", None)
    return subprocess.run(
        ["git", "--no-replace-objects", "--no-lazy-fetch", *args],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        timeout=15,
    ).stdout


def _clean(root: Path, candidate: str) -> bool:
    return (
        Path(_git(root, "rev-parse", "--show-toplevel").decode().rstrip("\n")) == root
        and _git(root, "rev-parse", "HEAD").strip() == candidate.encode()
        and not _git(root, "status", "--porcelain", "--untracked-files=all")
    )


def _tracked_digest(root: Path, candidate: str, relative: str) -> str:
    path = Path(relative)
    if (
        not relative
        or path.is_absolute()
        or path.as_posix() != relative
        or ".." in path.parts
        or (root / path).resolve() != root / path
        or not (root / path).is_file()
    ):
        raise Refused("expected a regular candidate-relative file")
    object_name = f"{candidate}:{relative}"
    if _git(root, "cat-file", "-t", object_name).strip() != b"blob":
        raise Refused("file is not a blob at the candidate commit")
    if int(_git(root, "cat-file", "-s", object_name)) > _MAX_FILE_BYTES:
        raise Refused("candidate file exceeds size limit")
    raw = _git(root, "cat-file", "blob", object_name)
    if (root / path).stat().st_size != len(raw) or (root / path).read_bytes() != raw:
        raise Refused("checkout file differs from the candidate blob")
    return hashlib.sha256(raw).hexdigest()


def _target_changed(args: argparse.Namespace) -> bool:
    changed = _git(
        args.root,
        "--literal-pathspecs",
        "-c",
        "diff.suppressBlankEmpty=false",
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--no-color",
        "--word-diff=none",
        "--line-prefix=",
        "--output-indicator-new=+",
        "--output-indicator-old=-",
        "--output-indicator-context= ",
        "--unified=0",
        "--inter-hunk-context=0",
        args.base,
        args.candidate,
        "--",
        args.expect_target_file,
    )
    return _has_added_line(changed, args.expect_line)


def _has_added_line(diff: bytes, target: int) -> bool:
    # Context consumes a candidate line but is not evidence of a change.
    current = None
    for line in diff.split(b"\n"):
        if match := _HUNK.match(line):
            current = int(match[1])
        elif current is not None:
            if line.startswith(b"+"):
                if current == target:
                    return True
                current += 1
            elif line.startswith(b" "):
                current += 1
            elif not line.startswith((b"-", rb"\ ")):
                current = None
    return False


def inspect(args: argparse.Namespace) -> dict:
    """Return separate states; the input never authorizes a review outcome."""
    report = {
        "base": args.base,
        "candidate": args.candidate,
        "node": args.expect_node,
        "file": "unproven",
        "execution": "unproven",
        "changed_line": "unproven",
        "assertion": "unproven",
    }
    try:
        if not _clean(args.root, args.candidate):
            raise Refused("candidate is not the clean Git root")
        if (
            args.base == args.candidate
            or _git(args.root, "rev-parse", "--verify", f"{args.base}^{{commit}}").strip()
            != args.base.encode()
            or _git(args.root, "merge-base", args.base, args.candidate).strip() != args.base.encode()
        ):
            raise Refused("expected a distinct ancestor commit")
        test_file, marker, _ = args.expect_node.partition("::")
        if not marker:
            raise Refused("expected a named pytest node")
        args.expect_test_sha256 = _tracked_digest(args.root, args.candidate, test_file)
        report["file"] = "present"
        args.expect_source_sha256 = _tracked_digest(args.root, args.candidate, args.expect_target_file)
        if not _target_changed(args):
            raise Refused("target is not an added or replaced line of the pinned diff")
        for item in (args.packet, args.record):
            if not item.is_file() or item.resolve().is_relative_to(args.root):
                raise Refused("evidence must be a regular file outside the candidate tree")
        if args.record.stat().st_size > MAX_RECORD_BYTES:
            raise Refused("execution record exceeds size limit")
        raw_record = args.record.read_bytes()
        if hashlib.sha256(raw_record).hexdigest() != args.record_sha256:
            raise Refused("execution record digest does not match")
        record = verify_record(
            record_path=args.record,
            execution_id=args.expect_run_id,
            reviewed_commit=args.candidate,
            review_root=args.root,
        )
        if not record.bound or test_file not in record.executed_files:
            raise Refused(f"named test not established by execution record ({record.state})")
        record_body = json.loads(raw_record)
        record_exit = record_body.get("session_exitstatus") if type(record_body) is dict else None
        if type(record_exit) is not int or record_exit != args.expect_exit:
            raise Refused("the execution record and expected exit status differ")
        if args.record.read_bytes() != raw_record:
            raise Refused("execution record changed during inspection")
        args.expect_commit = args.candidate
        body = load(args.packet)
        check_digest(body, args.expect_packet_sha256)
        check_identity(body, args)
        hits = body.get("hits")
        count = hits.get(args.expect_node) if type(hits) is dict else None
        if type(count) is not int or count < 0:
            raise Refused("target hit count is invalid")
        check_execution(body, argparse.Namespace(**(vars(args) | {"expect_hits": count})))
        if not _clean(args.root, args.candidate):
            raise Refused("candidate changed during inspection")
        report["execution"] = "bound"
        report["changed_line"] = "hit" if count > 0 else "not_hit"
        if count != args.expect_hits:
            raise Refused("target hits do not match")
    except Refused as exc:
        report["reason"] = str(exc)
    except (OSError, ValueError, subprocess.SubprocessError):
        report["reason"] = "could not read pinned Git objects or evidence"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--base", type=_oid, required=True)
    parser.add_argument("--candidate", type=_oid, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--record-sha256", type=_digest, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--expect-packet-sha256", type=_digest, required=True)
    parser.add_argument("--expect-run-id", required=True)
    parser.add_argument("--expect-node", required=True)
    parser.add_argument("--expect-target-file", required=True)
    parser.add_argument("--expect-line", type=_number, required=True)
    parser.add_argument("--expect-observer-sha256", type=_digest, required=True)
    parser.add_argument("--expect-hits", type=_number, required=True)
    parser.add_argument("--expect-call", choices=("passed", "failed"), required=True)
    parser.add_argument("--expect-exit", type=int, choices=(0, 1), required=True)
    args = parser.parse_args()
    args.root = args.root.resolve()
    # The shared execution-record reader launches Git too.
    os.environ["GIT_NO_LAZY_FETCH"] = "1"
    report = inspect(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return int("reason" in report)


if __name__ == "__main__":
    raise SystemExit(main())
