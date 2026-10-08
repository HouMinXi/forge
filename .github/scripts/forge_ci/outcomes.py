"""Fail-closed validation of the 27 reviewed real-test results.

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

REQUIRED_FULL_NODEIDS = (
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
REQUIRED_INTEGRATION_NODEIDS = (
    'tests/test_fixval_fail_closed.py::test_real_owned_fixval_transaction[red]',
    'tests/test_fixval_fail_closed.py::test_real_owned_fixval_transaction[hollow]',
    'tests/test_fixval_fail_closed.py::test_real_owned_fixval_transaction[setup]',
    'tests/test_fixval_fail_closed.py::test_real_owned_fixval_transaction[collection]',
    'tests/test_fixval_fail_closed.py::test_real_owned_fixval_transaction[internal]',
    'tests/test_fixval_fail_closed.py::test_real_owned_fixval_transaction[empty]',
    'tests/test_fixval_fail_closed.py::test_real_owned_fixval_transaction[strict_xpass]',
    'tests/test_fixval_fail_closed.py::test_real_owned_fixval_transaction[xfail]',
    'tests/test_fixval_fail_closed.py::test_real_owned_fixval_transaction[unittest_unexpected_success]',
    'tests/test_fixval_fail_closed.py::test_real_bounded_owner_cleanup_boundary[complete]',
    'tests/test_fixval_fail_closed.py::test_real_bounded_owner_cleanup_boundary[timeout]',
    'tests/test_fixval_fail_closed.py::test_real_bounded_owner_cleanup_boundary[interrupt]',
    'tests/test_fixval_fail_closed.py::test_real_public_terminal_fixval_topology[deletion]',
    'tests/test_fixval_fail_closed.py::test_real_public_terminal_fixval_topology[rename]',
    'tests/test_fixval_fail_closed.py::test_real_public_terminal_fixval_topology[binary]',
    'tests/test_fixval_fail_closed.py::test_real_public_terminal_fixval_topology[restoration]',
)
REQUIRED_BY_PHASE = {"full": REQUIRED_FULL_NODEIDS, "local-integration": REQUIRED_INTEGRATION_NODEIDS}
REQUIRED_NODEIDS = (*REQUIRED_FULL_NODEIDS, *REQUIRED_INTEGRATION_NODEIDS)
if (len(REQUIRED_FULL_NODEIDS), len(REQUIRED_INTEGRATION_NODEIDS), len(set(REQUIRED_NODEIDS))) != (11, 16, 27):
    raise RuntimeError("required phase manifests must be disjoint exact 11/16 sets")
EVENT_FILES = {"full": "required-events.json", "local-integration": "local-integration-required-events.json"}
PHASES = ("setup", "call", "teardown")
SCHEMA_VERSION = 4
SUBTEST_PYTEST_VERSION = "9.1.1"
SUBTEST_PARENT = "tests/test_phase3.py::TestCrossSourceIndex::test_rejects_writes_outside_private_root"
SUBTEST_CLASSNAME = "tests.test_phase3.TestCrossSourceIndex"
ORDINARY_REPORT = "_pytest.reports.TestReport"
NATIVE_REPORT = "_pytest.subtests.SubtestReport"
SUBTEST_SEQUENCE = (
    (ORDINARY_REPORT, "setup", None),
    (NATIVE_REPORT, "call", {"msg": None, "kwargs": {"writer": "'file_utils'"}}),
    (NATIVE_REPORT, "call", {"msg": None, "kwargs": {"writer": "'gap_detector'"}}),
    (ORDINARY_REPORT, "call", None),
    (ORDINARY_REPORT, "teardown", None),
)
MAX_SUBTEST_REPORTS = len(SUBTEST_SEQUENCE)
MAX_EVENTS_BY_PHASE = {phase: len(nodeids) * 6 for phase, nodeids in REQUIRED_BY_PHASE.items()}
MAX_EVENTS = sum(MAX_EVENTS_BY_PHASE.values())
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


def _validate_events(document: dict, errors: list[str], results: dict, phase: str) -> None:
    required = REQUIRED_BY_PHASE[phase]
    expected_keys = {"schema_version", "required_nodeids", "session", "events", "errors", "overflow",
                     "subtest_accounting", "phase"}
    if document.get("phase") != phase:
        errors.append("observer phase identity mismatch")
    if set(document) != expected_keys:
        errors.append("observer document has missing or unexpected fields")
    if type(document.get("schema_version")) is not int or document["schema_version"] != SCHEMA_VERSION:
        errors.append("observer schema version mismatch")
    if document.get("required_nodeids") != list(required):
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
        if not isinstance(collected, dict) or set(collected) != set(required):
            errors.append("observer collected-node manifest is missing or malformed")
        elif any(type(count) is not int or count != 1 for count in collected.values()):
            errors.append("each required node must be collected exactly once")
    if phase == "full":
        _validate_subtest_accounting(document.get("subtest_accounting"), errors)
    elif document.get("subtest_accounting") is not None:
        errors.append("local integration cannot claim native-subtest accounting")
    events = document.get("events")
    if not isinstance(events, list) or len(events) > MAX_EVENTS_BY_PHASE[phase]:
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


def _validate_subtest_accounting(accounting: object, errors: list[str]) -> None:
    if not isinstance(accounting, dict) or set(accounting) != {
        "pytest_version", "parent_nodeid", "collected", "reports"
    }:
        errors.append("subtest accounting is missing or malformed")
        return
    if accounting["pytest_version"] != SUBTEST_PYTEST_VERSION:
        errors.append("subtest accounting requires pytest 9.1.1")
    if accounting["parent_nodeid"] != SUBTEST_PARENT:
        errors.append("subtest accounting parent mismatch")
    if type(accounting["collected"]) is not int or accounting["collected"] != 1:
        errors.append("subtest parent must be collected exactly once")
    reports = accounting["reports"]
    if not isinstance(reports, list) or len(reports) != MAX_SUBTEST_REPORTS:
        errors.append("subtest accounting requires exactly five ordered reports")
        return
    for index, (report, (kind, when, context)) in enumerate(zip(reports, SUBTEST_SEQUENCE, strict=True)):
        if not isinstance(report, dict) or set(report) != {
            "kind", "nodeid", "when", "outcome", "wasxfail_present", "wasxfail", "context"
        }:
            errors.append(f"malformed subtest parent report {index}")
            continue
        if (report["kind"] != kind or report["nodeid"] != SUBTEST_PARENT or report["when"] != when
                or report["context"] != context):
            errors.append(f"subtest parent report {index} has unexpected type, identity, order or context")
        if (report["outcome"] != "passed" or report["wasxfail_present"] is not False
                or report["wasxfail"] is not None):
            errors.append(f"subtest parent report {index} did not pass without xfail")


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


def _junit_identities(phase: str) -> dict[tuple[str, str], str]:
    identities = {}
    for nodeid in REQUIRED_BY_PHASE[phase]:
        filename, name = nodeid.split("::")
        # pytest's standard xunit1/xunit2 mangling, plus an exact path classname.
        # No basename matching, arbitrary prefixes, suffixes or file-only fallback.
        identities[(filename[:-3].replace("/", "."), name)] = nodeid
        identities[(filename, name)] = nodeid
    return identities


def _junit_summary_errors(root: ET.Element, *, observer: dict | None = None, phase: str | None = None) -> list[str]:
    """Derive exact suite/root counts; only complete bound reports justify native calls."""
    errors: list[str] = []
    native_reports = []
    parent = None
    if observer is not None:
        if type(phase) is not str or phase not in REQUIRED_BY_PHASE:
            return ["observer requires a closed expected phase"]
        results = {nodeid: {"events": [], "junit_count": 0} for nodeid in REQUIRED_BY_PHASE[phase]}
        _validate_events(observer, errors, results, phase)
    if observer is not None and phase == "full":
        parents = [case for case in root.iter("testcase")
                   if (case.get("classname"), case.get("name"))
                   == (SUBTEST_CLASSNAME, SUBTEST_PARENT.rsplit("::", 1)[1])]
        if len(parents) != 1:
            errors.append("subtest parent requires exactly one canonical JUnit testcase")
        else:
            parent = parents[0]
            if parent.get("file", SUBTEST_PARENT.split("::")[0]) != SUBTEST_PARENT.split("::")[0]:
                errors.append("subtest parent JUnit file disagrees with classname")
            if (any(child.tag not in {"properties", "system-out", "system-err"} for child in parent)
                    or any(parent.get(key, "passed") != "passed" for key in ("status", "result"))):
                errors.append("subtest parent JUnit testcase is not an ordinary pass")
        if not errors:
            native_reports = [report for report in observer["subtest_accounting"]["reports"]
                              if report["kind"] == NATIVE_REPORT]
    for suite in root.iter():
        if suite.tag not in {"testsuites", "testsuite"}:
            continue
        cases = list(suite.iter("testcase"))
        counts = {
            "tests": len(cases) + sum(report["nodeid"] == SUBTEST_PARENT
                                      for report in native_reports if parent in cases),
            "failures": sum(case.find("failure") is not None for case in cases),
            "errors": sum(case.find("error") is not None for case in cases),
            "skipped": sum(case.find("skipped") is not None for case in cases),
        }
        for name, count in counts.items():
            if name in suite.attrib:
                value = suite.attrib[name]
                if not value.isascii() or not value.isdigit() or int(value) != count:
                    errors.append(f"JUnit {name} summary disagrees with testcase records")
    return errors


def _validate_junit(root: ET.Element, errors: list[str], results: dict, document: dict | None, phase: str) -> None:
    identities = _junit_identities(phase)
    foreign = {key for other in REQUIRED_BY_PHASE if other != phase for key in _junit_identities(other)}
    cases = list(root.iter("testcase"))
    if not cases:
        errors.append("JUnit contains no testcases")
    errors.extend(_junit_summary_errors(root, observer=document, phase=phase))
    for case in cases:
        if case.find("failure") is not None or case.find("error") is not None:
            errors.append("JUnit contains a failure/error")
        identity = (case.get("classname", ""), case.get("name", ""))
        if identity in foreign:
            errors.append("mandatory JUnit testcase belongs to a different phase")
        nodeid = identities.get(identity)
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


def _integrity_need(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def _report_integrity(report: dict) -> None:
    """Ordinary pytest failures/xfails are valid evidence, never a PASS here."""
    _integrity_need(report["when"] in PHASES and report["outcome"] in ("passed", "failed", "skipped"),
                    "unknown report phase or outcome")
    present, reason = report["wasxfail_present"], report["wasxfail"]
    _integrity_need(type(present) is bool and ((not present and reason is None)
                    or (present and type(reason) is str and len(reason) <= MAX_XFAIL_REASON)),
                    "malformed xfail report fields")


def _ordinary_sequence_integrity(events: list[dict]) -> None:
    order = [event["when"] for event in events]
    _integrity_need(order in (["setup", "call", "teardown"], ["setup", "teardown"]),
                    "incomplete or contradictory ordinary report sequence")
    _integrity_need((events[0]["outcome"] == "passed") == (len(events) == 3),
                    "ordinary call report contradicts setup outcome")


def _native_integrity(accounting: object) -> None:
    _integrity_need(type(accounting) is dict and set(accounting) == {
        "pytest_version", "parent_nodeid", "collected", "reports"}, "malformed native accounting")
    _integrity_need(accounting["pytest_version"] == SUBTEST_PYTEST_VERSION
                    and accounting["parent_nodeid"] == SUBTEST_PARENT
                    and type(accounting["collected"]) is int and accounting["collected"] == 1,
                    "native accounting identity or collection mismatch")
    reports = accounting["reports"]
    _integrity_need(type(reports) is list and 2 <= len(reports) <= MAX_SUBTEST_REPORTS,
                    "incomplete or overlong native reports")
    order = []
    for report in reports:
        _integrity_need(type(report) is dict and set(report) == {
            "kind", "nodeid", "when", "outcome", "wasxfail_present", "wasxfail", "context"},
            "malformed native parent report")
        _report_integrity(report)
        _integrity_need(report["nodeid"] == SUBTEST_PARENT, "wrong native parent report")
        signature = (report["kind"], report["when"], report["context"])
        _integrity_need(signature in SUBTEST_SEQUENCE, "unknown native report type or context")
        order.append(SUBTEST_SEQUENCE.index(signature))
    _integrity_need(order == sorted(set(order)) and order[0] == 0 and order[-1] == 4,
                    "incomplete or contradictory native report order")
    if reports[0]["outcome"] != "passed":
        _integrity_need(order == [0, 4], "native call contradicts failed setup")
    else:
        _integrity_need(3 in order, "native ordinary call report missing")
        native = [index for index in order if index in (1, 2)]
        _integrity_need(native == [1, 2][:len(native)], "native context sequence has a gap")
        call = reports[order.index(3)]
        _integrity_need(call["outcome"] != "passed" or order == list(range(5)),
                        "successful native parent has incomplete subtest reports")


def validate_phase_integrity(junit: bytes, events: bytes | None, *, phase: str, exitstatus: int) -> None:
    """Before the next spawn, require complete evidence, not passing tests.

    Exit 1, failed setup without a call, failed call/teardown and native subtest
    failures are structurally legitimate. They cannot pass final validation.
    """
    _integrity_need(type(phase) is str and phase in ("ownership", *REQUIRED_BY_PHASE)
                    and type(exitstatus) is int and exitstatus in (0, 1), "unknown phase or exit status")
    _integrity_need(type(junit) is bytes and 0 < len(junit) <= MAX_JUNIT_BYTES, "invalid phase JUnit byte bound")
    root = _load_junit(junit)
    _integrity_need(bool(list(root.iter("testcase"))), "phase JUnit has no testcase evidence")
    for case in root.iter("testcase"):
        _integrity_need(all(child.tag in {"properties", "system-out", "system-err", "failure", "error", "skipped"}
                            for child in case), "unknown phase JUnit outcome element")
    for suite in root.iter():
        if suite.tag in {"testsuites", "testsuite"}:
            for name in ("tests", "failures", "errors", "skipped"):
                value = suite.get(name)
                _integrity_need(value is None or (0 < len(value) <= 20 and value.isascii() and value.isdigit()),
                                "malformed phase JUnit count")
    if phase == "ownership":
        _integrity_need(events is None, "ownership cannot have observer evidence")
        _integrity_need(not _junit_summary_errors(root), "ownership JUnit counts disagree")
        return
    _integrity_need(type(events) is bytes and 0 < len(events) <= MAX_EVENTS_BYTES, "missing or overlong phase observer")
    document = _load_events(events)
    _integrity_need(set(document) == {"schema_version", "phase", "required_nodeids", "session", "events",
                                      "errors", "overflow", "subtest_accounting"}, "malformed phase observer fields")
    _integrity_need(type(document["schema_version"]) is int and document["schema_version"] == SCHEMA_VERSION
                    and document["phase"] == phase and document["required_nodeids"] == list(REQUIRED_BY_PHASE[phase]),
                    "phase observer schema, identity or manifest mismatch")
    _integrity_need(document["errors"] == [] and document["overflow"] is False,
                    "phase observer error or overflow")
    session = document["session"]
    _integrity_need(type(session) is dict and set(session) == {
        "started", "collection_complete", "collected", "finished", "exitstatus"}, "malformed observer session")
    _integrity_need(all(session[key] is True for key in ("started", "collection_complete", "finished"))
                    and type(session["exitstatus"]) is int and session["exitstatus"] == exitstatus,
                    "incomplete observer session or contradictory exit status")
    collected = session["collected"]
    _integrity_need(type(collected) is dict and set(collected) == set(REQUIRED_BY_PHASE[phase])
                    and all(type(count) is int and count == 1 for count in collected.values()),
                    "phase mandatory collection is missing or ambiguous")
    reports = document["events"]
    _integrity_need(type(reports) is list and len(reports) <= MAX_EVENTS_BY_PHASE[phase],
                    "malformed or overlong observer events")
    per_node = {nodeid: [] for nodeid in REQUIRED_BY_PHASE[phase]}
    for report in reports:
        _integrity_need(type(report) is dict and set(report) == {
            "nodeid", "when", "outcome", "wasxfail_present", "wasxfail"}, "malformed observer event")
        _integrity_need(type(report["nodeid"]) is str and report["nodeid"] in per_node,
                        "observer report is outside its phase manifest")
        _report_integrity(report)
        per_node[report["nodeid"]].append(report)
    for group in per_node.values():
        _ordinary_sequence_integrity(group)
    if phase == "full":
        _native_integrity(document["subtest_accounting"])
        # Pytest can emit extra JUnit records for failing native subtests.
        # Only final successful native accounting justifies exact full counts.
    else:
        _integrity_need(document["subtest_accounting"] is None, "local observer has native accounting")
        _integrity_need(not _junit_summary_errors(root), "local JUnit counts disagree")


def validate_phase_outcomes(junit: Path | str, events: Path | str, *, phase: str) -> dict:
    """Validate one fixed phase; this cannot certify the combined 27-case gate."""
    if type(phase) is not str or phase not in REQUIRED_BY_PHASE:
        raise EvidenceError("unknown mandatory phase")
    errors: list[str] = []
    results = {nodeid: {"events": [], "junit_count": 0} for nodeid in REQUIRED_BY_PHASE[phase]}
    digests = {}
    document = None
    events_bytes = None
    for label, path, limit, loader, validator in (
        ("events", Path(events), MAX_EVENTS_BYTES, _load_events, _validate_events),
        ("junit", Path(junit), MAX_JUNIT_BYTES, _load_junit, _validate_junit),
    ):
        try:
            data = _read_bounded(path, limit)
            digests[label] = hashlib.sha256(data).hexdigest()
            if label == "events":
                events_bytes = len(data)
            loaded = loader(data)
            if label == "events":
                document = loaded
                validator(loaded, errors, results, phase)
            else:
                validator(loaded, errors, results, document, phase)
        except (OSError, ValueError, UnicodeError, ET.ParseError, RecursionError) as exc:
            errors.append(f"{label}: unreadable or invalid evidence ({type(exc).__name__})")
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": "required-phase-test-outcomes",
        "phase": phase,
        "status": "STOP" if errors else "PASS",
        "qualified": not errors,
        "required_count": len(REQUIRED_BY_PHASE[phase]),
        "events_bytes": events_bytes,
        "sha256": digests,
        "results": results,
        "errors": errors,
    }


def validate_outcomes(full_junit: Path | str, full_events: Path | str,
                      integration_junit: Path | str, integration_events: Path | str) -> dict:
    """Exactly two phase-bound successful manifests and one aggregate byte cap."""
    reports = {
        "full": validate_phase_outcomes(full_junit, full_events, phase="full"),
        "local-integration": validate_phase_outcomes(integration_junit, integration_events, phase="local-integration"),
    }
    errors = [f"{phase}: {error}" for phase, report in reports.items() for error in report["errors"]]
    sizes = [report["events_bytes"] for report in reports.values()]
    total = sum(sizes) if all(type(size) is int for size in sizes) else None
    if total is None or total > MAX_EVENTS_BYTES:
        errors.append("aggregate observer bytes missing or exceed the 64 KiB bound")
    results = {nodeid: result for report in reports.values() for nodeid, result in report["results"].items()}
    if len(results) != 27 or set(results) != set(REQUIRED_NODEIDS):
        errors.append("combined mandatory results do not match the 27-case manifest")
    return {
        "schema_version": SCHEMA_VERSION, "gate": "required-real-test-outcomes",
        "status": "STOP" if errors else "PASS", "qualified": not errors,
        "required_count": len(REQUIRED_NODEIDS),
        "required_counts": {phase: report["required_count"] for phase, report in reports.items()},
        "events_bytes": total,
        "sha256": {phase: report["sha256"] for phase, report in reports.items()},
        "phases": reports, "results": results, "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("full-junit", "full-events", "integration-junit", "integration-events", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args(argv)
    inputs = (args.full_junit, args.full_events, args.integration_junit, args.integration_events)
    if args.output.resolve() in {path.resolve() for path in inputs}:
        parser.error("output must not overwrite input evidence")
    report = validate_outcomes(*inputs)
    try:
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"STOP: cannot preserve outcome report ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"{report['status']}: required real-test outcomes ({len(report['errors'])} errors)")
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
