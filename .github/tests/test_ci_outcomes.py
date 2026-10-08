"""Synthetic outcome evidence only; never execute the production sandbox tests."""

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

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from forge_ci import outcomes  # noqa: E402
from forge_ci.pytest_observer import EVENTS_ENV, RequiredOutcomeObserver  # noqa: E402


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
        "events": [
            {"nodeid": nodeid, "when": phase, "outcome": "passed",
             "wasxfail_present": False, "wasxfail": None}
            for nodeid in outcomes.REQUIRED_NODEIDS for phase in outcomes.PHASES
        ],
        "errors": [],
        "overflow": False,
    }
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", tests="11", failures="0", errors="0", skipped="0")
    for nodeid in outcomes.REQUIRED_NODEIDS:
        filename, name = nodeid.split("::")
        ET.SubElement(suite, "testcase", classname=filename[:-3].replace("/", "."), name=name)
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
        suite.set("tests", "10")
    elif change == "duplicate":
        suite.append(copy.deepcopy(case))
        suite.set("tests", "12")
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
    for case, nodeid in zip(evidence[2][0], outcomes.REQUIRED_NODEIDS, strict=True):
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
    suite.set("tests", "14")
    suite.set("skipped", "2")
    assert validate(evidence)["qualified"] is True


def test_irrelevant_failure_still_disagrees_with_exit_zero(evidence):
    suite = evidence[2][0]
    other = ET.SubElement(suite, "testcase", classname="tests.other", name="test_other")
    ET.SubElement(other, "failure")
    suite.set("tests", "12")
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
