"""Fail-closed validation of the eleven reviewed real-test results.

This is only a test-evidence gate. Runner identity, boundary proof, phase exit
statuses and evidence preservation must still be established by the controller.
The inputs must come from the current controller-owned, fresh evidence directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import xml.etree.ElementTree as ET

REQUIRED_NODEIDS = (
    "tests/test_mutation_patchcorpus.py::test_real_corpus_kills_guard_removal",
    "tests/test_mutation_patchcorpus.py::test_real_corpus_lists_entries_outside_selection",
    "tests/test_mutation_patchcorpus.py::test_real_corpus_concurrent_runs_keep_distinct_ownership",
    "tests/test_mutation_isolate.py::test_real_concurrent_probes_have_distinct_ownership",
    "tests/test_mutation_isolate.py::test_real_run_executes_in_workspace",
    "tests/test_mutation_isolate.py::test_limits_are_applied_before_payload_and_read_back",
    "tests/test_mutation_isolate.py::test_no_network_route_inside",
    "tests/test_mutation_isolate.py::test_pids_limit_caps_forking",
    "tests/test_mutation_isolate.py::test_watchdog_kills_payload_when_supervisor_dies",
    "tests/test_mutation_isolate.py::test_cleanup_removes_cgroup_and_payloads",
    "tests/test_mutation_isolate.py::test_sandbox_has_dev_null_and_proc",
)
PHASES = ("setup", "call", "teardown")
SCHEMA_VERSION = 1
MAX_EVENTS = len(REQUIRED_NODEIDS) * 6
MAX_EVENTS_BYTES = 64 * 1024
MAX_JUNIT_BYTES = 32 * 1024 * 1024
MAX_XFAIL_REASON = 512


class EvidenceError(ValueError):
    """The evidence is absent, ambiguous, malformed or outside its bounds."""


def _read_bounded(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise EvidenceError("evidence must be a regular file")
        data = stream.read(limit + 1)
    if not data or len(data) > limit:
        raise EvidenceError(f"empty or overlong evidence (limit {limit} bytes)")
    return data


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise EvidenceError(f"non-finite JSON value: {value}")


def _load_events(data: bytes) -> dict:
    document = json.loads(
        data.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )
    if not isinstance(document, dict):
        raise EvidenceError("observer document must be an object")
    return document


def _validate_events(document: dict, errors: list[str], results: dict) -> None:
    expected_keys = {"schema_version", "required_nodeids", "session", "events", "errors", "overflow"}
    if set(document) != expected_keys:
        errors.append("observer document has missing or unexpected fields")
    if type(document.get("schema_version")) is not int or document["schema_version"] != SCHEMA_VERSION:
        errors.append("observer schema version mismatch")
    if document.get("required_nodeids") != list(REQUIRED_NODEIDS):
        errors.append("observer required-node manifest mismatch")
    if document.get("overflow") is not False:
        errors.append("observer overflow/truncation flag is not false")
    if document.get("errors") != []:
        errors.append("observer reported errors or has no error record")
    session = document.get("session")
    session_keys = {"started", "collection_complete", "collected", "finished", "exitstatus"}
    if not isinstance(session, dict) or set(session) != session_keys:
        errors.append("observer session record is missing or malformed")
    else:
        errors.extend(
            f"observer session {flag} is not true"
            for flag in ("started", "collection_complete", "finished") if session[flag] is not True
        )
        if type(session["exitstatus"]) is not int or session["exitstatus"] != 0:
            errors.append("observer session did not finish with exit status 0")
        collected = session["collected"]
        if not isinstance(collected, dict) or set(collected) != set(REQUIRED_NODEIDS):
            errors.append("observer collected-node manifest is missing or malformed")
        elif any(type(count) is not int or count != 1 for count in collected.values()):
            errors.append("each required node must be collected exactly once")
    events = document.get("events")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        errors.append("observer events are missing or overlong")
        return
    for index, event in enumerate(events):
        if not isinstance(event, dict) or set(event) != {
            "nodeid", "when", "outcome", "wasxfail_present", "wasxfail"
        }:
            errors.append(f"malformed observer event {index}")
            continue
        nodeid = event["nodeid"]
        if not isinstance(nodeid, str) or nodeid not in results:
            errors.append(f"observer event {index} is outside the required-node manifest")
            continue
        results[nodeid]["events"].append(event)
        if event["when"] not in PHASES:
            errors.append(f"{nodeid}: unknown phase")
        if event["outcome"] != "passed":
            errors.append(f"{nodeid}: a report did not pass")
        if event["wasxfail_present"] is not False or event["wasxfail"] is not None:
            errors.append(f"{nodeid}: xfail/XPASS evidence or malformed wasxfail fields")
    for nodeid, result in results.items():
        if [event["when"] for event in result["events"]] != list(PHASES):
            errors.append(f"{nodeid}: expected exactly one setup/call/teardown report, in order")


def _load_junit(data: bytes) -> ET.Element:
    # UTF-8 only, with no DTD/entity declarations. Parse only after bounding input;
    # ElementTree never needs external resources for pytest's ordinary JUnit XML.
    xml = data.decode("utf-8-sig")
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise EvidenceError("JUnit DTD/entity declarations are forbidden")
    root = ET.fromstring(xml)  # noqa: S314 -- bounded UTF-8, DTD/entities rejected above
    if root.tag not in {"testsuites", "testsuite"}:
        raise EvidenceError("JUnit root must be testsuites or testsuite")
    suites = list(root) if root.tag == "testsuites" else [root]
    if not suites or any(suite.tag != "testsuite" for suite in suites):
        raise EvidenceError("JUnit requires direct testsuite children")
    for suite in suites:
        if any(child.tag not in {"properties", "testcase", "system-out", "system-err"} for child in suite):
            raise EvidenceError("JUnit testsuite has an unexpected child")
        if len(list(suite.iter("testcase"))) != len(suite.findall("testcase")):
            raise EvidenceError("JUnit testcases must be direct testsuite children")
    return root


def _junit_identities() -> dict[tuple[str, str], str]:
    identities = {}
    for nodeid in REQUIRED_NODEIDS:
        filename, name = nodeid.split("::")
        # pytest's standard xunit1/xunit2 mangling, plus an exact path classname.
        # No basename matching, arbitrary prefixes, suffixes or file-only fallback.
        identities[(filename[:-3].replace("/", "."), name)] = nodeid
        identities[(filename, name)] = nodeid
    return identities


def _validate_junit(root: ET.Element, errors: list[str], results: dict) -> None:
    identities = _junit_identities()
    cases = list(root.iter("testcase"))
    if not cases:
        errors.append("JUnit contains no testcases")
    for suite in root.iter():
        if suite.tag not in {"testsuites", "testsuite"}:
            continue
        suite_cases = list(suite.iter("testcase"))
        actual = {
            "tests": len(suite_cases),
            "failures": sum(case.find("failure") is not None for case in suite_cases),
            "errors": sum(case.find("error") is not None for case in suite_cases),
            "skipped": sum(case.find("skipped") is not None for case in suite_cases),
        }
        for name, count in actual.items():
            if name in suite.attrib:
                value = suite.attrib[name]
                if not value.isascii() or not value.isdigit() or int(value) != count:
                    errors.append(f"JUnit {name} summary disagrees with testcase records")
    for case in cases:
        if case.find("failure") is not None or case.find("error") is not None:
            errors.append("JUnit contains a failure/error")
        nodeid = identities.get((case.get("classname", ""), case.get("name", "")))
        if nodeid is None:
            continue
        results[nodeid]["junit_count"] += 1
        if case.get("file", nodeid.split("::")[0]) != nodeid.split("::")[0]:
            errors.append(f"{nodeid}: JUnit file disagrees with classname")
        if any(child.tag not in {"properties", "system-out", "system-err"} for child in case):
            errors.append(f"{nodeid}: JUnit has skip/failure/error or an unknown outcome element")
        errors.extend(
            f"{nodeid}: JUnit {attribute} is not passed"
            for attribute in ("status", "result")
            if attribute in case.attrib and case.attrib[attribute] != "passed"
        )
    for nodeid, result in results.items():
        if result["junit_count"] != 1:
            errors.append(f"{nodeid}: expected one JUnit testcase, found {result['junit_count']}")


def validate_outcomes(junit: Path | str, events: Path | str) -> dict:
    """Return a JSON-safe PASS/STOP report; malformed evidence always means STOP."""
    errors: list[str] = []
    results = {nodeid: {"events": [], "junit_count": 0} for nodeid in REQUIRED_NODEIDS}
    digests = {}
    for label, path, limit, loader, validator in (
        ("events", Path(events), MAX_EVENTS_BYTES, _load_events, _validate_events),
        ("junit", Path(junit), MAX_JUNIT_BYTES, _load_junit, _validate_junit),
    ):
        try:
            data = _read_bounded(path, limit)
            digests[label] = hashlib.sha256(data).hexdigest()
            validator(loader(data), errors, results)
        except (OSError, ValueError, UnicodeError, ET.ParseError, RecursionError) as exc:
            errors.append(f"{label}: unreadable or invalid evidence ({type(exc).__name__})")
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": "required-real-test-outcomes",
        "status": "STOP" if errors else "PASS",
        "qualified": not errors,
        "required_count": len(REQUIRED_NODEIDS),
        "sha256": digests,
        "results": results,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.resolve() in {args.junit.resolve(), args.events.resolve()}:
        parser.error("output must not overwrite input evidence")
    report = validate_outcomes(args.junit, args.events)
    try:
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"STOP: cannot preserve outcome report ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"{report['status']}: required real-test outcomes ({len(report['errors'])} errors)")
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
