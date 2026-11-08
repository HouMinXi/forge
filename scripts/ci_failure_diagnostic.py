"""Failure-only, non-qualifying CI diagnostics with opt-in native failure detail.

Reviewed test names, source-verified coordinates, fixed labels and ordinals
may leave the runner. Runtime text stays private except explicitly enabled
native-test exception representations in the failure artifact. Success receipts and aggregate validation do not use this
format. Unknown or inconsistent evidence suppresses the entire per-test list.
A stage is the last checkpoint reached, not proof of an exclusive root cause;
for example, log cleanup may also fail while an earlier exception propagates.
"""

from pathlib import Path
import sys

if __package__:
    from . import ci_native_failure_detail as native
    from . import ci_private_transport as transport
    from .ci_failure_source import Verifier, valid_path
else:
    import ci_native_failure_detail as native
    import ci_private_transport as transport
    from ci_failure_source import Verifier, valid_path

SCHEMA = "forge-public-ci-failure-v2"
MAX_TESTS = 256
TEST_STAGES = ("setup", "call", "teardown")
TEST_STATUSES = ("failed", "unexpected_skip", "unexpected_xfail")
# Fixed labels the runner may publish for a failure. They name an exception
# category, never its text: an assertion's operands stay on the machine.
FAILURE_KINDS = ("assertion", "timeout", "error")
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


def _tests(receipt, state, plan, manifest, policy, native_details=False):
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
    results = {}
    sources = {}
    details = {}
    verifier = Verifier(state["sha"])
    # Subtests first: an aggregate parent report carries no reprcrash, so
    # consuming it first would freeze a placeholder label for every subtest.
    for kind in ("subtests", "reports"):
        seen = set()
        for report in transport._list(receipt[kind], transport.MAX_REPORTS):
            transport._object(report, transport.REPORT_KEYS, transport.REPORT_OPTIONAL_KEYS)
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
                # failure_kind is a fixed label chosen on the runner; a receipt
                # written before classification existed simply has none. Two
                # failing subtests of one node can differ, so the first one wins
                # rather than a later sibling silently relabelling the entry.
                recorded = report.get("failure_kind")
                transport._require(recorded is None or recorded in FAILURE_KINDS)
                key = (ordinals[node], when, "failed")
                if key not in results:
                    results[key] = recorded
                    sources[key] = verifier.verify(report.get("failure_source"))
                    detail = report.get("native_failure_detail")
                    if native_details and native.eligible(node) and detail is not None:
                        transport._require(native.validate(detail))
                        details[key] = {
                            "schema": native.SCHEMA,
                            "nodeid": node,
                            **{key: state[key] for key in ("sha", "run", "attempt", "phase")},
                            "stage": when,
                            "traceback": native.scrub(detail["traceback"], native.credential_values()),
                            "truncated": detail["truncated"],
                        }
            if report["wasxfail"]:
                results[(ordinals[node], when, "unexpected_xfail")] = None
            if outcome == "skipped" and (
                kind == "subtests"
                or when == "teardown"
                or policy["runtime_skips"].get(node) != report["skip_reason"]
            ):
                results[(ordinals[node], when, "unexpected_skip")] = None
            transport._require(len(results) <= MAX_TESTS)
    return [
        {
            "id": identity,
            "stage": when,
            "status": status,
            "kind": kind,
            "test": manifest["nodes"][int(identity.split("-")[1])].split("[", 1)[0],
            "source": sources.get((identity, when, status)),
            **(
                {"native_detail": details[(identity, when, status)]}
                if (identity, when, status) in details
                else {}
            ),
        }
        for (identity, when, status), kind in sorted(results.items())
    ]


def _validate_output(value, phase, manifest=None):
    transport._object(value, {"schema", "sha", "phase", "stage", "status", "tests", "raw_evidence"})
    transport._sha(value["sha"])
    transport._require(value["schema"] == SCHEMA and value["phase"] == phase and phase in SHARDS)
    transport._require(value["stage"] in (*transport.FAILURE_STAGES, "unavailable"))
    transport._require(value["status"] in ("recorded", "suppressed", "unavailable"))
    transport._require(value["raw_evidence"] == "NOT_PUBLISHED")
    tests = transport._list(value["tests"], MAX_TESTS)
    if value["status"] != "recorded":
        transport._require(not tests)
    allowed = {f"public-{index}" for index in range(len(manifest["nodes"]))} if manifest else set()
    for item in tests:
        transport._object(item, {"id", "stage", "status", "test", "source"}, {"kind", "native_detail"})
        transport._require(item["id"] in allowed)
        node = manifest["nodes"][int(item["id"].split("-")[1])]
        transport._require(item["test"] == node.split("[", 1)[0])
        if "native_detail" in item:
            detail = item["native_detail"]
            transport._object(
                detail,
                {
                    "schema",
                    "nodeid",
                    "sha",
                    "run",
                    "attempt",
                    "phase",
                    "stage",
                    "traceback",
                    "truncated",
                },
            )
            transport._require(detail["schema"] == native.SCHEMA and native.eligible(node))
            transport._require(detail["nodeid"] == node and detail["sha"] == value["sha"])
            transport._require(detail["phase"] == phase and detail["stage"] == item["stage"])
            transport._require(item["status"] == "failed")
            context = transport.failure_context(value["sha"], phase)
            transport._require(
                detail["run"] == context["run"] and detail["attempt"] == context["attempt"]
            )
            transport._require(native.validate({key: detail[key] for key in ("traceback", "truncated")}))
        source = item["source"]
        if source is not None:
            transport._object(source, {"path", "line"})
            transport._require(item["status"] == "failed" and valid_path(source["path"]))
            transport._require(type(source["line"]) is int and source["line"] > 0)
        transport._require(item["stage"] in TEST_STAGES and item["status"] in TEST_STATUSES)
        # Only a failure can carry a category; a skip or an xfail has none.
        transport._require(item.get("kind") is None or item["status"] == "failed")
        transport._require(item.get("kind") is None or item["kind"] in FAILURE_KINDS)


def export_failure(
    sha,
    phase,
    plan_path,
    directory,
    public_directory,
    manifest_path=transport.MANIFEST,
    *,
    native_details=False,
):
    """Publish only a fully constructed safe object; failure evidence never qualifies CI."""
    transport._require(phase in SHARDS)
    transport._sha(sha)
    directory, public_directory = Path(directory), Path(public_directory)
    transport._separate_directories(directory, public_directory)
    transport._clear_public(public_directory, ["failure.json"])
    transport._require(not transport._output_files(public_directory))
    result = {
        "schema": SCHEMA,
        "sha": sha,
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
            tests = _tests(receipt, state, plan, manifest, policy, native_details)
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
        parser.add_argument("--native-details", action="store_true")
        args = parser.parse_args(argv)
        export_failure(
            args.sha,
            args.phase,
            args.plan,
            args.directory,
            args.public_directory,
            args.manifest,
            native_details=args.native_details,
        )
        return 0
    except BaseException:  # noqa: BLE001 - No exception, path, or argparse input is publishable.
        print("STOP: Failure diagnostic unavailable.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
