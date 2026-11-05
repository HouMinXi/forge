"""Failure-only, non-qualifying CI diagnostics with no runtime text publication.

Only fixed labels and ordinals from the independently reviewed public manifest
may leave the runner. Success receipts and aggregate validation do not use this
format. Unknown or inconsistent evidence suppresses the entire per-test list.
A stage is the last checkpoint reached, not proof of an exclusive root cause;
for example, log cleanup may also fail while an earlier exception propagates.
"""

from pathlib import Path
import sys

if __package__:
    from . import ci_private_transport as transport
else:
    import ci_private_transport as transport

SCHEMA = "forge-public-ci-failure-v1"
MAX_TESTS = 256
TEST_STAGES = ("setup", "call", "teardown")
TEST_STATUSES = ("failed", "unexpected_skip", "unexpected_xfail")
SHARDS = tuple(f"shard-{index}" for index in range(4))
STATE_KEYS = {"sha", "phase", "run", "attempt", "stage", "returncode"}


def _state(value, sha, phase):
    transport._object(value, STATE_KEYS)
    context = transport.failure_context(sha, phase)
    transport._require(all(value[key] == item for key, item in context.items()))
    transport._require(value["stage"] in transport.FAILURE_STAGES)
    code = value["returncode"]
    transport._require(code is None or (type(code) is int and -255 <= code <= 255))
    if value["stage"] == "prepare":
        transport._require(code is None)
    elif value["stage"] != "pytest":
        transport._require(code == 0)
    return value


def _tests(receipt, state, plan, manifest, policy):
    """Validate all private input before returning any ordinal, never a partial list."""
    transport._object(receipt, transport.RECEIPT_KEYS)
    transport._require(receipt["sha"] == state["sha"] and receipt["phase"] == state["phase"])
    code = receipt["exitstatus"]
    transport._require(type(code) is int and 0 <= code <= 5 and code == state["returncode"])
    phase = state["phase"]
    identities = transport._identities(manifest)
    allowed = set(plan["phases"][phase]["nodes"])
    collected = transport._strings(receipt["collected"], string_limit=transport.MAX_NODE)
    transport._require(all(node in identities and identities[node] in allowed for node in collected))
    nodes = set(collected)
    skips = transport._strings(receipt["collection_skips"])
    transport._require(set(skips) <= set(plan["phases"][phase]["collection_skips"]))
    transport._require(
        receipt["collection_skip_reasons"]
        == {module: policy["collection_skip_reasons"][module] for module in skips}
    )
    transport._require(type(receipt["collection_errors"]) is list and not receipt["collection_errors"])
    # Build from the reviewed source itself. Never strip or forward a runtime ID.
    ordinals = {node: f"public-{index}" for index, node in enumerate(manifest["nodes"])}
    results = set()
    for kind in ("reports", "subtests"):
        seen = set()
        for report in transport._list(receipt[kind], transport.MAX_REPORTS):
            transport._object(report, transport.REPORT_KEYS)
            node, when, outcome = report["nodeid"], report["when"], report["outcome"]
            transport._require(type(node) is str and node in nodes)
            transport._require(when in TEST_STAGES and outcome in ("passed", "failed", "skipped"))
            transport._require(type(report["wasxfail"]) is bool)
            if kind == "reports":
                transport._require((node, when) not in seen)
                seen.add((node, when))
            if outcome == "skipped":
                transport._string(report["skip_reason"], transport.MAX_REASON)
                transport._require(report["skip_reason"] in policy["runtime_skips"].values())
            else:
                transport._require(report["skip_reason"] is None)
            if outcome == "failed":
                results.add((ordinals[node], when, "failed"))
            if report["wasxfail"]:
                results.add((ordinals[node], when, "unexpected_xfail"))
            if outcome == "skipped" and (
                kind == "subtests"
                or when == "teardown"
                or policy["runtime_skips"].get(node) != report["skip_reason"]
            ):
                results.add((ordinals[node], when, "unexpected_skip"))
            transport._require(len(results) <= MAX_TESTS)
    return [
        {"id": identity, "stage": when, "status": status} for identity, when, status in sorted(results)
    ]


def _validate_output(value, phase, manifest=None):
    transport._object(value, {"schema", "phase", "stage", "status", "tests", "raw_evidence"})
    transport._require(value["schema"] == SCHEMA and value["phase"] == phase and phase in SHARDS)
    transport._require(value["stage"] in (*transport.FAILURE_STAGES, "unavailable"))
    transport._require(value["status"] in ("recorded", "suppressed", "unavailable"))
    transport._require(value["raw_evidence"] == "NOT_PUBLISHED")
    tests = transport._list(value["tests"], MAX_TESTS)
    if value["status"] != "recorded":
        transport._require(not tests)
    allowed = {f"public-{index}" for index in range(len(manifest["nodes"]))} if manifest else set()
    for item in tests:
        transport._object(item, {"id", "stage", "status"})
        transport._require(item["id"] in allowed)
        transport._require(item["stage"] in TEST_STAGES and item["status"] in TEST_STATUSES)


def export_failure(sha, phase, plan_path, directory, public_directory, manifest_path=transport.MANIFEST):
    """Publish only a fully constructed safe object; failure evidence never qualifies CI."""
    transport._require(phase in SHARDS)
    directory, public_directory = Path(directory), Path(public_directory)
    transport._separate_directories(directory, public_directory)
    transport._clear_public(public_directory, ["failure.json"])
    transport._require(not transport._output_files(public_directory))
    result = {
        "schema": SCHEMA,
        "phase": phase,
        "stage": "unavailable",
        "status": "unavailable",
        "tests": [],
        "raw_evidence": "NOT_PUBLISHED",
    }
    manifest = None
    try:
        state = _state(transport.read_json(directory / transport.FAILURE_STATE), sha, phase)
        result["stage"] = state["stage"]
        # A returned subprocess proves current targets were cleared and reserved.
        # Earlier failures cannot associate a possibly stale receipt with this run.
        if state["returncode"] is not None:
            manifest = transport.validate_manifest(transport.read_json(manifest_path))
            policy = transport._validate_policy(transport.read_json(transport.ci.POLICY), manifest)
            plan = transport.read_json(plan_path)
            transport._validate_public_plan(
                plan, sha, transport.project_policy(policy, manifest), manifest
            )
            receipt = transport.read_json(directory / f"receipt-{phase}.json")
            tests = _tests(receipt, state, plan, manifest, policy)
            result.update(status="recorded", tests=tests)
    except FileNotFoundError:
        result.update(status="unavailable", tests=[])
    except Exception:  # noqa: BLE001 - Untrusted evidence never reaches public error text.
        result.update(status="suppressed", tests=[])
    _validate_output(result, phase, manifest)
    transport._write_json(public_directory / "failure.json", result)
    transport._require(transport.read_json(public_directory / "failure.json") == result)
    transport._require(transport._output_files(public_directory) == [public_directory / "failure.json"])
    return result


def main(argv=None):
    try:
        parser = transport._Parser(description=__doc__)
        parser.add_argument("--sha", required=True)
        parser.add_argument("--phase", choices=SHARDS, required=True)
        parser.add_argument("--plan", type=Path, required=True)
        parser.add_argument("--directory", type=Path, required=True)
        parser.add_argument("--public-directory", type=Path, required=True)
        parser.add_argument("--manifest", type=Path, default=transport.MANIFEST)
        args = parser.parse_args(argv)
        export_failure(
            args.sha, args.phase, args.plan, args.directory, args.public_directory, args.manifest
        )
        return 0
    except BaseException:  # noqa: BLE001 - No exception, path, or argparse input is publishable.
        print("STOP: Failure diagnostic unavailable.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
