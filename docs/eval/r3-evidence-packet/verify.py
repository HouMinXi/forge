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
    try:
        if not path.is_file():
            raise Refused("packet is not a regular file")
        if path.stat().st_size > MAX_PACKET_BYTES:
            raise Refused("packet exceeds size limit")
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise Refused("packet is not valid UTF-8") from exc
    except OSError as exc:
        raise Refused("packet cannot be read") from exc
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Refused("packet is not valid JSON") from exc
    except ValueError as exc:
        # Preserve the interpreter's numeric parsing limits.
        raise Refused("packet contains an unsupported JSON value") from exc
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
            raise Refused(f"{label} does not match")
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
    if type(target.get("line")) is not int:
        # JSON `true` compares equal to an expected line of 1, so a boolean
        # would name the traced line without ever being an integer. Keep the
        # existing wording so a value refused before this change reports the
        # same text it always did.
        raise Refused("target does not match")
    if target.get("path") != args.expect_target_file or target.get("line") != args.expect_line:
        raise Refused("target does not match")
    source = identity["source"]
    if source.get("path") != target.get("path"):
        # The digest has to describe the traced file, or the packet can hash one
        # file and report hits for another.
        raise Refused("source digest does not describe the traced target")
    node = identity.get("node")
    test = identity["test"]
    test_file = test.get("path")
    if type(node) is not str or node != args.expect_node or node.split("::", 1)[0] != test_file:
        # The test digest and the verified node must name the same file. A
        # packet can otherwise carry one file's digest behind another's result.
        raise Refused("node does not name a node in the traced test file")


def check_execution(body: dict, args: argparse.Namespace) -> int:
    node = args.expect_node
    collected = body.get("collected", [])
    if type(collected) is not list or node not in collected:
        raise Refused("node was not collected in this run")
    if collected.count(node) > 1:
        raise Refused("target node was collected more than once")
    node_file = node.split("::", 1)[0]
    if any(type(item) is not str or item.split("::", 1)[0] != node_file for item in collected):
        raise Refused("collected nodes do not all belong to the traced test file")
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
        raise Refused("node call outcome does not match")
    exit_status = body.get("pytest_exit")
    if type(exit_status) is not int:
        # A JSON `false` equals an expected 0 in Python, so a boolean could
        # stand in for the producer's recorded exit status of a clean run.
        raise Refused("pytest exit is not an integer")
    if exit_status != args.expect_exit:
        raise Refused("pytest exit does not match")
    hits = body.get("hits", {})
    if type(hits) is not dict:
        raise Refused("hits is not a record")
    if node not in hits:
        raise Refused("no hit count recorded for the node")
    count = hits[node]
    if type(count) is not int:
        # `true` equals an expected hit count of 1, so a boolean would satisfy
        # a positive hit claim without being a count at all. A JSON float is a
        # number but not a count, so the text names an integer.
        raise Refused("target hits are not an integer")
    if count < 0:
        # A negative count is not zero hits. Reporting it as "not hit" would
        # state something the packet does not say.
        raise Refused("target hits are negative")
    compare_hits(count, args.expect_hits)
    return count


def compare_hits(count: int, claimed) -> None:
    """Refuse when a claim is present and disagrees. Both entries share this."""
    if claimed is not None and count != claimed:
        raise Refused("target hits do not match")


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
