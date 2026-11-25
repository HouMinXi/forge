"""Check one evidence packet against what the caller expected.

Refusals are the point of this file. A packet is only accepted when the run
identity, the commit, the source and test digests, the observer digest, the
target, the node's call phase and the target hits all match what the caller
recorded at execution time. A green run that never reached the target, a run
from an earlier execution of the same commit, and a packet edited after the
fact are each refused.

Nothing here is an attestation: the packet is unsigned, so the caller's
recorded digest is the only anchor against an edit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCHEMA = "forge-r3-evidence-v1"
MAX_PACKET_BYTES = 256 * 1024


class Refused(Exception):
    """The packet does not support the claim the caller wants to make."""


def canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_of(body: dict) -> str:
    unsigned = {key: value for key, value in body.items() if key != "packet_sha256"}
    return hashlib.sha256(canonical(unsigned)).hexdigest()


def load(path: Path) -> dict:
    if not path.is_file():
        raise Refused("packet is not a regular file")
    if path.stat().st_size > MAX_PACKET_BYTES:
        raise Refused("packet exceeds size limit")
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Refused("packet is not valid JSON") from exc
    if type(body) is not dict or body.get("schema") != SCHEMA:
        raise Refused("packet schema differs")
    return body


def check_digest(body: dict, expected: str) -> None:
    actual = digest_of(body)
    if actual != expected:
        raise Refused(f"packet digest is {actual[:12]}, caller recorded {expected[:12]}")
    if body.get("packet_sha256") != expected:
        raise Refused("packet digest field disagrees with the body")


def check_identity(body: dict, args: argparse.Namespace) -> None:
    identity = body.get("identity")
    if type(identity) is not dict:
        raise Refused("packet has no identity")
    pairs = [
        ("run identity", identity.get("run_id"), args.expect_run_id),
        ("commit", identity.get("commit"), args.expect_commit),
    ]
    for label, actual, expected in pairs:
        if actual != expected:
            raise Refused(f"{label} is {actual!r}, expected {expected!r}")
    for label, key, expected in (
        ("source digest", "source", args.expect_source_sha256),
        ("test digest", "test", args.expect_test_sha256),
        ("observer digest", "observer", args.expect_observer_sha256),
    ):
        record = identity.get(key, {})
        if type(record) is not dict or record.get("sha256") != expected:
            raise Refused(f"{label} does not match")
    target = identity.get("target", {})
    if type(target) is not dict:
        raise Refused("target is not a record")
    if target.get("path") != args.expect_target_file or target.get("line") != args.expect_line:
        raise Refused("target does not match")
    source = identity.get("source", {})
    if type(source) is not dict or source.get("path") != target.get("path"):
        # The digest has to describe the traced file, or the packet can hash one
        # file and report hits for another.
        raise Refused("source digest does not describe the traced target")
    node = identity.get("node")
    test = identity.get("test", {})
    if (
        type(node) is not str
        or type(test) is not dict
        or node != args.expect_node
        or node.split("::", 1)[0] != test.get("path")
    ):
        # The test digest and the verified node must name the same file. A
        # packet can otherwise carry one file's digest behind another's result.
        raise Refused(
            f"{node} does not name a node in {test.get('path') if type(test) is dict else None}"
        )


def check_execution(body: dict, args: argparse.Namespace) -> None:
    node = args.expect_node
    collected = body.get("collected", [])
    if type(collected) is not list or node not in collected:
        raise Refused("node was not collected in this run")
    if collected.count(node) > 1:
        raise Refused("target node was collected more than once")
    node_file = node.split("::", 1)[0]
    if any(type(item) is not str or item.split("::", 1)[0] != node_file for item in collected):
        raise Refused(f"{node} does not name a node in {node_file}")
    phases = body.get("phases", {})
    if type(phases) is not dict:
        raise Refused("phases is not a record")
    node_phases = phases.get(node, [])
    if type(node_phases) is not list or any(
        type(entry) is not list or len(entry) != 2 for entry in node_phases
    ):
        raise Refused("phase entry is not a when/outcome pair")
    call_outcomes = [outcome for when, outcome in node_phases if when == "call"]
    if not call_outcomes:
        raise Refused("node has no call phase: collected but not executed")
    if len(call_outcomes) != 1:
        raise Refused("target node has more than one call report")
    if call_outcomes[0] != args.expect_call:
        # A failing run says nothing about this node: another node may have
        # failed, or this one may have failed in teardown after a passing call.
        raise Refused(f"node call outcome is {call_outcomes!r}, expected {args.expect_call!r}")
    if body.get("pytest_exit") != args.expect_exit:
        raise Refused(f"pytest exit is {body.get('pytest_exit')!r}, expected {args.expect_exit}")
    hits = body.get("hits", {})
    if type(hits) is not dict:
        raise Refused("hits is not a record")
    count = hits.get(node, 0)
    if count != args.expect_hits:
        raise Refused(f"target hits are {count!r}, expected {args.expect_hits}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("--expect-run-id", required=True)
    parser.add_argument("--expect-commit", required=True)
    parser.add_argument("--expect-source-sha256", required=True)
    parser.add_argument("--expect-test-sha256", required=True)
    parser.add_argument("--expect-observer-sha256", required=True)
    parser.add_argument("--expect-target-file", required=True)
    parser.add_argument("--expect-line", type=int, required=True)
    parser.add_argument("--expect-node", required=True)
    parser.add_argument("--expect-exit", type=int, default=0)
    parser.add_argument(
        "--expect-call",
        required=True,
        choices=("passed", "failed"),
        help="outcome of this node's call phase; a failing run alone is not enough",
    )
    parser.add_argument("--expect-hits", type=int, required=True)
    parser.add_argument(
        "--expect-packet-sha256",
        required=True,
        help="digest printed by the producer at execution time",
    )
    args = parser.parse_args()
    try:
        body = load(args.packet)
        check_digest(body, args.expect_packet_sha256)
        check_identity(body, args)
        check_execution(body, args)
    except Refused as exc:
        print(f"packet refused: {exc}", file=sys.stderr)
        return 1
    print("verified one bound pytest execution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
