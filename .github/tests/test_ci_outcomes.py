"""Offline evidence tests, including one private-write parent; no sandbox execution."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest
from _pytest.subtests import SubtestContext, SubtestReport

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from forge_ci import outcomes  # noqa: E402
from forge_ci.pytest_observer import EVENTS_ENV, RequiredOutcomeObserver  # noqa: E402


def native_accounting():
    return {
        "pytest_version": "9.1.1", "parent_nodeid": outcomes.SUBTEST_PARENT, "collected": 1,
        "reports": [
            {"kind": kind, "nodeid": outcomes.SUBTEST_PARENT, "when": when,
             "outcome": "passed", "wasxfail_present": False, "wasxfail": None,
             "context": copy.deepcopy(context)}
            for kind, when, context in outcomes.SUBTEST_SEQUENCE
        ],
    }


def add_xml_parent(suite):
    return ET.SubElement(suite, "testcase", classname=outcomes.SUBTEST_CLASSNAME,
                         name=outcomes.SUBTEST_PARENT.rsplit("::", 1)[1])


@pytest.fixture
def evidence(tmp_path):
    document = {
        "schema_version": outcomes.SCHEMA_VERSION,
        "required_nodeids": list(outcomes.REQUIRED_NODEIDS),
        "session": {
            "started": True,
            "collection_complete": True,
            "collected": dict.fromkeys(outcomes.REQUIRED_NODEIDS, 1),
            "finished": True,
            "exitstatus": 0,
        },
        "subtest_accounting": native_accounting(),
        "events": [
            {"nodeid": nodeid, "when": phase, "outcome": "passed",
             "wasxfail_present": False, "wasxfail": None}
            for nodeid in outcomes.REQUIRED_NODEIDS for phase in outcomes.PHASES
        ],
        "errors": [],
        "overflow": False,
    }
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests="14", failures="0", errors="0", skipped="0")
    for nodeid in outcomes.REQUIRED_NODEIDS:
        filename, name = nodeid.split("::")
        ET.SubElement(suite, "testcase", classname=filename[:-3].replace("/", "."), name=name)
    add_xml_parent(suite)
    return tmp_path, document, root


def validate(evidence):
    directory, document, root = evidence
    events = directory / "events.json"
    events.write_text(json.dumps(document))
    junit = directory / "junit.xml"
    junit.write_bytes(ET.tostring(root))
    return outcomes.validate_outcomes(junit, events)


def assert_stop(evidence):
    report = validate(evidence)
    assert report["status"] == "STOP"
    assert report["qualified"] is False
    assert report["errors"]
    return report


def test_all_required_cases_and_phases_pass(evidence):
    report = validate(evidence)
    assert report["status"] == "PASS"
    assert report["qualified"] is True
    assert report["required_count"] == 11
    assert report["errors"] == []
    assert set(report["sha256"]) == {"events", "junit"}
    assert all(result["junit_count"] == 1 for result in report["results"].values())


@pytest.mark.parametrize("phase", outcomes.PHASES)
@pytest.mark.parametrize("outcome", ["skipped", "failed", "error", "rerun", "xpassed", None])
def test_nonpass_phase_rejected(evidence, phase, outcome):
    evidence[1]["events"][outcomes.PHASES.index(phase)]["outcome"] = outcome
    assert_stop(evidence)


@pytest.mark.parametrize("outcome", ["passed", "skipped"])
@pytest.mark.parametrize("reason", ["expected failure", "", None])
def test_xfail_and_xpass_rejected_even_with_passing_junit(evidence, outcome, reason):
    event = evidence[1]["events"][1]
    event.update(outcome=outcome, wasxfail_present=True, wasxfail=reason)
    assert_stop(evidence)


@pytest.mark.parametrize("key,value", [("wasxfail_present", None), ("wasxfail_present", 0),
                                        ("wasxfail", "stray reason")])
def test_malformed_wasxfail_fields_fail(evidence, key, value):
    evidence[1]["events"][1][key] = value
    assert_stop(evidence)


@pytest.mark.parametrize("change", ["missing", "duplicate", "out_of_order", "wrong_node", "wrong_phase"])
def test_missing_duplicate_or_changed_event_rejected(evidence, change):
    events = evidence[1]["events"]
    if change == "missing":
        events.pop()
    elif change == "duplicate":
        events.append(copy.deepcopy(events[0]))
    elif change == "out_of_order":
        events[0], events[1] = events[1], events[0]
    elif change == "wrong_node":
        events[0]["nodeid"] += "[different]"
    else:
        events[0]["when"] = "collection"
    assert_stop(evidence)


@pytest.mark.parametrize("change", ["missing", "duplicate", "skip", "xfail", "failure", "error",
                                    "rerun", "unknown", "classname", "file", "xpass_status"])
def test_junit_disagreement_rejected(evidence, change):
    suite = evidence[2][0]
    case = suite[0]
    if change == "missing":
        suite.remove(case)
        suite.set("tests", "13")
    elif change == "duplicate":
        suite.append(copy.deepcopy(case))
        suite.set("tests", "15")
    elif change in {"skip", "xfail", "failure", "error", "rerun", "unknown"}:
        tag = {"skip": "skipped", "xfail": "skipped", "rerun": "rerunFailure"}.get(change, change)
        child = ET.SubElement(case, tag)
        if change == "xfail":
            child.set("type", "pytest.xfail")
    elif change == "classname":
        case.set("classname", "unrelated.test_mutation_patchcorpus")
    elif change == "file":
        case.set("file", "different/test_mutation_patchcorpus.py")
    else:
        case.set("status", "xpassed")
    assert_stop(evidence)


@pytest.mark.parametrize("flag", ["started", "collection_complete", "finished"])
@pytest.mark.parametrize("value", [False, None, 1])
def test_incomplete_or_cancelled_session_fails(evidence, flag, value):
    evidence[1]["session"][flag] = value
    assert_stop(evidence)


@pytest.mark.parametrize("status", [None, False, 1, 2, 3, 4, 5, 130, -9, "0"])
def test_nonzero_or_unknown_session_exit_fails(evidence, status):
    evidence[1]["session"]["exitstatus"] = status
    assert_stop(evidence)


@pytest.mark.parametrize("count", [0, 2, True, "1"])
def test_collection_must_include_each_required_node_once(evidence, count):
    evidence[1]["session"]["collected"][outcomes.REQUIRED_NODEIDS[0]] = count
    assert_stop(evidence)


@pytest.mark.parametrize("change", ["overflow", "writer_error", "manifest", "schema", "extra_field",
                                    "missing_session", "missing_collected"])
def test_metadata_integrity_rejected(evidence, change):
    document = evidence[1]
    if change == "overflow":
        document["overflow"] = True
    elif change == "writer_error":
        document["errors"] = ["write failed"]
    elif change == "manifest":
        document["required_nodeids"].pop()
    elif change == "schema":
        document["schema_version"] = 0
    elif change == "extra_field":
        document["unexpected"] = True
    elif change == "missing_session":
        del document["session"]
    else:
        document["session"]["collected"].pop(outcomes.REQUIRED_NODEIDS[0])
    assert_stop(evidence)


@pytest.mark.parametrize("field", ["tests", "failures", "errors", "skipped"])
def test_junit_summary_must_agree(evidence, field):
    evidence[2][0].set(field, "999")
    assert_stop(evidence)


@pytest.mark.parametrize("family", ["xunit1", "xunit2", "exact_path", "root_suite"])
def test_supported_junit_classname_conventions(evidence, family):
    for case, nodeid in zip(list(evidence[2][0])[:-1], outcomes.REQUIRED_NODEIDS, strict=True):
        if family == "xunit1":
            case.set("file", nodeid.split("::")[0])
            case.set("line", "10")
        elif family == "exact_path":
            case.set("classname", nodeid.split("::")[0])
    if family == "root_suite":
        evidence = evidence[0], evidence[1], evidence[2][0]
    assert validate(evidence)["qualified"] is True


def test_extra_irrelevant_passes_skips_and_xfails_are_allowed(evidence):
    suite = evidence[2][0]
    ET.SubElement(suite, "testcase", classname="tests.other", name="test_other")
    skipped = ET.SubElement(suite, "testcase", classname="tests.other", name="test_skipped")
    ET.SubElement(skipped, "skipped")
    xfail = ET.SubElement(suite, "testcase", classname="tests.other", name="test_xfail")
    ET.SubElement(xfail, "skipped", type="pytest.xfail")
    suite.set("tests", "17")
    suite.set("skipped", "2")
    assert validate(evidence)["qualified"] is True


def test_irrelevant_failure_still_disagrees_with_exit_zero(evidence):
    suite = evidence[2][0]
    other = ET.SubElement(suite, "testcase", classname="tests.other", name="test_other")
    ET.SubElement(other, "failure")
    suite.set("tests", "15")
    suite.set("failures", "1")
    assert_stop(evidence)


@pytest.mark.parametrize("kind", ["events", "junit"])
@pytest.mark.parametrize("corruption", ["missing", "empty", "truncated", "malformed", "overlong"])
def test_missing_malformed_truncated_and_overlong_input_fails(evidence, kind, corruption):
    validate(evidence)
    directory = evidence[0]
    path = directory / ("events.json" if kind == "events" else "junit.xml")
    if corruption == "missing":
        path.unlink()
    elif corruption == "empty":
        path.write_bytes(b"")
    elif corruption == "truncated":
        path.write_bytes(path.read_bytes()[:-5])
    elif corruption == "malformed":
        path.write_bytes(b"{not an XML or JSON object}")
    else:
        limit = outcomes.MAX_EVENTS_BYTES if kind == "events" else outcomes.MAX_JUNIT_BYTES
        path.write_bytes(b" " * (limit + 1))
    report = outcomes.validate_outcomes(directory / "junit.xml", directory / "events.json")
    assert report["qualified"] is False


@pytest.mark.parametrize("bad", [b'{"a":1,"a":2}', b'{"a":NaN}', b'[]', b'"scalar"',
                                  b'\xff', b'{"nested":{"a":1,"a":2}}'])
def test_ambiguous_json_rejected(evidence, bad):
    validate(evidence)
    (evidence[0] / "events.json").write_bytes(bad)
    assert not outcomes.validate_outcomes(evidence[0] / "junit.xml", evidence[0] / "events.json")["qualified"]


@pytest.mark.parametrize("bad", [b'<!DOCTYPE testsuites [<!ENTITY x "payload">]><testsuites/>',
                                  b'<not_junit/>', b'\xff'])
def test_unsafe_or_wrong_xml_rejected(evidence, bad):
    validate(evidence)
    (evidence[0] / "junit.xml").write_bytes(bad)
    assert not outcomes.validate_outcomes(evidence[0] / "junit.xml", evidence[0] / "events.json")["qualified"]


def test_overlong_event_list_rejected(evidence):
    evidence[1]["events"] *= 3
    assert_stop(evidence)


def test_cli_success_failure_and_output_integrity(evidence, capsys):
    assert validate(evidence)["qualified"]
    directory = evidence[0]
    output = directory / "result.json"
    args = ["--events", str(directory / "events.json"), "--junit", str(directory / "junit.xml"),
            "--output", str(output)]
    assert outcomes.main(args) == 0
    assert json.loads(output.read_text())["qualified"] is True
    (directory / "events.json").write_text("truncated")
    assert outcomes.main(args) == 1
    assert json.loads(output.read_text())["qualified"] is False
    with pytest.raises(SystemExit):
        outcomes.main(args[:-1] + [str(directory / "events.json")])
    assert "STOP" in capsys.readouterr().out


def make_synthetic_suite(directory, variant="pass"):
    directory.mkdir(exist_ok=True)
    (directory / "pytest.ini").write_text("[pytest]\n")
    by_file = {}
    for nodeid in outcomes.REQUIRED_NODEIDS:
        filename, name = nodeid.split("::")
        by_file.setdefault(filename, ["import pytest\n"])
        if nodeid == outcomes.REQUIRED_NODEIDS[0]:
            code = {
                "pass": f"def {name}():\n    assert True\n",
                "skip": f"@pytest.mark.skip(reason='synthetic')\ndef {name}():\n    assert True\n",
                "xfail": f"@pytest.mark.xfail(reason='synthetic')\ndef {name}():\n    assert False\n",
                "xpass": f"@pytest.mark.xfail(reason='synthetic')\ndef {name}():\n    assert True\n",
                "strict_xpass": f"@pytest.mark.xfail(reason='synthetic', strict=True)\ndef {name}():\n    assert True\n",
                "failure": f"def {name}():\n    assert False\n",
                "teardown": ("@pytest.fixture\ndef broken():\n    yield\n    assert False\n"
                             f"def {name}(broken):\n    assert True\n"),
                "setup": ("@pytest.fixture\ndef broken():\n    assert False\n"
                          f"def {name}(broken):\n    assert True\n"),
                "missing": "",
            }[variant]
        else:
            code = f"def {name}():\n    assert True\n"
        by_file[filename].append(code)
    by_file["tests/test_phase3.py"] = [
        "import unittest\nclass TestCrossSourceIndex(unittest.TestCase):\n"
        "    def test_rejects_writes_outside_private_root(self):\n"
        "        for writer in ('file_utils', 'gap_detector'):\n"
        "            with self.subTest(writer=writer):\n                self.assertTrue(True)\n"
    ]
    by_file["tests/test_unrelated.py"] = [
        "import pytest\ndef test_extra():\n    assert True\n"
        "@pytest.mark.skip(reason='irrelevant')\ndef test_skip():\n    assert True\n"
    ]
    for filename, parts in by_file.items():
        path = directory / filename
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join(parts))


def run_synthetic(directory, *, observer=True, name="observed", family="xunit2", extra=()):
    env = dict(os.environ, PYTHONPATH=str(SCRIPTS), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_ADDOPTS", None)
    env.pop(EVENTS_ENV, None)
    events = directory / f"{name}.json"
    junit = directory / f"{name}.xml"
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-o", f"junit_family={family}", f"--junitxml={junit}"]
    if observer:
        env[EVENTS_ENV] = str(events)
        args.extend(["-p", "forge_ci.pytest_observer"])
    args.extend(extra)
    process = subprocess.run(args, cwd=directory, env=env, capture_output=True, text=True, timeout=30)
    return process, junit, events


def junit_observable(path):
    return [
        (case.get("classname"), case.get("name"), tuple(child.tag for child in case))
        for case in ET.parse(path).getroot().iter("testcase")  # noqa: S314 -- generated local fixtures
    ]


@pytest.mark.parametrize("variant", ["pass", "skip", "xfail", "xpass", "strict_xpass", "failure",
                                     "setup", "teardown", "missing"])
def test_actual_pytest_observer_is_report_only_and_validator_is_fail_closed(tmp_path, variant):
    make_synthetic_suite(tmp_path, variant)
    baseline, baseline_junit, _ = run_synthetic(tmp_path, observer=False, name="baseline")
    observed, junit, events = run_synthetic(tmp_path)
    assert observed.returncode == baseline.returncode, observed.stdout + observed.stderr
    assert junit_observable(junit) == junit_observable(baseline_junit)
    report = outcomes.validate_outcomes(junit, events)
    assert report["qualified"] is (variant == "pass"), report
    document = json.loads(events.read_text())
    assert set(event["nodeid"] for event in document["events"]) <= set(outcomes.REQUIRED_NODEIDS)
    assert document["session"]["finished"] is True
    assert document["session"]["exitstatus"] == observed.returncode


@pytest.mark.parametrize("family", ["xunit1", "xunit2"])
def test_actual_pytest_junit_families(tmp_path, family):
    make_synthetic_suite(tmp_path)
    process, junit, events = run_synthetic(tmp_path, family=family)
    assert process.returncode == 0, process.stdout + process.stderr
    assert outcomes.validate_outcomes(junit, events)["qualified"]


def test_actual_duplicate_collection_fails(tmp_path):
    make_synthetic_suite(tmp_path)
    process, junit, events = run_synthetic(tmp_path, extra=("tests", "tests", "--keep-duplicates"))
    assert process.returncode == 0, process.stdout + process.stderr
    report = outcomes.validate_outcomes(junit, events)
    assert not report["qualified"]
    assert any("exactly once" in error for error in report["errors"])


def test_existing_success_evidence_cannot_be_reused(tmp_path):
    make_synthetic_suite(tmp_path)
    process, junit, events = run_synthetic(tmp_path)
    assert process.returncode == 0
    assert outcomes.validate_outcomes(junit, events)["qualified"]
    process, junit, events = run_synthetic(tmp_path)
    assert process.returncode == 0
    assert not outcomes.validate_outcomes(junit, events)["qualified"]


def test_observer_bounded_incomplete_and_overlong_reason(tmp_path):
    path = tmp_path / "events.json"
    observer = RequiredOutcomeObserver(path)
    observer.pytest_sessionstart(SimpleNamespace())
    observer.pytest_collection_finish(SimpleNamespace(items=[
        SimpleNamespace(nodeid=nodeid) for nodeid in outcomes.REQUIRED_NODEIDS
    ]))
    report = SimpleNamespace(nodeid=outcomes.REQUIRED_NODEIDS[0], when="call", outcome="passed",
                             wasxfail="x" * (outcomes.MAX_XFAIL_REASON + 1))
    for _ in range(outcomes.MAX_EVENTS + 5):
        observer.pytest_runtest_logreport(report)
    document = json.loads(path.read_text())
    assert len(document["events"]) == outcomes.MAX_EVENTS
    assert document["overflow"] is True
    assert document["errors"]
    assert document["session"]["finished"] is False
    assert path.stat().st_size <= outcomes.MAX_EVENTS_BYTES
    observer.pytest_sessionfinish(SimpleNamespace(), 2)
    assert json.loads(path.read_text())["session"]["exitstatus"] == 2


def test_no_env_means_no_implicit_observer(monkeypatch):
    from forge_ci.pytest_observer import pytest_configure
    monkeypatch.delenv(EVENTS_ENV, raising=False)
    config = SimpleNamespace(pluginmanager=SimpleNamespace(register=lambda *args: pytest.fail("registered")))
    pytest_configure(config)


@pytest.mark.parametrize("kind", ["fifo", "symlink"])
def test_nonregular_evidence_rejected_without_waiting(evidence, kind):
    validate(evidence)
    directory = evidence[0]
    path = directory / "not-regular"
    if kind == "fifo":
        os.mkfifo(path)
    else:
        path.symlink_to(directory / "events.json")
    assert not outcomes.validate_outcomes(directory / "junit.xml", path)["qualified"]


def test_snapshot_write_failure_cannot_publish_finished_success(tmp_path, monkeypatch):
    path = tmp_path / "events.json"
    observer = RequiredOutcomeObserver(path)
    observer.pytest_sessionstart(SimpleNamespace())
    initial = path.read_bytes()

    def fail_fsync(fd):
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    observer.pytest_sessionfinish(SimpleNamespace(), 0)
    assert path.read_bytes() == initial
    assert json.loads(path.read_text())["session"]["finished"] is False
    assert observer.document["errors"] == ["observer write failed"]
    assert not list(tmp_path.glob(".forge-outcomes-*"))


def test_invalid_junit_case_placement_rejected(evidence):
    root = evidence[2]
    root.extend(list(root[0]))
    root.remove(root[0])
    assert_stop(evidence)


def test_cancel_after_all_required_reports_still_fails(tmp_path):
    import time

    make_synthetic_suite(tmp_path)
    baseline, junit, _ = run_synthetic(tmp_path, observer=False, name="baseline")
    assert baseline.returncode == 0
    marker = tmp_path / "waiting"
    (tmp_path / "tests/test_zz_wait.py").write_text(
        "import time\nfrom pathlib import Path\n"
        f"def test_wait():\n    Path({str(marker)!r}).touch()\n    time.sleep(60)\n"
    )
    events = tmp_path / "cancelled.json"
    env = dict(os.environ, PYTHONPATH=str(SCRIPTS), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               FORGE_CI_REQUIRED_EVENTS=str(events))
    env.pop("PYTEST_ADDOPTS", None)
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
               "-p", "forge_ci.pytest_observer"]
    with subprocess.Popen(command, cwd=tmp_path, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) as process:
        deadline = time.monotonic() + 10
        try:
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert marker.exists(), "synthetic test never reached cancellation point"
        finally:
            process.terminate()
            process.communicate(timeout=10)
    document = json.loads(events.read_text())
    assert len(document["events"]) == 33
    assert document["session"]["finished"] is False
    assert document["session"]["exitstatus"] is None
    assert not outcomes.validate_outcomes(junit, events)["qualified"]


@pytest.mark.parametrize("change", ["missing", "extra_key", "wrong_version", "wrong_parent",
    "uncollected", "collected_twice", "boolean_collection", "string_collection", "wrong_type",
    "native_as_ordinary", "ordinary_as_native", "wrong_node", "swapped", "missing_report",
    "extra_report", "duplicate_context", "bare_repr", "extra_context_key", "extra_kwarg", "message",
    "missing_context", "ordinary_context", "wrong_phase", "wrong_order", "failed", "skipped",
    "xfail", "boolean_xfail", "stray_xfail", "missing_field", "extra_field", "not_list"])
def test_native_accounting_rejects_every_unbound_shape(evidence, change):
    accounting = evidence[1]["subtest_accounting"]
    reports = accounting["reports"]
    native = reports[1]
    if change == "missing":
        del evidence[1]["subtest_accounting"]
    elif change == "extra_key":
        accounting["extra"] = True
    elif change == "wrong_version":
        accounting["pytest_version"] = "9.1.2"
    elif change == "wrong_parent":
        accounting["parent_nodeid"] += "_other"
    elif change in {"uncollected", "collected_twice", "boolean_collection", "string_collection"}:
        accounting["collected"] = {"uncollected": 0, "collected_twice": 2,
                                   "boolean_collection": True, "string_collection": "1"}[change]
    elif change in {"wrong_type", "native_as_ordinary"}:
        native["kind"] = "pytest_subtests.SubTestReport" if change == "wrong_type" else outcomes.ORDINARY_REPORT
    elif change == "ordinary_as_native":
        reports[0]["kind"] = outcomes.NATIVE_REPORT
    elif change == "wrong_node":
        native["nodeid"] += "_other"
    elif change == "swapped":
        reports[1], reports[2] = reports[2], reports[1]
    elif change == "missing_report":
        reports.pop(2)
    elif change == "extra_report":
        reports.append(copy.deepcopy(reports[-1]))
    elif change == "duplicate_context":
        reports[2]["context"] = copy.deepcopy(native["context"])
    elif change == "bare_repr":
        native["context"]["kwargs"]["writer"] = "file_utils"
    elif change == "extra_context_key":
        native["context"]["extra"] = None
    elif change == "extra_kwarg":
        native["context"]["kwargs"]["extra"] = "value"
    elif change == "message":
        native["context"]["msg"] = "file_utils"
    elif change == "missing_context":
        native["context"] = None
    elif change == "ordinary_context":
        reports[0]["context"] = copy.deepcopy(native["context"])
    elif change == "wrong_phase":
        native["when"] = "setup"
    elif change == "wrong_order":
        reports[0], reports[-1] = reports[-1], reports[0]
    elif change in {"failed", "skipped"}:
        native["outcome"] = change
    elif change in {"xfail", "boolean_xfail"}:
        native["wasxfail_present"] = True if change == "xfail" else 0
    elif change == "stray_xfail":
        native["wasxfail"] = "unexpected"
    elif change == "missing_field":
        del native["outcome"]
    elif change == "extra_field":
        native["extra"] = True
    else:
        accounting["reports"] = {}
    assert_stop(evidence)


@pytest.mark.parametrize("change", ["missing", "duplicate", "file", "classname", "skip", "failure",
                                    "error", "unknown", "status", "result"])
def test_native_xml_parent_must_be_one_canonical_pass(evidence, change):
    suite = evidence[2][0]
    parent = suite[-1]
    if change == "missing":
        suite.remove(parent)
    elif change == "duplicate":
        suite.append(copy.deepcopy(parent))
    elif change in {"file", "classname"}:
        parent.set(change, "wrong")
    elif change in {"status", "result"}:
        parent.set(change, "skipped")
    else:
        ET.SubElement(parent, "skipped" if change == "skip" else change)
    assert_stop(evidence)


@pytest.mark.parametrize("parent_suite", [0, 1])
def test_native_counts_attributed_only_to_containing_suite_and_root(evidence, parent_suite):
    root = evidence[2]
    root.set("tests", "15")
    other = ET.SubElement(root, "testsuite", tests="1", failures="0", errors="0", skipped="0")
    ET.SubElement(other, "testcase", classname="tests.other", name="test_other")
    if parent_suite == 1:
        parent = root[0][-1]
        root[0].remove(parent)
        other.append(parent)
        root[0].set("tests", "11")
        other.set("tests", "4")
    assert validate(evidence)["qualified"]
    root[parent_suite].set("tests", str(int(root[parent_suite].get("tests")) - 2))
    root[1 - parent_suite].set("tests", str(int(root[1 - parent_suite].get("tests")) + 2))
    assert_stop(evidence)  # The root still agrees; the misplaced suite count cannot pass.


def test_nested_junit_suites_remain_rejected(evidence):
    nested = ET.SubElement(evidence[2][0], "testsuite", tests="1")
    ET.SubElement(nested, "testcase", classname="tests.other", name="test_other")
    assert_stop(evidence)


@pytest.mark.parametrize("declared", ["12", "13", "15", "16", "14.0", "+14"])
def test_native_counts_require_exact_derived_summary(evidence, declared):
    evidence[2][0].set("tests", declared)
    assert_stop(evidence)


def test_unexplained_plus_two_and_old_schema_never_qualify(evidence):
    evidence[1]["schema_version"] = 1
    del evidence[1]["subtest_accounting"]
    assert_stop(evidence)


def make_report(kind=pytest.TestReport, nodeid=outcomes.SUBTEST_PARENT, when="call", **kwargs):
    return kind(nodeid=nodeid, location=("tests/test_phase3.py", 0, "test_parent"), keywords={},
                outcome="passed", longrepr=None, when=when, **kwargs)


@pytest.mark.parametrize("change", ["foreign_native", "required_native", "native_subclass", "ordinary_subclass",
                                    "duck_report", "ordinary_context", "missing_context", "wrong_kwargs",
                                    "bare_repr", "overlong_context", "wrong_order", "extra_reports"])
def test_observer_rejects_unexpected_native_reports_before_filtering(tmp_path, change):
    class NativeSubclass(SubtestReport):
        pass
    class OrdinarySubclass(pytest.TestReport):
        pass
    observer = RequiredOutcomeObserver(tmp_path / "unused")
    observer.pytest_runtest_logreport(make_report(when="setup"))
    report = make_report(SubtestReport, context=SubtestContext(msg=None, kwargs={"writer": "file_utils"}))
    if change == "foreign_native":
        report.nodeid = "tests/other.py::test_other"
    elif change == "required_native":
        report.nodeid = outcomes.REQUIRED_NODEIDS[0]
    elif change == "native_subclass":
        report = make_report(NativeSubclass, context=report.context)
    elif change == "ordinary_subclass":
        report = make_report(OrdinarySubclass)
    elif change == "duck_report":
        report = SimpleNamespace(nodeid=outcomes.SUBTEST_PARENT, when="call", outcome="passed")
    elif change == "ordinary_context":
        report = make_report(context=report.context)
    elif change == "missing_context":
        del report.context
    elif change == "wrong_kwargs":
        report.context = SubtestContext(msg=None, kwargs={"other": "file_utils"})
    elif change == "bare_repr":
        report.context = SimpleNamespace(msg=None, kwargs={"writer": "file_utils"})
    elif change == "overlong_context":
        report.context = SubtestContext(msg=None, kwargs={"writer": "x" * outcomes.MAX_EVENTS_BYTES})
    elif change == "wrong_order":
        report.context = SubtestContext(msg=None, kwargs={"writer": "gap_detector"})
    elif change == "extra_reports":
        for _ in range(outcomes.MAX_SUBTEST_REPORTS):
            observer.pytest_runtest_logreport(report)
    observer.pytest_runtest_logreport(report)
    assert observer.document["errors"]
    assert len(observer.document["subtest_accounting"]["reports"]) <= outcomes.MAX_SUBTEST_REPORTS
    assert len(json.dumps(observer.document).encode()) <= outcomes.MAX_EVENTS_BYTES


def test_observer_actual_native_parent_has_exact_bounded_records(tmp_path):
    make_synthetic_suite(tmp_path)
    process, junit, events = run_synthetic(tmp_path)
    assert process.returncode == 0, process.stdout + process.stderr
    document = json.loads(events.read_bytes())
    assert document["subtest_accounting"] == native_accounting()
    assert document["errors"] == [] and document["overflow"] is False
    assert outcomes.validate_outcomes(junit, events)["qualified"]


def test_actual_frozen_private_write_parent_pytest_911_format(tmp_path):
    # This exact product parent uses only private temporary files and mocks.
    # Its capture is a format regression, never production sandbox qualification.
    assert pytest.__version__ == "9.1.1"
    repo = SCRIPTS.parents[1]
    events, junit = tmp_path / "parent.json", tmp_path / "parent.xml"
    env = dict(os.environ, PYTHONPATH=str(SCRIPTS), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               FORGE_CI_REQUIRED_EVENTS=str(events), PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTEST_ADDOPTS", None)
    process = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "forge_ci.pytest_observer",
         outcomes.SUBTEST_PARENT, f"--junitxml={junit}"],
        cwd=repo, env=env, capture_output=True, text=True, timeout=30,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    document = json.loads(events.read_bytes())
    assert document["subtest_accounting"] == native_accounting()
    assert document["session"]["finished"] is True and document["session"]["exitstatus"] == 0
    assert document["errors"] == [] and document["overflow"] is False
    root = outcomes._load_junit(junit.read_bytes())
    assert len(list(root.iter("testcase"))) == 1 and root[0].get("tests") == "3"
    assert not outcomes.validate_outcomes(junit, events)["qualified"]  # Eleven gates were not executed.


@pytest.mark.parametrize("index", range(outcomes.MAX_SUBTEST_REPORTS))
@pytest.mark.parametrize("field,value", [("outcome", "failed"), ("outcome", "skipped"),
                                         ("wasxfail_present", True), ("wasxfail", "xfail")])
def test_every_parent_report_must_pass_without_xfail(evidence, index, field, value):
    evidence[1]["subtest_accounting"]["reports"][index][field] = value
    assert_stop(evidence)


@pytest.mark.parametrize("declared", ["12", "13", "15", "16"])
def test_root_aggregate_must_match_derived_native_counts(evidence, declared):
    evidence[2].set("tests", declared)
    assert_stop(evidence)


def test_duplicate_parent_in_other_suite_is_not_an_additional_allowance(evidence):
    other = ET.SubElement(evidence[2], "testsuite", tests="3")
    add_xml_parent(other)
    evidence[2].set("tests", "17")
    assert_stop(evidence)
